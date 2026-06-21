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
    return "SEB + CHOP Signal Generator is running!"

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

# Prevent repeated alerts
last_alert = {}

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

def calculate_standard_error_bands(df, period=52, std_err=2, avg_method='simple', avg_periods=3):
    """
    Calculate Standard Error Bands (Period 52, Standard Error = 2, Average Method = Simple, Average Periods = 3)
    
    Standard Error Bands are similar to Bollinger Bands but use standard error instead of standard deviation.
    
    Args:
        df: DataFrame with 'close' column
        period: Lookback period for regression (52)
        std_err: Number of standard errors to use (2)
        avg_method: Method for averaging (simple)
        avg_periods: Period for moving average of bands (3)
    """
    try:
        close = df['close']
        
        # Ensure we have enough data
        if len(close) < period + 10:
            return None, None, None
        
        # Calculate linear regression for each point
        def linear_regression(y, window=period):
            x = np.arange(window)
            x_mean = x.mean()
            y_mean = y.mean()
            
            # Calculate slope and intercept
            slope = np.sum((x - x_mean) * (y - y_mean)) / np.sum((x - x_mean) ** 2)
            intercept = y_mean - slope * x_mean
            
            # Calculate predicted values
            predicted = intercept + slope * x
            
            # Calculate standard error of the estimate
            residuals = y - predicted
            std_error = np.sqrt(np.sum(residuals ** 2) / (window - 2))
            
            return predicted[-1], std_error
        
        # Calculate rolling regression
        middle_band = []
        upper_band = []
        lower_band = []
        
        for i in range(period - 1, len(close)):
            y = close.iloc[i - period + 1:i + 1].values
            if len(y) == period:
                predicted, std_error = linear_regression(y, period)
                middle_band.append(predicted)
                upper_band.append(predicted + std_err * std_error)
                lower_band.append(predicted - std_err * std_error)
            else:
                middle_band.append(None)
                upper_band.append(None)
                lower_band.append(None)
        
        # Convert to Series with proper indexing
        middle_series = pd.Series(middle_band, index=close.index[period-1:])
        upper_series = pd.Series(upper_band, index=close.index[period-1:])
        lower_series = pd.Series(lower_band, index=close.index[period-1:])
        
        # Apply simple moving average if avg_periods > 1
        if avg_periods > 1 and avg_method.lower() == 'simple':
            middle_series = middle_series.rolling(window=avg_periods).mean()
            upper_series = upper_series.rolling(window=avg_periods).mean()
            lower_series = lower_series.rolling(window=avg_periods).mean()
        
        # Get the latest values
        current_middle = middle_series.iloc[-1]
        current_upper = upper_series.iloc[-1]
        current_lower = lower_series.iloc[-1]
        
        # Previous values for cross detection
        prev_middle = middle_series.iloc[-2] if len(middle_series) >= 2 else None
        prev_upper = upper_series.iloc[-2] if len(upper_series) >= 2 else None
        prev_lower = lower_series.iloc[-2] if len(lower_series) >= 2 else None
        
        # Get previous close price
        prev_close = close.iloc[-2] if len(close) >= 2 else None
        
        return {
            'current': {
                'upper': round(current_upper, 4) if current_upper is not None else None,
                'middle': round(current_middle, 4) if current_middle is not None else None,
                'lower': round(current_lower, 4) if current_lower is not None else None
            },
            'previous': {
                'upper': round(prev_upper, 4) if prev_upper is not None else None,
                'middle': round(prev_middle, 4) if prev_middle is not None else None,
                'lower': round(prev_lower, 4) if prev_lower is not None else None
            },
            'prev_close': prev_close
        }
    except Exception as e:
        print(f"Standard Error Bands calculation error: {e}")
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
    print("STANDARD ERROR BANDS + CHOPPINESS INDEX STRATEGY")
    print("="*50)
    print("\n📊 INDICATORS:")
    print("  • Choppiness Index (Period 21)")
    print("  • Standard Error Bands (Period 52, StdErr=2, Avg Method=SMA, Avg Periods=3)")
    print("\n📈 TRADING SIGNALS:")
    print("  🔴 SELL:")
    print("    Condition A: CHOP 40-60 + Price crosses UPPER band from outside to inside")
    print("    Condition B: CHOP 40-50 + Price crosses MIDDLE band from above to below")
    print("  🟢 BUY:")
    print("    Condition A: CHOP 40-60 + Price crosses LOWER band from outside to inside")
    print("    Condition B: CHOP 40-50 + Price crosses MIDDLE band from below to above")
    print("\n⏱️ CHECKING EVERY 2 MINUTES")
    print("="*50 + "\n")
    
    # Startup message
    send_alert(f"✅ SEB + CHOP Signal Generator Started\n\n"
               f"📊 Strategy: Standard Error Bands + Choppiness Index\n"
               f"🔍 Monitoring: {len(SYMBOLS)} trading pairs\n"
               f"⏱️ Check Frequency: Every 2 minutes\n"
               f"📊 Timeframe: 10-minute candles\n\n"
               f"📈 Signals Generated on Price Crossings")
    
    # Get available symbols
    available_symbols = get_available_symbols(EXCHANGE, SYMBOLS)
    print(f"✅ Available symbols: {len(available_symbols)}")
    
    while True:
        for symbol in available_symbols:
            try:
                # Get current and previous live price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # Get OHLCV data (need enough for SEB calculation)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='10m',  # Changed from 5m to 10m
                    limit=100  # Enough for 52-period SEB
                )
                
                if len(ohlcv) < 80:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # Calculate indicators
                chop_value = calculate_choppiness_index(df, period=21)
                seb_values = calculate_standard_error_bands(
                    df, 
                    period=52, 
                    std_err=2, 
                    avg_method='simple', 
                    avg_periods=3
                )
                
                # Skip if indicators couldn't be calculated
                if chop_value is None or seb_values is None:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue
                
                # Extract SEB values
                seb_current = seb_values['current']
                seb_previous = seb_values['previous']
                prev_close = seb_values['prev_close']
                
                if None in [seb_current['upper'], seb_current['middle'], seb_current['lower']]:
                    print(f"  → Skipping {symbol} - SEB values incomplete")
                    continue
                
                # Get previous close price (from OHLCV)
                prev_price = df['close'].iloc[-2] if len(df) >= 2 else None
                
                if prev_price is None:
                    print(f"  → Skipping {symbol} - no previous price")
                    continue
                
                # Format current price
                price_str = f"${current_price:.4f}" if current_price < 1000 else f"${current_price:.2f}"
                price_str = f"${current_price:.4f}" if current_price < 100 else price_str
                
                # Debug output
                print(f"{symbol} - Price: {price_str}, CHOP21: {chop_value}, "
                      f"SEB_U: {seb_current['upper']:.4f}, SEB_M: {seb_current['middle']:.4f}, SEB_L: {seb_current['lower']:.4f}")
                
                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None
                
                # ==============================================
                # CROSS DETECTION BOOLEANS
                # ==============================================
                
                # SELL Condition A: Previous price was ABOVE previous Upper SEB, current price is INSIDE Upper SEB
                upper_cross_outside_to_inside = (
                    prev_price > seb_previous['upper'] and 
                    current_price <= seb_current['upper']
                )
                
                # SELL Condition B: Previous price was ABOVE previous Middle SEB, current price is BELOW current Middle SEB
                middle_cross_above_to_below = (
                    prev_price > seb_previous['middle'] and 
                    current_price < seb_current['middle']
                )
                
                # BUY Condition A: Previous price was BELOW previous Lower SEB, current price is INSIDE Lower SEB
                lower_cross_outside_to_inside = (
                    prev_price < seb_previous['lower'] and 
                    current_price >= seb_current['lower']
                )
                
                # BUY Condition B: Previous price was BELOW previous Middle SEB, current price is ABOVE current Middle SEB
                middle_cross_below_to_above = (
                    prev_price < seb_previous['middle'] and 
                    current_price > seb_current['middle']
                )
                
                # ==============================================
                # SIGNAL EVALUATION - ALL INDEPENDENT
                # ==============================================
                
                # SELL Condition A: CHOP 40-60 + Price crossed UPPER band from outside to inside
                sell_a = (
                    40 < chop_value < 60 and
                    upper_cross_outside_to_inside
                )
                
                # SELL Condition B: CHOP 40-50 + Price crossed MIDDLE band from above to below
                sell_b = (
                    40 < chop_value < 50 and
                    middle_cross_above_to_below
                )
                
                # BUY Condition A: CHOP 40-60 + Price crossed LOWER band from outside to inside
                buy_a = (
                    40 < chop_value < 60 and
                    lower_cross_outside_to_inside
                )
                
                # BUY Condition B: CHOP 40-50 + Price crossed MIDDLE band from below to above
                buy_b = (
                    40 < chop_value < 50 and
                    middle_cross_below_to_above
                )
                
                # ==============================================
                # SELL SIGNAL - CONDITION A
                # ==============================================
                if sell_a and last_alert[symbol] != "SELL_A":
                    message = (
                        f"🔴🔴🔴 SELL SIGNAL 🔴🔴🔴\n\n"
                        f"Symbol: {symbol}\n"
                        f"Price: {price_str}\n"
                        f"CHOP21: {chop_value:.2f} (40-60 Range)\n"
                        f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                        f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                        f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                        f"Reason: Price crossed UPPER SEB from outside to inside\n"
                        f"Market is ranging (CHOP 40-60)\n"
                        f"📊 Timeframe: 10m"
                    )
                    send_alert(message)
                    print(f"{symbol} - 🔴 SELL SIGNAL (Condition A)")
                    last_alert[symbol] = "SELL_A"
                
                # ==============================================
                # SELL SIGNAL - CONDITION B
                # ==============================================
                elif sell_b and last_alert[symbol] != "SELL_B":
                    message = (
                        f"🔴🔴🔴 SELL SIGNAL 🔴🔴🔴\n\n"
                        f"Symbol: {symbol}\n"
                        f"Price: {price_str}\n"
                        f"CHOP21: {chop_value:.2f} (40-50 Range)\n"
                        f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                        f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                        f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                        f"Reason: Price crossed MIDDLE SEB from above to below\n"
                        f"Market is moderately ranging (CHOP 40-50)\n"
                        f"📊 Timeframe: 10m"
                    )
                    send_alert(message)
                    print(f"{symbol} - 🔴 SELL SIGNAL (Condition B)")
                    last_alert[symbol] = "SELL_B"
                
                # ==============================================
                # BUY SIGNAL - CONDITION A
                # ==============================================
                elif buy_a and last_alert[symbol] != "BUY_A":
                    message = (
                        f"🟢🟢🟢 BUY SIGNAL 🟢🟢🟢\n\n"
                        f"Symbol: {symbol}\n"
                        f"Price: {price_str}\n"
                        f"CHOP21: {chop_value:.2f} (40-60 Range)\n"
                        f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                        f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                        f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                        f"Reason: Price crossed LOWER SEB from outside to inside\n"
                        f"Market is ranging (CHOP 40-60)\n"
                        f"📊 Timeframe: 10m"
                    )
                    send_alert(message)
                    print(f"{symbol} - 🟢 BUY SIGNAL (Condition A)")
                    last_alert[symbol] = "BUY_A"
                
                # ==============================================
                # BUY SIGNAL - CONDITION B
                # ==============================================
                elif buy_b and last_alert[symbol] != "BUY_B":
                    message = (
                        f"🟢🟢🟢 BUY SIGNAL 🟢🟢🟢\n\n"
                        f"Symbol: {symbol}\n"
                        f"Price: {price_str}\n"
                        f"CHOP21: {chop_value:.2f} (40-50 Range)\n"
                        f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                        f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                        f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                        f"Reason: Price crossed MIDDLE SEB from below to above\n"
                        f"Market is moderately ranging (CHOP 40-50)\n"
                        f"📊 Timeframe: 10m"
                    )
                    send_alert(message)
                    print(f"{symbol} - 🟢 BUY SIGNAL (Condition B)")
                    last_alert[symbol] = "BUY_B"
                
                # Reset alerts if no conditions met
                else:
                    # Check if any condition is still true
                    any_condition_true = sell_a or sell_b or buy_a or buy_b
                    
                    if not any_condition_true and last_alert[symbol] is not None:
                        # Only reset if all conditions are false
                        print(f"{symbol} - Alert reset: {last_alert[symbol]} condition ended (CHOP: {chop_value:.2f})")
                        last_alert[symbol] = None
                
            except Exception as e:
                print(f"Error checking {symbol}: {e}")
        
        # Check every 30 seconds
        time.sleep(20)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
