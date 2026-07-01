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
    return "HMA + Heikin Ashi Signal Generator is running!"

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
API_CALL_INTERVAL = 1.5
CHECK_INTERVAL = 30
CANDLES_TO_FETCH = 200  # Increased for HMA calculations
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 200

# Trading pairs - TOP 7 COINS ONLY
SYMBOLS = [
    'BTC/USDT',   # ⭐⭐⭐⭐⭐ High
    'ETH/USDT',   # ⭐⭐⭐⭐⭐ High
    'SOL/USDT',   # ⭐⭐⭐⭐⭐ Very High
    'HYPE/USDT',  # ⭐⭐⭐⭐⭐ Extremely High
    'DOGE/USDT',  # ⭐⭐⭐⭐ Very High
    'XRP/USDT',   # ⭐⭐⭐⭐ High
    'SUI/USDT'    # ⭐⭐⭐⭐ High
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
    """Calculate HMA 100, HMA 52, and HMA 9 on Heikin Ashi close"""
    try:
        ha_close = ha_df['ha_close']
        
        hma_100 = calculate_hma(ha_close, 100)
        hma_52 = calculate_hma(ha_close, 52)
        hma_9 = calculate_hma(ha_close, 9)
        
        return {
            'hma_100': hma_100,
            'hma_52': hma_52,
            'hma_9': hma_9,
            'current_hma_100': hma_100.iloc[-1] if not pd.isna(hma_100.iloc[-1]) else 0,
            'current_hma_52': hma_52.iloc[-1] if not pd.isna(hma_52.iloc[-1]) else 0,
            'current_hma_9': hma_9.iloc[-1] if not pd.isna(hma_9.iloc[-1]) else 0
        }
    except Exception as e:
        print(f"  ❌ HMA calculation error: {e}")
        return None

# ============================================
# 6. SIGNAL DETECTION WITH HMA + HEIKIN ASHI
# ============================================

def check_hma_signal(symbol, df):
    """
    Signal detection using:
    1. Heikin Ashi candles (smoother price action)
    2. HMA 100, HMA 52, HMA 9 crossover strategy
    
    BUY Signal: HMA100 > HMA52 > HMA9 (Bullish alignment)
    SELL Signal: HMA100 < HMA52 < HMA9 (Bearish alignment)
    """
    try:
        # Calculate Heikin Ashi
        ha_df = calculate_heikin_ashi(df)
        if ha_df is None:
            return None, None, None
        
        # Calculate all HMAs
        hma_data = calculate_all_hmas(ha_df)
        if hma_data is None:
            return None, None, None
        
        # Get current values
        hma_100 = hma_data['current_hma_100']
        hma_52 = hma_data['current_hma_52']
        hma_9 = hma_data['current_hma_9']
        
        # Get previous values for trend confirmation
        hma_100_series = hma_data['hma_100']
        hma_52_series = hma_data['hma_52']
        hma_9_series = hma_data['hma_9']
        
        prev_hma_100 = hma_100_series.iloc[-2] if len(hma_100_series) > 1 else hma_100
        prev_hma_52 = hma_52_series.iloc[-2] if len(hma_52_series) > 1 else hma_52
        prev_hma_9 = hma_9_series.iloc[-2] if len(hma_9_series) > 1 else hma_9
        
        # Current price (Heikin Ashi close)
        current_price = ha_df['ha_close'].iloc[-1]
        
        # Check for BUY signal: HMA100 > HMA52 > HMA9
        if hma_100 > hma_52 > hma_9:
            # Check if this is a new crossover or continuation
            if (hma_100 > hma_52 > hma_9) and not (prev_hma_100 > prev_hma_52 > prev_hma_9):
                strength = 'STRONG'
                indicators = {
                    'hma_100': hma_100,
                    'hma_52': hma_52,
                    'hma_9': hma_9,
                    'current_price': current_price,
                    'alignment': 'HMA100 > HMA52 > HMA9',
                    'signal_type': 'BUY'
                }
                return 'BUY', strength, indicators
            else:
                # Already in bullish alignment
                strength = 'NORMAL'
                indicators = {
                    'hma_100': hma_100,
                    'hma_52': hma_52,
                    'hma_9': hma_9,
                    'current_price': current_price,
                    'alignment': 'HMA100 > HMA52 > HMA9 (Sustained)',
                    'signal_type': 'BUY'
                }
                return 'BUY', strength, indicators
        
        # Check for SELL signal: HMA100 < HMA52 < HMA9
        elif hma_100 < hma_52 < hma_9:
            # Check if this is a new crossover or continuation
            if (hma_100 < hma_52 < hma_9) and not (prev_hma_100 < prev_hma_52 < prev_hma_9):
                strength = 'STRONG'
                indicators = {
                    'hma_100': hma_100,
                    'hma_52': hma_52,
                    'hma_9': hma_9,
                    'current_price': current_price,
                    'alignment': 'HMA100 < HMA52 < HMA9',
                    'signal_type': 'SELL'
                }
                return 'SELL', strength, indicators
            else:
                # Already in bearish alignment
                strength = 'NORMAL'
                indicators = {
                    'hma_100': hma_100,
                    'hma_52': hma_52,
                    'hma_9': hma_9,
                    'current_price': current_price,
                    'alignment': 'HMA100 < HMA52 < HMA9 (Sustained)',
                    'signal_type': 'SELL'
                }
                return 'SELL', strength, indicators
        
        # No clear signal
        return None, None, None
        
    except Exception as e:
        print(f"  ❌ HMA signal error for {symbol}: {e}")
        return None, None, None

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
        'SUI/USDT': ('⭐⭐⭐⭐', 'High', 'Very Good')
    }
    return ratings.get(symbol, ('⭐⭐⭐', 'Medium', 'Good'))

def get_strength_emoji(strength):
    """Get emoji for signal strength"""
    emojis = {
        'STRONG': '🔥💪🚀',
        'NORMAL': '✅'
    }
    return emojis.get(strength, '✅')

# 8. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    print("\n" + "="*70)
    print("🚀 HMA + HEIKIN ASHI SIGNAL GENERATOR")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE_NAME}")
    print(f"\n📈 STRATEGY DETAILS:")
    print(f"  • Timeframe: 5 Minutes")
    print(f"  • Candles: Heikin Ashi (Smoother Price Action)")
    print(f"  • HMA 100 (Long-term trend)")
    print(f"  • HMA 52 (Medium-term trend)")
    print(f"  • HMA 9 (Short-term trend)")
    print(f"\n📊 SIGNAL RULES:")
    print(f"  🟢 BUY: HMA100 > HMA52 > HMA9 (Bullish Alignment)")
    print(f"  🔴 SELL: HMA100 < HMA52 < HMA9 (Bearish Alignment)")
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
            f"✅ <b>HMA + Heikin Ashi Bot Started</b>\n\n"
            f"📊 <b>Exchange:</b> {EXCHANGE_NAME}\n"
            f"⏱️ <b>Timeframe:</b> 5 Minutes\n"
            f"📈 <b>Strategy:</b>\n"
            f"  • Heikin Ashi Candles\n"
            f"  • HMA 100 - Long-term\n"
            f"  • HMA 52 - Medium-term\n"
            f"  • HMA 9 - Short-term\n"
            f"📊 <b>Signals:</b>\n"
            f"  🟢 BUY: HMA100 > HMA52 > HMA9\n"
            f"  🔴 SELL: HMA100 < HMA52 < HMA9\n"
            f"🔍 <b>Monitoring:</b> {len(available_symbols)} top coins"
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

                    if df is None or len(df) < 100:  # Need enough data for HMA 100
                        print(f"  ⚠️ {symbol}: Insufficient data (need 100+ candles)")
                        continue

                    # Check HMA signal
                    signal, strength, indicators = check_hma_signal(symbol, df)
                    
                    current_price = df['close'].iloc[-1]  # Original price for display
                    price_str = format_price(current_price)
                    rating, volume, quality = get_rating(symbol)

                    # Display current status
                    if signal:
                        emoji = "🟢" if signal == 'BUY' else "🔴"
                        print(f"  🎯 {rating} {symbol:12} | {price_str:12} | "
                              f"SIGNAL: {signal} {get_strength_emoji(strength)} | "
                              f"HA Close: {format_price(indicators['current_price'])}")
                        
                        result = update_signal_state(symbol, signal, strength, indicators)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1
                            signal_tracker[symbol]['alert_sent'] = True

                            # Build detailed alert message
                            message = (
                                f"🚨 <b>{signal} SIGNAL DETECTED</b> {get_strength_emoji(strength)}\n\n"
                                f"<b>Symbol:</b> {symbol} {rating.split()[0]}\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Heikin Ashi Close:</b> {format_price(indicators['current_price'])}\n"
                                f"<b>Strength:</b> {strength}\n"
                                f"<b>Quality:</b> {quality}\n\n"
                                f"<b>📊 HMA Values:</b>\n"
                                f"  • HMA 100: {indicators['hma_100']:.4f}\n"
                                f"  • HMA 52: {indicators['hma_52']:.4f}\n"
                                f"  • HMA 9: {indicators['hma_9']:.4f}\n\n"
                                f"<b>📈 Alignment:</b>\n"
                                f"  {indicators['alignment']}\n\n"
                                f"<b>⏱️ Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )

                            if send_alert(message):
                                print(f"  🚨 ALERT SENT: {symbol} {signal} ({strength})")
                            else:
                                print(f"  ❌ Alert FAILED for {symbol}")
                    else:
                        # Show status for all coins
                        print(f"  {rating} {symbol:12} | {price_str:12} | "
                              f"Waiting for HMA alignment...")

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
                    rating, _, _ = get_rating(sym)
                    print(f"    • {rating} {sym}: {info['signal']} ({info['strength']})")

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
