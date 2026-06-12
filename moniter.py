import ccxt
import pandas as pd
import requests
import time
import os
import threading
from flask import Flask
from monitor_logic import run_bot # Put your existing code in a function

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# This starts your monitor logic in the background
threading.Thread(target=run_bot).start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
# Configuration
TOKEN = '8620798606:AAGwjqu2xvFwRNM6EKkZ_Eemr1J0tZN2_-g'
CHAT_ID = '838023971'
# Add as many as you want here
SYMBOLS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'ADA/USD', 'PAXG/USD', 'XAUT/USD', 'BEAT/USD', 'H/USD', 'AIO/USD', 'XRP/USD', 'LAB/USD', 'ZEC/USD', 'SKYAI/USD', 'SLVON/USD', 'DOGE/USD', 'SIREN/USD', 'BNB/USD', 'LTC/USD', 'PIPPIN/USD', 'LINK/USD', 'XMR/USD', 'AIN/USD', 'DOT/USD', '1000SATS/USD', 'PENGU/USD', 'ARC/USD', 'M/USD', 'DOGS/USD'] 
EXCHANGE = ccxt.binance()

def send_alert(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id={CHAT_ID}&text={message}"
    requests.get(url)
    
def sleep_until_next_15m():
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    wait_minutes = 15 - (now.minute % 15) - 1
    wait_seconds = 60 - now.second
    time.sleep((wait_minutes * 60) + wait_seconds)
    
def check_markets():
    for symbol in SYMBOLS:
        try:
            # Fetch 52 candles of 15m data
            ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe='15m', limit=52)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            upper_band = df['high'].max()
            lower_band = df['low'].min()
            current_price = df['close'].iloc[-1]
            
            # Check for breakout
            if current_price >= upper_band:
                send_alert(f"🚀 {symbol} BREAKOUT: Price {current_price} > 52-period High {upper_band}")
            elif current_price <= lower_band:
                send_alert(f"🐻 {symbol} BREAKOUT: Price {current_price} < 52-period Low {lower_band}")
        
        except Exception as e:
            print(f"Error checking {symbol}: {e}")

# Continuous Loop
while True:
    check_markets()
    time.sleep(900) # Wait 15 minutes
