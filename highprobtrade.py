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
    return "HMA + ADX Signal Generator for Delta Exchange is running!"

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
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'delta').lower()

# API Keys (Optional - for public data, no keys needed)
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
DELTA_API_KEY = os.environ.get('DELTA_API_KEY', '')
DELTA_API_SECRET = os.environ.get('DELTA_API_SECRET', '')

# Performance Configuration
API_CALL_INTERVAL = 1.5
CHECK_INTERVAL = 15  # 15 seconds
CANDLES_TO_FETCH = 200  # Increased for HMA and ADX calculations
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 200

# Trading pairs - Delta Exchange format (without '/')
SYMBOLS = [
    'BTCUSD',   # ⭐⭐⭐⭐⭐ Bitcoin
    'ETHUSD',   # ⭐⭐⭐⭐⭐ Ethereum
    'XRPUSD',   # ⭐⭐⭐⭐⭐ Ripple
    'SOLUSD',   # ⭐⭐⭐⭐⭐ Solana
    'DOGEUSD',  # ⭐⭐⭐⭐ Dogecoin
    'SUIUSD',   # ⭐⭐⭐⭐ Sui
    'HYPEUSD',  # ⭐⭐⭐⭐ Hype
    'XAUTUSD',  # ⭐⭐⭐⭐ Tether Gold
    'PAXGUSD',  # ⭐⭐⭐⭐ PAX Gold
    'ADAUSD',   # ⭐⭐⭐⭐ Cardano
    'DOTUSD',   # ⭐⭐⭐⭐ Polkadot
    'LINKUSD'   # ⭐⭐⭐⭐ Chainlink
]

# Global variables
last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0
ohlcv_cache = {}

def get_cached_ohlcv(exchange, symbol, timeframe='5m', limit=200):
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
                print(f"  ⚠️ {symbol}: Incremental fetch failed ({e})")

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

# Signal Tracker
signal_tracker = {}

def update_signal_state(symbol, new_signal, strength='NORMAL', indicators=None):
    """Update signal state and send alerts"""
    now = datetime.now()

    if symbol not in signal_tracker:
        signal_tracker[symbol] = {
            'current_signal': None,
            'active': False,
            'alert_sent': False,
            'last_signal_time': now,
            'signal_strength': 'NORMAL',
            'indicators': {}
        }

    tracker = signal_tracker[symbol]

    if new_signal and new_signal != tracker['current_signal']:
        if tracker['active']:
            print(f"  ⚠️ {symbol}: {tracker['current_signal']} signal ended")

        tracker['current_signal'] = new_signal
        tracker['active'] = True
        tracker['alert_sent'] = False
        tracker['last_signal_time'] = now
        tracker['signal_strength'] = strength
        tracker['indicators'] = indicators or {}

        return 'NEW_SIGNAL'

    elif new_signal and new_signal == tracker['current_signal']:
        if tracker['active'] and not tracker['alert_sent']:
            tracker['alert_sent'] = True
            return 'NEW_SIGNAL'
        return 'SAME_SIGNAL'

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
                'alert_sent': tracker['alert_sent'],
                'indicators': tracker['indicators']
            }
    return active

# Exchange Initialization
def init_exchange(exchange_name='delta'):
    try:
        config = {
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'adjustForTimeDifference': True
            }
        }

        if exchange_name == 'binance':
            if BINANCE_API_KEY and BINANCE_API_SECRET:
                config['apiKey'] = BINANCE_API_KEY
                config['secret'] = BINANCE_API_SECRET
            exchange = ccxt.binance(config)
        elif exchange_name == 'kraken':
            if KRAKEN_API_KEY and KRAKEN_API_SECRET:
                config['apiKey'] = KRAKEN_API_KEY
                config['secret'] = KRAKEN_API_SECRET
            exchange = ccxt.kraken(config)
        elif exchange_name == 'coinbase':
            if COINBASE_API_KEY and COINBASE_API_SECRET:
                config['apiKey'] = COINBASE_API_KEY
                config['secret'] = COINBASE_API_SECRET
            exchange = ccxt.coinbase(config)
        elif exchange_name == 'kucoin':
            if KUCOIN_API_KEY and KUCOIN_API_SECRET and KUCOIN_PASSWORD:
                config['apiKey'] = KUCOIN_API_KEY
                config['secret'] = KUCOIN_API_SECRET
                config['password'] = KUCOIN_PASSWORD
            exchange = ccxt.kucoin(config)
        elif exchange_name == 'bybit':
            if BYBIT_API_KEY and BYBIT_API_SECRET:
                config['apiKey'] = BYBIT_API_KEY
                config['secret'] = BYBIT_API_SECRET
            exchange = ccxt.bybit(config)
        elif exchange_name == 'delta':
            # Delta Exchange - Public access (no API keys required for public data)
            if DELTA_API_KEY and DELTA_API_SECRET:
                config['apiKey'] = DELTA_API_KEY
                config['secret'] = DELTA_API_SECRET
            # Delta uses a specific configuration
            config['options'] = {
                'defaultType': 'spot',
                'adjustForTimeDifference': True,
                'fetchOHLCV': {
                    'method': 'public/get_candles'  # Delta endpoint for public candles
                }
            }
            exchange = ccxt.delta(config)
        else:
            exchange_class = getattr(ccxt, exchange_name)
            exchange = exchange_class(config)

        exchange.load_markets()
        print(f"✅ Connected to {exchange_name.capitalize()} successfully")
        return exchange

    except Exception as e:
        print(f"❌ Error initializing {exchange_name}: {e}")
        return None

def get_available_exchange():
    # Try Delta first as primary, then fallback to others
    exchanges_to_try = [PRIMARY_EXCHANGE, 'delta', 'binance', 'bybit', 'kucoin', 'kraken', 'coinbase']
    seen = set()
    exchanges_to_try = [x for x in exchanges_to_try if not (x in seen or seen.add(x))]

    for exchange_name in exchanges_to_try:
        exchange = init_exchange(exchange_name)
        if exchange:
            return exchange
        time.sleep(2)

    print("❌ No exchange available. Exiting.")
    exit(1)

EXCHANGE = get_available_exchange()
EXCHANGE_NAME = EXCHANGE.name.capitalize()

# ============================================
# 4. HEIKIN ASHI CALCULATION
# ============================================

def calculate_heikin_ashi(df):
    """Convert regular candles to Heikin Ashi candles"""
    try:
        ha_df = pd.DataFrame(index=df.index)

        # Calculate Heikin Ashi
        ha_df['ha_close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4

        # First HA open is regular open
        ha_df['ha_open'] = df['open'].copy()

        # Calculate HA open sequentially
        for i in range(1, len(df)):
            ha_df.loc[ha_df.index[i], 'ha_open'] = (
                ha_df.loc[ha_df.index[i-1], 'ha_open'] + 
                ha_df.loc[ha_df.index[i-1], 'ha_close']
            ) / 2

        ha_df['ha_high'] = df[['high', 'low']].max(axis=1)
        ha_df['ha_low'] = df[['high', 'low']].min(axis=1)

        # Update high/low based on HA open/close
        ha_df['ha_high'] = ha_df[['ha_high', 'ha_open', 'ha_close']].max(axis=1)
        ha_df['ha_low'] = ha_df[['ha_low', 'ha_open', 'ha_close']].min(axis=1)

        return ha_df
    except Exception as e:
        print(f"  ❌ Heikin Ashi calculation error: {e}")
        return None

# ============================================
# 5. HULL MOVING AVERAGE CALCULATION
# ============================================

def calculate_hma(data, period):
    """
    Calculate Hull Moving Average
    HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
    """
    try:
        # Weighted Moving Average function
        def wma(series, length):
            weights = np.arange(1, length + 1)
            return series.rolling(length).apply(
                lambda x: np.sum(weights * x) / weights.sum(),
                raw=True
            )

        half_period = int(period / 2)
        sqrt_period = int(np.sqrt(period))

        # Calculate HMA
        wma_half = wma(data, half_period)
        wma_full = wma(data, period)
        hma_raw = 2 * wma_half - wma_full
        hma = wma(hma_raw, sqrt_period)

        return hma
    except Exception as e:
        print(f"  ❌ HMA calculation error for period {period}: {e}")
        return None

def calculate_all_hmas(ha_df):
    """Calculate HMA 100, HMA 52, and HMA 9 on Heikin Ashi close"""
    try:
        ha_close = ha_df['ha_close']

        hma_100 = calculate_hma(ha_close, 100)
        hma_52 = calculate_hma(ha_close, 52)

        return {
            'hma_100': hma_100,
            'hma_52': hma_52,
            'current_hma_100': hma_100.iloc[-1] if not pd.isna(hma_100.iloc[-1]) else 0,
            'current_hma_52': hma_52.iloc[-1] if not pd.isna(hma_52.iloc[-1]) else 0
        }
    except Exception as e:
        print(f"  ❌ HMA calculation error: {e}")
        return None

# ============================================
# 6. ADX CALCULATION (Smoothing 14, DI Length 14)
# ============================================

def calculate_adx(df, period=14):
    """Calculate ADX with +DI and -DI (smoothing 14, DI length 14)"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        # True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # Directional Movements
        up_move = high - high.shift()
        down_move = low.shift() - low
        
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
        
        # Smoothed averages (using Wilder's smoothing - similar to RMA)
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        
        # DX and ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        
        return {
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'current_adx': adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 0,
            'current_plus_di': plus_di.iloc[-1] if not pd.isna(plus_di.iloc[-1]) else 0,
            'current_minus_di': minus_di.iloc[-1] if not pd.isna(minus_di.iloc[-1]) else 0
        }
    except Exception as e:
        print(f"  ❌ ADX calculation error: {e}")
        return None

# ============================================
# 7. SIGNAL DETECTION WITH HMA + ADX
# ============================================

# Track last alert to prevent spam
last_alert = {}

def check_combined_signal(symbol, df):
    """
    Signal detection combining HMA and ADX:
    
    Rule 1: HMA(52) > HMA(100) in Heikin Ashi
      - If ADX > 27 → BUY
      - Else → SELL
    
    Rule 2: HMA(52) < HMA(100) in Heikin Ashi
      - If ADX > 27 → SELL
      - Else → BUY
    """
    try:
        # Calculate Heikin Ashi
        ha_df = calculate_heikin_ashi(df)
        if ha_df is None:
            return None, None, None

        # Calculate HMAs
        hma_data = calculate_all_hmas(ha_df)
        if hma_data is None:
            return None, None, None

        # Calculate ADX
        adx_data = calculate_adx(df, period=14)
        if adx_data is None:
            return None, None, None

        # Get current values
        hma_52 = hma_data['current_hma_52']
        hma_100 = hma_data['current_hma_100']
        adx = adx_data['current_adx']
        plus_di = adx_data['current_plus_di']
        minus_di = adx_data['current_minus_di']
        
        # Current price (Heikin Ashi close)
        current_price = ha_df['ha_close'].iloc[-1]
        
        # Get current timestamp for alert tracking
        current_ts = df['ts'].iloc[-1]
        
        # Determine HMA alignment
        hma_alignment = "BULLISH" if hma_52 > hma_100 else "BEARISH"
        
        # Determine signal based on rules
        signal = None
        strength = 'NORMAL'
        indicators = {
            'hma_52': hma_52,
            'hma_100': hma_100,
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'current_price': current_price,
            'hma_alignment': hma_alignment
        }
        
        # Rule 1: HMA52 > HMA100 (Bullish alignment)
        if hma_52 > hma_100:
            if adx > 27:
                signal = 'BUY'
                strength = 'STRONG'
                indicators['reason'] = f'HMA52 > HMA100 (Bullish) AND ADX {adx:.1f} > 27 → BUY'
                indicators['signal_type'] = 'BUY'
            else:
                signal = 'SELL'
                strength = 'NORMAL'
                indicators['reason'] = f'HMA52 > HMA100 (Bullish) BUT ADX {adx:.1f} ≤ 27 → SELL'
                indicators['signal_type'] = 'SELL'
        
        # Rule 2: HMA52 < HMA100 (Bearish alignment)
        elif hma_52 < hma_100:
            if adx > 27:
                signal = 'SELL'
                strength = 'STRONG'
                indicators['reason'] = f'HMA52 < HMA100 (Bearish) AND ADX {adx:.1f} > 27 → SELL'
                indicators['signal_type'] = 'SELL'
            else:
                signal = 'BUY'
                strength = 'NORMAL'
                indicators['reason'] = f'HMA52 < HMA100 (Bearish) BUT ADX {adx:.1f} ≤ 27 → BUY'
                indicators['signal_type'] = 'BUY'
        
        # Check if already alerted for this candle
        if symbol in last_alert and last_alert[symbol] == current_ts:
            return None, None, None
        
        # Only alert on new signals or when signal changes
        if signal:
            last_alert[symbol] = current_ts
            return signal, strength, indicators
        
        return None, None, None

    except Exception as e:
        print(f"  ❌ Combined signal error for {symbol}: {e}")
        return None, None, None

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
            print(f"  ❌ Telegram error: {response.status_code}")
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
        'BTCUSD': ('⭐⭐⭐⭐⭐', 'Bitcoin', 'Excellent'),
        'ETHUSD': ('⭐⭐⭐⭐⭐', 'Ethereum', 'Excellent'),
        'XRPUSD': ('⭐⭐⭐⭐⭐', 'Ripple', 'Excellent'),
        'SOLUSD': ('⭐⭐⭐⭐⭐', 'Solana', 'Excellent'),
        'DOGEUSD': ('⭐⭐⭐⭐', 'Dogecoin', 'Very Good'),
        'SUIUSD': ('⭐⭐⭐⭐', 'Sui', 'Very Good'),
        'HYPEUSD': ('⭐⭐⭐⭐', 'Hype', 'Very Good'),
        'XAUTUSD': ('⭐⭐⭐⭐', 'Tether Gold', 'Very Good'),
        'PAXGUSD': ('⭐⭐⭐⭐', 'PAX Gold', 'Very Good'),
        'ADAUSD': ('⭐⭐⭐⭐', 'Cardano', 'Very Good'),
        'DOTUSD': ('⭐⭐⭐⭐', 'Polkadot', 'Very Good'),
        'LINKUSD': ('⭐⭐⭐⭐', 'Chainlink', 'Very Good')
    }
    return ratings.get(symbol, ('⭐⭐⭐', 'Medium', 'Good'))

def get_strength_emoji(strength):
    """Get emoji for signal strength"""
    emojis = {
        'STRONG': '🔥💪🚀',
        'NORMAL': '✅'
    }
    return emojis.get(strength, '✅')

# 9. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    print("\n" + "="*70)
    print("🚀 DELTA EXCHANGE - HMA + ADX SIGNAL GENERATOR")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE_NAME}")
    print(f"\n📈 STRATEGY DETAILS:")
    print(f"  • Timeframe: 5 Minutes")
    print(f"  • Candles: Heikin Ashi (Smoother Price Action)")
    print(f"  • HMA 52 (Medium-term trend)")
    print(f"  • HMA 100 (Long-term trend)")
    print(f"  • ADX (Smoothing 14, DI Length 14)")
    print(f"\n📊 SIGNAL RULES:")
    print(f"  📈 When HMA52 > HMA100 (Bullish Alignment):")
    print(f"    • ADX > 27 → 🟢 BUY (Strong Trend)")
    print(f"    • ADX ≤ 27 → 🔴 SELL (Weak/No Trend)")
    print(f"  📉 When HMA52 < HMA100 (Bearish Alignment):")
    print(f"    • ADX > 27 → 🔴 SELL (Strong Trend)")
    print(f"    • ADX ≤ 27 → 🟢 BUY (Weak/No Trend)")
    print(f"\n⏱️ Check Interval: 15 seconds")
    print(f"\n📊 MONITORING {len(SYMBOLS)} COINS ON DELTA EXCHANGE:")
    print("-" * 70)
    for symbol in SYMBOLS:
        rating, name, quality = get_rating(symbol)
        print(f"  {rating} {symbol:12} | {name:12} | {quality}")
    print("="*70 + "\n")

    available_symbols = []
    for symbol in SYMBOLS:
        try:
            if symbol in EXCHANGE.markets:
                available_symbols.append(symbol)
            else:
                # Try to find if symbol exists with different format
                found = False
                for market in EXCHANGE.markets:
                    if market.replace('/', '') == symbol or market.replace('_', '') == symbol:
                        available_symbols.append(market)
                        found = True
                        break
                if not found:
                    print(f"  ⚠️ {symbol} not found on {EXCHANGE_NAME}")
        except Exception as e:
            print(f"  ⚠️ Error checking {symbol}: {e}")
    
    print(f"✅ Monitoring {len(available_symbols)}/{len(SYMBOLS)} symbols on Delta Exchange")

    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>Delta Exchange - HMA + ADX Bot Started</b>\n\n"
            f"📊 <b>Exchange:</b> {EXCHANGE_NAME}\n"
            f"⏱️ <b>Timeframe:</b> 5 Minutes\n"
            f"⏱️ <b>Check Interval:</b> 15 seconds\n"
            f"📈 <b>Strategy:</b>\n"
            f"  • Heikin Ashi Candles\n"
            f"  • HMA 52 - Medium MA\n"
            f"  • HMA 100 - Slow MA\n"
            f"  • ADX (14,14) - Trend Strength\n"
            f"📊 <b>Rules:</b>\n"
            f"  📈 Bullish (HMA52 > HMA100):\n"
            f"    • ADX > 27 → BUY\n"
            f"    • ADX ≤ 27 → SELL\n"
            f"  📉 Bearish (HMA52 < HMA100):\n"
            f"    • ADX > 27 → SELL\n"
            f"    • ADX ≤ 27 → BUY\n"
            f"🔍 <b>Monitoring:</b> {len(available_symbols)} coins on Delta Exchange"
        )

    while True:
        try:
            cycle_count += 1
            new_signals = 0

            print(f"\n{'='*70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*70}")

            if cycle_count % 10 == 0:
                cleanup_cache()

            for i, symbol in enumerate(available_symbols):
                try:
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='5m', 
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 100:  # Need enough data for calculations
                        print(f"  ⚠️ {symbol}: Insufficient data (need 100+ candles)")
                        continue

                    # Check combined signal
                    signal, strength, indicators = check_combined_signal(symbol, df)

                    current_price = df['close'].iloc[-1]  # Original price for display
                    price_str = format_price(current_price)
                    rating, name, quality = get_rating(symbol)

                    # Display current status
                    if signal:
                        emoji = "🟢" if signal == 'BUY' else "🔴"
                        adx_status = "✅" if indicators['adx'] > 27 else "❌"
                        print(f"  🎯 {rating} {symbol:12} | {price_str:12} | "
                              f"SIGNAL: {signal} {get_strength_emoji(strength)} | "
                              f"ADX: {indicators['adx']:.1f} {adx_status} | "
                              f"HMA: {indicators['hma_alignment']}")

                        result = update_signal_state(symbol, signal, strength, indicators)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1
                            signal_tracker[symbol]['alert_sent'] = True

                            # Build detailed alert message
                            message = (
                                f"🚨 <b>{signal} SIGNAL DETECTED</b> {get_strength_emoji(strength)}\n\n"
                                f"<b>Symbol:</b> {symbol} ({name}) {rating.split()[0]}\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Heikin Ashi Close:</b> {format_price(indicators['current_price'])}\n"
                                f"<b>Strength:</b> {strength}\n"
                                f"<b>Quality:</b> {quality}\n"
                                f"<b>Exchange:</b> {EXCHANGE_NAME}\n\n"
                                f"<b>📊 Indicators:</b>\n"
                                f"  • HMA 52: {indicators['hma_52']:.4f}\n"
                                f"  • HMA 100: {indicators['hma_100']:.4f}\n"
                                f"  • HMA Alignment: {indicators['hma_alignment']}\n"
                                f"  • ADX: {indicators['adx']:.1f} {'(> 27 ✅)' if indicators['adx'] > 27 else '(≤ 27 ❌)'}\n"
                                f"  • +DI: {indicators['plus_di']:.1f}\n"
                                f"  • -DI: {indicators['minus_di']:.1f}\n\n"
                                f"<b>📈 Decision:</b>\n"
                                f"  {indicators['reason']}\n\n"
                                f"<b>⏱️ Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )

                            if send_alert(message):
                                print(f"  🚨 ALERT SENT: {symbol} {signal} ({strength})")
                            else:
                                print(f"  ❌ Alert FAILED for {symbol}")
                    else:
                        # Show status for all coins
                        adx_value = "N/A"
                        try:
                            # Try to show ADX for monitoring
                            ha_df = calculate_heikin_ashi(df)
                            if ha_df is not None:
                                adx_data = calculate_adx(df, period=14)
                                if adx_data is not None:
                                    adx_value = f"{adx_data['current_adx']:.1f}"
                        except:
                            pass
                        print(f"  {rating} {symbol:12} | {price_str:12} | "
                              f"ADX: {adx_value} | Monitoring...")

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    continue

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            active = get_active_signals()
            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Processed: {len(available_symbols)} symbols")
            print(f"  • New Signals: {new_signals}")
            print(f"  • Active Signals: {len(active)}")
            if active:
                for sym, info in active.items():
                    rating, name, _ = get_rating(sym)
                    print(f"    • {rating} {sym} ({name}): {info['signal']} ({info['strength']})")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            traceback.print_exc()
            time.sleep(60)

# 10. Start Bot
print("\n🚀 Starting Delta Exchange bot...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# 11. Start Flask Server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port)
