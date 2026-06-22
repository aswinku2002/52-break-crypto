import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime, timedelta
from collections import deque
import traceback

# 1. Setup Flask for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "SuperTrend + CHOP Signal Generator is running!"

@app.route('/health')
def health():
    return {
        "status": "ok",
        "last_check": last_check_time,
        "active_signals": sum(1 for v in last_signal_state.values() if v is not None),
        "signals": {k: v for k, v in last_signal_state.items() if v is not None}
    }

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Exchange configuration
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'binance').lower()

# Exchange API keys
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Rate limiting configuration
API_CALL_INTERVAL = 2  # Seconds between API calls for different symbols
CHECK_INTERVAL = 30    # Seconds between full scan cycles

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

# Global variables
last_check_time = "Never"
error_count = 0
MAX_ERRORS_BEFORE_RESTART = 10
cycle_count = 0

def init_exchange(exchange_name, config):
    """Initialize exchange with error handling and retry"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if exchange_name == 'binance':
                exchange = ccxt.binance(config)
            else:
                return None

            exchange.load_markets()
            print(f"✅ {exchange_name.capitalize()} markets loaded successfully")
            return exchange
        except Exception as e:
            print(f"❌ Attempt {attempt+1}/{max_retries}: Error loading {exchange_name.capitalize()} markets: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)

    return None

# Initialize Binance
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'rateLimit': 1200,
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
signal_history = {}  # Store signal history for better pattern detection

def calculate_choppiness_index(df, period=21):
    """
    Calculate Choppiness Index (Period 21)
    
    The Choppiness Index measures whether the market is trending (low values)
    or ranging/choppy (high values).
    Values below 38.2 indicate strong trends, above 61.8 indicate choppy markets.
    
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

        return choppiness
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
        Dictionary with 'supertrend' and 'trend' DataFrames
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

        # First value initialization
        final_upper_band.iloc[0] = basic_upper_band.iloc[0]
        final_lower_band.iloc[0] = basic_lower_band.iloc[0]
        supertrend.iloc[0] = final_lower_band.iloc[0]
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
            if current_basic_upper < prev_final_upper or prev_close > prev_final_upper:
                final_upper_band.iloc[i] = current_basic_upper
            else:
                final_upper_band.iloc[i] = prev_final_upper

            # Calculate Final Lower Band
            if current_basic_lower > prev_final_lower or prev_close < prev_final_lower:
                final_lower_band.iloc[i] = current_basic_lower
            else:
                final_lower_band.iloc[i] = prev_final_lower

            # Determine trend and SuperTrend value
            if current_close <= final_upper_band.iloc[i] and prev_trend == 1:
                # Flip to bearish
                trend.iloc[i] = -1
                supertrend.iloc[i] = final_upper_band.iloc[i]
            elif current_close >= final_lower_band.iloc[i] and prev_trend == -1:
                # Flip to bullish
                trend.iloc[i] = 1
                supertrend.iloc[i] = final_lower_band.iloc[i]
            elif prev_trend == 1:
                # Continue bullish
                trend.iloc[i] = 1
                supertrend.iloc[i] = final_lower_band.iloc[i]
            else:
                # Continue bearish
                trend.iloc[i] = -1
                supertrend.iloc[i] = final_upper_band.iloc[i]

        return {
            'supertrend': supertrend,
            'trend': trend
        }
    except Exception as e:
        print(f"SuperTrend calculation error: {e}")
        return None

def send_alert(message):
    """Send alert via Telegram with retry logic"""
    if not TOKEN or not CHAT_ID:
        return False
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={
                    "chat_id": CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML"
                },
                timeout=10
            )
            if response.status_code == 200:
                return True
            else:
                print(f"Telegram API error: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"Telegram error (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return False

def get_available_symbols(exchange, symbols):
    """Filter symbols to only those available on the exchange"""
    available = []
    for symbol in symbols:
        if symbol in exchange.markets:
            # Additional check: ensure the market is active
            market = exchange.markets[symbol]
            if market.get('active', True):
                available.append(symbol)
        else:
            print(f"⚠️ Symbol {symbol} not available on {exchange.name}")
    return available

def format_price(price):
    """Format price with appropriate decimal places"""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.8f}"

def check_signal_pattern_anytime(symbol, df, supertrend_data, chop_series):
    """
    SUPER COMPREHENSIVE SIGNAL DETECTION - ANYTIME PATTERN
    
    Sends alerts for pattern formations regardless of when crossing occurred.
    Will alert as long as the pattern exists in the current snapshot of 3 candles.
    
    Position definition:
    - "TOP" = Candle close ABOVE SuperTrend
    - "BOTTOM" = Candle close BELOW SuperTrend
    
    BUY Conditions (Current candle BELOW SuperTrend):
    Pattern 1: Top-Top-Bottom (STRONG - Previous 2 candles above ST, current below ST)
    Pattern 2: Top-Bottom-Bottom (BUY continuation - 2 candles ago above ST, last 2 below ST)  
    Pattern 3: Bottom-Top-Bottom (REVERSAL BUY - was below, popped above, now back below)
    
    SELL Conditions (Current candle ABOVE SuperTrend):
    Pattern 1: Bottom-Bottom-Top (STRONG - Previous 2 candles below ST, current above ST)
    Pattern 2: Bottom-Top-Top (SELL continuation - 2 candles ago below ST, last 2 above ST)
    Pattern 3: Top-Bottom-Top (REVERSAL SELL - was above, dipped below, now back above)
    """
    try:
        # Get data
        close = df['close']
        supertrend = supertrend_data['supertrend']
        
        # Need at least 3 candles
        if len(close) < 3 or len(supertrend) < 3:
            return None
        
        # Current and previous candles
        current_close = close.iloc[-1]
        current_st = supertrend.iloc[-1]
        
        prev_1_close = close.iloc[-2]
        prev_1_st = supertrend.iloc[-2]
        
        prev_2_close = close.iloc[-3]
        prev_2_st = supertrend.iloc[-3]
        
        # CHOP value - check if market is trending
        current_chop = chop_series.iloc[-1]
        
        # Must be trending (CHOP < 49)
        if pd.isna(current_chop) or current_chop >= 49:
            return None
        
        # Validate SuperTrend values
        if pd.isna(current_st) or pd.isna(prev_1_st) or pd.isna(prev_2_st):
            return None
        
        # Determine positions (TOP = above ST, BOTTOM = below ST)
        current_is_above = current_close > current_st
        prev_1_is_above = prev_1_close > prev_1_st
        prev_2_is_above = prev_2_close > prev_2_st
        
        # Create position labels for logging
        current_pos = "TOP" if current_is_above else "BOTTOM"
        prev_1_pos = "TOP" if prev_1_is_above else "BOTTOM"
        prev_2_pos = "TOP" if prev_2_is_above else "BOTTOM"
        
        pattern = f"{prev_2_pos}-{prev_1_pos}-{current_pos}"
        
        # ============ BUY SIGNALS (Current candle BELOW SuperTrend) ============
        if not current_is_above:  # Current candle is BOTTOM (below ST)
            
            # Pattern 1: Top-Top-Bottom (Strong BUY - reversal from uptrend)
            if prev_2_is_above and prev_1_is_above:
                print(f"  🟢 STRONG BUY [{pattern}] - Double top reversal")
                return 'BUY'
            
            # Pattern 2: Top-Bottom-Bottom (BUY continuation)
            elif prev_2_is_above and not prev_1_is_above:
                print(f"  🟢 BUY [{pattern}] - Continued below ST")
                return 'BUY'
            
            # Pattern 3: Bottom-Top-Bottom (Reversal BUY - false breakout)
            elif not prev_2_is_above and prev_1_is_above:
                print(f"  🟢 WEAK BUY [{pattern}] - Failed breakout")
                return 'BUY'
            
            # Pattern 4: Bottom-Bottom-Bottom (all below ST - no signal)
            # else: No buy signal, all candles already below ST
        
        # ============ SELL SIGNALS (Current candle ABOVE SuperTrend) ============
        elif current_is_above:  # Current candle is TOP (above ST)
            
            # Pattern 1: Bottom-Bottom-Top (Strong SELL - reversal from downtrend)
            if not prev_2_is_above and not prev_1_is_above:
                print(f"  🔴 STRONG SELL [{pattern}] - Double bottom reversal")
                return 'SELL'
            
            # Pattern 2: Bottom-Top-Top (SELL continuation)
            elif not prev_2_is_above and prev_1_is_above:
                print(f"  🔴 SELL [{pattern}] - Continued above ST")
                return 'SELL'
            
            # Pattern 3: Top-Bottom-Top (Reversal SELL - false breakdown)
            elif prev_2_is_above and not prev_1_is_above:
                print(f"  🔴 WEAK SELL [{pattern}] - Failed breakdown")
                return 'SELL'
            
            # Pattern 4: Top-Top-Top (all above ST - no signal)
            # else: No sell signal, all candles already above ST
        
        return None
        
    except Exception as e:
        print(f"Pattern check error for {symbol}: {e}")
        traceback.print_exc()
        return None

def run_bot():
    global last_check_time, error_count, cycle_count
    
    print("\n" + "="*60)
    print("🚀 SUPERTREND + CHOP SIGNAL GENERATOR v3.0")
    print("="*60)
    print(f"📊 Exchange: {EXCHANGE.name.capitalize()}")
    print(f"🤖 Telegram: {'Enabled' if TOKEN and CHAT_ID else 'Disabled'}")
    print("\n📈 STRATEGY CONFIGURATION:")
    print("  • SuperTrend (Period 10, Multiplier 3)")
    print("  • Choppiness Index (Period 21, Threshold < 49)")
    print("  • Timeframe: 5-minute candles")
    print(f"  • Scan Interval: Every {CHECK_INTERVAL} seconds")
    print("\n🎯 ANYTIME PATTERN DETECTION:")
    print("  🟢 BUY Patterns (Current candle BELOW SuperTrend):")
    print("     1. Top-Top-Bottom (Strong reversal)")
    print("     2. Top-Bottom-Bottom (Continuation)")
    print("     3. Bottom-Top-Bottom (Failed breakout)")
    print("  🔴 SELL Patterns (Current candle ABOVE SuperTrend):")
    print("     1. Bottom-Bottom-Top (Strong reversal)")
    print("     2. Bottom-Top-Top (Continuation)")
    print("     3. Top-Bottom-Top (Failed breakdown)")
    print("="*60 + "\n")

    # Get available symbols
    available_symbols = get_available_symbols(EXCHANGE, SYMBOLS)
    print(f"✅ Available symbols: {len(available_symbols)}/{len(SYMBOLS)}")
    
    if not available_symbols:
        error_msg = "❌ No symbols available to monitor!"
        print(error_msg)
        send_alert(error_msg)
        return
    
    # Display symbols being monitored
    print("\n📋 Monitored Symbols:")
    for i in range(0, len(available_symbols), 5):
        print("  " + ", ".join(available_symbols[i:i+5]))
    print()

    # Initialize signal states and history for all available symbols
    for symbol in available_symbols:
        last_signal_state[symbol] = None
        signal_history[symbol] = deque(maxlen=10)

    # Startup message
    startup_msg = (
        f"✅ <b>SuperTrend + CHOP Signal Generator v3.0 Started</b>\n\n"
        f"📊 <b>Strategy:</b> SuperTrend(10,3) + CHOP21\n"
        f"🔍 <b>Monitoring:</b> {len(available_symbols)} trading pairs\n"
        f"⏱️ <b>Check Frequency:</b> Every {CHECK_INTERVAL} seconds\n"
        f"🕒 <b>Start Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        f"🎯 <b>Anytime Pattern Detection Enabled</b>\n"
        f"Signals based on candle position patterns,\n"
        f"not just crossing events"
    )
    send_alert(startup_msg)

    while True:
        try:
            cycle_count += 1
            signals_found_this_cycle = 0
            processed_count = 0
            
            print(f"\n{'='*60}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}")
            
            for i, symbol in enumerate(available_symbols):
                try:
                    # Rate limiting: delay between API calls
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)
                    
                    # Get OHLCV data including live/incomplete candle
                    ohlcv = EXCHANGE.fetch_ohlcv(
                        symbol,
                        timeframe='5m',
                        limit=50  # Enough for calculations and pattern detection
                    )

                    if len(ohlcv) < 20:
                        print(f"  ⚠️ {symbol}: Insufficient data ({len(ohlcv)} candles)")
                        continue

                    df = pd.DataFrame(
                        ohlcv,
                        columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                    )

                    # Calculate indicators
                    chop_series = calculate_choppiness_index(df, period=21)
                    supertrend_data = calculate_supertrend(df, period=10, multiplier=3)

                    # Skip if indicators couldn't be calculated
                    if chop_series is None or supertrend_data is None:
                        continue

                    # Get current CHOP value
                    current_chop = chop_series.iloc[-1]
                    
                    # Get current price
                    current_price = df['close'].iloc[-1]
                    price_str = format_price(current_price)
                    
                    # Get SuperTrend values for display
                    current_st = supertrend_data['supertrend'].iloc[-1]
                    current_trend_num = supertrend_data['trend'].iloc[-1]
                    current_trend_str = "🟢 BULLISH" if current_trend_num == 1 else "🔴 BEARISH"
                    
                    # Determine position relative to ST
                    position = "ABOVE" if current_price > current_st else "BELOW"
                    
                    # Log current state (only for trending markets to reduce noise)
                    if not pd.isna(current_chop) and current_chop < 49:
                        print(f"  {symbol:12} | Price: {price_str:12} | CHOP: {current_chop:6.2f} | "
                              f"Trend: {current_trend_str} | Pos: {position} ST")
                    
                    # Check for signals using ANYTIME pattern detection
                    signal = check_signal_pattern_anytime(symbol, df, supertrend_data, chop_series)
                    
                    if signal:
                        # Only alert if signal changed from previous state
                        if signal != last_signal_state[symbol]:
                            signals_found_this_cycle += 1
                            
                            # Get detailed position information
                            close = df['close']
                            supertrend = supertrend_data['supertrend']
                            
                            prev_2_pos = "ABOVE" if close.iloc[-3] > supertrend.iloc[-3] else "BELOW"
                            prev_1_pos = "ABOVE" if close.iloc[-2] > supertrend.iloc[-2] else "BELOW"
                            curr_pos = "ABOVE" if close.iloc[-1] > supertrend.iloc[-1] else "BELOW"
                            
                            emoji = "🟢" if signal == 'BUY' else "🔴"
                            signal_type = "BUY (Price Below ST)" if signal == 'BUY' else "SELL (Price Above ST)"
                            
                            message = (
                                f"{emoji} <b>{signal} SIGNAL DETECTED</b>\n\n"
                                f"<b>Symbol:</b> {symbol}\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>CHOP21:</b> {current_chop:.2f} (Trending)\n"
                                f"<b>SuperTrend:</b> {format_price(current_st)}\n"
                                f"<b>Market Trend:</b> {current_trend_str}\n\n"
                                f"<b>📊 Candle Position Pattern:</b>\n"
                                f"  • 2 candles ago: <b>{prev_2_pos}</b> SuperTrend\n"
                                f"  • 1 candle ago: <b>{prev_1_pos}</b> SuperTrend\n"
                                f"  • Current candle: <b>{curr_pos}</b> SuperTrend\n\n"
                                f"<b>Signal Type:</b> {signal_type}\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                                f"<b>Cycle:</b> #{cycle_count}"
                            )
                            
                            if send_alert(message):
                                print(f"  ✅ {symbol}: {signal} SIGNAL - Alert sent successfully!")
                                last_signal_state[symbol] = signal
                                # Add to history
                                signal_history[symbol].append({
                                    'time': datetime.now(),
                                    'signal': signal,
                                    'price': current_price,
                                    'chop': current_chop,
                                    'pattern': f"{prev_2_pos}-{prev_1_pos}-{curr_pos}"
                                })
                            else:
                                print(f"  ❌ {symbol}: {signal} SIGNAL - Alert FAILED!")
                        else:
                            # Signal still active, no need to alert again
                            if cycle_count % 10 == 0:  # Log every 10 cycles to reduce noise
                                print(f"  ℹ️ {symbol}: {signal} signal still active (no duplicate alert)")
                    
                    elif not pd.isna(current_chop) and current_chop < 49:
                        # Market is trending but no signal pattern detected
                        if last_signal_state.get(symbol) is not None:
                            # Signal has ended - optionally notify
                            prev_signal = last_signal_state[symbol]
                            print(f"  ⚠️ {symbol}: {prev_signal} signal ENDED - Pattern broken")
                            last_signal_state[symbol] = None
                    
                    else:
                        # Market is ranging/choppy - reset any active signals
                        if last_signal_state.get(symbol) is not None:
                            prev_signal = last_signal_state[symbol]
                            print(f"  ⚠️ {symbol}: {prev_signal} signal CANCELLED - Market choppy (CHOP: {current_chop:.2f})")
                            last_signal_state[symbol] = None
                    
                    processed_count += 1
                    
                except ccxt.RateLimitExceeded as e:
                    print(f"  ⚠️ Rate limit hit for {symbol}, waiting 30s...")
                    time.sleep(30)
                    continue
                except ccxt.NetworkError as e:
                    print(f"  ⚠️ Network error for {symbol}: {e}")
                    continue
                except ccxt.ExchangeError as e:
                    print(f"  ⚠️ Exchange error for {symbol}: {e}")
                    if "Invalid symbol" in str(e).lower() or "not found" in str(e).lower():
                        print(f"  ❌ Removing {symbol} from monitoring list")
                        available_symbols.remove(symbol)
                    continue
                except Exception as e:
                    print(f"  ❌ Unexpected error checking {symbol}: {e}")
                    error_count += 1
                    if error_count > MAX_ERRORS_BEFORE_RESTART:
                        print(f"  🔄 Too many errors ({error_count}), continuing...")
                        error_count = 0
                    continue

            # Update last check time
            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            
            # Cycle summary
            active_signals = sum(1 for v in last_signal_state.values() if v is not None)
            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Processed: {processed_count}/{len(available_symbols)} symbols")
            print(f"  • New Signals: {signals_found_this_cycle}")
            print(f"  • Active Signals: {active_signals}")
            
            if active_signals > 0:
                active_list = [f"{sym}: {sig}" for sym, sig in last_signal_state.items() if sig is not None]
                print(f"  • Active: {', '.join(active_list)}")
            
            next_check = datetime.now() + timedelta(seconds=CHECK_INTERVAL)
            print(f"  • Next Check: {next_check.strftime('%H:%M:%S')}")
            print(f"{'='*60}\n")
            
            # Wait for next cycle
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            send_alert("🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error in main loop: {e}")
            print(traceback.format_exc())
            error_count += 1
            if error_count > MAX_ERRORS_BEFORE_RESTART:
                send_alert(f"❌ Bot encountered critical error: {str(e)[:200]}")
                error_count = 0
            time.sleep(60)

# 3. Start bot in background
print("\n🚀 Starting bot in background thread...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server starting on port {port}")
    print(f"📊 Health check: http://localhost:{port}/health")
    print(f"{'='*60}\n")
    app.run(host='0.0.0.0', port=port)