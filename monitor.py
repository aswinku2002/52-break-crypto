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
    'XMR/USDT', 'AIN/USDT', '1000SATS/USDT',
    'PENGU/USDT', 'ARC/USDT', 'DOGS/USDT'
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
    send_alert("✅ Donchian 52-Bar Zone Bot Started")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol: {symbol}")
                    continue

                # Get 53 candles
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

                # Highest high and lowest low of previous 52 candles
                HH = df['high'][:-1].max()
                LL = df['low'][:-1].min()

                # Your formulas
                bullish_level = HH - ((HH - LL) * 0.025)
                bearish_level = LL + ((HH - LL) * 0.025)

                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']

                if symbol not in last_alert:
                    last_alert[symbol] = None

                # Bullish zone (top 5% of channel)
                if current_price >= bullish_level:

                    if last_alert[symbol] != "HIGH":
                        send_alert(
                            f"🚀 BULLISH ZONE\n"
                            f"Symbol: {symbol}\n"
                            f"Price: {current_price}\n"
                            f"Level: {bullish_level:.8f}\n"
                            f"HH: {HH:.8f}\n"
                            f"LL: {LL:.8f}"
                        )

                        print(f"{symbol} bullish zone")
                        last_alert[symbol] = "HIGH"

                # Bearish zone (bottom 5% of channel)
                elif current_price <= bearish_level:

                    if last_alert[symbol] != "LOW":
                        send_alert(
                            f"🐻 BEARISH ZONE\n"
                            f"Symbol: {symbol}\n"
                            f"Price: {current_price}\n"
                            f"Level: {bearish_level:.8f}\n"
                            f"HH: {HH:.8f}\n"
                            f"LL: {LL:.8f}"
                        )

                        print(f"{symbol} bearish zone")
                        last_alert[symbol] = "LOW"

                else:
                    # Reset when price leaves the zones
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
