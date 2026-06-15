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

# Delta Exchange API keys (optional but recommended)
DELTA_API_KEY = os.environ.get('DELTA_API_KEY', '')
DELTA_API_SECRET = os.environ.get('DELTA_API_SECRET', '')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT',
    'PAXG/USDT', 'XAUT/USDT', 'XRP/USDT', 'DOGE/USDT',
    'BNB/USDT', 'LTC/USDT', 'LINK/USDT', 'MATIC/USDT',
    'DOT/USDT', 'AVAX/USDT', 'UNI/USDT', 'ATOM/USDT'
]

# Initialize Delta Exchange
delta_config = {
    'apiKey': DELTA_API_KEY,
    'secret': DELTA_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
}

EXCHANGE = ccxt.delta(delta_config)

try:
    EXCHANGE.load_markets()
    print("Delta Exchange markets loaded successfully")
except Exception as e:
    print(f"Error loading Delta markets: {e}")
    print("Make sure you have internet connection and Delta Exchange is accessible")

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
    print("Exchange: Delta Exchange (India-based)")
    print("===== ALERT CONDITIONS =====")
    print("1️⃣ CHOP > 60 & Price >= HH → 🔴 SELL (Trend ending, reversal coming)")
    print("2️⃣ CHOP > 60 & Price <= LL → 🟢 BUY (Trend ending, reversal coming)")
    print("3️⃣ CHOP < 40 & Price >= HH → 🔴 SELL (Strong trend, momentum continuation)")
    print("4️⃣ CHOP < 40 & Price <= LL → 🟢 BUY (Strong trend, momentum continuation)")
    print("============================")

    # Startup message
    send_alert("✅ Bot Started on Delta Exchange (India)\n\n"
               "📊 Donchian Channel (52) + Choppiness Index (14)\n\n"
               "🔴 SELL CONDITIONS:\n"
               "• CHOP > 60 & Price ≥ HH (Trend Reversal)\n"
               "• CHOP < 40 & Price ≥ HH (Strong Trend)\n\n"
               "🟢 BUY CONDITIONS:\n"
               "• CHOP > 60 & Price ≤ LL (Trend Reversal)\n"
               "• CHOP < 40 & Price ≤ LL (Strong Trend)")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Delta
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol on Delta: {symbol}")
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

                # ============ CHOPPINESS INDEX (14) ============
                chop_value = calculate_choppiness_index(df, period=14)

                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']

                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None

                # Debug print
                print(f"{symbol} - CHOP: {chop_value}, Price: ₹{current_price:.8f}, HH: ₹{HH:.8f}, LL: ₹{LL:.8f}")

                # Skip if CHOP couldn't be calculated
                if chop_value == 50:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue

                # ==============================================
                # CONDITION 1: CHOP > 60 & Price >= HH (SELL - Trend Reversal)
                # ==============================================
                if chop_value > 60 and current_price >= HH:
                    if last_alert[symbol] != "SELL_CHOP_HIGH":
                        message = (
                            f"🔴🔴🔴 SELL ALERT - TREND REVERSAL 🔴🔴🔴\n\n"
                            f"Exchange: Delta Exchange (India)\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ₹{current_price:.8f}\n"
                            f"Donchian High (HH): ₹{HH:.8f}\n"
                            f"Choppiness Index: {chop_value} (>60)\n\n"
                            f"📊 Market Condition: CHOPPY MARKET\n"
                            f"⚠️ Trend ending, potential reversal from UP to DOWN\n"
                            f"🎯 SELL SIGNAL TRIGGERED"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 SELL SIGNAL (Reversal: CHOP>60, Price at HH)")
                        last_alert[symbol] = "SELL_CHOP_HIGH"

                # ==============================================
                # CONDITION 2: CHOP > 60 & Price <= LL (BUY - Trend Reversal)
                # ==============================================
                elif chop_value > 60 and current_price <= LL:
                    if last_alert[symbol] != "BUY_CHOP_HIGH":
                        message = (
                            f"🟢🟢🟢 BUY ALERT - TREND REVERSAL 🟢🟢🟢\n\n"
                            f"Exchange: Delta Exchange (India)\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ₹{current_price:.8f}\n"
                            f"Donchian Low (LL): ₹{LL:.8f}\n"
                            f"Choppiness Index: {chop_value} (>60)\n\n"
                            f"📊 Market Condition: CHOPPY MARKET\n"
                            f"⚠️ Trend ending, potential reversal from DOWN to UP\n"
                            f"🎯 BUY SIGNAL TRIGGERED"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BUY SIGNAL (Reversal: CHOP>60, Price at LL)")
                        last_alert[symbol] = "BUY_CHOP_HIGH"

                # ==============================================
                # CONDITION 3: CHOP < 40 & Price >= HH (SELL - Strong Trend)
                # ==============================================
                elif chop_value < 40 and current_price >= HH:
                    if last_alert[symbol] != "SELL_CHOP_LOW":
                        message = (
                            f"🔴🔴🔴 SELL ALERT - STRONG TREND CONTINUATION 🔴🔴🔴\n\n"
                            f"Exchange: Delta Exchange (India)\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ₹{current_price:.8f}\n"
                            f"Donchian High (HH): ₹{HH:.8f}\n"
                            f"Choppiness Index: {chop_value} (<40)\n\n"
                            f"📊 Market Condition: TRENDING MARKET\n"
                            f"⚠️ Strong trend detected, momentum to continue\n"
                            f"🎯 SELL SIGNAL TRIGGERED"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 SELL SIGNAL (Strong Trend: CHOP<40, Price at HH)")
                        last_alert[symbol] = "SELL_CHOP_LOW"

                # ==============================================
                # CONDITION 4: CHOP < 40 & Price <= LL (BUY - Strong Trend)
                # ==============================================
                elif chop_value < 40 and current_price <= LL:
                    if last_alert[symbol] != "BUY_CHOP_LOW":
                        message = (
                            f"🟢🟢🟢 BUY ALERT - STRONG TREND CONTINUATION 🟢🟢🟢\n\n"
                            f"Exchange: Delta Exchange (India)\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ₹{current_price:.8f}\n"
                            f"Donchian Low (LL): ₹{LL:.8f}\n"
                            f"Choppiness Index: {chop_value} (<40)\n\n"
                            f"📊 Market Condition: TRENDING MARKET\n"
                            f"⚠️ Strong trend detected, momentum to continue\n"
                            f"🎯 BUY SIGNAL TRIGGERED"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BUY SIGNAL (Strong Trend: CHOP<40, Price at LL)")
                        last_alert[symbol] = "BUY_CHOP_LOW"

                # Reset alert when conditions no longer met
                else:
                    if last_alert[symbol] is not None:
                        print(f"{symbol} - Alert reset: {last_alert[symbol]} condition ended")
                        last_alert[symbol] = None

            except Exception as e:
                print(f"Error checking {symbol} on Delta Exchange: {e}")

        # Check every 10 seconds (increased from 5 to avoid rate limits)
        time.sleep(10)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)