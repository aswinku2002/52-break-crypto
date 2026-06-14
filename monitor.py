import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask

# 1. Setup Flask for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# 2. Configuration
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

# Prevent repeated alerts
last_combined_alert = {}

def calculate_adx(df, period=14):
    """Manual ADX calculation - fixed version"""
    try:
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        n = len(df)
        if n < period + 1:
            return 0
        
        # Initialize arrays
        tr = np.zeros(n)
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        
        # Calculate True Range and Directional Movement
        for i in range(1, n):
            # True Range
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i-1])
            lc = abs(low[i] - close[i-1])
            tr[i] = max(hl, hc, lc)
            
            # Directional Movement
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
        
        # Calculate smoothed averages using Wilder's method
        atr = np.zeros(n)
        smooth_plus_dm = np.zeros(n)
        smooth_minus_dm = np.zeros(n)
        
        # First average (simple average for first period)
        atr[period] = np.sum(tr[1:period+1]) / period
        smooth_plus_dm[period] = np.sum(plus_dm[1:period+1]) / period
        smooth_minus_dm[period] = np.sum(minus_dm[1:period+1]) / period
        
        # Subsequent averages (Wilder's smoothing)
        for i in range(period+1, n):
            atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
            smooth_plus_dm[i] = (smooth_plus_dm[i-1] * (period - 1) + plus_dm[i]) / period
            smooth_minus_dm[i] = (smooth_minus_dm[i-1] * (period - 1) + minus_dm[i]) / period
        
        # Calculate Plus DI and Minus DI
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
        
        # Calculate ADX (smoothed DX)
        adx = np.zeros(n)
        adx[period + period - 1] = np.sum(dx[period:period+period-1]) / (period - 1)
        
        for i in range(period + period, n):
            adx[i] = (adx[i-1] * (period - 1) + dx[i]) / period
        
        result = adx[-1] if not np.isnan(adx[-1]) else 0
        return round(result, 2)
        
    except Exception as e:
        print(f"ADX calculation error: {e}")
        return 0

def calculate_choppiness_index(df, period=14):
    """Calculate Choppiness Index"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Sum of True Range over period
        sum_tr = tr.rolling(window=period).sum()
        
        # Highest high and lowest low over period
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()
        
        # Avoid division by zero
        price_range = highest_high - lowest_low
        price_range = price_range.replace(0, np.nan)
        
        # Choppiness Index formula
        choppiness = 100 * np.log10(sum_tr / price_range) / np.log10(period)
        
        result = choppiness.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
        
    except Exception as e:
        print(f"Choppiness calculation error: {e}")
        return 50

def send_alert(message):
    if TOKEN and CHAT_ID:
        try:
            requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={
                    "chat_id": CHAT_ID,
                    "text": message
                },
                timeout=10
            )
        except Exception as e:
            print(f"Telegram error: {e}")

def run_bot():
    print("Bot loop started...")

    # Startup message
    send_alert("✅ Multi-Indicator Bot Started (DC52 + ADX14 + CHOP14)")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol: {symbol}")
                    continue

                # Get enough candles for all indicators
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=100  # Increased for better calculation
                )

                if len(ohlcv) < 70:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # ============ DONCHIAN CHANNEL (52 candles) ============
                HH = df['high'][-53:-1].max()
                LL = df['low'][-53:-1].min()
                
                bullish_level = HH - ((HH - LL) * 0.025)
                bearish_level = LL + ((HH - LL) * 0.025)
                
                # ============ ADX 14 ============
                adx_value = calculate_adx(df, period=14)
                
                # ============ CHOPPINESS INDEX 14 ============
                chop_value = calculate_choppiness_index(df, period=14)
                
                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # Initialize alert tracking
                if symbol not in last_combined_alert:
                    last_combined_alert[symbol] = None
                
                # Debug print to verify calculations
                print(f"{symbol} - ADX: {adx_value}, CHOP: {chop_value}, Price: ${current_price:.8f}")
                
                # Skip if ADX or CHOP couldn't be calculated
                if adx_value == 0 or chop_value == 50:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue
                
                # ============ CHECK FOR BULLISH TREND ============
                if current_price >= bullish_level and adx_value > 25 and chop_value < 40:
                    if last_combined_alert[symbol] != "BULLISH_TREND":
                        message = (
                            f"🟢🟢🟢 BULLISH TREND CONFIRMATION 🟢🟢🟢\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.8f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • Price in BULLISH ZONE (top 5%)\n"
                            f"  • HH: ${HH:.8f}\n"
                            f"  • LL: ${LL:.8f}\n"
                            f"  • Level: ${bullish_level:.8f}\n\n"
                            f"📈 ADX (14): {adx_value} (>25 ✅)\n"
                            f"  → Strong Trend Detected\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value} (<40 ✅)\n"
                            f"  → Non-Choppy/Trending Market\n\n"
                            f"⚡ VERDICT: STRONG BULLISH ALIGNMENT - All indicators confirm uptrend!"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BULLISH TREND ALERT")
                        last_combined_alert[symbol] = "BULLISH_TREND"
                
                # ============ CHECK FOR BEARISH TREND ============
                elif current_price <= bearish_level and adx_value > 25 and chop_value < 40:
                    if last_combined_alert[symbol] != "BEARISH_TREND":
                        message = (
                            f"🔴🔴🔴 BEARISH TREND CONFIRMATION 🔴🔴🔴\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.8f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • Price in BEARISH ZONE (bottom 5%)\n"
                            f"  • HH: ${HH:.8f}\n"
                            f"  • LL: ${LL:.8f}\n"
                            f"  • Level: ${bearish_level:.8f}\n\n"
                            f"📈 ADX (14): {adx_value} (>25 ✅)\n"
                            f"  → Strong Trend Detected\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value} (<40 ✅)\n"
                            f"  → Non-Choppy/Trending Market\n\n"
                            f"⚡ VERDICT: STRONG BEARISH ALIGNMENT - All indicators confirm downtrend!"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 BEARISH TREND ALERT")
                        last_combined_alert[symbol] = "BEARISH_TREND"
                
                # ============ CHECK FOR POTENTIAL REVERSAL ============
                elif adx_value < 25 and chop_value > 60:
                    if last_combined_alert[symbol] != "REVERSAL":
                        # Determine if price is near extremes for extra context
                        price_position = "neutral"
                        if current_price >= bullish_level:
                            price_position = "near resistance (bullish zone)"
                        elif current_price <= bearish_level:
                            price_position = "near support (bearish zone)"
                        else:
                            price_position = "middle of channel"
                        
                        message = (
                            f"⚠️⚠️⚠️ POTENTIAL REVERSAL ALERT ⚠️⚠️⚠️\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.8f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • Price position: {price_position}\n"
                            f"  • HH: ${HH:.8f}\n"
                            f"  • LL: ${LL:.8f}\n\n"
                            f"📉 ADX (14): {adx_value} (<25 ✅)\n"
                            f"  → Weak/No Trend\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value} (>60 ✅)\n"
                            f"  → Choppy/Ranging Market\n\n"
                            f"⚡ VERDICT: Market is choppy with no clear trend!\n"
                            f"  → Potential reversal or breakout imminent\n"
                            f"  → Wait for trend confirmation before entering trades"
                        )
                        send_alert(message)
                        print(f"{symbol} - ⚠️ REVERSAL ALERT")
                        last_combined_alert[symbol] = "REVERSAL"
                
                # Reset alert when conditions no longer met
                else:
                    if last_combined_alert[symbol] is not None:
                        print(f"{symbol} - Alert reset: {last_combined_alert[symbol]} condition ended")
                        last_combined_alert[symbol] = None

            except Exception as e:
                print(f"Error checking {symbol}: {e}")

        # Check every 5 seconds
        time.sleep(5)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)