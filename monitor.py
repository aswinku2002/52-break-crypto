import os
import time
import ccxt
import pandas as pd
import requests
import threading
import pytz
from datetime import datetime
from flask import Flask

# 1. Setup Flask for Render
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is running!"

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'ADA/USD', 'PAXG/USD', 'XAUT/USD', 'BEAT/USD', 'H/USD', 'AIO/USD', 'XRP/USD', 'LAB/USD', 'ZEC/USD', 'SKYAI/USD', 'SLVON/USD', 'DOGE/USD', 'SIREN/USD', 'BNB/USD', 'LTC/USD', 'PIPPIN/USD', 'LINK/USD', 'XMR/USD', 'AIN/USD', 'DOT/USD', '1000SATS/USD', 'PENGU/USD', 'ARC/USD', 'M/USD', 'DOGS/USD'] 
EXCHANGE = ccxt.binance({
    'enableRateLimit': True,
})
EXCHANGE.load_markets()

def send_alert(message):
    if TOKEN and CHAT_ID:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id={CHAT_ID}&text={message}"
        requests.get(url)

def run_bot():
    print("Bot loop started...")
    while True:
        for symbol in SYMBOLS:
            try:
                ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=52)
                df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
                upper_band = df['high'].max()
                lower_band = df['low'].min()
                current_price = df['close'].iloc[-1]
                
                if current_price >= upper_band:
                    send_alert(f"🚀 {symbol} BREAKOUT: Price {current_price} > High {upper_band}")
                elif current_price <= lower_band:
                    send_alert(f"🐻 {symbol} BREAKOUT: Price {current_price} < Low {lower_band}")
            except Exception as e:
                print(f"Error checking {symbol}: {e}")
        
        # Sync with IST 15m intervals
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.now(ist)
        wait_minutes = 15 - (now.minute % 15) - 1
        wait_seconds = 60 - now.second
        time.sleep((wait_minutes * 60) + wait_seconds)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
