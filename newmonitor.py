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
    return "HMA Crossover Signal Generator is running!"

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

# API Keys
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
API_CALL_INTERVAL = 1.0
CHECK_INTERVAL = 60  # 60 seconds
CANDLES_TO_FETCH = 500  # Increased for HMA 416 calculation
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 500

# Trading pairs - TOP 12 COINS
SYMBOLS = [
    'BTC/USDT',   # ⭐⭐⭐⭐⭐ High
    'ETH/USDT',   # ⭐⭐⭐⭐⭐ High
    'SOL/USDT',   # ⭐⭐⭐⭐⭐ Very High
    'HYPE/USDT',  # ⭐⭐⭐⭐⭐ Extremely High
    'DOGE/USDT',  # ⭐⭐⭐⭐ Very High
    'XRP/USDT',   # ⭐⭐⭐⭐ High
    'SUI/USDT',   # ⭐⭐⭐⭐ High
    'ADA/USDT',   # ⭐⭐⭐⭐ Cardano
    'DOT/USDT',   # ⭐⭐⭐⭐ Polkadot
    'LINK/USDT',  # ⭐⭐⭐⭐ Chainlink
    'XAUT/USDT',  # ⭐⭐⭐⭐ Gold Token
    'PAXG/USDT'   # ⭐⭐⭐⭐ Gold Token
]

# Global variables
last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0
ohlcv_cache = {}

def get_cached_ohlcv(exchange, symbol, timeframe='3m', limit=500):
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

# Signal Tracker - Track crossovers
signal_tracker = {}

def update_signal_state(symbol, signal_type, indicators=None):
    """Update signal state for crossovers"""
    now = datetime.now()
    
    if symbol not in signal_tracker:
        signal_tracker[symbol] = {
            'current_signal': None,
            'active': False,
            'last_cross_time': now,
            'indicators': {}
        }
    
    tracker = signal_tracker[symbol]
    
    # Check if it's a new crossover
    if signal_type and signal_type != tracker['current_signal']:
        tracker['current_signal'] = signal_type
        tracker['active'] = True
        tracker['last_cross_time'] = now
        tracker['indicators'] = indicators or {}
        return 'NEW_CROSS'
    elif signal_type and signal_type == tracker['current_signal']:
        return 'SAME_CROSS'
    else:
        return None

def get_active_signals():
    """Get all currently active signals"""
    active = {}
    for symbol, tracker in signal_tracker.items():
        if tracker['active']:
            active[symbol] = {
                'signal': tracker['current_signal'],
                'cross_time': tracker['last_cross_time'],
                'indicators': tracker['indicators']
            }
    return active

# Exchange Initialization
def init_exchange(exchange_name='binance'):
    try:
        config = {
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
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
    exchanges_to_try = [PRIMARY_EXCHANGE, 'binance', 'kraken', 'coinbase', 'kucoin', 'bybit']
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
    """Calculate HMA 416 and HMA 52 on Heikin Ashi close"""
    try:
        ha_close = ha_df['ha_close']

        hma_416 = calculate_hma(ha_close, 416)
        hma_52 = calculate_hma(ha_close, 52)

        return {
            'hma_416': hma_416,
            'hma_52': hma_52,
            'current_hma_416': hma_416.iloc[-1] if not pd.isna(hma_416.iloc[-1]) else 0,
            'current_hma_52': hma_52.iloc[-1] if not pd.isna(hma_52.iloc[-1]) else 0,
            'prev_hma_416': hma_416.iloc[-2] if len(hma_416) > 1 and not pd.isna(hma_416.iloc[-2]) else 0,
            'prev_hma_52': hma_52.iloc[-2] if len(hma_52) > 1 and not pd.isna(hma_52.iloc[-2]) else 0
        }
    except Exception as e:
        print(f"  ❌ HMA calculation error: {e}")
        return None

# ============================================
# 6. SIGNAL DETECTION - HMA CROSSOVER ONLY
# ============================================

# Track last alert to prevent spam
last_alert = {}

def check_hma_crossover(symbol, df):
    """
    Detect HMA 416 and HMA 52 crossovers on Heikin Ashi
    
    Alert when:
    - HMA 416 crosses ABOVE HMA 52 (Bullish Crossover)
    - HMA 416 crosses BELOW HMA 52 (Bearish Crossover)
    """
    try:
        # Calculate Heikin Ashi
        ha_df = calculate_heikin_ashi(df)
        if ha_df is None:
            return None, None

        # Calculate HMAs
        hma_data = calculate_all_hmas(ha_df)
        if hma_data is None:
            return None, None

        # Get current and previous values
        current_hma_416 = hma_data['current_hma_416']
        current_hma_52 = hma_data['current_hma_52']
        prev_hma_416 = hma_data['prev_hma_416']
        prev_hma_52 = hma_data['prev_hma_52']
        
        # Get current price for display
        current_price = df['close'].iloc[-1]
        
        # Get current timestamp for alert tracking
        current_ts = df['ts'].iloc[-1]
        
        # Check for crossover
        signal = None
        indicators = {
            'hma_416': current_hma_416,
            'hma_52': current_hma_52,
            'prev_hma_416': prev_hma_416,
            'prev_hma_52': prev_hma_52,
            'current_price': current_price,
            'ha_price': ha_df['ha_close'].iloc[-1],
            'hma_416_greater': current_hma_416 > current_hma_52,
            'hma_52_greater': current_hma_52 > current_hma_416
        }
        
        # Check if already alerted for this candle
        if symbol in last_alert and last_alert[symbol] == current_ts:
            return None, None
        
        # Detect Bullish Crossover: HMA 416 crosses above HMA 52
        if prev_hma_416 <= prev_hma_52 and current_hma_416 > current_hma_52:
            signal = 'BULLISH CROSSOVER'
            indicators['reason'] = f'HMA 416 ({current_hma_416:.4f}) crossed ABOVE HMA 52 ({current_hma_52:.4f})'
            indicators['signal_type'] = 'BULLISH'
            
        # Detect Bearish Crossover: HMA 416 crosses below HMA 52
        elif prev_hma_416 >= prev_hma_52 and current_hma_416 < current_hma_52:
            signal = 'BEARISH CROSSOVER'
            indicators['reason'] = f'HMA 416 ({current_hma_416:.4f}) crossed BELOW HMA 52 ({current_hma_52:.4f})'
            indicators['signal_type'] = 'BEARISH'
        
        # Only alert on new crossovers
        if signal:
            last_alert[symbol] = current_ts
            return signal, indicators
        
        return None, None

    except Exception as e:
        print(f"  ❌ HMA crossover error for {symbol}: {e}")
        return None, None

# 7. Alert System
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
        'BTC/USDT': ('⭐⭐⭐⭐⭐', 'High', 'Excellent'),
        'ETH/USDT': ('⭐⭐⭐⭐⭐', 'High', 'Excellent'),
        'SOL/USDT': ('⭐⭐⭐⭐⭐', 'Very High', 'Excellent'),
        'HYPE/USDT': ('⭐⭐⭐⭐⭐', 'Extremely High', 'Excellent'),
        'DOGE/USDT': ('⭐⭐⭐⭐', 'Very High', 'Excellent'),
        'XRP/USDT': ('⭐⭐⭐⭐', 'High', 'Very Good'),
        'SUI/USDT': ('⭐⭐⭐⭐', 'High', 'Very Good'),
        'ADA/USDT': ('⭐⭐⭐⭐', 'High', 'Very Good'),
        'DOT/USDT': ('⭐⭐⭐⭐', 'High', 'Very Good'),
        'LINK/USDT': ('⭐⭐⭐⭐', 'High', 'Very Good'),
        'XAUT/USDT': ('⭐⭐⭐⭐', 'Gold', 'Very Good'),
        'PAXG/USDT': ('⭐⭐⭐⭐', 'Gold', 'Very Good')
    }
    return ratings.get(symbol, ('⭐⭐⭐', 'Medium', 'Good'))

def get_cross_emoji(signal_type):
    """Get emoji for crossover type"""
    if signal_type == 'BULLISH':
        return '🚀📈'
    elif signal_type == 'BEARISH':
        return '🔻📉'
    return '📊'

# 8. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    print("\n" + "="*70)
    print("🚀 HMA CROSSOVER SIGNAL GENERATOR")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE_NAME}")
    print(f"\n📈 STRATEGY DETAILS:")
    print(f"  • Timeframe: 3 Minutes")
    print(f"  • Candles: Heikin Ashi (Smoother Price Action)")
    print(f"  • HMA 416 (Long-term trend)")
    print(f"  • HMA 52 (Medium-term trend)")
    print(f"\n📊 SIGNAL RULES:")
    print(f"  🟢 When HMA 416 crosses ABOVE HMA 52 → BULLISH CROSSOVER")
    print(f"  🔴 When HMA 416 crosses BELOW HMA 52 → BEARISH CROSSOVER")
    print(f"\n⏱️ Check Interval: 60 seconds")
    print(f"\n📊 MONITORING {len(SYMBOLS)} TOP COINS:")
    print("-" * 70)
    for symbol in SYMBOLS:
        rating, volume, quality = get_rating(symbol)
        print(f"  {rating} {symbol:12} | {volume:12} | {quality}")
    print("="*70 + "\n")

    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)}/{len(SYMBOLS)} symbols")

    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>HMA CROSSOVER Bot Started</b>\n\n"
            f"📊 <b>Exchange:</b> {EXCHANGE_NAME}\n"
            f"⏱️ <b>Timeframe:</b> 3 Minutes\n"
            f"⏱️ <b>Check Interval:</b> 60 seconds\n"
            f"📈 <b>Strategy:</b>\n"
            f"  • Heikin Ashi Candles\n"
            f"  • HMA 416 - Long-term MA\n"
            f"  • HMA 52 - Medium MA\n"
            f"📊 <b>Rules:</b>\n"
            f"  🟢 HMA 416 crosses ABOVE HMA 52 → BULLISH\n"
            f"  🔴 HMA 416 crosses BELOW HMA 52 → BEARISH\n"
            f"🔍 <b>Monitoring:</b> {len(available_symbols)} coins"
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
                        timeframe='3m', 
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 420:  # Need at least 420 candles for HMA 416
                        print(f"  ⚠️ {symbol}: Insufficient data (need 420+ candles)")
                        continue

                    # Check HMA crossover
                    signal, indicators = check_hma_crossover(symbol, df)

                    current_price = df['close'].iloc[-1]
                    price_str = format_price(current_price)
                    rating, volume, quality = get_rating(symbol)

                    # Display current status
                    if signal:
                        emoji = "🟢" if 'BULLISH' in signal else "🔴"
                        cross_emoji = get_cross_emoji(indicators['signal_type'])
                        print(f"  🎯 {rating} {symbol:12} | {price_str:12} | "
                              f"SIGNAL: {signal} {cross_emoji}")

                        result = update_signal_state(symbol, signal, indicators)

                        if result == 'NEW_CROSS':
                            new_signals += 1
                            signal_tracker[symbol]['alert_sent'] = True

                            # Build detailed alert message
                            message = (
                                f"🚨 <b>{signal}</b> {cross_emoji}\n\n"
                                f"<b>Symbol:</b> {symbol} {rating.split()[0]}\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Heikin Ashi Close:</b> {format_price(indicators['ha_price'])}\n"
                                f"<b>Quality:</b> {quality}\n\n"
                                f"<b>📊 Indicators:</b>\n"
                                f"  • HMA 416: {indicators['hma_416']:.4f}\n"
                                f"  • HMA 52: {indicators['hma_52']:.4f}\n"
                                f"  • Previous HMA 416: {indicators['prev_hma_416']:.4f}\n"
                                f"  • Previous HMA 52: {indicators['prev_hma_52']:.4f}\n"
                                f"  • HMA 416 > HMA 52: {'✅ YES' if indicators['hma_416_greater'] else '❌ NO'}\n\n"
                                f"<b>📈 Decision:</b>\n"
                                f"  {indicators['reason']}\n\n"
                                f"<b>⏱️ Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )

                            if send_alert(message):
                                print(f"  🚨 ALERT SENT: {symbol} {signal}")
                            else:
                                print(f"  ❌ Alert FAILED for {symbol}")
                    else:
                        # Show current HMA status for monitoring
                        if indicators:
                            print(f"  {rating} {symbol:12} | {price_str:12} | "
                                  f"HMA416: {indicators.get('hma_416', 0):.4f} | "
                                  f"HMA52: {indicators.get('hma_52', 0):.4f} | "
                                  f"{'📈' if indicators.get('hma_416_greater', False) else '📉'}")
                        else:
                            print(f"  {rating} {symbol:12} | {price_str:12} | "
                                  f"Monitoring...")

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    continue

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            active = get_active_signals()
            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Processed: {len(available_symbols)} symbols")
            print(f"  • New Crossovers: {new_signals}")
            print(f"  • Active Signals: {len(active)}")
            if active:
                for sym, info in active.items():
                    rating, _, _ = get_rating(sym)
                    print(f"    • {rating} {sym}: {info['signal']} (cross at {info['cross_time'].strftime('%H:%M:%S')})")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            traceback.print_exc()
            time.sleep(60)

# 9. Start Bot
print("\n🚀 Starting bot...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# 10. Start Flask Server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port)
