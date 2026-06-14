import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# ============ CONFIGURATION ============
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT',
    'PAXG/USDT', 'XAUT/USDT', 'BEAT/USDT', 'H/USDT',
    'AIO/USDT', 'XRP/USDT', 'LAB/USDT', 'ZEC/USDT',
    'SKYAI/USDT', 'SLVON/USDT', 'DOGE/USDT', 'SIREN/USDT',
    'BNB/USDT', 'LTC/USDT', 'PIPPIN/USDT', 'LINK/USDT',
    'XMR/USDT', 'AIN/USDT', '1000SATS/USDT',
    'PENGU/USDT', 'ARC/USDT', 'DOGS/USDT'
]

EXCHANGE = ccxt.binance({
    'enableRateLimit': True,
})
EXCHANGE.load_markets()

# ============ FILTER CONFIGURATION ============
class Filters:
    def __init__(self):
        self.require_volume_surge = True
        self.require_liquid_hours = True
        self.require_rsi_confirmation = True
        self.min_risk_reward = 2.0
        self.require_ema_alignment = True
        
        # Trading hours (UTC)
        self.liquid_start_hour = 13  # 1 PM UTC
        self.liquid_end_hour = 21    # 9 PM UTC
        
        self.volume_multiplier = 1.5
        self.rsi_oversold = 30
        self.rsi_overbought = 70

filters = Filters()

# ============ INDICATOR FUNCTIONS ============

def calculate_adx(df, period=14):
    """Calculate ADX"""
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
            if down_move > up_move and down_move > 0:
                minus_dm[i] = down_move
        
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
        if n >= period + period - 1:
            adx[period + period - 1] = np.sum(dx[period:period+period-1]) / (period - 1)
            for i in range(period + period, n):
                adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period
        
        return round(adx[-1], 2) if not np.isnan(adx[-1]) else 0
    except:
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
        
        return round(result, 2) if not pd.isna(result) else 50
    except:
        return 50

def calculate_rsi(df, period=14):
    """Calculate RSI"""
    try:
        close = df['close']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi.iloc[-1], 2)
    except:
        return 50

def calculate_ema(df, period):
    """Calculate EMA"""
    return df['close'].ewm(span=period, adjust=False).mean()

def calculate_atr(df, period=14):
    """Calculate ATR"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.rolling(window=period).mean()
        return round(atr.iloc[-1], 8)
    except:
        return 0.0001

# ============ FILTER FUNCTIONS ============

def check_volume_surge(df):
    """Check if volume is above average"""
    if not filters.require_volume_surge:
        return True, "Volume filter disabled"
    
    try:
        current_volume = df['vol'].iloc[-1]
        avg_volume = df['vol'].tail(20).mean()
        ratio = current_volume / avg_volume
        
        if ratio >= filters.volume_multiplier:
            return True, f"🔥 Volume: {ratio:.1f}x avg"
        else:
            return False, f"Volume: {ratio:.1f}x (needs {filters.volume_multiplier}x)"
    except:
        return True, "Volume check failed"

def check_liquid_hours():
    """Check if current time is during liquid hours"""
    if not filters.require_liquid_hours:
        return True, "Hours filter disabled"
    
    current_hour = datetime.now().hour
    current_minute = datetime.now().minute
    current_decimal = current_hour + current_minute/60.0
    
    is_liquid = (current_decimal >= filters.liquid_start_hour and 
                 current_decimal <= filters.liquid_end_hour)
    
    if is_liquid:
        return True, f"⏰ Liquid hours ({current_hour}:{current_minute:02d} UTC)"
    else:
        return False, f"⏰ Low liquidity ({current_hour}:{current_minute:02d} UTC)"

def check_rsi_filter(df, direction):
    """Check RSI confirmation"""
    if not filters.require_rsi_confirmation:
        return True, "RSI filter disabled"
    
    rsi = calculate_rsi(df, 14)
    
    if direction == "BUY":
        if rsi <= filters.rsi_oversold:
            return True, f"✅ RSI: {rsi} (Oversold - good for BUY)"
        elif rsi >= filters.rsi_overbought:
            return False, f"❌ RSI: {rsi} (Overbought - avoid BUY)"
        else:
            return True, f"📊 RSI: {rsi} (Neutral)"
    
    elif direction == "SELL":
        if rsi >= filters.rsi_overbought:
            return True, f"✅ RSI: {rsi} (Overbought - good for SELL)"
        elif rsi <= filters.rsi_oversold:
            return False, f"❌ RSI: {rsi} (Oversold - avoid SELL)"
        else:
            return True, f"📊 RSI: {rsi} (Neutral)"
    
    return True, f"RSI: {rsi}"

def check_ema_alignment(df, direction):
    """Check EMA alignment"""
    if not filters.require_ema_alignment:
        return True, "EMA filter disabled"
    
    ema20 = calculate_ema(df, 20).iloc[-1]
    ema50 = calculate_ema(df, 50).iloc[-1]
    current_price = df['close'].iloc[-1]
    
    if direction == "BUY":
        if current_price > ema20 > ema50:
            return True, f"✅ EMAs bullish ({ema20:.2f} > {ema50:.2f})"
        else:
            return False, f"❌ EMAs not bullish"
    
    elif direction == "SELL":
        if current_price < ema20 < ema50:
            return True, f"✅ EMAs bearish ({ema20:.2f} < {ema50:.2f})"
        else:
            return False, f"❌ EMAs not bearish"
    
    return True, "EMA check passed"

def calculate_risk_reward(df, entry, direction, channel_high, channel_low):
    """Calculate risk/reward ratio"""
    atr = calculate_atr(df, 14)
    channel_range = channel_high - channel_low
    
    if direction == "BUY":
        stop_loss = entry - (atr * 1.5)
        take_profit = entry + (atr * 2.5)
        risk = entry - stop_loss
        reward = take_profit - entry
        
        if risk > 0:
            rr = reward / risk
            return rr, stop_loss, take_profit
    
    elif direction == "SELL":
        stop_loss = entry + (atr * 1.5)
        take_profit = entry - (atr * 2.5)
        risk = stop_loss - entry
        reward = entry - take_profit
        
        if risk > 0:
            rr = reward / risk
            return rr, stop_loss, take_profit
    
    return 0, 0, 0

# ============ TELEGRAM ALERT ============

last_alert = {}

def send_alert(message):
    """Send alert to Telegram"""
    if TOKEN and CHAT_ID:
        try:
            requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={"chat_id": CHAT_ID, "text": message},
                timeout=10
            )
        except Exception as e:
            print(f"Telegram error: {e}")

# ============ MAIN BOT ============

def run_bot():
    print("="*60)
    print("🤖 HIGH PROBABILITY TRADING BOT STARTED")
    print("="*60)
    print(f"Symbols: {len(SYMBOLS)}")
    print(f"Timeframe: 15min")
    print(f"Donchian: 52 periods")
    print(f"Filters enabled:")
    print(f"  - Volume surge: {filters.require_volume_surge}")
    print(f"  - Liquid hours: {filters.require_liquid_hours}")
    print(f"  - RSI confirmation: {filters.require_rsi_confirmation}")
    print(f"  - EMA alignment: {filters.require_ema_alignment}")
    print(f"  - Min R:R: {filters.min_risk_reward}:1")
    print("="*60)
    
    send_alert("✅ BOT STARTED\nMonitoring 52-bar Donchian Channel on 15min charts")
    
    while True:
        for symbol in SYMBOLS:
            try:
                # Skip if symbol not available
                if symbol not in EXCHANGE.markets:
                    continue
                
                # Fetch data
                ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=100)
                if len(ohlcv) < 70:
                    continue
                
                df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                
                # ============ DONCHIAN CHANNEL ============
                HH = df['high'].tail(52).max()
                LL = df['low'].tail(52).min()
                channel_range = HH - LL
                
                # Zone calculations
                warning_zone_top = HH - (channel_range * 0.15)
                extreme_zone_top = HH - (channel_range * 0.03)
                warning_zone_bottom = LL + (channel_range * 0.15)
                extreme_zone_bottom = LL + (channel_range * 0.03)
                
                current_price = df['close'].iloc[-1]
                
                # ============ INDICATORS ============
                adx = calculate_adx(df, 14)
                chop = calculate_choppiness_index(df, 14)
                atr = calculate_atr(df, 14)
                rsi = calculate_rsi(df, 14)
                
                # Market regime
                is_trending = adx > 25 and chop < 40
                is_ranging = (adx < 20 or chop > 60) and not is_trending
                
                # Skip if indicators not ready
                if adx == 0 or chop == 50:
                    continue
                
                # ============ CHECK FILTERS (once per symbol per cycle) ============
                volume_ok, volume_msg = check_volume_surge(df)
                hours_ok, hours_msg = check_liquid_hours()
                
                # ============ SIGNAL LOGIC ============
                
                # SIGNAL 1: BREAKOUT BUY (Trending + Top)
                if current_price >= extreme_zone_top and is_trending:
                    if last_alert.get(symbol) != "BREAKOUT_BUY":
                        
                        # Additional filters for BUY
                        rsi_ok, rsi_msg = check_rsi_filter(df, "BUY")
                        ema_ok, ema_msg = check_ema_alignment(df, "BUY")
                        
                        # Calculate levels
                        entry = HH + (atr * 0.3)
                        rr, stop_loss, take_profit = calculate_risk_reward(df, entry, "BUY", HH, LL)
                        rr_ok = rr >= filters.min_risk_reward
                        
                        # Build message
                        message = f"""
🟢🟢🟢 BUY SIGNAL - BREAKOUT 🟢🟢🟢

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📈 MARKET CONDITION:
• TRENDING MARKET (ADX: {adx})
• CHOP: {chop} (Trending)
• RSI: {rsi}
━━━━━━━━━━━━━━━━━━━━━━

✅ ACTION: BUY (Long)

📊 DONCHIAN LEVELS:
• Channel High: ${HH:.6f}
• Channel Low: ${LL:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((entry - stop_loss)/entry * 100):.2f}%
• Reward: {((take_profit - entry)/entry * 100):.2f}%
• R/R Ratio: {rr:.1f}:1

🔍 FILTERS:
{volume_msg}
{hours_msg}
{rsi_msg}
{ema_msg}
━━━━━━━━━━━━━━━━━━━━━━
"""
                        if not rr_ok:
                            message += f"⚠️ R/R {rr:.1f}:1 below minimum {filters.min_risk_reward}:1\n"
                        
                        if volume_ok and hours_ok and rsi_ok and ema_ok and rr_ok:
                            message += "\n✅✅✅ HIGH PROBABILITY - ALL FILTERS PASSED"
                        else:
                            message += "\n⚠️ MODERATE PROBABILITY - Some filters failed"
                        
                        send_alert(message)
                        print(f"{symbol} - 🟢 BREAKOUT BUY SIGNAL")
                        last_alert[symbol] = "BREAKOUT_BUY"
                
                # SIGNAL 2: REVERSAL SELL (Ranging + Top)
                elif current_price >= extreme_zone_top and is_ranging:
                    if last_alert.get(symbol) != "REVERSAL_SELL":
                        
                        rsi_ok, rsi_msg = check_rsi_filter(df, "SELL")
                        ema_ok, ema_msg = check_ema_alignment(df, "SELL")
                        
                        entry = current_price
                        rr, stop_loss, take_profit = calculate_risk_reward(df, entry, "SELL", HH, LL)
                        rr_ok = rr >= filters.min_risk_reward
                        
                        message = f"""
🔴🔴🔴 SELL SIGNAL - REVERSAL 🔴🔴🔴

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📉 MARKET CONDITION:
• RANGING MARKET (CHOP: {chop})
• ADX: {adx} (Weak trend)
• RSI: {rsi}
━━━━━━━━━━━━━━━━━━━━━━

✅ ACTION: SELL (Short)

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((stop_loss - entry)/entry * 100):.2f}%
• Reward: {((entry - take_profit)/entry * 100):.2f}%
• R/R Ratio: {rr:.1f}:1

🔍 FILTERS:
{volume_msg}
{hours_msg}
{rsi_msg}
{ema_msg}
━━━━━━━━━━━━━━━━━━━━━━
"""
                        if not rr_ok:
                            message += f"⚠️ R/R {rr:.1f}:1 below minimum {filters.min_risk_reward}:1\n"
                        
                        if volume_ok and hours_ok and rsi_ok and ema_ok and rr_ok:
                            message += "\n✅✅✅ HIGH PROBABILITY - ALL FILTERS PASSED"
                        
                        send_alert(message)
                        print(f"{symbol} - 🔴 REVERSAL SELL SIGNAL")
                        last_alert[symbol] = "REVERSAL_SELL"
                
                # SIGNAL 3: BREAKDOWN SELL (Trending + Bottom)
                elif current_price <= extreme_zone_bottom and is_trending:
                    if last_alert.get(symbol) != "BREAKDOWN_SELL":
                        
                        rsi_ok, rsi_msg = check_rsi_filter(df, "SELL")
                        ema_ok, ema_msg = check_ema_alignment(df, "SELL")
                        
                        entry = LL - (atr * 0.3)
                        rr, stop_loss, take_profit = calculate_risk_reward(df, entry, "SELL", HH, LL)
                        rr_ok = rr >= filters.min_risk_reward
                        
                        message = f"""
🔴🔴🔴 SELL SIGNAL - BREAKDOWN 🔴🔴🔴

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📉 MARKET CONDITION:
• TRENDING MARKET (ADX: {adx})
• CHOP: {chop} (Trending)
• RSI: {rsi}
━━━━━━━━━━━━━━━━━━━━━━

✅ ACTION: SELL (Short)

📊 DONCHIAN LEVELS:
• Channel High: ${HH:.6f}
• Channel Low: ${LL:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((stop_loss - entry)/entry * 100):.2f}%
• Reward: {((entry - take_profit)/entry * 100):.2f}%
• R/R Ratio: {rr:.1f}:1

🔍 FILTERS:
{volume_msg}
{hours_msg}
{rsi_msg}
{ema_msg}
━━━━━━━━━━━━━━━━━━━━━━
"""
                        if not rr_ok:
                            message += f"⚠️ R/R {rr:.1f}:1 below minimum {filters.min_risk_reward}:1\n"
                        
                        if volume_ok and hours_ok and rsi_ok and ema_ok and rr_ok:
                            message += "\n✅✅✅ HIGH PROBABILITY - ALL FILTERS PASSED"
                        
                        send_alert(message)
                        print(f"{symbol} - 🔴 BREAKDOWN SELL SIGNAL")
                        last_alert[symbol] = "BREAKDOWN_SELL"
                
                # SIGNAL 4: BOUNCE BUY (Ranging + Bottom)
                elif current_price <= extreme_zone_bottom and is_ranging:
                    if last_alert.get(symbol) != "BOUNCE_BUY":
                        
                        rsi_ok, rsi_msg = check_rsi_filter(df, "BUY")
                        ema_ok, ema_msg = check_ema_alignment(df, "BUY")
                        
                        entry = current_price
                        rr, stop_loss, take_profit = calculate_risk_reward(df, entry, "BUY", HH, LL)
                        rr_ok = rr >= filters.min_risk_reward
                        
                        message = f"""
🟢🟢🟢 BUY SIGNAL - BOUNCE 🟢🟢🟢

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📈 MARKET CONDITION:
• RANGING MARKET (CHOP: {chop})
• ADX: {adx} (Weak trend)
• RSI: {rsi}
━━━━━━━━━━━━━━━━━━━━━━

✅ ACTION: BUY (Long)

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((entry - stop_loss)/entry * 100):.2f}%
• Reward: {((take_profit - entry)/entry * 100):.2f}%
• R/R Ratio: {rr:.1f}:1

🔍 FILTERS:
{volume_msg}
{hours_msg}
{rsi_msg}
{ema_msg}
━━━━━━━━━━━━━━━━━━━━━━
"""
                        if not rr_ok:
                            message += f"⚠️ R/R {rr:.1f}:1 below minimum {filters.min_risk_reward}:1\n"
                        
                        if volume_ok and hours_ok and rsi_ok and ema_ok and rr_ok:
                            message += "\n✅✅✅ HIGH PROBABILITY - ALL FILTERS PASSED"
                        
                        send_alert(message)
                        print(f"{symbol} - 🟢 BOUNCE BUY SIGNAL")
                        last_alert[symbol] = "BOUNCE_BUY"
                
                # EARLY WARNING (no trade signal, just watch)
                elif current_price >= warning_zone_top and current_price < extreme_zone_top:
                    if last_alert.get(symbol) not in ["WATCH_TOP", "BREAKOUT_BUY", "REVERSAL_SELL"]:
                        message = f"""
👀 EARLY WATCH: {symbol}

Price in TOP 15% of Donchian Channel
Current: ${current_price:.6f}
Channel High: ${HH:.6f}

ADX: {adx} | CHOP: {chop}

📌 Prepare for:
• BUY if trending (ADX>25)
• SELL if ranging (CHOP>60)

Watch price action!
"""
                        send_alert(message)
                        last_alert[symbol] = "WATCH_TOP"
                
                elif current_price <= warning_zone_bottom and current_price > extreme_zone_bottom:
                    if last_alert.get(symbol) not in ["WATCH_BOTTOM", "BREAKDOWN_SELL", "BOUNCE_BUY"]:
                        message = f"""
👀 EARLY WATCH: {symbol}

Price in BOTTOM 15% of Donchian Channel
Current: ${current_price:.6f}
Channel Low: ${LL:.6f}

ADX: {adx} | CHOP: {chop}

📌 Prepare for:
• SELL if trending (ADX>25)
• BUY if ranging (CHOP>60)

Watch price action!
"""
                        send_alert(message)
                        last_alert[symbol] = "WATCH_BOTTOM"
                
                # Reset when price leaves all zones
                elif current_price < warning_zone_top and current_price > warning_zone_bottom:
                    if last_alert.get(symbol) not in [None, "RESET"]:
                        last_alert[symbol] = None
                
                # Small delay between symbols
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error with {symbol}: {e}")
                continue
        
        # Wait before next full cycle
        time.sleep(5)

# ============ START BOT ============

# Run bot in background thread
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Start Flask app
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
