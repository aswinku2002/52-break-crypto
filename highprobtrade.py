import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime, timedelta
from collections import deque, defaultdict
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
        "exchange": PRIMARY_EXCHANGE.upper(),
        "last_check": last_check_time,
        "cycle": cycle_count,
        "active_signals": sum(1 for v in signal_tracker.items() if v[1]['confirmed']),
        "cache_stats": {
            "symbols_cached": len(ohlcv_cache),
            "total_api_calls_saved": api_calls_saved
        }
    }

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'binance').lower()
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# ============ NEW: Performance Configuration ============
API_CALL_INTERVAL = 1.5        # Seconds between API calls
CHECK_INTERVAL = 30            # Seconds between full scans
CANDLES_TO_FETCH = 25          # Reduced from 50 to 25 (SOLUTION #4)
CACHE_EXPIRY_SECONDS = 60      # How long to keep cached data
MAX_CANDLES_IN_CACHE = 30      # Maximum candles to store per symbol

# ============ NEW: Signal Confirmation Settings ============
CONFIRMATION_CYCLES_REQUIRED = 2   # Cycles needed to confirm a signal (SOLUTION #3)
RESET_CYCLES_REQUIRED = 2          # Cycles needed to reset a signal
# ========================================================

# Trading pairs to monitor
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT',
    'DOGE/USDT', 'BNB/USDT', 'LTC/USDT', 'LINK/USDT',
    'AVAX/USDT', 'ADA/USDT', 'SUI/USDT', 'TRX/USDT',
    'BCH/USDT', 'AAVE/USDT', 'ETC/USDT', 'NEAR/USDT',
    'UNI/USDT', 'ZEC/USDT', 'ENJ/USDT', 'XMR/USDT',
    'AXS/USDT', 'JTO/USDT', 'IO/USDT', 'ALT/USDT',
]

# Global variables
last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0  # Track API calls saved by caching

# ============ NEW: OHLCV Cache System (SOLUTION #4) ============
ohlcv_cache = {}  # {symbol: {'data': DataFrame, 'last_update': datetime, 'last_timestamp': int}}

def get_cached_ohlcv(exchange, symbol, timeframe='5m', limit=25):
    """
    Smart OHLCV fetcher with caching
    Only fetches new candles, reuses cached data
    
    Returns: DataFrame with OHLCV data
    """
    global api_calls_saved
    
    now = datetime.now()
    cache_key = f"{symbol}_{timeframe}"
    
    # Check if we have cached data
    if cache_key in ohlcv_cache:
        cache_entry = ohlcv_cache[cache_key]
        age_seconds = (now - cache_entry['last_update']).total_seconds()
        
        # If cache is fresh enough, try to fetch only new candles
        if age_seconds < CACHE_EXPIRY_SECONDS:
            try:
                # Get the timestamp of last cached candle
                last_cached_ts = cache_entry['last_timestamp']
                
                # Fetch only candles since last cached timestamp
                new_ohlcv = exchange.fetch_ohlcv(
                    symbol,
                    timeframe=timeframe,
                    since=last_cached_ts + 1,  # +1 to avoid duplicate
                    limit=5  # Only fetch few new candles
                )
                
                if new_ohlcv and len(new_ohlcv) > 0:
                    # Convert new data to DataFrame
                    new_df = pd.DataFrame(
                        new_ohlcv,
                        columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                    )
                    
                    # Append to cached data
                    old_df = cache_entry['data']
                    combined_df = pd.concat([old_df, new_df], ignore_index=True)
                    
                    # Remove duplicates based on timestamp
                    combined_df = combined_df.drop_duplicates(subset=['ts'], keep='last')
                    
                    # Keep only last MAX_CANDLES_IN_CACHE candles
                    combined_df = combined_df.tail(MAX_CANDLES_IN_CACHE)
                    
                    # Update cache
                    ohlcv_cache[cache_key] = {
                        'data': combined_df,
                        'last_update': now,
                        'last_timestamp': combined_df['ts'].iloc[-1]
                    }
                    
                    api_calls_saved += 1  # We saved a full fetch
                    
                    print(f"  📦 {symbol}: Incremental update - "
                          f"added {len(new_df)} new candles, "
                          f"total cached: {len(combined_df)}")
                    
                    return combined_df
                
                else:
                    # No new candles, use cache directly
                    api_calls_saved += 1
                    print(f"  💾 {symbol}: Using cache (no new candles)")
                    return cache_entry['data']
                    
            except Exception as e:
                # If incremental fetch fails, fall back to full fetch
                print(f"  ⚠️ {symbol}: Incremental fetch failed ({e}), doing full fetch")
    
    # Full fetch (first time or cache expired)
    try:
        ohlcv = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=limit
        )
        
        if len(ohlcv) > 0:
            df = pd.DataFrame(
                ohlcv,
                columns=['ts', 'open', 'high', 'low', 'close', 'vol']
            )
            
            # Update cache
            ohlcv_cache[cache_key] = {
                'data': df,
                'last_update': now,
                'last_timestamp': df['ts'].iloc[-1]
            }
            
            print(f"  🔄 {symbol}: Full fetch - {len(df)} candles cached")
            return df
        else:
            # If fetch fails but we have old cache, use it
            if cache_key in ohlcv_cache:
                print(f"  💾 {symbol}: Fetch failed, using old cache")
                return ohlcv_cache[cache_key]['data']
            return None
            
    except Exception as e:
        print(f"  ❌ {symbol}: Fetch error: {e}")
        # Fall back to cache if available
        if cache_key in ohlcv_cache:
            print(f"  💾 {symbol}: Using stale cache")
            return ohlcv_cache[cache_key]['data']
        return None

def cleanup_cache():
    """Remove expired cache entries"""
    now = datetime.now()
    expired_keys = []
    
    for key, entry in ohlcv_cache.items():
        age = (now - entry['last_update']).total_seconds()
        if age > 300:  # Remove cache older than 5 minutes
            expired_keys.append(key)
    
    for key in expired_keys:
        del ohlcv_cache[key]
    
    if expired_keys:
        print(f"  🧹 Cleaned {len(expired_keys)} expired cache entries")

# ============ NEW: Signal Tracker with Confirmation (SOLUTION #3) ============
signal_tracker = {}  # {symbol: {
                     #   'current_signal': 'BUY'/'SELL'/None,
                     #   'confirmation_count': 0,
                     #   'reset_count': 0,
                     #   'confirmed': False,
                     #   'last_signal_time': datetime,
                     #   'signal_strength': 'STRONG'/'NORMAL'/'WEAK'
                     # }}

def update_signal_state(symbol, new_signal, strength='NORMAL'):
    """
    Update signal state with confirmation logic
    
    - Requires CONFIRMATION_CYCLES_REQUIRED cycles to confirm a signal
    - Requires RESET_CYCLES_REQUIRED cycles to reset a signal
    - Prevents false signals from temporary wicks
    
    Returns: 'CONFIRMED', 'PENDING', 'RESET', or None
    """
    now = datetime.now()
    
    # Initialize tracker for new symbols
    if symbol not in signal_tracker:
        signal_tracker[symbol] = {
            'current_signal': None,
            'confirmation_count': 0,
            'reset_count': 0,
            'confirmed': False,
            'last_signal_time': now,
            'signal_strength': 'NORMAL'
        }
    
    tracker = signal_tracker[symbol]
    
    # Case 1: Same signal as before
    if new_signal and new_signal == tracker['current_signal']:
        if not tracker['confirmed']:
            # Increment confirmation counter
            tracker['confirmation_count'] += 1
            
            print(f"  🔄 {symbol}: {new_signal} confirming... "
                  f"({tracker['confirmation_count']}/{CONFIRMATION_CYCLES_REQUIRED})")
            
            # Check if we have enough confirmations
            if tracker['confirmation_count'] >= CONFIRMATION_CYCLES_REQUIRED:
                tracker['confirmed'] = True
                tracker['last_signal_time'] = now
                tracker['signal_strength'] = strength
                tracker['reset_count'] = 0
                
                print(f"  ✅ {symbol}: {new_signal} CONFIRMED! "
                      f"(Strength: {strength}, after {tracker['confirmation_count']} cycles)")
                return 'CONFIRMED'
            
            return 'PENDING'
        else:
            # Already confirmed, reset counter for potential exit
            tracker['reset_count'] = 0
            return None
    
    # Case 2: Different signal or no signal
    elif new_signal != tracker['current_signal']:
        if tracker['confirmed']:
            # Previously confirmed signal, now checking for reset
            tracker['reset_count'] += 1
            
            print(f"  ⏳ {symbol}: {tracker['current_signal']} possibly ending... "
                  f"({tracker['reset_count']}/{RESET_CYCLES_REQUIRED})")
            
            if tracker['reset_count'] >= RESET_CYCLES_REQUIRED:
                # Signal reset confirmed
                old_signal = tracker['current_signal']
                
                # Reset tracker
                tracker['current_signal'] = None
                tracker['confirmation_count'] = 0
                tracker['reset_count'] = 0
                tracker['confirmed'] = False
                tracker['signal_strength'] = 'NORMAL'
                
                print(f"  ⚠️ {symbol}: {old_signal} signal ENDED "
                      f"(after {RESET_CYCLES_REQUIRED} confirmation cycles)")
                return 'RESET'
            
            return 'PENDING_RESET'
        else:
            # New potential signal or no signal
            if new_signal:
                # Start tracking new signal
                tracker['current_signal'] = new_signal
                tracker['confirmation_count'] = 1
                tracker['reset_count'] = 0
                tracker['confirmed'] = False
                tracker['signal_strength'] = strength
                
                print(f"  🔍 {symbol}: New {new_signal} signal detected "
                      f"(Strength: {strength}, {tracker['confirmation_count']}/{CONFIRMATION_CYCLES_REQUIRED})")
                return 'NEW_SIGNAL'
            else:
                # No signal, reset if not confirmed
                if not tracker['confirmed']:
                    tracker['current_signal'] = None
                    tracker['confirmation_count'] = 0
                    tracker['reset_count'] = 0
                    tracker['signal_strength'] = 'NORMAL'
                return None
    
    return None

def get_confirmed_signals():
    """Get all currently confirmed signals"""
    confirmed = {}
    for symbol, tracker in signal_tracker.items():
        if tracker['confirmed']:
            confirmed[symbol] = {
                'signal': tracker['current_signal'],
                'strength': tracker['signal_strength'],
                'confirmed_at': tracker['last_signal_time'],
                'cycles_held': tracker['reset_count']
            }
    return confirmed

# 3. Exchange Initialization
def init_exchange():
    """Initialize exchange"""
    try:
        if PRIMARY_EXCHANGE == 'binance':
            exchange = ccxt.binance({
                'apiKey': BINANCE_API_KEY,
                'secret': BINANCE_API_SECRET,
                'enableRateLimit': True,
                'options': {'defaultType': 'spot'}
            })
            exchange.load_markets()
            return exchange
    except Exception as e:
        print(f"❌ Exchange initialization error: {e}")
        return None

EXCHANGE = init_exchange()
if not EXCHANGE:
    print("❌ No exchange available. Exiting.")
    exit(1)

# 4. Indicator Calculations
def calculate_choppiness_index(df, period=21):
    """Calculate Choppiness Index"""
    try:
        high, low, close = df['high'], df['low'], df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        sum_tr = tr.rolling(window=period).sum()
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()
        price_range = highest_high - lowest_low
        price_range = price_range.replace(0, np.nan)
        
        choppiness = 100 * np.log10(sum_tr / price_range) / np.log10(period)
        return choppiness
    except Exception as e:
        return None

def calculate_supertrend(df, period=10, multiplier=3):
    """Calculate SuperTrend indicator"""
    try:
        high, low, close = df['high'], df['low'], df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        hl2 = (high + low) / 2
        basic_upper_band = hl2 + (multiplier * atr)
        basic_lower_band = hl2 - (multiplier * atr)
        
        final_upper_band = pd.Series(index=df.index, dtype=float)
        final_lower_band = pd.Series(index=df.index, dtype=float)
        supertrend = pd.Series(index=df.index, dtype=float)
        trend = pd.Series(index=df.index, dtype=int)
        
        final_upper_band.iloc[0] = basic_upper_band.iloc[0]
        final_lower_band.iloc[0] = basic_lower_band.iloc[0]
        supertrend.iloc[0] = final_lower_band.iloc[0]
        trend.iloc[0] = 1
        
        for i in range(1, len(df)):
            prev_close = close.iloc[i-1]
            prev_final_upper = final_upper_band.iloc[i-1]
            prev_final_lower = final_lower_band.iloc[i-1]
            prev_trend = trend.iloc[i-1]
            
            current_close = close.iloc[i]
            current_basic_upper = basic_upper_band.iloc[i]
            current_basic_lower = basic_lower_band.iloc[i]
            
            if current_basic_upper < prev_final_upper or prev_close > prev_final_upper:
                final_upper_band.iloc[i] = current_basic_upper
            else:
                final_upper_band.iloc[i] = prev_final_upper
            
            if current_basic_lower > prev_final_lower or prev_close < prev_final_lower:
                final_lower_band.iloc[i] = current_basic_lower
            else:
                final_lower_band.iloc[i] = prev_final_lower
            
            if current_close <= final_upper_band.iloc[i] and prev_trend == 1:
                trend.iloc[i] = -1
                supertrend.iloc[i] = final_upper_band.iloc[i]
            elif current_close >= final_lower_band.iloc[i] and prev_trend == -1:
                trend.iloc[i] = 1
                supertrend.iloc[i] = final_lower_band.iloc[i]
            elif prev_trend == 1:
                trend.iloc[i] = 1
                supertrend.iloc[i] = final_lower_band.iloc[i]
            else:
                trend.iloc[i] = -1
                supertrend.iloc[i] = final_upper_band.iloc[i]
        
        return {'supertrend': supertrend, 'trend': trend}
    except Exception as e:
        return None

# 5. Pattern Detection
def check_signal_pattern(symbol, df, supertrend_data, chop_series):
    """
    Detect trading patterns with strength classification
    Returns: (signal_type, strength) or (None, None)
    """
    try:
        close = df['close']
        supertrend = supertrend_data['supertrend']
        
        if len(close) < 3 or len(supertrend) < 3:
            return None, None
        
        current_close = close.iloc[-1]
        current_st = supertrend.iloc[-1]
        prev_1_close = close.iloc[-2]
        prev_1_st = supertrend.iloc[-2]
        prev_2_close = close.iloc[-3]
        prev_2_st = supertrend.iloc[-3]
        
        current_chop = chop_series.iloc[-1]
        
        if pd.isna(current_chop) or current_chop >= 49:
            return None, None
        
        current_is_above = current_close > current_st
        prev_1_is_above = prev_1_close > prev_1_st
        prev_2_is_above = prev_2_close > prev_2_st
        
        # BUY Signals (Current BELOW ST)
        if not current_is_above:
            if prev_2_is_above and prev_1_is_above:
                return 'BUY', 'STRONG'     # Top-Top-Bottom
            elif prev_2_is_above and not prev_1_is_above:
                return 'BUY', 'NORMAL'     # Top-Bottom-Bottom
            elif not prev_2_is_above and prev_1_is_above:
                return 'BUY', 'WEAK'       # Bottom-Top-Bottom
        
        # SELL Signals (Current ABOVE ST)
        elif current_is_above:
            if not prev_2_is_above and not prev_1_is_above:
                return 'SELL', 'STRONG'    # Bottom-Bottom-Top
            elif not prev_2_is_above and prev_1_is_above:
                return 'SELL', 'NORMAL'    # Bottom-Top-Top
            elif prev_2_is_above and not prev_1_is_above:
                return 'SELL', 'WEAK'      # Top-Bottom-Top
        
        return None, None
        
    except Exception as e:
        return None, None

# 6. Alert System
def send_alert(message):
    """Send Telegram alert"""
    if not TOKEN or not CHAT_ID:
        return False
    
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
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

def format_price(price):
    """Format price with appropriate decimals"""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.8f}"

# 7. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved
    
    print("\n" + "="*70)
    print("🚀 SUPERTREND + CHOP SIGNAL GENERATOR v3.1")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE.name.capitalize()}")
    print(f"\n📈 OPTIMIZATIONS:")
    print(f"  • Cache System: Incremental OHLCV fetching")
    print(f"  • Max Candles Fetched: {CANDLES_TO_FETCH} (was 50)")
    print(f"  • Cache Expiry: {CACHE_EXPIRY_SECONDS}s")
    print(f"  • Signal Confirmation: {CONFIRMATION_CYCLES_REQUIRED} cycles required")
    print(f"  • Signal Reset: {RESET_CYCLES_REQUIRED} cycles required")
    print(f"  • API Call Interval: {API_CALL_INTERVAL}s")
    print(f"  • Scan Interval: {CHECK_INTERVAL}s")
    print("="*70 + "\n")
    
    # Get available symbols
    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)} symbols\n")
    
    # Startup alert
    send_alert(
        f"✅ <b>SuperTrend Bot v3.1 Started</b>\n\n"
        f"📊 <b>Optimizations Active:</b>\n"
        f"• Smart OHLCV caching\n"
        f"• Signal confirmation ({CONFIRMATION_CYCLES_REQUIRED} cycles)\n"
        f"• Signal reset protection ({RESET_CYCLES_REQUIRED} cycles)\n"
        f"🔍 <b>Monitoring:</b> {len(available_symbols)} pairs\n"
        f"🕒 <b>Start:</b> {datetime.now().strftime('%H:%M:%S')}"
    )
    
    while True:
        try:
            cycle_count += 1
            confirmed_signals = 0
            pending_signals = 0
            new_signals = 0
            reset_signals = 0
            processed = 0
            
            print(f"\n{'='*70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*70}")
            
            # Cleanup old cache entries every 10 cycles
            if cycle_count % 10 == 0:
                cleanup_cache()
            
            for i, symbol in enumerate(available_symbols):
                try:
                    # Rate limiting
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)
                    
                    # ============ USE SMART CACHING (SOLUTION #4) ============
                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='5m', 
                        limit=CANDLES_TO_FETCH  # Only 25 candles now
                    )
                    
                    if df is None or len(df) < 20:
                        continue
                    
                    # Calculate indicators
                    chop_series = calculate_choppiness_index(df, period=21)
                    supertrend_data = calculate_supertrend(df, period=10, multiplier=3)
                    
                    if chop_series is None or supertrend_data is None:
                        continue
                    
                    # Get current values
                    current_chop = chop_series.iloc[-1]
                    current_price = df['close'].iloc[-1]
                    current_st = supertrend_data['supertrend'].iloc[-1]
                    price_str = format_price(current_price)
                    
                    # Log trending markets
                    if not pd.isna(current_chop) and current_chop < 49:
                        position = "ABOVE" if current_price > current_st else "BELOW"
                        print(f"  {symbol:12} | {price_str:12} | CHOP: {current_chop:5.2f} | "
                              f"Pos: {position} ST | Cache: {len(df)} candles")
                    
                    # Check for patterns
                    signal, strength = check_signal_pattern(symbol, df, supertrend_data, chop_series)
                    
                    # ============ UPDATE SIGNAL STATE WITH CONFIRMATION (SOLUTION #3) ============
                    result = update_signal_state(symbol, signal, strength)
                    
                    if result == 'CONFIRMED':
                        confirmed_signals += 1
                        
                        # Get detailed info for alert
                        tracker = signal_tracker[symbol]
                        
                        close = df['close']
                        supertrend = supertrend_data['supertrend']
                        prev_2_pos = "ABOVE" if close.iloc[-3] > supertrend.iloc[-3] else "BELOW"
                        prev_1_pos = "ABOVE" if close.iloc[-2] > supertrend.iloc[-2] else "BELOW"
                        curr_pos = "ABOVE" if close.iloc[-1] > supertrend.iloc[-1] else "BELOW"
                        
                        emoji = "🟢" if signal == 'BUY' else "🔴"
                        strength_emoji = {
                            'STRONG': '💪',
                            'NORMAL': '✅',
                            'WEAK': '⚠️'
                        }
                        
                        message = (
                            f"{emoji} <b>{signal} SIGNAL CONFIRMED</b> {strength_emoji.get(strength, '')}\n\n"
                            f"<b>Symbol:</b> {symbol}\n"
                            f"<b>Price:</b> {price_str}\n"
                            f"<b>Strength:</b> {strength}\n"
                            f"<b>CHOP21:</b> {current_chop:.2f}\n"
                            f"<b>SuperTrend:</b> {format_price(current_st)}\n\n"
                            f"<b>Pattern:</b> {prev_2_pos}-{prev_1_pos}-{curr_pos}\n"
                            f"<b>Confirmed after:</b> {CONFIRMATION_CYCLES_REQUIRED} cycles\n"
                            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"<b>Cycle:</b> #{cycle_count}"
                        )
                        
                        if send_alert(message):
                            print(f"  ✅ ALERT SENT: {symbol} {signal} ({strength})")
                        else:
                            print(f"  ❌ Alert FAILED for {symbol}")
                    
                    elif result == 'RESET':
                        reset_signals += 1
                        
                        # Optional: send reset alert
                        old_tracker = signal_tracker[symbol]
                        message = (
                            f"⚠️ <b>SIGNAL ENDED</b>\n\n"
                            f"<b>Symbol:</b> {symbol}\n"
                            f"<b>Previous Signal:</b> {old_tracker['current_signal']}\n"
                            f"<b>Price:</b> {price_str}\n"
                            f"<b>Reason:</b> Pattern broken for {RESET_CYCLES_REQUIRED} cycles\n"
                            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        send_alert(message)
                        print(f"  ⚠️ Signal ENDED: {symbol}")
                    
                    elif result == 'PENDING':
                        pending_signals += 1
                    
                    elif result == 'NEW_SIGNAL':
                        new_signals += 1
                    
                    processed += 1
                    
                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    continue
            
            # Update last check time
            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            
            # Cycle summary
            confirmed = get_confirmed_signals()
            
            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Processed: {processed}/{len(available_symbols)} symbols")
            print(f"  • New Patterns: {new_signals}")
            print(f"  • Pending Confirmation: {pending_signals}")
            print(f"  • Newly Confirmed: {confirmed_signals}")
            print(f"  • Signals Ended: {reset_signals}")
            print(f"  • API Calls Saved: {api_calls_saved} (cumulative)")
            print(f"  • Active Confirmed Signals: {len(confirmed)}")
            
            if confirmed:
                for sym, info in confirmed.items():
                    print(f"    • {sym}: {info['signal']} ({info['strength']}) - "
                          f"held for {info['cycles_held']} cycles")
            
            # Cache statistics
            print(f"  • Cache Size: {len(ohlcv_cache)} symbols cached")
            
            next_check = datetime.now() + timedelta(seconds=CHECK_INTERVAL)
            print(f"  • Next Check: {next_check.strftime('%H:%M:%S')}")
            print(f"{'='*70}\n")
            
            # Wait for next cycle
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            send_alert("🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            traceback.print_exc()
            time.sleep(60)

# 8. Start Bot
print("\n🚀 Starting bot...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# 9. Start Flask Server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port)