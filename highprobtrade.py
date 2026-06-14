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
last_alert = {}

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
    print("Strategy: DC52 + CHOP14 (Chop < 40 only)")
    print("BUY: Price >= Top 5% of DC | SELL: Price <= Bottom 5% of DC")
    
    # Startup message
    send_alert("✅ Bot Started - New Strategy Active\n\n📊 Donchian Channel (52) + Choppiness Index (14)\n🟢 BUY: Chop < 40 & Price >= Top 5%\n🔴 SELL: Chop < 40 & Price <= Bottom 5%")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol: {symbol}")
                    continue

                # Get enough candles for calculations
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=100
                )

                if len(ohlcv) < 70:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # ============ DONCHIAN CHANNEL (52 candles) ============
                HH = df['high'][-53:-1].max()  # Highest high
                LL = df['low'][-53:-1].min()   # Lowest low
                
                # Calculate 5% zones
                channel_range = HH - LL
                top_5_percent = HH - (channel_range * 0.05)  # Top 5% threshold
                bottom_5_percent = LL + (channel_range * 0.05)  # Bottom 5% threshold
                
                # ============ CHOPPINESS INDEX (14) ============
                chop_value = calculate_choppiness_index(df, period=14)
                
                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None
                
                # Debug print
                print(f"{symbol} - CHOP: {chop_value}, Price: ${current_price:.8f}, Top5%: ${top_5_percent:.8f}, Bottom5%: ${bottom_5_percent:.8f}")
                
                # Skip if CHOP couldn't be calculated
                if chop_value == 50:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue
                
                # ============ CHECK FOR BUY SIGNAL ============
                # Condition: CHOP < 40 AND price >= top 5% of channel
                if chop_value < 40 and current_price >= top_5_percent:
                    if last_alert[symbol] != "BUY":
                        message = (
                            f"🟢🟢🟢 BUY SIGNAL 🟢🟢🟢\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.8f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • HH (Highest High): ${HH:.8f}\n"
                            f"  • LL (Lowest Low): ${LL:.8f}\n"
                            f"  • Channel Range: ${channel_range:.8f}\n"
                            f"  • TOP 5% Threshold: ${top_5_percent:.8f}\n"
                            f"  • Current Price: ${current_price:.8f} (≥ Top 5% ✅)\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value} (<40 ✅)\n"
                            f"  → Trending Market (Non-Choppy)\n\n"
                            f"⚡ VERDICT: BUY SIGNAL CONFIRMED!\n"
                            f"  → Price at resistance breakout zone\n"
                            f"  → Trending market conditions\n"
                            f"  → Consider LONG position"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BUY SIGNAL")
                        last_alert[symbol] = "BUY"
                
                # ============ CHECK FOR SELL SIGNAL ============
                # Condition: CHOP < 40 AND price <= bottom 5% of channel
                elif chop_value < 40 and current_price <= bottom_5_percent:
                    if last_alert[symbol] != "SELL":
                        message = (
                            f"🔴🔴🔴 SELL SIGNAL 🔴🔴🔴\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.8f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • HH (Highest High): ${HH:.8f}\n"
                            f"  • LL (Lowest Low): ${LL:.8f}\n"
                            f"  • Channel Range: ${channel_range:.8f}\n"
                            f"  • BOTTOM 5% Threshold: ${bottom_5_percent:.8f}\n"
                            f"  • Current Price: ${current_price:.8f} (≤ Bottom 5% ✅)\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value} (<40 ✅)\n"
                            f"  → Trending Market (Non-Choppy)\n\n"
                            f"⚡ VERDICT: SELL SIGNAL CONFIRMED!\n"
                            f"  → Price at support breakdown zone\n"
                            f"  → Trending market conditions\n"
                            f"  → Consider SHORT position"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 SELL SIGNAL")
                        last_alert[symbol] = "SELL"
                
                # Reset alert when conditions no longer met
                else:
                    if last_alert[symbol] is not None:
                        print(f"{symbol} - Alert reset: {last_alert[symbol]} condition ended")
                        last_alert[symbol] = None

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
