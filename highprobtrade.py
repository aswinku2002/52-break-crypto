import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime

# 1. Setup Flask for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "SuperTrend + CHOP Signal Generator is running!"

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Exchange configuration
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'binance').lower()

# Exchange API keys
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Trading pairs to monitor
SYMBOLS = [
    # Major Cryptocurrencies
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT',
    'DOGE/USDT', 'BNB/USDT', 'LTC/USDT', 'LINK/USDT',
    'AVAX/USDT', 'ADA/USDT', 'SUI/USDT', 'TRX/USDT',
    'BCH/USDT', 'AAVE/USDT', 'ETC/USDT', 'NEAR/USDT',
    'ORDI/USDT', 'WLD/USDT', 'HYPE/USDT', 'XLM/USDT',

    # Metal Tokens
    'XAUT/USDT', 'PAXG/USDT',

    # Additional Altcoins
    'UNI/USDT', 'ZEC/USDT', 'ENJ/USDT', 'XMR/USDT',
    'AXS/USDT', 'JTO/USDT', 'IO/USDT', 'ALT/USDT',

    # New/Recent Tokens
    'ACT/USDT', 'EVA/USDT', 'SLVON/USDT', 'EDEN/USDT',
    'SKYAI/USDT', 'EIGEN/USDT', 'SIREN/USDT', 'VVV/USDT',
    'WCT/USDT', 'SPCXX/USDT', 'AIO/USDT', 'SWARMS/USDT',
    'ALLO/USDT', 'RIVER/USDT', 'PIPPIN/USDT', 'BILL/USDT',
    'M/USDT', 'XPL/USDT', 'COAI/USDT', 'QQQX/USDT',
    'RAVE/USDT', 'BASED/USDT', 'BLESS/USDT', 'VELVET/USDT',
    'LAB/USDT', 'BEAT/USDT', 'H/USDT'
]

def init_exchange(exchange_name, config):
    """Initialize exchange with error handling"""
    try:
        if exchange_name == 'binance':
            exchange = ccxt.binance(config)
        else:
            return None

        exchange.load_markets()
        print(f"✅ {exchange_name.capitalize()} markets loaded successfully")
        return exchange
    except Exception as e:
        print(f"❌ Error loading {exchange_name.capitalize()} markets: {e}")
        return None

# Initialize Binance
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
}
EXCHANGE = init_exchange('binance', binance_config)

if not EXCHANGE:
    print("❌ No exchanges available. Please check your configuration.")
    exit(1)

print(f"✅ Using {EXCHANGE.name.capitalize()} as primary exchange")

# Prevent repeated alerts - store last signal state per symbol
last_signal_state = {}  # Stores 'BUY' or 'SELL' or None per symbol

def calculate_choppiness_index(df, period=21):
    """
    Calculate Choppiness Index (Period 21)
    
    The Choppiness Index measures whether the market is trending (low values)
    or ranging/choppy (high values).
    
    Formula: CI = 100 * log10(SUM(TR, n) / (MAX(HIGH, n) - MIN(LOW, n))) / log10(n)
    """
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
            return None
        return round(result, 2)
    except Exception as e:
        print(f"Choppiness calculation error: {e}")
        return None

def calculate_supertrend(df, period=10, multiplier=3):
    """
    Calculate TradingView-compatible SuperTrend indicator
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ATR period (10)
        multiplier: ATR multiplier (3)
    
    Returns:
        DataFrame with 'supertrend' and 'trend' columns
        trend: 1 for Bullish (green), -1 for Bearish (red)
    """
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate ATR (Average True Range)
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        # Calculate Basic Upper and Lower Bands
        hl2 = (high + low) / 2
        basic_upper_band = hl2 + (multiplier * atr)
        basic_lower_band = hl2 - (multiplier * atr)
        
        # Initialize final bands and SuperTrend
        final_upper_band = pd.Series(index=df.index, dtype=float)
        final_lower_band = pd.Series(index=df.index, dtype=float)
        supertrend = pd.Series(index=df.index, dtype=float)
        trend = pd.Series(index=df.index, dtype=int)
        
        # First value
        final_upper_band.iloc[0] = basic_upper_band.iloc[0]
        final_lower_band.iloc[0] = basic_lower_band.iloc[0]
        supertrend.iloc[0] = 0
        trend.iloc[0] = 1  # Start with bullish
        
        for i in range(1, len(df)):
            # Previous values
            prev_close = close.iloc[i-1]
            prev_final_upper = final_upper_band.iloc[i-1]
            prev_final_lower = final_lower_band.iloc[i-1]
            prev_trend = trend.iloc[i-1]
            
            # Current values
            current_close = close.iloc[i]
            current_basic_upper = basic_upper_band.iloc[i]
            current_basic_lower = basic_lower_band.iloc[i]
            
            # Calculate Final Upper Band
            if prev_trend == 1:  # Previously bullish
                if prev_final_upper < current_basic_upper:
                    final_upper_band.iloc[i] = current_basic_upper
                else:
                    final_upper_band.iloc[i] = prev_final_upper
            else:  # Previously bearish
                final_upper_band.iloc[i] = current_basic_upper
            
            # Calculate Final Lower Band
            if prev_trend == -1:  # Previously bearish
                if prev_final_lower > current_basic_lower:
                    final_lower_band.iloc[i] = current_basic_lower
                else:
                    final_lower_band.iloc[i] = prev_final_lower
            else:  # Previously bullish
                final_lower_band.iloc[i] = current_basic_lower
            
            # Determine SuperTrend value and trend
            current_final_upper = final_upper_band.iloc[i]
            current_final_lower = final_lower_band.iloc[i]
            
            if prev_trend == 1:  # Previously bullish
                if current_close <= current_final_upper:
                    # Flip to bearish
                    trend.iloc[i] = -1
                    supertrend.iloc[i] = current_final_upper
                else:
                    trend.iloc[i] = 1
                    if current_final_lower > prev_final_lower:
                        supertrend.iloc[i] = current_final_lower
                    else:
                        supertrend.iloc[i] = prev_final_lower
            else:  # Previously bearish
                if current_close >= current_final_lower:
                    # Flip to bullish
                    trend.iloc[i] = 1
                    supertrend.iloc[i] = current_final_lower
                else:
                    trend.iloc[i] = -1
                    if current_final_upper < prev_final_upper:
                        supertrend.iloc[i] = current_final_upper
                    else:
                        supertrend.iloc[i] = prev_final_upper
        
        return {
            'supertrend': supertrend,
            'trend': trend
        }
    except Exception as e:
        print(f"SuperTrend calculation error: {e}")
        return None

def send_alert(message):
    """Send alert via Telegram"""
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

def get_available_symbols(exchange, symbols):
    """Filter symbols to only those available on the exchange"""
    available = []
    for symbol in symbols:
        if symbol in exchange.markets:
            available.append(symbol)
    return available

def run_bot():
    print("Bot loop started...")
    print(f"Exchange: {EXCHANGE.name.capitalize()}")
    print("\n" + "="*50)
    print("SUPERTREND + CHOPPINESS INDEX STRATEGY")
    print("="*50)
    print("\n📊 INDICATORS:")
    print("  • SuperTrend (Period 10, Multiplier 3)")
    print("  • Choppiness Index (Period 21)")
    print("\n📈 TRADING SIGNALS:")
    print("  🟢 BUY Signal:")
    print("    - CHOP21 < 49")
    print("    - SuperTrend flips from Bearish (-1) to Bullish (+1)")
    print("  🔴 SELL Signal:")
    print("    - CHOP21 < 49")
    print("    - SuperTrend flips from Bullish (+1) to Bearish (-1)")
    print("\n⏱️ CHECKING AFTER EACH CANDLE CLOSE (5-minute)")
    print("="*50 + "\n")

    # Startup message
    send_alert(f"✅ SuperTrend + CHOP Signal Generator Started\n\n"
               f"📊 Strategy: SuperTrend(10,3) + CHOP21\n"
               f"🔍 Monitoring: {len(SYMBOLS)} trading pairs\n"
               f"⏱️ Check Frequency: Every 30 seconds\n\n"
               f"📈 Signals Generated on SuperTrend Trend Flips")

    # Get available symbols
    available_symbols = get_available_symbols(EXCHANGE, SYMBOLS)
    print(f"✅ Available symbols: {len(available_symbols)}")

    while True:
        for symbol in available_symbols:
            try:
                # Get OHLCV data (need enough candles for calculations)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='5m',
                    limit=100  # Enough for calculations
                )

                if len(ohlcv) < 20:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # USE ONLY CLOSED CANDLES
                # Current closed candle (most recent)
                current_candle = df.iloc[-2]  # Second most recent (most recently completed)
                # Previous closed candle
                previous_candle = df.iloc[-3]  # Third most recent (previous completed)
                
                # Ensure we have enough candles
                if len(df) < 3:
                    print(f"  → Skipping {symbol} - not enough candles")
                    continue

                # Calculate indicators
                chop_value = calculate_choppiness_index(df, period=21)
                supertrend_data = calculate_supertrend(df, period=10, multiplier=3)

                # Skip if indicators couldn't be calculated
                if chop_value is None or supertrend_data is None:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue

                # Extract SuperTrend trend values
                trend_series = supertrend_data['trend']
                supertrend_series = supertrend_data['supertrend']

                # Get current and previous trend values (from closed candles)
                current_trend = trend_series.iloc[-2]  # Second most recent
                previous_trend = trend_series.iloc[-3]  # Third most recent
                
                # Get current SuperTrend value
                current_supertrend = supertrend_series.iloc[-2]  # Second most recent

                if pd.isna(previous_trend) or pd.isna(current_trend):
                    print(f"  → Skipping {symbol} - trend values incomplete")
                    continue

                # Current closed candle price
                current_price = current_candle['close']

                # Format current price
                price_str = f"${current_price:.4f}" if current_price < 1000 else f"${current_price:.2f}"
                price_str = f"${current_price:.4f}" if current_price < 100 else price_str

                # Get current time
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Debug logging
                print(f"{symbol} | CHOP={chop_value:.2f} | PrevTrend={previous_trend} | CurrTrend={current_trend}")
                
                # Check for trend flip
                if previous_trend != current_trend:
                    print(f"FLIP DETECTED: {symbol} {previous_trend} -> {current_trend} | CHOP={chop_value:.2f}")

                # Initialize last signal state for this symbol
                if symbol not in last_signal_state:
                    last_signal_state[symbol] = None

                # Check for SuperTrend trend flip
                # BUY SIGNAL: Bearish (-1) to Bullish (+1) flip
                # SELL SIGNAL: Bullish (+1) to Bearish (-1) flip

                # Condition: CHOP21 < 49
                if chop_value < 49:
                    # Check for BUY signal (Bearish to Bullish flip)
                    if previous_trend == -1 and current_trend == 1:
                        # Only send alert if not already in BUY state
                        if last_signal_state[symbol] != "BUY":
                            message = (
                                f"🟢 BUY SIGNAL\n\n"
                                f"Symbol: {symbol}\n"
                                f"Price: {price_str}\n"
                                f"CHOP21: {chop_value:.2f}\n"
                                f"SuperTrend Flip: RED → GREEN\n"
                                f"Time: {current_time}"
                            )
                            send_alert(message)
                            print(f"{symbol} - 🟢 BUY SIGNAL (SuperTrend flip from -1 to +1)")
                            last_signal_state[symbol] = "BUY"

                    # Check for SELL signal (Bullish to Bearish flip)
                    elif previous_trend == 1 and current_trend == -1:
                        # Only send alert if not already in SELL state
                        if last_signal_state[symbol] != "SELL":
                            message = (
                                f"🔴 SELL SIGNAL\n\n"
                                f"Symbol: {symbol}\n"
                                f"Price: {price_str}\n"
                                f"CHOP21: {chop_value:.2f}\n"
                                f"SuperTrend Flip: GREEN → RED\n"
                                f"Time: {current_time}"
                            )
                            send_alert(message)
                            print(f"{symbol} - 🔴 SELL SIGNAL (SuperTrend flip from +1 to -1)")
                            last_signal_state[symbol] = "SELL"
                    else:
                        # No trend flip, keep existing state
                        pass
                else:
                    # CHOP21 >= 49, market is ranging, reset state
                    if last_signal_state[symbol] is not None:
                        print(f"{symbol} - Alert reset: {last_signal_state[symbol]} condition ended (CHOP: {chop_value:.2f})")
                        last_signal_state[symbol] = None

            except Exception as e:
                print(f"Error checking {symbol}: {e}")
                # Don't reset state on error, to avoid missing signals
                # But log the error for debugging

        # Check every 30 seconds
        time.sleep(30)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)