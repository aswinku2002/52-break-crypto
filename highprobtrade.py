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
    return "Scalping Bot is running!"

# ============ CONFIGURATION ============
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# HIGH VOLATILITY SYMBOLS FOR SCALPING
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'DOGE/USDT',
    'XRP/USDT', 'ADA/USDT', 'LINK/USDT', 'AVAX/USDT',
    'MATIC/USDT', 'DOT/USDT', 'UNI/USDT', 'ATOM/USDT'
]

EXCHANGE = ccxt.binance({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',  # or 'future' for futures
    }
})
EXCHANGE.load_markets()

# ============ SCALPING CONFIGURATION ============
class ScalpingConfig:
    def __init__(self):
        # Timeframe settings
        self.timeframe = '3m'  # 1m, 3m, or 5m for scalping
        self.donchian_period = 20  # 20 candles = 1 hour on 3min chart
        
        # Zone thresholds (tighter for scalping)
        self.entry_zone_pct = 0.02  # 2% from edge for entry
        self.signal_zone_pct = 0.08  # 8% from edge for early signal
        
        # Scalping specific filters
        self.min_volume_ratio = 1.5  # Volume must be 1.5x average
        self.min_price_movement = 0.001  # Minimum 0.1% movement expected
        self.max_spread_pct = 0.002  # Maximum 0.2% spread
        
        # Risk management for scalping
        self.risk_reward_ratio = 1.5  # Tighter for scalping
        self.max_risk_pct = 0.005  # Max 0.5% risk per trade
        self.take_profit_multiplier = 1.5  # 1.5x risk for profit
        
        # Time filters (highest volume hours)
        self.liquid_start_hour = 12  # 12 PM UTC
        self.liquid_end_hour = 20    # 8 PM UTC
        self.avoid_weekend = True
        
        # Momentum filters
        self.min_momentum_candles = 3  # 3 consecutive candles in same direction
        self.require_volume_confirmation = True

config = ScalpingConfig()

# ============ FAST INDICATORS FOR SCALPING ============

def calculate_fast_ema(df, period):
    """Fast EMA for scalping"""
    return df['close'].ewm(span=period, adjust=False).mean()

def calculate_macd(df, fast=12, slow=26, signal=9):
    """MACD for momentum"""
    try:
        exp1 = df['close'].ewm(span=fast, adjust=False).mean()
        exp2 = df['close'].ewm(span=slow, adjust=False).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        histogram = macd - signal_line
        return macd.iloc[-1], signal_line.iloc[-1], histogram.iloc[-1]
    except:
        return 0, 0, 0

def calculate_rsi(df, period=7):  # Shorter period for scalping
    """RSI - shorter period for faster signals"""
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

def calculate_stochastic(df, k_period=14, d_period=3):
    """Stochastic RSI for overbought/oversold"""
    try:
        low_14 = df['low'].rolling(window=k_period).min()
        high_14 = df['high'].rolling(window=k_period).max()
        stoch = 100 * ((df['close'] - low_14) / (high_14 - low_14))
        k = stoch.rolling(window=d_period).mean()
        d = k.rolling(window=d_period).mean()
        return round(k.iloc[-1], 2), round(d.iloc[-1], 2)
    except:
        return 50, 50

def calculate_bollinger_bands(df, period=20, std_dev=2):
    """Bollinger Bands for volatility"""
    try:
        sma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        return upper.iloc[-1], sma.iloc[-1], lower.iloc[-1]
    except:
        return 0, 0, 0

def calculate_vwap(df):
    """VWAP for institutional levels"""
    try:
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap = (typical_price * df['vol']).cumsum() / df['vol'].cumsum()
        return vwap.iloc[-1]
    except:
        return df['close'].iloc[-1]

def calculate_volume_profile(df, lookback=20):
    """Check if current volume is significant"""
    try:
        current_vol = df['vol'].iloc[-1]
        avg_vol = df['vol'].tail(lookback).mean()
        vol_ratio = current_vol / avg_vol
        return vol_ratio
    except:
        return 1

def calculate_momentum(df, period=5):
    """Price momentum over last N candles"""
    try:
        momentum = (df['close'].iloc[-1] - df['close'].iloc[-period]) / df['close'].iloc[-period]
        return momentum * 100  # Percentage
    except:
        return 0

def check_consecutive_candles(df, direction):
    """Check for consecutive candles in same direction"""
    try:
        consecutive = 0
        for i in range(1, config.min_momentum_candles + 1):
            if direction == "UP":
                if df['close'].iloc[-i] > df['close'].iloc[-i-1]:
                    consecutive += 1
                else:
                    break
            else:  # DOWN
                if df['close'].iloc[-i] < df['close'].iloc[-i-1]:
                    consecutive += 1
                else:
                    break
        return consecutive >= config.min_momentum_candles
    except:
        return False

# ============ DONCHIAN FOR SCALPING ============

def calculate_donchian_scalping(df):
    """Optimized Donchian for scalping"""
    period = config.donchian_period
    
    HH = df['high'].tail(period).max()
    LL = df['low'].tail(period).min()
    channel_range = HH - LL
    
    # Tighter zones for scalping
    entry_top = HH - (channel_range * config.entry_zone_pct)
    entry_bottom = LL + (channel_range * config.entry_zone_pct)
    signal_top = HH - (channel_range * config.signal_zone_pct)
    signal_bottom = LL + (channel_range * config.signal_zone_pct)
    
    return HH, LL, channel_range, entry_top, entry_bottom, signal_top, signal_bottom

# ============ SCALPING SIGNAL SCORING ============

def calculate_scalp_score(df, symbol, signal_type, current_price, HH, LL, adx):
    """Calculate probability score for scalping"""
    score = 50
    reasons = []
    
    # Volume score (0-25 points)
    vol_ratio = calculate_volume_profile(df)
    if vol_ratio >= config.min_volume_ratio:
        score += 20
        reasons.append(f"🔥 Volume {vol_ratio:.1f}x (+20)")
    elif vol_ratio >= 1.2:
        score += 10
        reasons.append(f"📊 Volume {vol_ratio:.1f}x (+10)")
    else:
        score -= 15
        reasons.append(f"⚠️ Low volume {vol_ratio:.1f}x (-15)")
    
    # RSI score (0-20 points)
    rsi = calculate_rsi(df, 7)
    if signal_type in ["BUY", "BOUNCE"]:
        if rsi < 35:
            score += 20
            reasons.append(f"📉 RSI {rsi} oversold (+20)")
        elif rsi < 45:
            score += 10
            reasons.append(f"📊 RSI {rsi} low (+10)")
        elif rsi > 75:
            score -= 20
            reasons.append(f"❌ RSI {rsi} overbought (-20)")
    else:  # SELL signals
        if rsi > 65:
            score += 20
            reasons.append(f"📈 RSI {rsi} overbought (+20)")
        elif rsi > 55:
            score += 10
            reasons.append(f"📊 RSI {rsi} high (+10)")
        elif rsi < 25:
            score -= 20
            reasons.append(f"❌ RSI {rsi} oversold (-20)")
    
    # MACD momentum (0-20 points)
    macd, signal, hist = calculate_macd(df, 8, 17, 5)  # Faster MACD for scalping
    if signal_type in ["BUY", "BOUNCE"]:
        if hist > 0 and macd > signal:
            score += 15
            reasons.append(f"📈 MACD bullish (+15)")
        elif hist > 0:
            score += 8
            reasons.append(f"📊 MACD turning (+8)")
        else:
            score -= 10
            reasons.append(f"⚠️ MACD bearish (-10)")
    else:
        if hist < 0 and macd < signal:
            score += 15
            reasons.append(f"📉 MACD bearish (+15)")
        elif hist < 0:
            score += 8
            reasons.append(f"📊 MACD turning (+8)")
        else:
            score -= 10
            reasons.append(f"⚠️ MACD bullish (-10)")
    
    # Stochastic (0-15 points)
    k, d = calculate_stochastic(df, 10, 3)
    if signal_type in ["BUY", "BOUNCE"]:
        if k < 20 and d < 20:
            score += 15
            reasons.append(f"🎯 Stochastic oversold (+15)")
        elif k < 30:
            score += 8
            reasons.append(f"📊 Stochastic low (+8)")
    else:
        if k > 80 and d > 80:
            score += 15
            reasons.append(f"🎯 Stochastic overbought (+15)")
        elif k > 70:
            score += 8
            reasons.append(f"📊 Stochastic high (+8)")
    
    # Consecutive candles (0-10 points)
    if signal_type in ["BUY", "BOUNCE"]:
        if check_consecutive_candles(df, "UP"):
            score += 10
            reasons.append(f"🕯️ {config.min_momentum_candles}+ green candles (+10)")
    else:
        if check_consecutive_candles(df, "DOWN"):
            score += 10
            reasons.append(f"🕯️ {config.min_momentum_candles}+ red candles (+10)")
    
    # Bollinger Bands position (0-10 points)
    upper, middle, lower = calculate_bollinger_bands(df, 14, 2)
    if signal_type in ["BUY", "BOUNCE"]:
        if current_price <= lower * 1.001:
            score += 10
            reasons.append(f"📊 At lower Bollinger Band (+10)")
    else:
        if current_price >= upper * 0.999:
            score += 10
            reasons.append(f"📊 At upper Bollinger Band (+10)")
    
    # Cap score
    score = max(0, min(100, score))
    
    return score, reasons

# ============ TELEGRAM ALERT ============

last_alert = {}
alert_cooldown = {}  # Prevent spam on same symbol

def send_alert(message):
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

# ============ MAIN SCALPING BOT ============

def run_scalping_bot():
    print("="*70)
    print("⚡ HIGH PROBABILITY SCALPING BOT ⚡")
    print("="*70)
    print(f"Timeframe: {config.timeframe}")
    print(f"Donchian Period: {config.donchian_period} candles")
    print(f"Symbols: {len(SYMBOLS)}")
    print(f"Min Volume Ratio: {config.min_volume_ratio}x")
    print(f"Min R:R: {config.risk_reward_ratio}:1")
    print(f"Max Risk: {config.max_risk_pct*100}%")
    print("="*70)
    
    send_alert(f"""⚡ SCALPING BOT ACTIVATED ⚡

📊 Timeframe: {config.timeframe}
🎯 Strategy: Donchian Breakout/Reversal
📈 Min R:R: {config.risk_reward_ratio}:1
🔥 Volume Filter: {config.min_volume_ratio}x
""")
    
    while True:
        for symbol in SYMBOLS:
            try:
                # Rate limiting per symbol
                current_time = time.time()
                if symbol in alert_cooldown:
                    if current_time - alert_cooldown[symbol] < 60:  # 1 minute cooldown
                        continue
                
                if symbol not in EXCHANGE.markets:
                    continue
                
                # Fetch data - more candles for indicators
                ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe=config.timeframe, limit=100)
                if len(ohlcv) < 50:
                    continue
                
                df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                
                # Calculate Donchian
                HH, LL, channel_range, entry_top, entry_bottom, signal_top, signal_bottom = calculate_donchian_scalping(df)
                current_price = df['close'].iloc[-1]
                
                # Skip if channel is too tight (no movement expected)
                if channel_range < config.min_price_movement:
                    continue
                
                # Fast indicators for scalping
                adx = calculate_adx(df, 10)  # Shorter ADX for scalping
                vwap = calculate_vwap(df)
                momentum = calculate_momentum(df, 3)
                
                # Check if price is in signal zone
                in_signal_zone_top = current_price >= signal_top
                in_signal_zone_bottom = current_price <= signal_bottom
                in_entry_zone_top = current_price >= entry_top
                in_entry_zone_bottom = current_price <= entry_bottom
                
                # ============ SCALPING SIGNALS ============
                
                # SIGNAL 1: BREAKOUT BUY (Price breaking above resistance)
                if in_entry_zone_top and momentum > 0.1:
                    if last_alert.get(symbol) != "SCALP_BUY_BREAKOUT":
                        
                        # Calculate entry levels
                        entry = current_price
                        stop_loss = entry - (channel_range * 0.3)  # Tight stop for scalping
                        take_profit = entry + (channel_range * 0.45)
                        
                        risk_pct = ((entry - stop_loss) / entry) * 100
                        reward_pct = ((take_profit - entry) / entry) * 100
                        rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 0
                        
                        # Volume check
                        vol_ratio = calculate_volume_profile(df)
                        
                        # Calculate probability score
                        prob_score, reasons = calculate_scalp_score(df, symbol, "BUY", current_price, HH, LL, adx)
                        
                        # Only alert if high probability
                        if prob_score >= 65 and rr_ratio >= config.risk_reward_ratio and vol_ratio >= config.min_volume_ratio:
                            
                            stars = "⭐⭐⭐⭐⭐" if prob_score >= 85 else "⭐⭐⭐⭐" if prob_score >= 75 else "⭐⭐⭐"
                            
                            message = f"""
{stars} SCALPING SIGNAL - {prob_score}% PROBABILITY {stars}

🟢🟢🟢 BUY - BREAKOUT 🟢🟢🟢

📊 {symbol} ({config.timeframe})
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
⚡ SIGNAL DETAILS:
• Type: BREAKOUT BUY
• Momentum: {momentum:.2f}%
• Volume: {vol_ratio:.1f}x average
━━━━━━━━━━━━━━━━━━━━━━

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {risk_pct:.2f}%
• Reward: {reward_pct:.2f}%
• R:R Ratio: {rr_ratio:.1f}:1

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}
• VWAP: ${vwap:.6f}

🎲 PROBABILITY SCORE: {prob_score}%

📈 KEY FACTORS:
{chr(10).join(reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
⚡ ACTION: BUY NOW! Tight stop loss.
"""
                            send_alert(message)
                            print(f"{symbol} - 🟢 SCALP BUY - {prob_score}%")
                            last_alert[symbol] = "SCALP_BUY_BREAKOUT"
                            alert_cooldown[symbol] = current_time
                
                # SIGNAL 2: REVERSAL SELL (Price at resistance, fading)
                elif in_entry_zone_top and momentum < -0.05:
                    if last_alert.get(symbol) != "SCALP_SELL_REVERSAL":
                        
                        entry = current_price
                        stop_loss = entry + (channel_range * 0.25)
                        take_profit = entry - (channel_range * 0.4)
                        
                        risk_pct = ((stop_loss - entry) / entry) * 100
                        reward_pct = ((entry - take_profit) / entry) * 100
                        rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 0
                        
                        vol_ratio = calculate_volume_profile(df)
                        prob_score, reasons = calculate_scalp_score(df, symbol, "SELL", current_price, HH, LL, adx)
                        
                        if prob_score >= 65 and rr_ratio >= config.risk_reward_ratio:
                            
                            stars = "⭐⭐⭐⭐⭐" if prob_score >= 85 else "⭐⭐⭐⭐" if prob_score >= 75 else "⭐⭐⭐"
                            
                            message = f"""
{stars} SCALPING SIGNAL - {prob_score}% PROBABILITY {stars}

🔴🔴🔴 SELL - REVERSAL 🔴🔴🔴

📊 {symbol} ({config.timeframe})
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
⚡ SIGNAL DETAILS:
• Type: REVERSAL SELL
• Momentum: {momentum:.2f}%
• Volume: {vol_ratio:.1f}x average
━━━━━━━━━━━━━━━━━━━━━━

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {risk_pct:.2f}%
• Reward: {reward_pct:.2f}%
• R:R Ratio: {rr_ratio:.1f}:1

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}
• VWAP: ${vwap:.6f}

🎲 PROBABILITY SCORE: {prob_score}%

📉 KEY FACTORS:
{chr(10).join(reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
⚡ ACTION: SELL NOW! Quick scalp.
"""
                            send_alert(message)
                            print(f"{symbol} - 🔴 SCALP SELL - {prob_score}%")
                            last_alert[symbol] = "SCALP_SELL_REVERSAL"
                            alert_cooldown[symbol] = current_time
                
                # SIGNAL 3: BOUNCE BUY (Price at support)
                elif in_entry_zone_bottom and momentum > 0.05:
                    if last_alert.get(symbol) != "SCALP_BUY_BOUNCE":
                        
                        entry = current_price
                        stop_loss = entry - (channel_range * 0.25)
                        take_profit = entry + (channel_range * 0.4)
                        
                        risk_pct = ((entry - stop_loss) / entry) * 100
                        reward_pct = ((take_profit - entry) / entry) * 100
                        rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 0
                        
                        vol_ratio = calculate_volume_profile(df)
                        prob_score, reasons = calculate_scalp_score(df, symbol, "BUY", current_price, HH, LL, adx)
                        
                        if prob_score >= 65 and rr_ratio >= config.risk_reward_ratio:
                            
                            stars = "⭐⭐⭐⭐⭐" if prob_score >= 85 else "⭐⭐⭐⭐" if prob_score >= 75 else "⭐⭐⭐"
                            
                            message = f"""
{stars} SCALPING SIGNAL - {prob_score}% PROBABILITY {stars}

🟢🟢🟢 BUY - BOUNCE 🟢🟢🟢

📊 {symbol} ({config.timeframe})
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
⚡ SIGNAL DETAILS:
• Type: BOUNCE BUY
• Momentum: {momentum:.2f}%
• Volume: {vol_ratio:.1f}x average
━━━━━━━━━━━━━━━━━━━━━━

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {risk_pct:.2f}%
• Reward: {reward_pct:.2f}%
• R:R Ratio: {rr_ratio:.1f}:1

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}
• VWAP: ${vwap:.6f}

🎲 PROBABILITY SCORE: {prob_score}%

📈 KEY FACTORS:
{chr(10).join(reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
⚡ ACTION: BUY ON BOUNCE! Quick scalp.
"""
                            send_alert(message)
                            print(f"{symbol} - 🟢 SCALP BOUNCE BUY - {prob_score}%")
                            last_alert[symbol] = "SCALP_BUY_BOUNCE"
                            alert_cooldown[symbol] = current_time
                
                # SIGNAL 4: BREAKDOWN SELL (Price breaking below support)
                elif in_entry_zone_bottom and momentum < -0.1:
                    if last_alert.get(symbol) != "SCALP_SELL_BREAKDOWN":
                        
                        entry = current_price
                        stop_loss = entry + (channel_range * 0.3)
                        take_profit = entry - (channel_range * 0.45)
                        
                        risk_pct = ((stop_loss - entry) / entry) * 100
                        reward_pct = ((entry - take_profit) / entry) * 100
                        rr_ratio = reward_pct / risk_pct if risk_pct > 0 else 0
                        
                        vol_ratio = calculate_volume_profile(df)
                        prob_score, reasons = calculate_scalp_score(df, symbol, "SELL", current_price, HH, LL, adx)
                        
                        if prob_score >= 65 and rr_ratio >= config.risk_reward_ratio and vol_ratio >= config.min_volume_ratio:
                            
                            stars = "⭐⭐⭐⭐⭐" if prob_score >= 85 else "⭐⭐⭐⭐" if prob_score >= 75 else "⭐⭐⭐"
                            
                            message = f"""
{stars} SCALPING SIGNAL - {prob_score}% PROBABILITY {stars}

🔴🔴🔴 SELL - BREAKDOWN 🔴🔴🔴

📊 {symbol} ({config.timeframe})
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
⚡ SIGNAL DETAILS:
• Type: BREAKDOWN SELL
• Momentum: {momentum:.2f}%
• Volume: {vol_ratio:.1f}x average
━━━━━━━━━━━━━━━━━━━━━━

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {risk_pct:.2f}%
• Reward: {reward_pct:.2f}%
• R:R Ratio: {rr_ratio:.1f}:1

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}
• VWAP: ${vwap:.6f}

🎲 PROBABILITY SCORE: {prob_score}%

📉 KEY FACTORS:
{chr(10).join(reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
⚡ ACTION: SELL NOW! Momentum breakdown.
"""
                            send_alert(message)
                            print(f"{symbol} - 🔴 SCALP BREAKDOWN SELL - {prob_score}%")
                            last_alert[symbol] = "SCALP_SELL_BREAKDOWN"
                            alert_cooldown[symbol] = current_time
                
                # Reset when price moves away
                elif current_price < signal_top and current_price > signal_bottom:
                    if last_alert.get(symbol) not in [None, "RESET"]:
                        last_alert[symbol] = None
                
                # Small delay between symbols
                time.sleep(0.2)
                
            except Exception as e:
                print(f"Error with {symbol}: {e}")
                continue
        
        # Wait before next cycle
        time.sleep(3)

# Add missing ADX function for scalping
def calculate_adx(df, period=10):
    """ADX for scalping with shorter period"""
    try:
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        n = len(df)
        if n < period + 1:
            return 20
        
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
        
        return round(adx[-1], 2) if not np.isnan(adx[-1]) else 20
    except:
        return 20

# ============ START BOT ============

# Run bot in background thread
bot_thread = threading.Thread(target=run_scalping_bot, daemon=True)
bot_thread.start()

# Start Flask app
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
