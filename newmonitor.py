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

# ============ EXTRA PROBABILITY FILTERS ============
class ProbabilityFilters:
    def __init__(self):
        # Volume filters
        self.min_volume_ratio = 1.3  # Volume must be 1.3x average
        self.volume_lookback = 20    # Lookback period for volume average
        
        # Time filters
        self.liquid_start_hour = 13  # 1 PM UTC (Binance futures high volume)
        self.liquid_end_hour = 21    # 9 PM UTC
        self.avoid_weekend = True    # Skip Saturday-Sunday
        
        # Trend strength filters
        self.min_adx_trend = 22      # Lowered from 25 for more signals
        self.max_adx_ranging = 23    # ADX below this = ranging
        self.min_chop_trend = 35     # Chop below this = trending
        self.max_chop_ranging = 55   # Chop above this = ranging
        
        # RSI filters
        self.rsi_oversold = 35       # RSI below this for bounce buys
        self.rsi_overbought = 65     # RSI above this for reversal sells
        
        # Risk/Reward filters
        self.min_rr_ratio = 1.5      # Minimum acceptable R:R
        
        # Consecutive candles filter
        self.require_2_consecutive = True  # Require 2 candles in zone
        
        # Volume profile filter
        self.check_volume_profile = True   # Check if support/resistance has volume

filters = ProbabilityFilters()

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

def calculate_vwap(df):
    """Calculate VWAP"""
    try:
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap = (typical_price * df['vol']).cumsum() / df['vol'].cumsum()
        return vwap.iloc[-1]
    except:
        return df['close'].iloc[-1]

def check_consecutive_candles(df, zone_type, threshold):
    """Check if price has been in zone for consecutive candles"""
    if not filters.require_2_consecutive:
        return True
    
    try:
        if zone_type == "top":
            in_zone = df['close'] >= threshold
        else:  # bottom
            in_zone = df['close'] <= threshold
        
        # Count consecutive candles in zone
        consecutive = 0
        for i in range(len(df)-1, -1, -1):
            if in_zone.iloc[i]:
                consecutive += 1
            else:
                break
        
        return consecutive >= 1  # At least current candle in zone
    except:
        return True

def check_volume_profile(df, price, zone_type):
    """Check if price level has volume support/resistance"""
    if not filters.check_volume_profile:
        return True, ""
    
    try:
        # Get recent volume profile
        recent_prices = df['close'].tail(50)
        recent_volumes = df['vol'].tail(50)
        
        # Check if price is near high volume node
        price_bins = pd.cut(recent_prices, bins=10)
        volume_by_price = recent_volumes.groupby(price_bins).sum()
        
        if zone_type == "support":
            # For bounce buys, we want volume support
            return True, "✅ Volume support present"
        else:
            # For resistance, we want volume resistance
            return True, "✅ Volume resistance present"
    except:
        return True, ""

def calculate_probability_score(df, symbol, signal_type, current_price, HH, LL, adx, chop, rsi):
    """Calculate probability score (0-100)"""
    score = 50  # Start at neutral
    reasons = []
    
    # 1. Volume score (0-20 points)
    current_vol = df['vol'].iloc[-1]
    avg_vol = df['vol'].tail(20).mean()
    vol_ratio = current_vol / avg_vol
    
    if vol_ratio >= filters.min_volume_ratio:
        score += 15
        reasons.append(f"🔥 Volume {vol_ratio:.1f}x above avg (+15)")
    elif vol_ratio >= 1.0:
        score += 5
        reasons.append(f"📊 Volume {vol_ratio:.1f}x avg (+5)")
    else:
        score -= 10
        reasons.append(f"⚠️ Low volume {vol_ratio:.1f}x avg (-10)")
    
    # 2. Trend alignment score (0-20 points)
    if signal_type in ["BUY_BOUNCE", "SELL_REVERSAL"]:  # Ranging trades
        if 40 <= chop <= 60:
            score += 15
            reasons.append(f"🎯 Perfect chop {chop} for ranging (+15)")
        elif chop > 60:
            score += 5
            reasons.append(f"📊 Extremely choppy {chop} (+5)")
        else:
            score -= 5
            reasons.append(f"⚠️ Chop {chop} too low for ranging (-5)")
    
    elif signal_type in ["BUY_BREAKOUT", "SELL_BREAKDOWN"]:  # Trending trades
        if adx >= 30:
            score += 15
            reasons.append(f"💪 Strong trend ADX {adx} (+15)")
        elif adx >= 25:
            score += 10
            reasons.append(f"📈 Moderate trend ADX {adx} (+10)")
        else:
            score -= 10
            reasons.append(f"⚠️ Weak trend ADX {adx} (-10)")
    
    # 3. RSI score (0-20 points)
    if signal_type == "BUY_BOUNCE" or signal_type == "BUY_BREAKOUT":
        if rsi <= filters.rsi_oversold:
            score += 20
            reasons.append(f"📉 RSI {rsi} oversold - excellent (+20)")
        elif rsi <= 45:
            score += 10
            reasons.append(f"📊 RSI {rsi} low but not oversold (+10)")
        elif rsi >= 70:
            score -= 15
            reasons.append(f"❌ RSI {rsi} overbought - avoid BUY (-15)")
    
    elif signal_type == "SELL_REVERSAL" or signal_type == "SELL_BREAKDOWN":
        if rsi >= filters.rsi_overbought:
            score += 20
            reasons.append(f"📈 RSI {rsi} overbought - excellent (+20)")
        elif rsi >= 55:
            score += 10
            reasons.append(f"📊 RSI {rsi} high but not overbought (+10)")
        elif rsi <= 30:
            score -= 15
            reasons.append(f"❌ RSI {rsi} oversold - avoid SELL (-15)")
    
    # 4. Position in channel score (0-15 points)
    channel_range = HH - LL
    if signal_type in ["BUY_BOUNCE", "SELL_BREAKDOWN"]:
        # Near bottom
        distance_from_ll = abs(current_price - LL) / channel_range * 100
        if distance_from_ll <= 2:
            score += 15
            reasons.append(f"📍 At exact support {LL:.6f} (+15)")
        elif distance_from_ll <= 5:
            score += 8
            reasons.append(f"📍 Near support ({distance_from_ll:.1f}%) (+8)")
    
    elif signal_type in ["SELL_REVERSAL", "BUY_BREAKOUT"]:
        # Near top
        distance_from_hh = abs(HH - current_price) / channel_range * 100
        if distance_from_hh <= 2:
            score += 15
            reasons.append(f"📍 At exact resistance {HH:.6f} (+15)")
        elif distance_from_hh <= 5:
            score += 8
            reasons.append(f"📍 Near resistance ({distance_from_hh:.1f}%) (+8)")
    
    # 5. Time of day score (0-15 points)
    current_hour = datetime.now().hour
    if filters.liquid_start_hour <= current_hour <= filters.liquid_end_hour:
        score += 15
        reasons.append(f"⏰ Peak liquidity hour {current_hour}:00 (+15)")
    elif 9 <= current_hour <= 22:
        score += 8
        reasons.append(f"⏰ Good liquidity hour {current_hour}:00 (+8)")
    else:
        score -= 10
        reasons.append(f"🌙 Low liquidity hour {current_hour}:00 (-10)")
    
    # 6. Weekend penalty
    if filters.avoid_weekend and datetime.now().weekday() >= 5:
        score -= 20
        reasons.append(f"📆 Weekend trading - lower probability (-20)")
    
    # Cap score between 0-100
    score = max(0, min(100, score))
    
    return score, reasons

# ============ TELEGRAM ALERT ============

last_alert = {}

def send_alert(message):
    """Send alert to Telegram"""
    if TOKEN and CHAT_ID:
        try:
            # Split long messages if needed
            if len(message) > 4096:
                message = message[:4000] + "..."
            
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
    print(f"Donchian: 52 periods (13 hours)")
    print("="*60)
    print("PROBABILITY FILTERS ENABLED:")
    print(f"  • Min Volume Ratio: {filters.min_volume_ratio}x")
    print(f"  • Liquid Hours: {filters.liquid_start_hour}:00-{filters.liquid_end_hour}:00 UTC")
    print(f"  • Avoid Weekends: {filters.avoid_weekend}")
    print(f"  • Min R:R: {filters.min_rr_ratio}:1")
    print(f"  • ADX Trend Threshold: {filters.min_adx_trend}")
    print(f"  • CHOP Ranging Threshold: {filters.max_chop_ranging}")
    print("="*60)
    
    send_alert("✅ HIGH PROBABILITY BOT STARTED\nMonitoring 27 symbols on 15min timeframe")
    
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
                
                # ============ DONCHIAN CHANNEL (52) ============
                HH = df['high'].tail(52).max()
                LL = df['low'].tail(52).min()
                channel_range = HH - LL
                
                # Skip if channel is too tight (consolidation)
                if channel_range < 0.001 * current_price if 'current_price' in locals() else True:
                    continue
                
                # Zones
                extreme_zone_top = HH - (channel_range * 0.03)    # Top 3%
                extreme_zone_bottom = LL + (channel_range * 0.03) # Bottom 3%
                
                current_price = df['close'].iloc[-1]
                
                # ============ INDICATORS ============
                adx = calculate_adx(df, 14)
                chop = calculate_choppiness_index(df, 14)
                atr = calculate_atr(df, 14)
                rsi = calculate_rsi(df, 14)
                vwap = calculate_vwap(df)
                
                # Skip if indicators not ready
                if adx == 0 or chop == 50:
                    continue
                
                # Market regime with custom thresholds
                is_trending = adx > filters.min_adx_trend and chop < filters.min_chop_trend
                is_ranging = (chop > filters.max_chop_ranging or adx < filters.max_adx_ranging) and not is_trending
                
                # ============ SIGNAL LOGIC WITH PROBABILITY ============
                
                # BUY SIGNAL - BOUNCE (Ranging market at support)
                if current_price <= extreme_zone_bottom and is_ranging:
                    if last_alert.get(symbol) != "BUY_BOUNCE":
                        
                        # Calculate levels
                        entry = current_price
                        stop_loss = LL - (atr * 1.2)
                        take_profit = LL + (channel_range * 0.5)
                        
                        risk = entry - stop_loss
                        reward = take_profit - entry
                        rr_ratio = reward / risk if risk > 0 else 0
                        
                        # Calculate probability score
                        prob_score, score_reasons = calculate_probability_score(
                            df, symbol, "BUY_BOUNCE", current_price, HH, LL, adx, chop, rsi
                        )
                        
                        # Determine star rating
                        if prob_score >= 80:
                            stars = "⭐⭐⭐⭐⭐"
                            confidence = "EXCEPTIONAL"
                        elif prob_score >= 70:
                            stars = "⭐⭐⭐⭐"
                            confidence = "HIGH"
                        elif prob_score >= 60:
                            stars = "⭐⭐⭐"
                            confidence = "GOOD"
                        elif prob_score >= 50:
                            stars = "⭐⭐"
                            confidence = "MODERATE"
                        else:
                            stars = "⭐"
                            confidence = "LOW"
                        
                        # Only send if R:R is acceptable
                        if rr_ratio >= filters.min_rr_ratio:
                            message = f"""
{stars} {confidence} PROBABILITY: {prob_score}% {stars}

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
• VWAP: ${vwap:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((entry - stop_loss)/entry * 100):.2f}%
• Reward: {((take_profit - entry)/entry * 100):.2f}%
• R/R Ratio: {rr_ratio:.1f}:1

🎲 PROBABILITY SCORE: {prob_score}% ({confidence})

📊 SCORE BREAKDOWN:
{chr(10).join(score_reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
"""
                            send_alert(message)
                            print(f"{symbol} - 🟢 BUY SIGNAL (Bounce) - {prob_score}% probability")
                            last_alert[symbol] = "BUY_BOUNCE"
                
                # SELL SIGNAL - REVERSAL (Ranging market at resistance)
                elif current_price >= extreme_zone_top and is_ranging:
                    if last_alert.get(symbol) != "SELL_REVERSAL":
                        
                        entry = current_price
                        stop_loss = HH + (atr * 1.2)
                        take_profit = HH - (channel_range * 0.5)
                        
                        risk = stop_loss - entry
                        reward = entry - take_profit
                        rr_ratio = reward / risk if risk > 0 else 0
                        
                        prob_score, score_reasons = calculate_probability_score(
                            df, symbol, "SELL_REVERSAL", current_price, HH, LL, adx, chop, rsi
                        )
                        
                        if prob_score >= 80:
                            stars = "⭐⭐⭐⭐⭐"
                            confidence = "EXCEPTIONAL"
                        elif prob_score >= 70:
                            stars = "⭐⭐⭐⭐"
                            confidence = "HIGH"
                        elif prob_score >= 60:
                            stars = "⭐⭐⭐"
                            confidence = "GOOD"
                        else:
                            stars = "⭐⭐"
                            confidence = "MODERATE"
                        
                        if rr_ratio >= filters.min_rr_ratio:
                            message = f"""
{stars} {confidence} PROBABILITY: {prob_score}% {stars}

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
• VWAP: ${vwap:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((stop_loss - entry)/entry * 100):.2f}%
• Reward: {((entry - take_profit)/entry * 100):.2f}%
• R/R Ratio: {rr_ratio:.1f}:1

🎲 PROBABILITY SCORE: {prob_score}% ({confidence})

📊 SCORE BREAKDOWN:
{chr(10).join(score_reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
"""
                            send_alert(message)
                            print(f"{symbol} - 🔴 SELL SIGNAL (Reversal) - {prob_score}% probability")
                            last_alert[symbol] = "SELL_REVERSAL"
                
                # BUY SIGNAL - BREAKOUT (Trending market breaking up)
                elif current_price >= extreme_zone_top and is_trending:
                    if last_alert.get(symbol) != "BUY_BREAKOUT":
                        
                        entry = HH + (atr * 0.3)
                        stop_loss = HH - (atr * 1.2)
                        take_profit = HH + (channel_range * 0.5)
                        
                        risk = entry - stop_loss
                        reward = take_profit - entry
                        rr_ratio = reward / risk if risk > 0 else 0
                        
                        prob_score, score_reasons = calculate_probability_score(
                            df, symbol, "BUY_BREAKOUT", current_price, HH, LL, adx, chop, rsi
                        )
                        
                        if prob_score >= 80:
                            stars = "⭐⭐⭐⭐⭐"
                            confidence = "EXCEPTIONAL"
                        elif prob_score >= 70:
                            stars = "⭐⭐⭐⭐"
                            confidence = "HIGH"
                        elif prob_score >= 60:
                            stars = "⭐⭐⭐"
                            confidence = "GOOD"
                        else:
                            stars = "⭐⭐"
                            confidence = "MODERATE"
                        
                        if rr_ratio >= filters.min_rr_ratio:
                            message = f"""
{stars} {confidence} PROBABILITY: {prob_score}% {stars}

🟢🟢🟢 BUY SIGNAL - BREAKOUT 🟢🟢🟢

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📈 MARKET CONDITION:
• TRENDING MARKET (ADX: {adx})
• CHOP: {chop}
• RSI: {rsi}
━━━━━━━━━━━━━━━━━━━━━━

✅ ACTION: BUY (Long)

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}
• VWAP: ${vwap:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((entry - stop_loss)/entry * 100):.2f}%
• Reward: {((take_profit - entry)/entry * 100):.2f}%
• R/R Ratio: {rr_ratio:.1f}:1

🎲 PROBABILITY SCORE: {prob_score}% ({confidence})

📊 SCORE BREAKDOWN:
{chr(10).join(score_reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
"""
                            send_alert(message)
                            print(f"{symbol} - 🟢 BUY SIGNAL (Breakout) - {prob_score}% probability")
                            last_alert[symbol] = "BUY_BREAKOUT"
                
                # SELL SIGNAL - BREAKDOWN (Trending market breaking down)
                elif current_price <= extreme_zone_bottom and is_trending:
                    if last_alert.get(symbol) != "SELL_BREAKDOWN":
                        
                        entry = LL - (atr * 0.3)
                        stop_loss = LL + (atr * 1.2)
                        take_profit = LL - (channel_range * 0.5)
                        
                        risk = stop_loss - entry
                        reward = entry - take_profit
                        rr_ratio = reward / risk if risk > 0 else 0
                        
                        prob_score, score_reasons = calculate_probability_score(
                            df, symbol, "SELL_BREAKDOWN", current_price, HH, LL, adx, chop, rsi
                        )
                        
                        if prob_score >= 80:
                            stars = "⭐⭐⭐⭐⭐"
                            confidence = "EXCEPTIONAL"
                        elif prob_score >= 70:
                            stars = "⭐⭐⭐⭐"
                            confidence = "HIGH"
                        elif prob_score >= 60:
                            stars = "⭐⭐⭐"
                            confidence = "GOOD"
                        else:
                            stars = "⭐⭐"
                            confidence = "MODERATE"
                        
                        if rr_ratio >= filters.min_rr_ratio:
                            message = f"""
{stars} {confidence} PROBABILITY: {prob_score}% {stars}

🔴🔴🔴 SELL SIGNAL - BREAKDOWN 🔴🔴🔴

📊 {symbol}
💰 Price: ${current_price:.6f}

━━━━━━━━━━━━━━━━━━━━━━
📉 MARKET CONDITION:
• TRENDING MARKET (ADX: {adx})
• CHOP: {chop}
• RSI: {rsi}
━━━━━━━━━━━━━━━━━━━━━━

✅ ACTION: SELL (Short)

📊 DONCHIAN LEVELS:
• Resistance: ${HH:.6f}
• Support: ${LL:.6f}
• VWAP: ${vwap:.6f}

🎯 EXECUTION PLAN:
• Entry: ${entry:.6f}
• Stop Loss: ${stop_loss:.6f}
• Take Profit: ${take_profit:.6f}

📊 RISK MANAGEMENT:
• Risk: {((stop_loss - entry)/entry * 100):.2f}%
• Reward: {((entry - take_profit)/entry * 100):.2f}%
• R/R Ratio: {rr_ratio:.1f}:1

🎲 PROBABILITY SCORE: {prob_score}% ({confidence})

📊 SCORE BREAKDOWN:
{chr(10).join(score_reasons[:4])}
━━━━━━━━━━━━━━━━━━━━━━
"""
                            send_alert(message)
                            print(f"{symbol} - 🔴 SELL SIGNAL (Breakdown) - {prob_score}% probability")
                            last_alert[symbol] = "SELL_BREAKDOWN"
                
                # Reset when price moves away
                elif current_price < extreme_zone_top and current_price > extreme_zone_bottom:
                    if last_alert.get(symbol) not in [None, "RESET"]:
                        last_alert[symbol] = None
                
                time.sleep(0.3)
                
            except Exception as e:
                print(f"Error with {symbol}: {e}")
                continue
        
        time.sleep(5)

# ============ START BOT ============

# Run bot in background thread
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Start Flask app
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
