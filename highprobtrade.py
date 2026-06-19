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

# Binance API keys (optional but recommended for higher rate limits)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT',
    'PAXG/USDT', 'XAUT/USDT', 'XRP/USDT', 'DOGE/USDT',
    'BNB/USDT', 'LTC/USDT', 'LINK/USDT', 'MATIC/USDT',
    'DOT/USDT', 'AVAX/USDT', 'UNI/USDT', 'ATOM/USDT'
]

# Initialize Binance
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',  # spot trading
    }
}

EXCHANGE = ccxt.binance(binance_config)

try:
    EXCHANGE.load_markets()
    print("Binance markets loaded successfully")
    print(f"Loaded {len(EXCHANGE.markets)} trading pairs")
except Exception as e:
    print(f"Error loading Binance markets: {e}")
    print("Make sure you have internet connection and Binance is accessible")

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

def calculate_atr(df, period=14):
    """Calculate Average True Range (ATR)"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']

        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Calculate ATR using exponential moving average
        atr = tr.ewm(span=period, adjust=False).mean()

        result = atr.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return None
        return round(result, 2)

    except Exception as e:
        print(f"ATR calculation error: {e}")
        return None

def calculate_rsi(df, period=14):
    """Calculate RSI (Relative Strength Index)"""
    try:
        close = df['close']
        delta = close.diff()
        
        # Separate gains and losses
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        # Calculate RS and RSI
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        result = rsi.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
        
    except Exception as e:
        print(f"RSI calculation error: {e}")
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

def calculate_channel_percentile(HH, LL, current_price):
    """Calculate where price sits in the channel (0% = LL, 100% = HH)"""
    if HH == LL:
        return 50
    percentile = ((current_price - LL) / (HH - LL)) * 100
    return round(percentile, 2)

def run_bot():
    print("Bot loop started...")
    print("Exchange: Binance (Global)")
    print("===== ALERT CONDITIONS =====")
    print("1️⃣ CHOP > 60 & Price in TOP 5% of channel → SELL REVERSAL (Mean-reversion)")
    print("2️⃣ CHOP > 60 & Price in BOTTOM 5% of channel → BUY REVERSAL (Mean-reversion)")
    print("3️⃣ CHOP < 40 & Price in TOP 5% of channel & RSI > 55 → BUY TREND CONTINUATION")
    print("4️⃣ CHOP < 40 & Price in BOTTOM 5% of channel & RSI < 45 → SELL TREND CONTINUATION")
    print("============================")

    # Startup message
    send_alert("✅ Bot Started on Binance\n\n"
               "📊 Donchian Channel (52) + Choppiness Index (14) + RSI (14)\n"
               "🎯 Alert Zone: Top 5% / Bottom 5% of Channel\n\n"
               "🔴 SELL REVERSAL:\n"
               "• CHOP > 60 + Top 5%\n"
               "• SL: ATR × 2 | TP: ATR × 1.5\n\n"
               "🟢 BUY REVERSAL:\n"
               "• CHOP > 60 + Bottom 5%\n"
               "• SL: ATR × 2 | TP: ATR × 1.5\n\n"
               "🟢 BUY TREND:\n"
               "• CHOP < 40 + Top 5% + RSI > 55\n"
               "• SL: ATR × 2 | TP: ATR × 3\n\n"
               "🔴 SELL TREND:\n"
               "• CHOP < 40 + Bottom 5% + RSI < 45\n"
               "• SL: ATR × 2 | TP: ATR × 3")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol on Binance: {symbol}")
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
                channel_range = HH - LL

                # ============ CHOPPINESS INDEX (14) ============
                chop_value = calculate_choppiness_index(df, period=14)

                # ============ ATR (14) ============
                atr_value = calculate_atr(df, period=14)

                # ============ RSI (14) ============
                rsi_value = calculate_rsi(df, period=14)

                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']

                # Calculate position in channel
                channel_percentile = calculate_channel_percentile(HH, LL, current_price)

                # Determine if price is in alert zones
                is_top_zone = channel_percentile >= 95  # Top 5% of channel
                is_bottom_zone = channel_percentile <= 5  # Bottom 5% of channel

                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None

                # Debug print with all indicators
                print(f"{symbol} - Price: ${current_price:.2f}, RSI: {rsi_value}, CHOP: {chop_value}, "
                      f"ATR: ${atr_value:.2f}, Channel%: {channel_percentile}%, "
                      f"Top Zone: {is_top_zone}, Bottom Zone: {is_bottom_zone}")

                # Skip if indicators couldn't be calculated
                if chop_value == 50 or atr_value is None or rsi_value == 50:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue

                # ==============================================
                # CONDITION A: CHOP > 60 & Price in TOP 5% (SELL REVERSAL)
                # ==============================================
                if chop_value > 60 and is_top_zone:
                    if last_alert[symbol] != "SELL_REVERSAL":
                        distance_to_hh = ((HH - current_price) / HH) * 100

                        # Calculate Stop Loss and Take Profit for SELL REVERSAL
                        stop_loss = current_price + (atr_value * 2)
                        take_profit = current_price - (atr_value * 1.5)

                        message = (
                            f"🔴🔴🔴 SELL REVERSAL 🔴🔴🔴\n\n"
                            f"Exchange: Binance\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value}\n"
                            f"Choppiness Index: {chop_value} (>60)\n"
                            f"Channel Position: {channel_percentile}% (Top 5% Zone)\n"
                            f"ATR: ${atr_value:.2f}\n\n"
                            f"📊 Market Condition: RANGING/CHOPPY MARKET\n"
                            f"⚠️ Price in top 5% of channel in choppy market\n"
                            f"🎯 SELL SIGNAL: Mean-reversion expected\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.2f} (ATR×2 above entry)\n"
                            f"💰 Take Profit: ${take_profit:.2f} (ATR×1.5 below entry)\n"
                            f"📈 Risk/Reward: ~1:0.75"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 SELL REVERSAL (CHOP>60, Top 5% at {channel_percentile}%, RSI: {rsi_value})")
                        last_alert[symbol] = "SELL_REVERSAL"

                # ==============================================
                # CONDITION B: CHOP > 60 & Price in BOTTOM 5% (BUY REVERSAL)
                # ==============================================
                elif chop_value > 60 and is_bottom_zone:
                    if last_alert[symbol] != "BUY_REVERSAL":
                        distance_to_ll = ((current_price - LL) / LL) * 100

                        # Calculate Stop Loss and Take Profit for BUY REVERSAL
                        stop_loss = current_price - (atr_value * 2)
                        take_profit = current_price + (atr_value * 1.5)

                        message = (
                            f"🟢🟢🟢 BUY REVERSAL 🟢🟢🟢\n\n"
                            f"Exchange: Binance\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value}\n"
                            f"Choppiness Index: {chop_value} (>60)\n"
                            f"Channel Position: {channel_percentile}% (Bottom 5% Zone)\n"
                            f"ATR: ${atr_value:.2f}\n\n"
                            f"📊 Market Condition: RANGING/CHOPPY MARKET\n"
                            f"⚠️ Price in bottom 5% of channel in choppy market\n"
                            f"🎯 BUY SIGNAL: Mean-reversion expected\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.2f} (ATR×2 below entry)\n"
                            f"💰 Take Profit: ${take_profit:.2f} (ATR×1.5 above entry)\n"
                            f"📈 Risk/Reward: ~1:0.75"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BUY REVERSAL (CHOP>60, Bottom 5% at {channel_percentile}%, RSI: {rsi_value})")
                        last_alert[symbol] = "BUY_REVERSAL"

                # ==============================================
                # CONDITION C: CHOP < 40 & Price in TOP 5% & RSI > 55 (BUY TREND CONTINUATION)
                # ==============================================
                elif chop_value < 40 and is_top_zone and rsi_value > 55:
                    if last_alert[symbol] != "BUY_TREND":
                        distance_to_hh = ((HH - current_price) / HH) * 100

                        # Calculate Stop Loss and Take Profit for BUY TREND
                        stop_loss = current_price - (atr_value * 2)
                        take_profit = current_price + (atr_value * 3)

                        message = (
                            f"🟢🟢🟢 BUY TREND CONTINUATION 🟢🟢🟢\n\n"
                            f"Exchange: Binance\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value} (>55)\n"
                            f"Choppiness Index: {chop_value} (<40)\n"
                            f"Channel Position: {channel_percentile}% (Top 5% Zone)\n"
                            f"ATR: ${atr_value:.2f}\n\n"
                            f"📊 Market Condition: STRONG TRENDING MARKET\n"
                            f"⚠️ Strong uptrend detected, momentum expected to continue\n"
                            f"🎯 BUY SIGNAL: Trend continuation\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.2f} (ATR×2 below entry)\n"
                            f"💰 Take Profit: ${take_profit:.2f} (ATR×3 above entry)\n"
                            f"📈 Risk/Reward: ~1:1.5"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BUY TREND (CHOP<40, Top 5% at {channel_percentile}%, RSI: {rsi_value})")
                        last_alert[symbol] = "BUY_TREND"

                # ==============================================
                # CONDITION D: CHOP < 40 & Price in BOTTOM 5% & RSI < 45 (SELL TREND CONTINUATION)
                # ==============================================
                elif chop_value < 40 and is_bottom_zone and rsi_value < 45:
                    if last_alert[symbol] != "SELL_TREND":
                        distance_to_ll = ((current_price - LL) / LL) * 100

                        # Calculate Stop Loss and Take Profit for SELL TREND
                        stop_loss = current_price + (atr_value * 2)
                        take_profit = current_price - (atr_value * 3)

                        message = (
                            f"🔴🔴🔴 SELL TREND CONTINUATION 🔴🔴🔴\n\n"
                            f"Exchange: Binance\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value} (<45)\n"
                            f"Choppiness Index: {chop_value} (<40)\n"
                            f"Channel Position: {channel_percentile}% (Bottom 5% Zone)\n"
                            f"ATR: ${atr_value:.2f}\n\n"
                            f"📊 Market Condition: STRONG TRENDING MARKET\n"
                            f"⚠️ Strong downtrend detected, momentum expected to continue\n"
                            f"🎯 SELL SIGNAL: Trend continuation\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.2f} (ATR×2 above entry)\n"
                            f"💰 Take Profit: ${take_profit:.2f} (ATR×3 below entry)\n"
                            f"📈 Risk/Reward: ~1:1.5"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 SELL TREND (CHOP<40, Bottom 5% at {channel_percentile}%, RSI: {rsi_value})")
                        last_alert[symbol] = "SELL_TREND"

                # Reset alert when conditions no longer met
                else:
                    if last_alert[symbol] is not None:
                        print(f"{symbol} - Alert reset: {last_alert[symbol]} condition ended")
                        last_alert[symbol] = None

            except Exception as e:
                print(f"Error checking {symbol} on Binance: {e}")

        # Check every 20 seconds
        time.sleep(20)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)