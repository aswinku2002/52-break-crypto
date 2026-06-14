import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime
from collections import deque

app = Flask(__name__)

@app.route('/')
def home():
    return "High Probability Trading Bot is running!"

# ============ CONFIGURATION ============
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 24/7 Trading Symbols (high liquidity)
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT',
    'XRP/USDT', 'DOGE/USDT', 'ADA/USDT', 'LINK/USDT',
    'AVAX/USDT', 'MATIC/USDT', 'DOT/USDT', 'UNI/USDT'
]

EXCHANGE = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})
EXCHANGE.load_markets()

# ============ STRATEGY CONFIGURATION ============
class StrategyConfig:
    def __init__(self):
        # Timeframes for multi-confirmation
        self.fast_timeframe = '5m'      # Fast signal (5 min candles)
        self.slow_timeframe = '15m'     # Confirmation (15 min candles)
        
        # Strategy parameters
        self.ema_fast = 9               # Fast EMA
        self.ema_slow = 21              # Slow EMA
        self.rsi_period = 14            # RSI period
        self.rsi_oversold = 30          # RSI oversold threshold
        self.rsi_overbought = 70        # RSI overbought threshold
        self.macd_fast = 12             # MACD fast period
        self.macd_slow = 26             # MACD slow period
        self.macd_signal = 9            # MACD signal period
        
        # Volume confirmation
        self.min_volume_ratio = 1.3     # Volume must be 1.3x average
        
        # Risk management
        self.min_risk_reward = 1.5      # Minimum R:R ratio
        self.atr_multiplier_stop = 1.5  # ATR multiplier for stop loss
        self.atr_multiplier_target = 2.0 # ATR multiplier for take profit
        
        # Time filters (optional - set to 0-24 for 24/7)
        self.trading_hours_start = 0    # 0 = 24/7 trading
        self.trading_hours_end = 24     # 24 = 24/7 trading
        self.trade_on_weekends = True   # Trade on weekends

config = StrategyConfig()

# ============ INDICATOR FUNCTIONS ============

def calculate_ema(close, period):
    """Calculate EMA"""
    return close.ewm(span=period, adjust=False).mean()

def calculate_rsi(close, period=14):
    """Calculate RSI"""
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(close, fast=12, slow=26, signal=9):
    """Calculate MACD"""
    exp1 = close.ewm(span=fast, adjust=False).mean()
    exp2 = close.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram

def calculate_atr(df, period=14):
    """Calculate ATR"""
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.rolling(window=period).mean()
    return atr

def calculate_bollinger_bands(close, period=20, std=2):
    """Calculate Bollinger Bands"""
    sma = close.rolling(window=period).mean()
    std_dev = close.rolling(window=period).std()
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    return upper, sma, lower

def calculate_vwap(df):
    """Calculate VWAP"""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    vwap = (typical_price * df['vol']).cumsum() / df['vol'].cumsum()
    return vwap

def calculate_volume_ratio(df, period=20):
    """Calculate volume ratio"""
    current_vol = df['vol'].iloc[-1]
    avg_vol = df['vol'].tail(period).mean()
    return current_vol / avg_vol if avg_vol > 0 else 1

def check_support_resistance(df, current_price, lookback=50):
    """Find nearest support and resistance levels"""
    highs = df['high'].tail(lookback)
    lows = df['low'].tail(lookback)
    
    # Find recent swing highs and lows
    resistance_levels = []
    support_levels = []
    
    for i in range(2, len(highs)-2):
        if highs.iloc[i] > highs.iloc[i-1] and highs.iloc[i] > highs.iloc[i-2] and \
           highs.iloc[i] > highs.iloc[i+1] and highs.iloc[i] > highs.iloc[i+2]:
            resistance_levels.append(highs.iloc[i])
        
        if lows.iloc[i] < lows.iloc[i-1] and lows.iloc[i] < lows.iloc[i-2] and \
           lows.iloc[i] < lows.iloc[i+1] and lows.iloc[i] < lows.iloc[i+2]:
            support_levels.append(lows.iloc[i])
    
    # Find nearest levels
    nearest_resistance = min([r for r in resistance_levels if r > current_price], default=current_price * 1.02)
    nearest_support = max([s for s in support_levels if s < current_price], default=current_price * 0.98)
    
    return nearest_support, nearest_resistance

# ============ SIGNAL DETECTION ============

def detect_buy_signal(df_fast, df_slow, symbol):
    """
    Detect BUY signal using multiple confirmations
    Returns: (is_buy_signal, confidence_score, reasons, entry_price, stop_loss, take_profit)
    """
    try:
        # Get current price
        current_price = df_fast['close'].iloc[-1]
        
        # Calculate indicators on fast timeframe
        ema9_fast = calculate_ema(df_fast['close'], config.ema_fast)
        ema21_fast = calculate_ema(df_fast['close'], config.ema_slow)
        rsi_fast = calculate_rsi(df_fast['close'], config.rsi_period)
        macd_fast, signal_fast, hist_fast = calculate_macd(df_fast['close'], 
                                                           config.macd_fast, 
                                                           config.macd_slow, 
                                                           config.macd_signal)
        
        # Calculate indicators on slow timeframe (confirmation)
        ema9_slow = calculate_ema(df_slow['close'], config.ema_fast)
        ema21_slow = calculate_ema(df_slow['close'], config.ema_slow)
        rsi_slow = calculate_rsi(df_slow['close'], config.rsi_period)
        macd_slow, signal_slow, hist_slow = calculate_macd(df_slow['close'],
                                                           config.macd_fast,
                                                           config.macd_slow,
                                                           config.macd_signal)
        
        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(df_fast['close'], 20, 2)
        
        # Volume
        vol_ratio = calculate_volume_ratio(df_fast)
        
        # ATR for stop loss
        atr = calculate_atr(df_fast).iloc[-1]
        
        # Support/Resistance
        support, resistance = check_support_resistance(df_fast, current_price)
        
        # BUY CONDITIONS
        conditions = []
        score = 0
        
        # Condition 1: EMA Crossover (Fast EMA above Slow EMA)
        if ema9_fast.iloc[-1] > ema21_fast.iloc[-1]:
            conditions.append("✅ EMA Bullish Crossover (5min)")
            score += 20
        elif ema9_fast.iloc[-1] > ema21_fast.iloc[-2]:
            conditions.append("📈 EMA Starting to Cross Up (5min)")
            score += 10
        
        # Condition 2: RSI (Not overbought, ideally oversold or rising from oversold)
        if rsi_fast.iloc[-1] < config.rsi_oversold:
            conditions.append(f"✅ RSI Oversold: {rsi_fast.iloc[-1]:.1f} (5min)")
            score += 25
        elif rsi_fast.iloc[-1] < 50 and rsi_fast.iloc[-1] > rsi_fast.iloc[-2]:
            conditions.append(f"📈 RSI Rising from {rsi_fast.iloc[-2]:.1f} to {rsi_fast.iloc[-1]:.1f} (5min)")
            score += 15
        elif rsi_fast.iloc[-1] < 50:
            conditions.append(f"📊 RSI Below 50: {rsi_fast.iloc[-1]:.1f} (5min)")
            score += 5
        
        # Condition 3: MACD Bullish
        if hist_fast.iloc[-1] > 0 and hist_fast.iloc[-1] > hist_fast.iloc[-2]:
            conditions.append(f"✅ MACD Bullish & Increasing (5min)")
            score += 20
        elif hist_fast.iloc[-1] > 0:
            conditions.append(f"📈 MACD Bullish (5min)")
            score += 10
        
        # Condition 4: Price near support or Bollinger lower band
        if current_price <= support * 1.005:
            conditions.append(f"✅ Price at Support: ${support:.4f}")
            score += 20
        elif current_price <= bb_lower.iloc[-1] * 1.005:
            conditions.append(f"✅ Price at Lower Bollinger Band: ${bb_lower.iloc[-1]:.4f}")
            score += 15
        
        # Condition 5: Volume confirmation
        if vol_ratio >= config.min_volume_ratio:
            conditions.append(f"✅ Volume Surge: {vol_ratio:.1f}x average")
            score += 15
        elif vol_ratio >= 1.0:
            conditions.append(f"📊 Volume: {vol_ratio:.1f}x average")
            score += 5
        
        # Condition 6: Slow timeframe confirmation (Higher timeframe bullish)
        if ema9_slow.iloc[-1] > ema21_slow.iloc[-1]:
            conditions.append("✅ Higher Timeframe Bullish (15min)")
            score += 20
        elif macd_slow.iloc[-1] > signal_slow.iloc[-1]:
            conditions.append("📈 Higher Timeframe MACD Bullish (15min)")
            score += 10
        
        if rsi_slow.iloc[-1] < 50:
            conditions.append(f"📊 Higher Timeframe RSI: {rsi_slow.iloc[-1]:.1f} (15min)")
            score += 5
        
        # Condition 7: Price action - green candle
        if df_fast['close'].iloc[-1] > df_fast['open'].iloc[-1]:
            conditions.append("✅ Green Candle Confirmation")
            score += 10
        
        # Determine if BUY signal
        is_buy = score >= 60  # 60+ score = BUY signal
        
        # Calculate stop loss and take profit
        if is_buy:
            entry = current_price
            stop_loss = entry - (atr * config.atr_multiplier_stop)
            take_profit = entry + (atr * config.atr_multiplier_target)
            
            risk = entry - stop_loss
            reward = take_profit - entry
            rr_ratio = reward / risk if risk > 0 else 0
            
            return True, score, conditions, entry, stop_loss, take_profit, rr_ratio, vol_ratio
        
        return False, score, conditions, None, None, None, 0, vol_ratio
        
    except Exception as e:
        print(f"Buy signal error: {e}")
        return False, 0, [], None, None, None, 0, 0

def detect_sell_signal(df_fast, df_slow, symbol):
    """
    Detect SELL signal using multiple confirmations
    Returns: (is_sell_signal, confidence_score, reasons, entry_price, stop_loss, take_profit)
    """
    try:
        current_price = df_fast['close'].iloc[-1]
        
        # Calculate indicators on fast timeframe
        ema9_fast = calculate_ema(df_fast['close'], config.ema_fast)
        ema21_fast = calculate_ema(df_fast['close'], config.ema_slow)
        rsi_fast = calculate_rsi(df_fast['close'], config.rsi_period)
        macd_fast, signal_fast, hist_fast = calculate_macd(df_fast['close'],
                                                           config.macd_fast,
                                                           config.macd_slow,
                                                           config.macd_signal)
        
        # Calculate indicators on slow timeframe
        ema9_slow = calculate_ema(df_slow['close'], config.ema_fast)
        ema21_slow = calculate_ema(df_slow['close'], config.ema_slow)
        rsi_slow = calculate_rsi(df_slow['close'], config.rsi_period)
        macd_slow, signal_slow, hist_slow = calculate_macd(df_slow['close'],
                                                           config.macd_fast,
                                                           config.macd_slow,
                                                           config.macd_signal)
        
        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(df_fast['close'], 20, 2)
        
        # Volume
        vol_ratio = calculate_volume_ratio(df_fast)
        
        # ATR for stop loss
        atr = calculate_atr(df_fast).iloc[-1]
        
        # Support/Resistance
        support, resistance = check_support_resistance(df_fast, current_price)
        
        # SELL CONDITIONS
        conditions = []
        score = 0
        
        # Condition 1: EMA Crossover (Fast EMA below Slow EMA)
        if ema9_fast.iloc[-1] < ema21_fast.iloc[-1]:
            conditions.append("✅ EMA Bearish Cross (5min)")
            score += 20
        elif ema9_fast.iloc[-1] < ema21_fast.iloc[-2]:
            conditions.append("📉 EMA Starting to Cross Down (5min)")
            score += 10
        
        # Condition 2: RSI (Not oversold, ideally overbought or falling from overbought)
        if rsi_fast.iloc[-1] > config.rsi_overbought:
            conditions.append(f"✅ RSI Overbought: {rsi_fast.iloc[-1]:.1f} (5min)")
            score += 25
        elif rsi_fast.iloc[-1] > 50 and rsi_fast.iloc[-1] < rsi_fast.iloc[-2]:
            conditions.append(f"📉 RSI Falling from {rsi_fast.iloc[-2]:.1f} to {rsi_fast.iloc[-1]:.1f} (5min)")
            score += 15
        elif rsi_fast.iloc[-1] > 50:
            conditions.append(f"📊 RSI Above 50: {rsi_fast.iloc[-1]:.1f} (5min)")
            score += 5
        
        # Condition 3: MACD Bearish
        if hist_fast.iloc[-1] < 0 and hist_fast.iloc[-1] < hist_fast.iloc[-2]:
            conditions.append(f"✅ MACD Bearish & Decreasing (5min)")
            score += 20
        elif hist_fast.iloc[-1] < 0:
            conditions.append(f"📉 MACD Bearish (5min)")
            score += 10
        
        # Condition 4: Price near resistance or Bollinger upper band
        if current_price >= resistance * 0.995:
            conditions.append(f"✅ Price at Resistance: ${resistance:.4f}")
            score += 20
        elif current_price >= bb_upper.iloc[-1] * 0.995:
            conditions.append(f"✅ Price at Upper Bollinger Band: ${bb_upper.iloc[-1]:.4f}")
            score += 15
        
        # Condition 5: Volume confirmation
        if vol_ratio >= config.min_volume_ratio:
            conditions.append(f"✅ Volume Surge: {vol_ratio:.1f}x average")
            score += 15
        elif vol_ratio >= 1.0:
            conditions.append(f"📊 Volume: {vol_ratio:.1f}x average")
            score += 5
        
        # Condition 6: Slow timeframe confirmation (Higher timeframe bearish)
        if ema9_slow.iloc[-1] < ema21_slow.iloc[-1]:
            conditions.append("✅ Higher Timeframe Bearish (15min)")
            score += 20
        elif macd_slow.iloc[-1] < signal_slow.iloc[-1]:
            conditions.append("📉 Higher Timeframe MACD Bearish (15min)")
            score += 10
        
        if rsi_slow.iloc[-1] > 50:
            conditions.append(f"📊 Higher Timeframe RSI: {rsi_slow.iloc[-1]:.1f} (15min)")
            score += 5
        
        # Condition 7: Price action - red candle
        if df_fast['close'].iloc[-1] < df_fast['open'].iloc[-1]:
            conditions.append("✅ Red Candle Confirmation")
            score += 10
        
        # Determine if SELL signal
        is_sell = score >= 60
        
        # Calculate stop loss and take profit
        if is_sell:
            entry = current_price
            stop_loss = entry + (atr * config.atr_multiplier_stop)
            take_profit = entry - (atr * config.atr_multiplier_target)
            
            risk = stop_loss - entry
            reward = entry - take_profit
            rr_ratio = reward / risk if risk > 0 else 0
            
            return True, score, conditions, entry, stop_loss, take_profit, rr_ratio, vol_ratio
        
        return False, score, conditions, None, None, None, 0, vol_ratio
        
    except Exception as e:
        print(f"Sell signal error: {e}")
        return False, 0, [], None, None, None, 0, 0

# ============ TELEGRAM ALERT ============

last_alerts = {}
alert_cooldown = {}

def send_telegram_alert(message):
    """Send alert to Telegram"""
    if TOKEN and CHAT_ID:
        try:
            if len(message) > 4096:
                message = message[:4000] + "..."
            requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={"chat_id": CHAT_ID, "text": message},
                timeout=10
            )
        except Exception as e:
            print(f"Telegram error: {e}")

def should_send_alert(symbol, signal_type, score):
    """Prevent duplicate alerts"""
    key = f"{symbol}_{signal_type}"
    current_time = time.time()
    
    if key in alert_cooldown:
        if current_time - alert_cooldown[key] < 300:  # 5 minute cooldown
            return False
    
    alert_cooldown[key] = current_time
    return True

# ============ MAIN BOT ============

def run_bot():
    print("="*70)
    print("🎯 HIGH PROBABILITY ANY-TIME TRADING BOT")
    print("="*70)
    print(f"Strategy: Multi-Timeframe EMA + RSI + MACD")
    print(f"Fast Timeframe: {config.fast_timeframe}")
    print(f"Slow Timeframe: {config.slow_timeframe}")
    print(f"Symbols: {len(SYMBOLS)}")
    print(f"Min Confidence: 60/100")
    print(f"Min R:R: {config.min_risk_reward}:1")
    print(f"Trading Hours: {config.trading_hours_start}:00 - {config.trading_hours_end}:00")
    print(f"Weekend Trading: {'Yes' if config.trade_on_weekends else 'No'}")
    print("="*70)
    
    send_telegram_alert(f"""🎯 HIGH PROBABILITY BOT ACTIVATED

📊 Strategy: Multi-Timeframe
⚡ Fast TF: {config.fast_timeframe}
🐢 Slow TF: {config.slow_timeframe}
📈 Min Confidence: 60%
🎯 Min R:R: {config.min_risk_reward}:1

Waiting for signals...""")
    
    while True:
        for symbol in SYMBOLS:
            try:
                if symbol not in EXCHANGE.markets:
                    continue
                
                # Fetch fast timeframe data
                ohlcv_fast = EXCHANGE.fetch_ohlcv(symbol, timeframe=config.fast_timeframe, limit=100)
                if len(ohlcv_fast) < 60:
                    continue
                
                df_fast = pd.DataFrame(ohlcv_fast, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                
                # Fetch slow timeframe data for confirmation
                ohlcv_slow = EXCHANGE.fetch_ohlcv(symbol, timeframe=config.slow_timeframe, limit=60)
                if len(ohlcv_slow) < 40:
                    continue
                
                df_slow = pd.DataFrame(ohlcv_slow, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                
                current_price = df_fast['close'].iloc[-1]
                
                # Check trading hours
                current_hour = datetime.now().hour
                if not (config.trading_hours_start <= current_hour < config.trading_hours_end):
                    continue
                
                # Check weekend
                if not config.trade_on_weekends and datetime.now().weekday() >= 5:
                    continue
                
                # DETECT BUY SIGNAL
                is_buy, buy_score, buy_reasons, buy_entry, buy_stop, buy_target, buy_rr, buy_vol = detect_buy_signal(df_fast, df_slow, symbol)
                
                if is_buy and buy_rr >= config.min_risk_reward and should_send_alert(symbol, "BUY", buy_score):
                    # Determine star rating
                    if buy_score >= 85:
                        stars = "⭐⭐⭐⭐⭐"
                        confidence = "EXCEPTIONAL"
                    elif buy_score >= 75:
                        stars = "⭐⭐⭐⭐"
                        confidence = "HIGH"
                    elif buy_score >= 65:
                        stars = "⭐⭐⭐"
                        confidence = "GOOD"
                    else:
                        stars = "⭐⭐"
                        confidence = "MODERATE"
                    
                    risk_pct = ((buy_entry - buy_stop) / buy_entry) * 100
                    reward_pct = ((buy_target - buy_entry) / buy_entry) * 100
                    
                    message = f"""
{stars} {confidence} CONFIDENCE: {buy_score}% {stars}

🟢🟢🟢 BUY SIGNAL 🟢🟢🟢

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📈 SIGNAL CONFIRMATIONS:
{chr(10).join(buy_reasons[:5])}
━━━━━━━━━━━━━━━━━━━━━━

🎯 EXECUTION PLAN:
• Entry: ${buy_entry:.6f}
• Stop Loss: ${buy_stop:.6f}
• Take Profit: ${buy_target:.6f}

📊 RISK MANAGEMENT:
• Risk: {risk_pct:.2f}%
• Reward: {reward_pct:.2f}%
• R:R Ratio: {buy_rr:.1f}:1
• Volume: {buy_vol:.1f}x avg

⚡ ACTION: ENTER LONG POSITION
⏰ Expected hold time: 10-30 minutes
"""
                    send_telegram_alert(message)
                    print(f"{symbol} - 🟢 BUY SIGNAL - Score: {buy_score}% - {datetime.now().strftime('%H:%M:%S')}")
                
                # DETECT SELL SIGNAL
                is_sell, sell_score, sell_reasons, sell_entry, sell_stop, sell_target, sell_rr, sell_vol = detect_sell_signal(df_fast, df_slow, symbol)
                
                if is_sell and sell_rr >= config.min_risk_reward and should_send_alert(symbol, "SELL", sell_score):
                    # Determine star rating
                    if sell_score >= 85:
                        stars = "⭐⭐⭐⭐⭐"
                        confidence = "EXCEPTIONAL"
                    elif sell_score >= 75:
                        stars = "⭐⭐⭐⭐"
                        confidence = "HIGH"
                    elif sell_score >= 65:
                        stars = "⭐⭐⭐"
                        confidence = "GOOD"
                    else:
                        stars = "⭐⭐"
                        confidence = "MODERATE"
                    
                    risk_pct = ((sell_stop - sell_entry) / sell_entry) * 100
                    reward_pct = ((sell_entry - sell_target) / sell_entry) * 100
                    
                    message = f"""
{stars} {confidence} CONFIDENCE: {sell_score}% {stars}

🔴🔴🔴 SELL SIGNAL 🔴🔴🔴

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📉 SIGNAL CONFIRMATIONS:
{chr(10).join(sell_reasons[:5])}
━━━━━━━━━━━━━━━━━━━━━━

🎯 EXECUTION PLAN:
• Entry: ${sell_entry:.6f}
• Stop Loss: ${sell_stop:.6f}
• Take Profit: ${sell_target:.6f}

📊 RISK MANAGEMENT:
• Risk: {risk_pct:.2f}%
• Reward: {reward_pct:.2f}%
• R:R Ratio: {sell_rr:.1f}:1
• Volume: {sell_vol:.1f}x avg

⚡ ACTION: ENTER SHORT POSITION
⏰ Expected hold time: 10-30 minutes
"""
                    send_telegram_alert(message)
                    print(f"{symbol} - 🔴 SELL SIGNAL - Score: {sell_score}% - {datetime.now().strftime('%H:%M:%S')}")
                
                # Small delay between symbols
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error with {symbol}: {e}")
                continue
        
        # Wait before next full cycle
        time.sleep(10)  # Check every 10 seconds

# ============ START BOT ============

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
