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
    return "ADX Signal Generator is running!"

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

# API Keys (optional - only needed for authenticated endpoints)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')
KRAKEN_API_KEY = os.environ.get('KRAKEN_API_KEY', '')
KRAKEN_API_SECRET = os.environ.get('KRAKEN_API_SECRET', '')
COINBASE_API_KEY = os.environ.get('COINBASE_API_KEY', '')
COINBASE_API_SECRET = os.environ.get('COINBASE_API_SECRET', '')
KUCOIN_API_KEY = os.environ.get('KUCOIN_API_KEY', '')
KUCOIN_API_SECRET = os.environ.get('KUCOIN_API_SECRET', '')
KUCOIN_PASSWORD = os.environ.get('KUCOIN_PASSWORD', '')
BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', '')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', '')

# Performance Configuration
API_CALL_INTERVAL = 1.5        # Seconds between API calls
CHECK_INTERVAL = 30            # Seconds between full scans
CANDLES_TO_FETCH = 100         # Increased for ADX calculation (needs 21+ periods)
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 100

# Signal Settings - INSTANT ALERTS
CONFIRMATION_CYCLES_REQUIRED = 1   # 1 = Instant alert on first detection
RESET_CYCLES_REQUIRED = 2          # Keep for reset protection

# Trading pairs to monitor - ONLY BTC/USDT
SYMBOLS = [
    'BTC/USDT'   # ⭐⭐⭐⭐⭐ High
]

# Global variables
last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0

# OHLCV Cache System
ohlcv_cache = {}

def get_cached_ohlcv(exchange, symbol, timeframe='5m', limit=100):
    """Smart OHLCV fetcher with caching - NO API KEYS NEEDED for public data"""
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

# 3. Exchange Initialization - MULTI-EXCHANGE SUPPORT
def init_exchange(exchange_name='binance'):
    """
    Initialize exchange with proper configuration
    Supports: binance, kraken, coinbase, kucoin, bybit
    Uses public endpoints by default (no API keys needed for OHLCV data)
    """
    try:
        # Base config for all exchanges
        config = {
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        }

        # Exchange-specific configurations
        if exchange_name == 'binance':
            if BINANCE_API_KEY and BINANCE_API_SECRET:
                config['apiKey'] = BINANCE_API_KEY
                config['secret'] = BINANCE_API_SECRET
                print(f"🔑 Binance: Using authenticated endpoints")
            else:
                print(f"🔓 Binance: Using public endpoints (no API keys)")
            exchange = ccxt.binance(config)

        elif exchange_name == 'kraken':
            if KRAKEN_API_KEY and KRAKEN_API_SECRET:
                config['apiKey'] = KRAKEN_API_KEY
                config['secret'] = KRAKEN_API_SECRET
                print(f"🔑 Kraken: Using authenticated endpoints")
            else:
                print(f"🔓 Kraken: Using public endpoints (no API keys)")
            exchange = ccxt.kraken(config)

        elif exchange_name == 'coinbase':
            if COINBASE_API_KEY and COINBASE_API_SECRET:
                config['apiKey'] = COINBASE_API_KEY
                config['secret'] = COINBASE_API_SECRET
                print(f"🔑 Coinbase: Using authenticated endpoints")
            else:
                print(f"🔓 Coinbase: Using public endpoints (no API keys)")
            exchange = ccxt.coinbase(config)

        elif exchange_name == 'kucoin':
            if KUCOIN_API_KEY and KUCOIN_API_SECRET and KUCOIN_PASSWORD:
                config['apiKey'] = KUCOIN_API_KEY
                config['secret'] = KUCOIN_API_SECRET
                config['password'] = KUCOIN_PASSWORD
                print(f"🔑 KuCoin: Using authenticated endpoints")
            else:
                print(f"🔓 KuCoin: Using public endpoints (no API keys)")
            exchange = ccxt.kucoin(config)

        elif exchange_name == 'bybit':
            if BYBIT_API_KEY and BYBIT_API_SECRET:
                config['apiKey'] = BYBIT_API_KEY
                config['secret'] = BYBIT_API_SECRET
                print(f"🔑 Bybit: Using authenticated endpoints")
            else:
                print(f"🔓 Bybit: Using public endpoints (no API keys)")
            exchange = ccxt.bybit(config)

        else:
            # Try to dynamically load any exchange
            print(f"⚠️ Unknown exchange: {exchange_name}, trying to load anyway...")
            exchange_class = getattr(ccxt, exchange_name)
            exchange = exchange_class(config)

        # Load markets
        exchange.load_markets()
        print(f"✅ Connected to {exchange_name.capitalize()} successfully")
        return exchange

    except ccxt.NetworkError as e:
        print(f"❌ Network error connecting to {exchange_name}: {e}")
        return None
    except ccxt.ExchangeError as e:
        print(f"❌ Exchange error with {exchange_name}: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error initializing {exchange_name}: {e}")
        return None

def get_available_exchange():
    """
    Try multiple exchanges in order until one works
    Falls back to Binance if all others fail
    """
    # List of exchanges to try in order
    exchanges_to_try = [PRIMARY_EXCHANGE, 'binance', 'kraken', 'coinbase', 'kucoin', 'bybit']

    # Remove duplicates while preserving order
    seen = set()
    exchanges_to_try = [x for x in exchanges_to_try if not (x in seen or seen.add(x))]

    print(f"\n🔄 Attempting to connect to exchanges in order: {', '.join(exchanges_to_try)}")

    for exchange_name in exchanges_to_try:
        print(f"\n📡 Trying {exchange_name.capitalize()}...")
        exchange = init_exchange(exchange_name)
        if exchange:
            return exchange
        print(f"❌ Failed to connect to {exchange_name}, trying next...")
        time.sleep(2)

    print("\n❌ All exchanges failed! Please check your internet connection.")
    return None

# Initialize exchange with fallback support
EXCHANGE = get_available_exchange()
if not EXCHANGE:
    print("❌ No exchange available. Exiting.")
    exit(1)

# Store which exchange we're using
EXCHANGE_NAME = EXCHANGE.name.capitalize()

# 4. ADX Indicator Calculation
def calculate_adx(df, period=21):
    """
    Calculate Average Directional Index (ADX)
    Returns ADX value and direction (1 for uptrend, -1 for downtrend)
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

        # Calculate Directional Movement
        up_move = high - high.shift()
        down_move = low.shift() - low

        plus_dm = pd.Series(index=df.index, dtype=float)
        minus_dm = pd.Series(index=df.index, dtype=float)

        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)

        # Smooth with Wilder's smoothing (similar to EMA)
        atr = tr.rolling(window=period).mean()

        # Smooth the directional movements
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)

        # Calculate DX and ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()

        # Determine trend direction
        # Positive DI > Negative DI indicates uptrend
        direction = 1 if plus_di.iloc[-1] > minus_di.iloc[-1] else -1

        return {
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'direction': direction,
            'current_adx': adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 0,
            'current_plus_di': plus_di.iloc[-1] if not pd.isna(plus_di.iloc[-1]) else 0,
            'current_minus_di': minus_di.iloc[-1] if not pd.isna(minus_di.iloc[-1]) else 0
        }
    except Exception as e:
        print(f"  ❌ ADX calculation error: {e}")
        return None

# 5. Signal Detection - ADX > 25
def check_adx_signal(symbol, df, adx_data):
    """
    Check if ADX > 25 (trending market)
    Returns signal and strength
    """
    try:
        if adx_data is None:
            return None, None

        current_adx = adx_data['current_adx']
        direction = adx_data['direction']

        # ADX must be > 25 to indicate a strong trend
        if pd.isna(current_adx) or current_adx <= 25:
            return None, None

        # Direction determination
        if direction == 1:
            return 'BUY', 'STRONG' if current_adx > 40 else 'NORMAL'
        else:
            return 'SELL', 'STRONG' if current_adx > 40 else 'NORMAL'

    except Exception as e:
        print(f"  ❌ ADX signal detection error for {symbol}: {e}")
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

def get_rating(symbol):
    """Get the rating for each symbol"""
    ratings = {
        'BTC/USDT': ('⭐⭐⭐⭐⭐', 'High', 'Excellent')
    }
    return ratings.get(symbol, ('⭐⭐⭐', 'Medium', 'Good'))

# 7. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    print("\n" + "="*70)
    print("🚀 ADX SIGNAL GENERATOR v1.0 - BTC ONLY")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE_NAME} (PUBLIC ENDPOINTS)")
    print(f"🔑 Auth Mode: {'Authenticated' if EXCHANGE.apiKey else 'Public (No API Keys)'}")
    print(f"\n📈 CONFIGURATION:")
    print(f"  • ⚡ INSTANT ALERTS: Signal sent immediately on detection")
    print(f"  • 🔓 NO API KEYS REQUIRED - Using public endpoints")
    print(f"  • Timeframe: 5 MINUTES")  # Updated to 5 minutes
    print(f"  • ADX Period: 21")
    print(f"  • ADX Threshold: > 25 (Strong Trend)")
    print(f"  • Cache System: Incremental OHLCV fetching")
    print(f"  • Max Candles Fetched: {CANDLES_TO_FETCH}")
    print(f"  • Cache Expiry: {CACHE_EXPIRY_SECONDS}s")
    print(f"  • API Call Interval: {API_CALL_INTERVAL}s")
    print(f"  • Scan Interval: {CHECK_INTERVAL}s")
    print(f"\n📊 SIGNAL LOGIC (INSTANT):")
    print(f"  • BUY: ADX(21) > 25 AND +DI > -DI (Uptrend)")
    print(f"  • SELL: ADX(21) > 25 AND -DI > +DI (Downtrend)")
    print(f"  • STRONG: ADX > 40 (Very Strong Trend)")
    print(f"  • 🚀 Alert sent on FIRST detection!")
    print(f"\n📊 MONITORING BTC/USDT ONLY:")
    print("-" * 70)
    print(f"  ⭐⭐⭐⭐⭐ BTC/USDT | High | Excellent")
    print("="*70 + "\n")

    # Get available symbols
    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)}/{len(SYMBOLS)} symbols on {EXCHANGE_NAME}")

    # Show unavailable symbols
    unavailable = [s for s in SYMBOLS if s not in EXCHANGE.markets]
    if unavailable:
        print(f"⚠️ {len(unavailable)} symbols not available:")
        for sym in unavailable:
            print(f"  • {sym}")
    print()

    # Startup alert
    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>ADX Bot v1.0 Started - BTC ONLY</b>\n\n"
            f"📊 <b>Exchange:</b> {EXCHANGE_NAME}\n"
            f"🔓 <b>Mode:</b> Public endpoints - No API keys required\n"
            f"⏱️ <b>Timeframe:</b> 5 Minutes\n"  # Updated to 5 minutes
            f"⚡ <b>Alert Mode:</b> INSTANT - Signal sent immediately on detection\n"
            f"📊 <b>Signal Logic:</b>\n"
            f"• BUY: ADX(21) > 25 AND +DI > -DI\n"
            f"• SELL: ADX(21) > 25 AND -DI > +DI\n"
            f"• STRONG: ADX > 40\n"
            f"🔍 <b>Monitoring:</b> BTC/USDT only\n"
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

            # Process ONLY BTC/USDT
            for i, symbol in enumerate(available_symbols):
                try:
                    # Rate limiting
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='5m',  # Updated to 5 minutes
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 30:
                        print(f"  ⚠️ {symbol}: Insufficient data ({len(df) if df is not None else 0} candles)")
                        continue

                    # Calculate ADX
                    adx_data = calculate_adx(df, period=21)

                    if adx_data is None:
                        print(f"  ⚠️ {symbol}: ADX calculation failed")
                        continue

                    # Get current values
                    current_price = df['close'].iloc[-1]
                    price_str = format_price(current_price)
                    current_adx = adx_data['current_adx']
                    plus_di = adx_data['current_plus_di']
                    minus_di = adx_data['current_minus_di']
                    direction = "UP" if adx_data['direction'] == 1 else "DOWN"
                    rating, volume, quality = get_rating(symbol)

                    # Show BTC details
                    print(f"  ⭐⭐⭐⭐⭐ {symbol:12} | {price_str:12} | "
                          f"ADX: {current_adx:6.2f} | +DI: {plus_di:6.2f} | -DI: {minus_di:6.2f} | {direction:4} | {quality}")

                    # Check for ADX signal
                    signal, strength = check_adx_signal(symbol, df, adx_data)

                    if signal:
                        print(f"  🎯 {symbol}: {signal} signal detected! (ADX={current_adx:.2f})")

                        # Update signal state
                        result = update_signal_state(symbol, signal, strength)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1

                            # SEND ALERT IMMEDIATELY!
                            emoji = "🟢" if signal == 'BUY' else "🔴"
                            strength_emoji = {
                                'STRONG': '💪',
                                'NORMAL': '✅'
                            }
                            rating_emoji = rating.split()[0]  # Get the stars

                            # Track that alert was sent
                            signal_tracker[symbol]['alert_sent'] = True

                            message = (
                                f"🚨 <b>IMMEDIATE {signal} SIGNAL</b> {strength_emoji.get(strength, '')}\n\n"
                                f"<b>Symbol:</b> {symbol} {rating_emoji}\n"
                                f"<b>Exchange:</b> {EXCHANGE_NAME}\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Strength:</b> {strength}\n"
                                f"<b>ADX(21):</b> {current_adx:.2f}\n"
                                f"<b>+DI:</b> {plus_di:.2f}\n"
                                f"<b>-DI:</b> {minus_di:.2f}\n"
                                f"<b>Trend:</b> {direction}\n"
                                f"<b>Quality:</b> {quality}\n\n"
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
                    traceback.print_exc()
                    continue

            # Update last check time
            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            # Cycle summary
            active = get_active_signals()

            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Exchange: {EXCHANGE_NAME}")
            print(f"  • Timeframe: 5 Minutes")  # Updated to 5 minutes
            print(f"  • Processed: {processed}/{len(available_symbols)} symbols")
            print(f"  • New Signals (Alert Sent): {new_signals}")
            print(f"  • Signals Ended: {ended_signals}")
            print(f"  • API Calls Saved: {api_calls_saved} (cumulative)")
            print(f"  • Active Signals: {len(active)}")

            if active:
                for sym, info in active.items():
                    rating, _, _ = get_rating(sym)
                    alert_status = "✅ ALERT SENT" if info['alert_sent'] else "⏳ PENDING"
                    print(f"    • {rating} {sym}: {info['signal']} ({info['strength']}) - {alert_status}")

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