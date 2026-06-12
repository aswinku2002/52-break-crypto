import os
import time
import ccxt
import pandas as pd
import requests
import threading
from flask import Flask

# Flask app for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# Telegram Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Symbols to monitor
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT',
    'PAXG/USDT', 'XAUT/USDT', 'BEAT/USDT', 'H/USDT',
    'AIO/USDT', 'XRP/USDT', 'LAB/USDT', 'ZEC/USDT',
    'SKYAI/USDT', 'SLVON/USDT', 'DOGE/USDT', 'SIREN/USDT',
    'BNB/USDT', 'LTC/USDT', 'PIPPIN/USDT', 'LINK/USDT',
    'XMR/USDT', 'AIN/USDT', 'DOT/USDT', '1000SATS/USDT',
    'PENGU/USDT', 'ARC/USDT', 'M/USDT', 'DOGS/USDT'
]

# Binance Exchange
exchange = ccxt.binance({
    'enableRateLimit': True,
})

exchange.load_markets()

# Prevent repeated alerts
alerted_high = {}
alerted_low = {}

def send_alert(message):
    if TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.get(url, params={
            'chat_id': CHAT_ID,
            'text': message
        })

def run_bot():
    print("Bot started...")

    while True:
        for symbol in SYMBOLS:
            try:
                # 53 candles = 52 previous + current
                ohlcv = exchange.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=53
                )

                df = pd.DataFrame(
                    ohlcv,
                    columns=[
                        'ts',
                        'open',
                        'high',
                        'low',
                        'close',
                        'volume'
                    ]
                )

                highest_high = df['high'][:-1].max()
                lowest_low = df['low'][:-1].min()

                current_high = df['high'].iloc[-1]
                current_low = df['low'].iloc[-1]

                # Touch highest high
                if current_high >= highest_high:
                    if alerted_high.get(symbol) != highest_high:
                        send_alert(
                            f"🚀 {symbol}\n"
                            f"Touched 52-Candle High\n"
                            f"Current High: {current_high}\n"
                            f"Level: {highest_high}"
                        )
                        alerted_high[symbol] = highest_high

                # Touch lowest low
                if current_low <= lowest_low:
                    if alerted_low.get(symbol) != lowest_low:
                        send_alert(
                            f"🐻 {symbol}\n"
                            f"Touched 52-Candle Low\n"
                            f"Current Low: {current_low}\n"
                            f"Level: {lowest_low}"
                        )
                        alerted_low[symbol] = lowest_low

            except Exception as e:
                print(f"Error checking {symbol}: {e}")

        # Check every minute
        time.sleep(60)

# Start bot thread
threading.Thread(
    target=run_bot,
    daemon=True
).start()

# Start Flask
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
