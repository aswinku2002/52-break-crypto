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
        "active_signals": sum(1 for v in signal_tracker.items() if v[1]['active']),
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

# Performance Configuration
API_CALL_INTERVAL = 1.5        # Seconds between API calls
CHECK_INTERVAL = 30            # Seconds between full scans
CANDLES_TO_FETCH = 50          # Increased for better indicators
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 50

# Signal Settings - INSTANT ALERTS
CONFIRMATION_CYCLES_REQUIRED = 1   # 1 = Instant alert on first detection
RESET_CYCLES_REQUIRED = 2          # Keep for reset protection

# Trading pairs to monitor - ALL YOUR SYMBOLS
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
cycle_count = 0
api_calls_saved = 0

# OHLCV Cache System
ohlcv_cache = {}

def get_cached_ohlcv(exchange, symbol, timeframe='5m', limit=50):
    """Smart OHLCV fetcher with caching"""
    global api_calls_saved

    now = datetime.now()
    cache_key = f"{symbol}_{timeframe}"

    if cache_key in ohlcv_cache:
        cache_entry = ohlcv_cache[cache_key]
        age_seconds = (now - cache_entry['last_update']).total_seconds()

        if age_seconds < CACHE_EXPIRY_SECONDS:
            try:
                last_cached_ts = cache_entry['last_timestamp']
                new_ohlcv = exchange.fetch_ohlcv(
                    symbol,
                    timeframe=timeframe,
                    since=last_cached_ts + 1,
                    limit=5
                )

                if new_ohlcv and len(new_ohlcv) > 0:
                    new_df = pd.DataFrame(
                        new_ohlcv,
                        columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                    )
                    old_df = cache_entry['data']
                    combined_df = pd.concat([old_df, new_df], ignore_index=True)
                    combined_df = combined_df.drop_duplicates(subset=['ts'], keep='last')
                    combined_df = combined_df.tail(MAX_CANDLES_IN_CACHE)

                    ohlcv_cache[cache_key] = {
                        'data': combined_df,
                        'last_update': now,
                        'last_timestamp': combined_df['ts'].iloc[-1]
                    }

                    api_calls_saved += 1
                    return combined_df
                else:
                    api_calls_saved += 1
                    return cache_entry['data']
            except Exception as e:
                print(f"  ⚠️ {symbol}: Incremental fetch failed ({e}), doing full fetch")

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
            ohlcv_cache[cache_key] = {
                'data': df,
                'last_update': now,
                'last_timestamp': df['ts'].iloc[-1]
            }
            return df
        else:
            if cache_key in ohlcv_cache:
                return ohlcv_cache[cache_key]['data']
            return None
    except Exception as e:
        print(f"  ❌ {symbol}: Fetch error: {e}")
        if cache_key in ohlcv_cache:
            return ohlcv_cache[cache_key]['data']
        return None

def cleanup_cache():
    """Remove expired cache entries"""
    now = datetime.now()
    expired_keys = []
    for key, entry in ohlcv_cache.items():
        age = (now - entry['last_update']).total_seconds()
        if age > 300:
            expired_keys.append(key)
    for key in expired_keys:
        del ohlcv_cache[key]
    if expired_keys:
        print(f"  🧹 Cleaned {len(expired_keys)} expired cache entries")

# Signal Tracker - SIMPLIFIED FOR INSTANT ALERTS
signal_tracker = {}

def update_signal_state(symbol, new_signal, strength='NORMAL'):
    """
    SIMPLIFIED: Send alert IMMEDIATELY on first detection
    Only track for reset protection
    """
    now = datetime.now()

    if symbol not in signal_tracker:
        signal_tracker[symbol] = {
            'current_signal': None,
            'active': False,
            'alert_sent': False,      # Track if we already sent alert
            'last_signal_time': now,
            'signal_strength': 'NORMAL'
        }

    tracker = signal_tracker[symbol]

    # NEW SIGNAL DETECTED - SEND ALERT IMMEDIATELY
    if new_signal and new_signal != tracker['current_signal']:
        # Old signal ended
        if tracker['active']:
            print(f"  ⚠️ {symbol}: {tracker['current_signal']} signal ended")

        # Start new signal
        tracker['current_signal'] = new_signal
        tracker['active'] = True
        tracker['alert_sent'] = False  # Reset alert flag for new signal
        tracker['last_signal_time'] = now
        tracker['signal_strength'] = strength

        # SEND ALERT INSTANTLY
        return 'NEW_SIGNAL'

    # Same signal - check if alert already sent
    elif new_signal and new_signal == tracker['current_signal']:
        if tracker['active'] and not tracker['alert_sent']:
            # Should not happen, but just in case
            tracker['alert_sent'] = True
            return 'NEW_SIGNAL'
        return 'SAME_SIGNAL'

    # No signal
    else:
        if tracker['active']:
            tracker['active'] = False
            tracker['alert_sent'] = False
            return 'SIGNAL_ENDED'
        return None

def get_active_signals():
    """Get all currently active signals"""
    active = {}
    for symbol, tracker in signal_tracker.items():
        if tracker['active']:
            active[symbol] = {
                'signal': tracker['current_signal'],
                'strength': tracker['signal_strength'],
                'active_since': tracker['last_signal_time'],
                'alert_sent': tracker['alert_sent']
            }
    return active

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

# 4. Indicator Calculations - FIXED CHOP CALCULATION
def calculate_choppiness_index(df, period=21):
    """Calculate Choppiness Index - FIXED version"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']

        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Sum of True Range over period
        sum_tr = tr.rolling(window=period).sum()
        
        # Highest High and Lowest Low over period
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()
        
        # Price range
        price_range = highest_high - lowest_low
        
        # Avoid division by zero
        price_range = price_range.replace(0, np.nan)
        
        # Choppiness Index = 100 * log10(sum(TR) / (High - Low)) / log10(period)
        choppiness = 100 * np.log10(sum_tr / price_range) / np.log10(period)
        
        return choppiness
    except Exception as e:
        print(f"  ❌ CHOP calculation error: {e}")
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
        print(f"  ❌ SuperTrend calculation error: {e}")
        return None

# 5. FIXED Pattern Detection - More permissive and with debug output
def check_signal_pattern(symbol, df, supertrend_data, chop_series):
    """
    FIXED LOGIC - More permissive:
    - BUY when CHOP < 49 AND (candle 1 above ST & current below ST)
    - SELL when CHOP < 49 AND (candle 1 below ST & current above ST)
    """
    try:
        close = df['close']
        supertrend = supertrend_data['supertrend']

        if len(close) < 5 or len(supertrend) < 5:
            return None, None

        # Get last 3 candles
        current_close = close.iloc[-1]
        current_st = supertrend.iloc[-1]
        prev_1_close = close.iloc[-2]
        prev_1_st = supertrend.iloc[-2]
        prev_2_close = close.iloc[-3]
        prev_2_st = supertrend.iloc[-3]

        current_chop = chop_series.iloc[-1]
        prev_chop = chop_series.iloc[-2]

        # CHOP condition: Must be below 49 (trending market)
        if pd.isna(current_chop) or current_chop >= 49:
            return None, None

        # Positions relative to SuperTrend
        current_is_above = current_close > current_st
        prev_1_is_above = prev_1_close > prev_1_st
        prev_2_is_above = prev_2_close > prev_2_st

        # ============ BUY CONDITIONS ============
        # Pattern: Above-Below (1 or 2 candles above ST, current below ST)
        if not current_is_above and prev_1_is_above:
            # Strong: 2+ candles above before crossing down
            if prev_2_is_above:
                return 'BUY', 'STRONG'
            else:
                return 'BUY', 'NORMAL'

        # ============ SELL CONDITIONS ============
        # Pattern: Below-Above (1 or 2 candles below ST, current above ST)
        elif current_is_above and not prev_1_is_above:
            # Strong: 2+ candles below before crossing up
            if not prev_2_is_above:
                return 'SELL', 'STRONG'
            else:
                return 'SELL', 'NORMAL'

        return None, None

    except Exception as e:
        print(f"  ❌ Pattern detection error for {symbol}: {e}")
        return None, None

# 6. Alert System
def send_alert(message):
    """Send Telegram alert"""
    if not TOKEN or not CHAT_ID:
        print("  ⚠️ No Telegram credentials configured!")
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
        if response.status_code == 200:
            print("  ✅ Telegram alert sent successfully!")
            return True
        else:
            print(f"  ❌ Telegram error: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")
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
    print("🚀 SUPERTREND + CHOP SIGNAL GENERATOR v3.4 (FIXED - ALL SYMBOLS)")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE.name.capitalize()}")
    print(f"\n📈 CONFIGURATION:")
    print(f"  • ⚡ INSTANT ALERTS: Signal sent immediately on detection")
    print(f"  • Cache System: Incremental OHLCV fetching")
    print(f"  • Max Candles Fetched: {CANDLES_TO_FETCH}")
    print(f"  • Cache Expiry: {CACHE_EXPIRY_SECONDS}s")
    print(f"  • API Call Interval: {API_CALL_INTERVAL}s")
    print(f"  • Scan Interval: {CHECK_INTERVAL}s")
    print(f"\n📊 SIGNAL LOGIC (INSTANT):")
    print(f"  • BUY: CHOP < 49 AND (Prev1 Above ST AND Current Below ST)")
    print(f"  • SELL: CHOP < 49 AND (Prev1 Below ST AND Current Above ST)")
    print(f"  • 🚀 Alert sent on FIRST detection!")
    print(f"\n📊 MONITORING {len(SYMBOLS)} SYMBOLS")
    print("="*70 + "\n")

    # Get available symbols
    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)}/{len(SYMBOLS)} symbols")

    # Show unavailable symbols
    unavailable = [s for s in SYMBOLS if s not in EXCHANGE.markets]
    if unavailable:
        print(f"⚠️ {len(unavailable)} symbols not available:")
        for sym in unavailable[:10]:
            print(f"  • {sym}")
        if len(unavailable) > 10:
            print(f"  • ... and {len(unavailable)-10} more")
    print()

    # Startup alert
    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>SuperTrend Bot v3.4 Started (ALL SYMBOLS)</b>\n\n"
            f"⚡ <b>Alert Mode:</b> INSTANT - Signal sent immediately on detection\n"
            f"📊 <b>Signal Logic:</b>\n"
            f"• BUY: CHOP < 49 AND (Prev1 Above ST AND Current Below ST)\n"
            f"• SELL: CHOP < 49 AND (Prev1 Below ST AND Current Above ST)\n"
            f"🔍 <b>Monitoring:</b> {len(available_symbols)} pairs\n"
            f"🕒 <b>Start:</b> {datetime.now().strftime('%H:%M:%S')}"
        )

    while True:
        try:
            cycle_count += 1
            new_signals = 0
            ended_signals = 0
            processed = 0

            print(f"\n{'='*70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*70}")

            # Cleanup old cache entries every 10 cycles
            if cycle_count % 10 == 0:
                cleanup_cache()

            # Process ALL symbols - but log progress
            for i, symbol in enumerate(available_symbols):
                try:
                    # Rate limiting
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    # Show progress every 10 symbols
                    if i % 10 == 0 and i > 0:
                        print(f"  📍 Progress: {i}/{len(available_symbols)} symbols processed")

                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='5m', 
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 25:
                        # Only log first few failures to avoid spam
                        if i < 5:
                            print(f"  ⚠️ {symbol}: Insufficient data ({len(df) if df is not None else 0} candles)")
                        continue

                    # Calculate indicators
                    chop_series = calculate_choppiness_index(df, period=21)
                    supertrend_data = calculate_supertrend(df, period=10, multiplier=3)

                    if chop_series is None or supertrend_data is None:
                        if i < 5:
                            print(f"  ⚠️ {symbol}: Indicator calculation failed")
                        continue

                    # Get current values
                    current_chop = chop_series.iloc[-1]
                    current_price = df['close'].iloc[-1]
                    current_st = supertrend_data['supertrend'].iloc[-1]
                    price_str = format_price(current_price)

                    # Log only when CHOP < 49 (potential signals) - reduces log spam
                    if current_chop < 49:
                        close = df['close']
                        supertrend = supertrend_data['supertrend']
                        current_is_above = current_price > current_st
                        prev_1_is_above = close.iloc[-2] > supertrend.iloc[-2]
                        prev_2_is_above = close.iloc[-3] > supertrend.iloc[-3]

                        print(f"  📊 {symbol:12} | {price_str:12} | CHOP: {current_chop:5.2f} | "
                              f"Pos: {'▲' if current_is_above else '▼'} ST | "
                              f"P1: {'▲' if prev_1_is_above else '▼'} | "
                              f"P2: {'▲' if prev_2_is_above else '▼'}")

                    # Check for patterns
                    signal, strength = check_signal_pattern(symbol, df, supertrend_data, chop_series)

                    if signal:
                        print(f"  🎯 {symbol}: {signal} signal detected! (CHOP={current_chop:.2f})")
                        
                        # Update signal state
                        result = update_signal_state(symbol, signal, strength)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1

                            # SEND ALERT IMMEDIATELY!
                            close = df['close']
                            supertrend = supertrend_data['supertrend']
                            prev_1_pos = "ABOVE" if close.iloc[-2] > supertrend.iloc[-2] else "BELOW"
                            curr_pos = "ABOVE" if close.iloc[-1] > supertrend.iloc[-1] else "BELOW"

                            emoji = "🟢" if signal == 'BUY' else "🔴"
                            strength_emoji = {
                                'STRONG': '💪',
                                'NORMAL': '✅'
                            }

                            # Track that alert was sent
                            signal_tracker[symbol]['alert_sent'] = True

                            message = (
                                f"🚨 <b>IMMEDIATE {signal} SIGNAL</b> {strength_emoji.get(strength, '')}\n\n"
                                f"<b>Symbol:</b> {symbol}\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Strength:</b> {strength}\n"
                                f"<b>CHOP21:</b> {current_chop:.2f}\n"
                                f"<b>SuperTrend:</b> {format_price(current_st)}\n\n"
                                f"<b>Pattern:</b> Prev1: {prev_1_pos} → Current: {curr_pos}\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"<b>Cycle:</b> #{cycle_count}\n\n"
                                f"⚡ <b>ALERT SENT IMMEDIATELY ON DETECTION!</b>"
                            )

                            if send_alert(message):
                                print(f"  🚨 ALERT SENT: {symbol} {signal} ({strength}) - INSTANT!")
                            else:
                                print(f"  ❌ Alert FAILED for {symbol}")

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    # Don't traceback for every error, just log it
                    if i < 5:
                        traceback.print_exc()
                    continue

            # Update last check time
            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            # Cycle summary
            active = get_active_signals()

            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Processed: {processed}/{len(available_symbols)} symbols")
            print(f"  • New Signals (Alert Sent): {new_signals}")
            print(f"  • Signals Ended: {ended_signals}")
            print(f"  • API Calls Saved: {api_calls_saved} (cumulative)")
            print(f"  • Active Signals: {len(active)}")

            if active:
                for sym, info in active.items():
                    alert_status = "✅ ALERT SENT" if info['alert_sent'] else "⏳ PENDING"
                    print(f"    • {sym}: {info['signal']} ({info['strength']}) - {alert_status}")

            print(f"  • Cache Size: {len(ohlcv_cache)} symbols cached")

            next_check = datetime.now() + timedelta(seconds=CHECK_INTERVAL)
            print(f"  • Next Check: {next_check.strftime('%H:%M:%S')}")
            print(f"{'='*70}\n")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            if TOKEN and CHAT_ID:
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