import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime, time as dt_time
from collections import deque

app = Flask(__name__)

@app.route('/')
def home():
    return "High Probability Scalping Bot is running!"

# Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT',
    'DOGE/USDT', 'BNB/USDT', 'ADA/USDT'
]

EXCHANGE = ccxt.binance({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
})

EXCHANGE.load_markets()

# Track alerts and performance
last_alerts = {}
signal_history = deque(maxlen=100)  # Track last 100 signals for quality monitoring
daily_trades = 0
last_reset_date = datetime.now().date()

# Probability scoring system
PROBABILITY_THRESHOLD = 75  # Minimum 75% probability to send alert

def calculate_probability_score(indicators):
    """Calculate overall trade probability based on multiple factors"""
    score = 0
    max_score = 100
    
    # Factor 1: RSI extremeness (20 points)
    if indicators['rsi'] < 25:
        score += 20
    elif indicators['rsi'] < 30:
        score += 15
    elif indicators['rsi'] < 35:
        score += 10
    elif indicators['rsi'] > 75:
        score += 20
    elif indicators['rsi'] > 70:
        score += 15
    elif indicators['rsi'] > 65:
        score += 10
    
    # Factor 2: ADX trend strength (20 points)
    if indicators['adx'] > 40:
        score += 20
    elif indicators['adx'] > 30:
        score += 15
    elif indicators['adx'] > 25:
        score += 10
    
    # Factor 3: Bollinger Band position (15 points)
    if indicators['bb_position'] < 0.05 or indicators['bb_position'] > 0.95:
        score += 15
    elif indicators['bb_position'] < 0.1 or indicators['bb_position'] > 0.9:
        score += 10
    
    # Factor 4: Volume confirmation (15 points)
    if indicators['volume_ratio'] > 2.5:
        score += 15
    elif indicators['volume_ratio'] > 2.0:
        score += 12
    elif indicators['volume_ratio'] > 1.5:
        score += 8
    
    # Factor 5: MACD alignment (10 points)
    if indicators['macd_aligned']:
        score += 10
    
    # Factor 6: Multiple timeframe alignment (10 points)
    if indicators['mtf_aligned']:
        score += 10
    
    # Factor 7: Support/Resistance confluence (10 points)
    if indicators['sr_confluence']:
        score += 10
    
    return min(score, max_score)

def calculate_multiple_timeframe_alignment(symbol, direction='long'):
    """Check if 3m and 15m timeframes align"""
    try:
        # Get 15m data
        ohlcv_15m = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=50)
        df_15m = pd.DataFrame(ohlcv_15m, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        
        # Calculate 15m RSI
        rsi_15m = calculate_rsi(df_15m, period=14)
        
        # Calculate 15m ADX
        adx_15m = calculate_adx(df_15m, period=14)
        
        if direction == 'long':
            # For long: 15m should show bullish bias (RSI > 40, ADX rising)
            return rsi_15m > 40 and adx_15m > 20
        else:
            # For short: 15m should show bearish bias (RSI < 60, ADX rising)
            return rsi_15m < 60 and adx_15m > 20
            
    except Exception as e:
        print(f"MTF error: {e}")
        return False

def find_support_resistance(df, current_price, window=20):
    """Find nearby support and resistance levels"""
    highs = df['high'].tail(window).values
    lows = df['low'].tail(window).values
    
    # Find pivot points
    resistance_levels = []
    support_levels = []
    
    for i in range(2, len(highs)-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistance_levels.append(highs[i])
        
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            support_levels.append(lows[i])
    
    # Check if current price is near any level (within 0.5%)
    near_resistance = any(abs(current_price - level) / current_price < 0.005 for level in resistance_levels)
    near_support = any(abs(current_price - level) / current_price < 0.005 for level in support_levels)
    
    return near_support, near_resistance

def calculate_order_flow_imbalance(df, period=10):
    """Calculate buying vs selling pressure using volume analysis"""
    close = df['close']
    volume = df['vol']
    
    # Calculate volume-weighted price change
    price_change = close.diff()
    buying_volume = volume.where(price_change > 0, 0).rolling(window=period).sum()
    selling_volume = volume.where(price_change < 0, 0).rolling(window=period).sum()
    
    total_volume = buying_volume + selling_volume
    if total_volume.iloc[-1] > 0:
        imbalance = (buying_volume.iloc[-1] - selling_volume.iloc[-1]) / total_volume.iloc[-1]
        return imbalance
    return 0

def calculate_elders_impulse(df):
    """Elder's Impulse System - combines EMA and MACD histogram"""
    close = df['close']
    
    # 13-period EMA
    ema13 = close.ewm(span=13, adjust=False).mean()
    
    # MACD histogram (already calculated)
    macd_line, signal_line, histogram = calculate_macd(df)
    
    # Impulse: 1=bullish, -1=bearish, 0=neutral
    if close.iloc[-1] > ema13.iloc[-1] and histogram > 0:
        return 1  # Bullish impulse
    elif close.iloc[-1] < ema13.iloc[-1] and histogram < 0:
        return -1  # Bearish impulse
    else:
        return 0  # Neutral

def is_optimal_trading_time():
    """Only trade during high liquidity periods"""
    current_time = datetime.now().time()
    
    # Best trading hours (UTC)
    # London: 8-17 UTC, New York: 13-22 UTC, Asia: 0-9 UTC
    optimal_times = [
        (dt_time(0, 0), dt_time(9, 0)),    # Asia session
        (dt_time(8, 0), dt_time(17, 0)),   # London session
        (dt_time(13, 0), dt_time(22, 0)),  # New York session
    ]
    
    for start, end in optimal_times:
        if start <= current_time <= end:
            return True
    
    # Weekend check (lower liquidity)
    if datetime.now().weekday() >= 5:  # Saturday or Sunday
        return False
    
    return False

def calculate_volatility_regime(df, period=20):
    """Determine if volatility is optimal for scalping"""
    returns = df['close'].pct_change().dropna()
    current_vol = returns.tail(5).std()
    avg_vol = returns.tail(period).std()
    
    if avg_vol > 0:
        vol_ratio = current_vol / avg_vol
        # Optimal volatility: not too low (no movement) and not too high (too risky)
        return 0.5 < vol_ratio < 2.0
    return True

def calculate_bollinger_bands(df, period=20, std_dev=2):
    """Calculate Bollinger Bands"""
    close = df['close']
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    middle = sma
    
    return upper.iloc[-1], middle.iloc[-1], lower.iloc[-1]

def calculate_rsi(df, period=14):
    """Calculate RSI"""
    close = df['close']
    delta = close.diff()
    
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi.iloc[-1]

def calculate_adx(df, period=14):
    """ADX calculation"""
    try:
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        n = len(df)
        if n < period + 1:
            return 0
        
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        
        for i in range(1, n):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i-1])
            lc = abs(low[i] - close[i-1])
            tr[i] = max(hl, hc, lc)
            
            up_move = high[i] - high[i-1]
            down_move = low[i-1] - low[i]
            
            if up_move > down_move and up_move > 0:
                plus_dm[i] = up_move
            else:
                plus_dm[i] = 0
                
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
            else:
                minus_dm[i] = 0
        
        atr = np.zeros(n)
        smooth_plus_dm = np.zeros(n)
        smooth_minus_dm = np.zeros(n)
        
        atr[period] = np.sum(tr[1:period+1]) / period
        smooth_plus_dm[period] = np.sum(plus_dm[1:period+1]) / period
        smooth_minus_dm[period] = np.sum(minus_dm[1:period+1]) / period
        
        for i in range(period+1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
            smooth_plus_dm[i] = (smooth_plus_dm[i-1] * (period - 1) + plus_dm[i]) / period
            smooth_minus_dm[i] = (smooth_minus_dm[i-1] * (period - 1) + minus_dm[i]) / period
        
        plus_di = np.zeros(n)
        minus_di = np.zeros(n)
        dx = np.zeros(n)
        
        for i in range(period, n):
            if atr[i] != 0:
                plus_di[i] = 100 * smooth_plus_dm[i] / atr[i]
                minus_di[i] = 100 * smooth_minus_dm[i] / atr[i]
                
                di_sum = plus_di[i] + minus_di[i]
                if di_sum != 0:
                    dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / di_sum
        
        adx = np.zeros(n)
        adx[period + period - 1] = np.sum(dx[period:period+period-1]) / (period - 1)
        
        for i in range(period + period, n):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period
        
        result = adx[-1] if not np.isnan(adx[-1]) else 0
        return round(result, 2)
        
    except Exception as e:
        print(f"ADX error: {e}")
        return 0

def calculate_choppiness_index(df, period=14):
    """Calculate Choppiness Index"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        sum_tr = tr.rolling(window=period).sum()
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()
        
        price_range = highest_high - lowest_low
        price_range = price_range.replace(0, np.nan)
        
        choppiness = 100 * np.log10(sum_tr / price_range) / np.log10(period)
        
        result = choppiness.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
        
    except Exception as e:
        print(f"Choppiness error: {e}")
        return 50

def calculate_macd(df, fast=12, slow=26, signal=9):
    """Calculate MACD"""
    close = df['close']
    
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    
    return macd_line.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]

def calculate_volume_surge(df, period=20):
    """Check for volume surge"""
    current_volume = df['vol'].iloc[-1]
    avg_volume = df['vol'].rolling(window=period).mean().iloc[-1]
    
    if avg_volume > 0:
        volume_ratio = current_volume / avg_volume
        return volume_ratio
    return 1

def send_alert(message):
    if TOKEN and CHAT_ID:
        try:
            requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={"chat_id": CHAT_ID, "text": message},
                timeout=10
            )
        except Exception as e:
            print(f"Telegram error: {e}")

def run_bot():
    print("High Probability Scalping Bot started...")
    send_alert("✅ HIGH PROBABILITY SCALPING BOT STARTED (75%+ Probability Threshold)")

    while True:
        # Reset daily trade counter
        current_date = datetime.now().date()
        global daily_trades, last_reset_date
        if current_date != last_reset_date:
            daily_trades = 0
            last_reset_date = current_date
        
        for symbol in SYMBOLS:
            try:
                if symbol not in EXCHANGE.markets:
                    continue

                # Get 3m data
                ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe='3m', limit=150)
                
                if len(ohlcv) < 80:
                    continue

                df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                current_price = df['close'].iloc[-1]
                
                # Calculate all indicators
                upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(df, period=20, std_dev=2)
                rsi = calculate_rsi(df, period=14)
                adx = calculate_adx(df, period=14)
                chop = calculate_choppiness_index(df, period=14)
                macd_line, signal_line, macd_hist = calculate_macd(df)
                volume_ratio = calculate_volume_surge(df)
                
                # New probability boosters
                bb_position = (current_price - lower_bb) / (upper_bb - lower_bb) if upper_bb != lower_bb else 0.5
                order_flow = calculate_order_flow_imbalance(df)
                elders_impulse = calculate_elders_impulse(df)
                near_support, near_resistance = find_support_resistance(df, current_price)
                volatility_optimal = calculate_volatility_regime(df)
                trading_time_optimal = is_optimal_trading_time()
                
                # Check for LONG setup
                long_base_conditions = (
                    current_price <= lower_bb * 1.005 and
                    rsi < 35 and
                    adx > 25 and
                    chop < 45 and
                    macd_hist > -0.001
                )
                
                # Check for SHORT setup
                short_base_conditions = (
                    current_price >= upper_bb * 0.995 and
                    rsi > 65 and
                    adx > 25 and
                    chop < 45 and
                    macd_hist < 0.001
                )
                
                # Calculate probability scores
                if long_base_conditions:
                    # Check multiple timeframe alignment
                    mtf_aligned = calculate_multiple_timeframe_alignment(symbol, 'long')
                    
                    # Prepare indicators dict for scoring
                    indicators = {
                        'rsi': rsi,
                        'adx': adx,
                        'bb_position': bb_position,
                        'volume_ratio': volume_ratio,
                        'macd_aligned': macd_hist > 0,
                        'mtf_aligned': mtf_aligned,
                        'sr_confluence': near_support
                    }
                    
                    probability = calculate_probability_score(indicators)
                    
                    # Extra boosters that add probability
                    if order_flow > 0.2:  # Strong buying pressure
                        probability += 5
                    if elders_impulse == 1:  # Bullish impulse
                        probability += 5
                    if volatility_optimal:
                        probability += 5
                    if not trading_time_optimal:
                        probability -= 10  # Penalty for bad trading hours
                    
                    # Final check with probability threshold
                    if probability >= PROBABILITY_THRESHOLD and daily_trades < 10:
                        if last_alerts.get(symbol) != "LONG":
                            # Calculate dynamic targets based on volatility
                            atr = df['high'].iloc[-20:].max() - df['low'].iloc[-20:].min()
                            dynamic_target1 = current_price + (atr * 0.5)
                            dynamic_target2 = current_price + (atr * 1.0)
                            dynamic_stop = current_price - (atr * 0.3)
                            
                            signal_strength = "🔥🔥 EXTREME 🔥🔥" if probability >= 90 else "🔥 HIGH 🔥" if probability >= 85 else "✅ MEDIUM ✅"
                            
                            message = (
                                f"🟢🟢🟢 LONG SCALP SIGNAL {signal_strength} 🟢🟢🟢\n\n"
                                f"Symbol: {symbol}\n"
                                f"Price: ${current_price:.6f}\n"
                                f"Probability: {probability}%\n"
                                f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
                                f"📊 Primary Indicators:\n"
                                f"• BB Position: {bb_position*100:.1f}% (Lower: ${lower_bb:.6f})\n"
                                f"• RSI (14): {rsi:.1f} (Oversold ✅)\n"
                                f"• ADX (14): {adx} (Strong Trend ✅)\n"
                                f"• CHOP (14): {chop} (Trending ✅)\n"
                                f"• Volume: {volume_ratio:.1f}x avg\n\n"
                                f"🎯 Probability Boosters:\n"
                                f"• Multiple Timeframe: {'✅ Aligned' if mtf_aligned else '❌'}\n"
                                f"• Order Flow: {'🟢 Bullish' if order_flow > 0 else '🔴 Bearish'} ({order_flow:.2f})\n"
                                f"• Elder's Impulse: {'🟢 Bullish' if elders_impulse == 1 else '⚪ Neutral'}\n"
                                f"• S/R Confluence: {'✅ Near Support' if near_support else '❌'}\n"
                                f"• Volatility: {'✅ Optimal' if volatility_optimal else '⚠️ Suboptimal'}\n"
                                f"• Trading Time: {'✅ Peak Liquidity' if trading_time_optimal else '⚠️ Low Liquidity'}\n\n"
                                f"🎯 Dynamic Targets (ATR-based):\n"
                                f"• Target 1: ${dynamic_target1:.6f} (+{(dynamic_target1/current_price-1)*100:.2f}%)\n"
                                f"• Target 2: ${dynamic_target2:.6f} (+{(dynamic_target2/current_price-1)*100:.2f}%)\n"
                                f"• Stop Loss: ${dynamic_stop:.6f} (-{(1-dynamic_stop/current_price)*100:.2f}%)\n\n"
                                f"⚡ Action: HIGH PROBABILITY SETUP - Enter with 1.5x normal size"
                            )
                            send_alert(message)
                            print(f"{symbol} - 🟢 LONG SIGNAL ({probability}% prob)")
                            last_alerts[symbol] = "LONG"
                            daily_trades += 1
                            
                            # Store signal for performance tracking
                            signal_history.append({
                                'time': datetime.now(),
                                'symbol': symbol,
                                'type': 'LONG',
                                'probability': probability,
                                'price': current_price
                            })
                
                elif short_base_conditions:
                    # Check multiple timeframe alignment for short
                    mtf_aligned = calculate_multiple_timeframe_alignment(symbol, 'short')
                    
                    indicators = {
                        'rsi': rsi,
                        'adx': adx,
                        'bb_position': bb_position,
                        'volume_ratio': volume_ratio,
                        'macd_aligned': macd_hist < 0,
                        'mtf_aligned': mtf_aligned,
                        'sr_confluence': near_resistance
                    }
                    
                    probability = calculate_probability_score(indicators)
                    
                    # Extra boosters for short
                    if order_flow < -0.2:  # Strong selling pressure
                        probability += 5
                    if elders_impulse == -1:  # Bearish impulse
                        probability += 5
                    if volatility_optimal:
                        probability += 5
                    if not trading_time_optimal:
                        probability -= 10
                    
                    if probability >= PROBABILITY_THRESHOLD and daily_trades < 10:
                        if last_alerts.get(symbol) != "SHORT":
                            # Dynamic targets for short
                            atr = df['high'].iloc[-20:].max() - df['low'].iloc[-20:].min()
                            dynamic_target1 = current_price - (atr * 0.5)
                            dynamic_target2 = current_price - (atr * 1.0)
                            dynamic_stop = current_price + (atr * 0.3)
                            
                            signal_strength = "🔥🔥 EXTREME 🔥🔥" if probability >= 90 else "🔥 HIGH 🔥" if probability >= 85 else "✅ MEDIUM ✅"
                            
                            message = (
                                f"🔴🔴🔴 SHORT SCALP SIGNAL {signal_strength} 🔴🔴🔴\n\n"
                                f"Symbol: {symbol}\n"
                                f"Price: ${current_price:.6f}\n"
                                f"Probability: {probability}%\n"
                                f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
                                f"📊 Primary Indicators:\n"
                                f"• BB Position: {bb_position*100:.1f}% (Upper: ${upper_bb:.6f})\n"
                                f"• RSI (14): {rsi:.1f} (Overbought ✅)\n"
                                f"• ADX (14): {adx} (Strong Trend ✅)\n"
                                f"• CHOP (14): {chop} (Trending ✅)\n"
                                f"• Volume: {volume_ratio:.1f}x avg\n\n"
                                f"🎯 Probability Boosters:\n"
                                f"• Multiple Timeframe: {'✅ Aligned' if mtf_aligned else '❌'}\n"
                                f"• Order Flow: {'🔴 Bearish' if order_flow < 0 else '🟢 Bullish'} ({order_flow:.2f})\n"
                                f"• Elder's Impulse: {'🔴 Bearish' if elders_impulse == -1 else '⚪ Neutral'}\n"
                                f"• S/R Confluence: {'✅ Near Resistance' if near_resistance else '❌'}\n"
                                f"• Volatility: {'✅ Optimal' if volatility_optimal else '⚠️ Suboptimal'}\n"
                                f"• Trading Time: {'✅ Peak Liquidity' if trading_time_optimal else '⚠️ Low Liquidity'}\n\n"
                                f"🎯 Dynamic Targets (ATR-based):\n"
                                f"• Target 1: ${dynamic_target1:.6f} (-{(1-dynamic_target1/current_price)*100:.2f}%)\n"
                                f"• Target 2: ${dynamic_target2:.6f} (-{(1-dynamic_target2/current_price)*100:.2f}%)\n"
                                f"• Stop Loss: ${dynamic_stop:.6f} (+{(dynamic_stop/current_price-1)*100:.2f}%)\n\n"
                                f"⚡ Action: HIGH PROBABILITY SETUP - Enter with 1.5x normal size"
                            )
                            send_alert(message)
                            print(f"{symbol} - 🔴 SHORT SIGNAL ({probability}% prob)")
                            last_alerts[symbol] = "SHORT"
                            daily_trades += 1
                            
                            signal_history.append({
                                'time': datetime.now(),
                                'symbol': symbol,
                                'type': 'SHORT',
                                'probability': probability,
                                'price': current_price
                            })
                
                # Reset alert if conditions no longer met
                elif not long_base_conditions and not short_base_conditions and last_alerts.get(symbol) is not None:
                    last_alerts[symbol] = None
                
                time.sleep(0.5)  # Small delay between symbols
                
            except Exception as e:
                print(f"Error with {symbol}: {e}")
        
        # Performance report every hour
        if int(time.time()) % 3600 < 60:  # Every hour
            if signal_history:
                avg_probability = sum(s['probability'] for s in signal_history) / len(signal_history)
                print(f"\n📊 Performance Stats - Last {len(signal_history)} signals:")
                print(f"   Average Probability: {avg_probability:.1f}%")
                print(f"   Daily Trades: {daily_trades}/10")
                print(f"   Active Symbols: {len([s for s in SYMBOLS if s in EXCHANGE.markets])}\n")
        
        time.sleep(60)  # Main loop delay

# Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
