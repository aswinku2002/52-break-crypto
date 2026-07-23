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
    return "VWMA-RSI Signal Generator is running!"

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
CANDLES_TO_FETCH = 100         # Need enough for indicators
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 100

# Signal Settings - INSTANT ALERTS
CONFIRMATION_CYCLES_REQUIRED = 1   # 1 = Instant alert on first detection
RESET_CYCLES_REQUIRED = 2          # Keep for reset protection

# Trading pairs to monitor - ONLY ETH/USDT
SYMBOLS = [
    'ETH/USDT'   # ⭐⭐⭐⭐⭐ Only monitoring Ethereum
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

# 4. Heikin Ashi Calculation
def calculate_heikin_ashi(df):
    """
    Calculate Heikin Ashi candles from standard OHLCV data
    """
    try:
        ha_df = pd.DataFrame(index=df.index)
        
        # Heikin Ashi calculations
        ha_df['ha_close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
        
        # Initialize ha_open with first close
        ha_df['ha_open'] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
        
        # Calculate ha_open for all rows
        for i in range(1, len(df)):
            ha_df.loc[ha_df.index[i], 'ha_open'] = (
                ha_df.loc[ha_df.index[i-1], 'ha_open'] + 
                ha_df.loc[ha_df.index[i-1], 'ha_close']
            ) / 2
        
        ha_df['ha_high'] = df[['high', 'open', 'close']].max(axis=1)
        ha_df['ha_low'] = df[['low', 'open', 'close']].min(axis=1)
        
        return ha_df
    except Exception as e:
        print(f"  ❌ Heikin Ashi calculation error: {e}")
        return None

# 5. VWMA Calculation
def calculate_vwma(df, ha_df, period):
    """
    Calculate Volume Weighted Moving Average using Heikin Ashi data
    """
    try:
        # Use Heikin Ashi close for VWMA
        ha_close = ha_df['ha_close']
        volume = df['vol']
        
        # VWMA = SUM(Price * Volume) / SUM(Volume)
        vwma = (ha_close * volume).rolling(window=period).sum() / volume.rolling(window=period).sum()
        
        return vwma
    except Exception as e:
        print(f"  ❌ VWMA calculation error: {e}")
        return None

# 6. RSI Calculation
def calculate_rsi(df, period=7):
    """
    Calculate RSI using Heikin Ashi close
    """
    try:
        # Use Heikin Ashi close for RSI
        ha_close = df['ha_close']
        
        delta = ha_close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    except Exception as e:
        print(f"  ❌ RSI calculation error: {e}")
        return None

# 7. Signal Detection - VWMA + RSI Strategy
def check_vwma_rsi_signal(symbol, df, ha_df, vwma_9, vwma_26, rsi):
    """
    Check VWMA + RSI strategy conditions:
    - BUY: RSI(7) > 69 AND VWMA(9) > VWMA(26)
    - SELL: RSI(7) < 31 AND VWMA(9) < VWMA(26)
    """
    try:
        if ha_df is None or len(ha_df) < 30:
            return None, None
        
        # Get latest values
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 0
        current_vwma_9 = vwma_9.iloc[-1] if not pd.isna(vwma_9.iloc[-1]) else 0
        current_vwma_26 = vwma_26.iloc[-1] if not pd.isna(vwma_26.iloc[-1]) else 0
        
        # Check for NaN
        if pd.isna(current_rsi) or pd.isna(current_vwma_9) or pd.isna(current_vwma_26):
            return None, None
        
        # BUY Condition: RSI > 69 AND VWMA9 > VWMA26
        if current_rsi > 69 and current_vwma_9 > current_vwma_26:
            # Determine strength based on RSI
            if current_rsi > 80:
                return 'BUY', 'VERY STRONG'
            elif current_rsi > 75:
                return 'BUY', 'STRONG'
            else:
                return 'BUY', 'NORMAL'
        
        # SELL Condition: RSI < 31 AND VWMA9 < VWMA26
        elif current_rsi < 31 and current_vwma_9 < current_vwma_26:
            # Determine strength based on RSI
            if current_rsi < 20:
                return 'SELL', 'VERY STRONG'
            elif current_rsi < 25:
                return 'SELL', 'STRONG'
            else:
                return 'SELL', 'NORMAL'
        
        return None, None
        
    except Exception as e:
        print(f"  ❌ VWMA-RSI signal detection error for {symbol}: {e}")
        return None, None

# 8. Alert System
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
        'ETH/USDT': ('⭐⭐⭐⭐⭐', 'High', 'Excellent')
    }
    return ratings.get(symbol, ('⭐⭐⭐', 'Medium', 'Good'))

# 9. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    print("\n" + "="*70)
    print("🚀 VWMA-RSI SIGNAL GENERATOR v2.0 - ETH/USDT ONLY")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE_NAME} (PUBLIC ENDPOINTS)")
    print(f"🔑 Auth Mode: {'Authenticated' if EXCHANGE.apiKey else 'Public (No API Keys)'}")
    print(f"\n📈 CONFIGURATION:")
    print(f"  • ⚡ INSTANT ALERTS: Signal sent immediately on detection")
    print(f"  • 🔓 NO API KEYS REQUIRED - Using public endpoints")
    print(f"  • Timeframe: 5 MINUTES")
    print(f"  • VWMA Periods: 9 and 26")
    print(f"  • RSI Period: 7")
    print(f"  • Heikin Ashi: Enabled")
    print(f"  • BUY: RSI > 69 AND VWMA9 > VWMA26")
    print(f"  • SELL: RSI < 31 AND VWMA9 < VWMA26")
    print(f"  • Cache System: Incremental OHLCV fetching")
    print(f"  • Max Candles Fetched: {CANDLES_TO_FETCH}")
    print(f"  • Cache Expiry: {CACHE_EXPIRY_SECONDS}s")
    print(f"  • API Call Interval: {API_CALL_INTERVAL}s")
    print(f"  • Scan Interval: {CHECK_INTERVAL}s")
    print(f"\n📊 SIGNAL LOGIC (INSTANT):")
    print(f"  • BUY: RSI(7) > 69 AND VWMA(9) > VWMA(26) - Uptrend momentum")
    print(f"  • SELL: RSI(7) < 31 AND VWMA(9) < VWMA(26) - Downtrend momentum")
    print(f"  • STRONG: RSI > 75 (BUY) or RSI < 25 (SELL)")
    print(f"  • VERY STRONG: RSI > 80 (BUY) or RSI < 20 (SELL)")
    print(f"  • 🚀 Alert sent on FIRST detection!")
    print(f"\n📊 MONITORING: ETH/USDT ONLY")
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
            f"✅ <b>VWMA-RSI Bot v2.0 Started - ETH/USDT ONLY</b>\n\n"
            f"📊 <b>Exchange:</b> {EXCHANGE_NAME}\n"
            f"🔓 <b>Mode:</b> Public endpoints - No API keys required\n"
            f"⏱️ <b>Timeframe:</b> 5 Minutes\n"
            f"⚡ <b>Alert Mode:</b> INSTANT - Signal sent immediately on detection\n"
            f"📊 <b>Signal Logic:</b>\n"
            f"• BUY: RSI(7) > 69 AND VWMA(9) > VWMA(26)\n"
            f"• SELL: RSI(7) < 31 AND VWMA(9) < VWMA(26)\n"
            f"• STRONG: RSI > 75 (BUY) or RSI < 25 (SELL)\n"
            f"🔍 <b>Monitoring:</b> ETH/USDT only\n"
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

            # Process ETH/USDT only
            for i, symbol in enumerate(available_symbols):
                try:
                    # Rate limiting
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='5m', 
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 30:
                        print(f"  ⚠️ {symbol}: Insufficient data ({len(df) if df is not None else 0} candles)")
                        continue

                    # Calculate Heikin Ashi
                    ha_df = calculate_heikin_ashi(df)
                    
                    if ha_df is None:
                        print(f"  ⚠️ {symbol}: Heikin Ashi calculation failed")
                        continue

                    # Calculate VWMA
                    vwma_9 = calculate_vwma(df, ha_df, 9)
                    vwma_26 = calculate_vwma(df, ha_df, 26)
                    
                    if vwma_9 is None or vwma_26 is None:
                        print(f"  ⚠️ {symbol}: VWMA calculation failed")
                        continue

                    # Calculate RSI
                    rsi = calculate_rsi(ha_df, 7)
                    
                    if rsi is None:
                        print(f"  ⚠️ {symbol}: RSI calculation failed")
                        continue

                    # Get current values
                    current_price = df['close'].iloc[-1]
                    price_str = format_price(current_price)
                    current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 0
                    current_vwma_9 = vwma_9.iloc[-1] if not pd.isna(vwma_9.iloc[-1]) else 0
                    current_vwma_26 = vwma_26.iloc[-1] if not pd.isna(vwma_26.iloc[-1]) else 0
                    
                    # Determine trend
                    trend = "🟢 BULLISH" if current_vwma_9 > current_vwma_26 else "🔴 BEARISH"
                    
                    rating, volume, quality = get_rating(symbol)

                    # Show ETH/USDT data
                    print(f"  {rating} {symbol:12} | {price_str:12} | "
                          f"RSI: {current_rsi:6.2f} | VWMA9: {current_vwma_9:8.2f} | VWMA26: {current_vwma_26:8.2f} | {trend} | {quality}")

                    # Check for VWMA + RSI signal
                    signal, strength = check_vwma_rsi_signal(symbol, df, ha_df, vwma_9, vwma_26, rsi)

                    if signal:
                        print(f"  🎯 {symbol}: {signal} signal detected! (RSI={current_rsi:.2f})")

                        # Update signal state
                        result = update_signal_state(symbol, signal, strength)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1

                            # SEND ALERT IMMEDIATELY!
                            emoji = "🟢" if signal == 'BUY' else "🔴"
                            strength_emoji = {
                                'VERY STRONG': '💪💪',
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
                                f"<b>RSI(7):</b> {current_rsi:.2f}\n"
                                f"<b>VWMA(9):</b> {current_vwma_9:.4f}\n"
                                f"<b>VWMA(26):</b> {current_vwma_26:.4f}\n"
                                f"<b>Trend:</b> {trend}\n"
                                f"<b>Quality:</b> {quality}\n\n"
                                f"<b>Signal Conditions:</b>\n"
                                f"• {'RSI > 69 ✅' if signal == 'BUY' else f'RSI < 31 ✅'}\n"
                                f"• {'VWMA9 > VWMA26 ✅' if signal == 'BUY' else 'VWMA9 < VWMA26 ✅'}\n\n"
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
            print(f"  • Timeframe: 5 Minutes")
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

# 10. Start Bot
print("\n🚀 Starting bot...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# 11. Start Flask Server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port)
