import os
import time
import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import threading
import numpy as np
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
last_alert = {}
last_trend_alert = {}  # Track trend/reversal alerts separately

def calculate_adx(df, period=14):
    """Calculate ADX (Average Directional Index)"""
    adx = ta.adx(df['high'], df['low'], df['close'], length=period)
    return adx[f'ADX_{period}'].iloc[-1] if not pd.isna(adx[f'ADX_{period}'].iloc[-1]) else 0

def calculate_choppiness_index(df, period=14):
    """Calculate Choppiness Index"""
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
    
    # Choppiness Index formula
    choppiness = 100 * np.log10(sum_tr / (highest_high - lowest_low)) / np.log10(period)
    
    return choppiness.iloc[-1] if not pd.isna(choppiness.iloc[-1]) else 50

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
    send_alert("✅ Donchian 52-Bar Zone Bot with ADX & Choppiness Index Started")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol: {symbol}")
                    continue

                # Get enough candles for all indicators (need 52 for DC + 14 for indicators + buffer = 70 candles)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=70  # Increased to have enough data for all indicators
                )

                if len(ohlcv) < 70:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # ============ DONCHIAN CHANNEL (52 candles) ============
                # Highest high and lowest low of previous 52 candles
                HH = df['high'][-53:-1].max()  # Last 52 candles before current
                LL = df['low'][-53:-1].min()
                
                # Your formulas
                bullish_level = HH - ((HH - LL) * 0.025)
                bearish_level = LL + ((HH - LL) * 0.025)
                
                # ============ ADX 14 ============
                # Use last 60 candles for ADX calculation to ensure enough data
                adx_df = df.tail(60).copy()
                adx_value = calculate_adx(adx_df, period=14)
                
                # ============ CHOPPINESS INDEX 14 ============
                chop_value = calculate_choppiness_index(df.tail(60), period=14)
                
                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None
                if symbol not in last_trend_alert:
                    last_trend_alert[symbol] = None
                
                # ============ TREND ALERT (ADX > 25 & CHOP < 40) ============
                if adx_value > 25 and chop_value < 40:
                    if last_trend_alert[symbol] != "TREND":
                        message = (
                            f"🟢 TREND ALERT 🟢\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.4f}\n"
                            f"ADX(14): {adx_value:.2f} (>25 ✅)\n"
                            f"Choppiness Index(14): {chop_value:.2f} (<40 ✅)\n"
                            f"Donchian Channel(52):\n"
                            f"  HH: ${HH:.4f}\n"
                            f"  LL: ${LL:.4f}\n"
                            f"  Range: ${(HH-LL):.4f}\n"
                            f"Status: Strong Trend Detected 🟢"
                        )
                        send_alert(message)
                        print(f"{symbol} - TREND ALERT: ADX={adx_value:.2f}, CHOP={chop_value:.2f}")
                        last_trend_alert[symbol] = "TREND"
                
                # ============ REVERSAL ALERT (ADX < 25 & CHOP > 60) ============
                elif adx_value < 25 and chop_value > 60:
                    if last_trend_alert[symbol] != "REVERSAL":
                        message = (
                            f"🔴 REVERSAL ALERT 🔴\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.4f}\n"
                            f"ADX(14): {adx_value:.2f} (<25 ✅)\n"
                            f"Choppiness Index(14): {chop_value:.2f} (>60 ✅)\n"
                            f"Donchian Channel(52):\n"
                            f"  HH: ${HH:.4f}\n"
                            f"  LL: ${LL:.4f}\n"
                            f"  Range: ${(HH-LL):.4f}\n"
                            f"Status: Choppy/Ranging Market - Potential Reversal 🔴"
                        )
                        send_alert(message)
                        print(f"{symbol} - REVERSAL ALERT: ADX={adx_value:.2f}, CHOP={chop_value:.2f}")
                        last_trend_alert[symbol] = "REVERSAL"
                
                # Reset trend alert when conditions no longer met
                else:
                    if last_trend_alert[symbol] in ["TREND", "REVERSAL"]:
                        last_trend_alert[symbol] = None
                
                # ============ ORIGINAL DONCHIAN ZONE ALERTS ============
                # Bullish zone (top 5% of channel)
                if current_price >= bullish_level:
                    if last_alert[symbol] != "HIGH":
                        send_alert(
                            f"🚀 BULLISH ZONE\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.4f}\n"
                            f"Level: ${bullish_level:.4f}\n"
                            f"HH: ${HH:.4f}\n"
                            f"LL: ${LL:.4f}\n"
                            f"ADX(14): {adx_value:.2f}\n"
                            f"CHOP(14): {chop_value:.2f}"
                        )
                        print(f"{symbol} bullish zone")
                        last_alert[symbol] = "HIGH"

                # Bearish zone (bottom 5% of channel)
                elif current_price <= bearish_level:
                    if last_alert[symbol] != "LOW":
                        send_alert(
                            f"🐻 BEARISH ZONE\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.4f}\n"
                            f"Level: ${bearish_level:.4f}\n"
                            f"HH: ${HH:.4f}\n"
                            f"LL: ${LL:.4f}\n"
                            f"ADX(14): {adx_value:.2f}\n"
                            f"CHOP(14): {chop_value:.2f}"
                        )
                        print(f"{symbol} bearish zone")
                        last_alert[symbol] = "LOW"

                else:
                    # Reset when price leaves the zones
                    if last_alert[symbol] is not None:
                        last_alert[symbol] = None
                
                # Optional: Print current indicators for monitoring
                print(f"{symbol} - ADX: {adx_value:.2f}, CHOP: {chop_value:.2f}, Price: ${current_price:.4f}")

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
