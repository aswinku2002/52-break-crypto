import os
import time
import ccxt
import pandas as pd
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
    'XMR/USDT', 'AIN/USDT', 'DOT/USDT', '1000SATS/USDT',
    'PENGU/USDT', 'ARC/USDT', 'M/USDT', 'DOGS/USDT'
]

EXCHANGE = ccxt.binance({
    'enableRateLimit': True,
})

EXCHANGE.load_markets()

# Prevent repeated alerts
last_alert = {}

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
    send_alert("✅ Donchian Touch Bot Started")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol: {symbol}")
                    continue

                # Get 53 candles:
                # 52 completed candles + current candle
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=53
                )

                if len(ohlcv) < 53:
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # Highest high / lowest low of PREVIOUS 52 candles
                upper_band = df['high'][:-1].max()
                lower_band = df['low'][:-1].min()

                # Live market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']

                if symbol not in last_alert:
                    last_alert[symbol] = None

                # Touch High
                if current_price >= upper_band:
                    if last_alert[symbol] != "HIGH":
                        send_alert(
                            f"🚀 {symbol} TOUCHED 52-BAR HIGH\n"
                            f"Price: {current_price}\n"
                            f"52-Bar High: {upper_band}"
                        )
                        print(f"{symbol} HIGH touched")
                        last_alert[symbol] = "HIGH"

                # Touch Low
                elif current_price <= lower_band:
                    if last_alert[symbol] != "LOW":
                        send_alert(
                            f"🐻 {symbol} TOUCHED 52-BAR LOW\n"
                            f"Price: {current_price}\n"
                            f"52-Bar Low: {lower_band}"
                        )
                        print(f"{symbol} LOW touched")
                        last_alert[symbol] = "LOW"

                else:
                    # Reset when price moves away
                    last_alert[symbol] = None

            except Exception as e:
                print(f"Error checking {symbol}: {e}")

        # Check every minute
        time.sleep(60)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)