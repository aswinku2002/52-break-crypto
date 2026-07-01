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
    return "ADX + Extra Indicators Signal Generator is running!"

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
CANDLES_TO_FETCH = 150  # Increased for all indicators
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 150

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

def get_cached_ohlcv(exchange, symbol, timeframe='3m', limit=150):
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
# 4. INDICATOR CALCULATIONS
# ============================================

def calculate_adx(df, period=21):
    """Calculate ADX with +DI and -DI"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        up_move = high - high.shift()
        down_move = low.shift() - low
        
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
        
        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        
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

def calculate_rsi(df, period=14):
    """Calculate RSI"""
    try:
        close = df['close']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return {
            'rsi': rsi,
            'current_rsi': rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        }
    except Exception as e:
        print(f"  ❌ RSI calculation error: {e}")
        return None

def calculate_macd(df, fast=12, slow=26, signal=9):
    """Calculate MACD"""
    try:
        close = df['close']
        exp1 = close.ewm(span=fast, adjust=False).mean()
        exp2 = close.ewm(span=slow, adjust=False).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        histogram = macd - signal_line
        
        return {
            'macd': macd,
            'signal': signal_line,
            'histogram': histogram,
            'current_macd': macd.iloc[-1] if not pd.isna(macd.iloc[-1]) else 0,
            'current_signal': signal_line.iloc[-1] if not pd.isna(signal_line.iloc[-1]) else 0,
            'current_histogram': histogram.iloc[-1] if not pd.isna(histogram.iloc[-1]) else 0
        }
    except Exception as e:
        print(f"  ❌ MACD calculation error: {e}")
        return None

def calculate_bollinger_bands(df, period=20, std_dev=2):
    """Calculate Bollinger Bands"""
    try:
        close = df['close']
        sma = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        return {
            'upper': upper,
            'middle': sma,
            'lower': lower,
            'current_upper': upper.iloc[-1] if not pd.isna(upper.iloc[-1]) else 0,
            'current_middle': sma.iloc[-1] if not pd.isna(sma.iloc[-1]) else 0,
            'current_lower': lower.iloc[-1] if not pd.isna(lower.iloc[-1]) else 0
        }
    except Exception as e:
        print(f"  ❌ Bollinger Bands calculation error: {e}")
        return None

def calculate_volume_sma(df, period=20):
    """Calculate Volume SMA and volume ratio"""
    try:
        volume = df['vol']
        vol_sma = volume.rolling(window=period).mean()
        vol_ratio = volume / vol_sma
        
        return {
            'vol_sma': vol_sma,
            'vol_ratio': vol_ratio,
            'current_volume': volume.iloc[-1] if not pd.isna(volume.iloc[-1]) else 0,
            'current_vol_sma': vol_sma.iloc[-1] if not pd.isna(vol_sma.iloc[-1]) else 1,
            'current_vol_ratio': vol_ratio.iloc[-1] if not pd.isna(vol_ratio.iloc[-1]) else 1
        }
    except Exception as e:
        print(f"  ❌ Volume calculation error: {e}")
        return None

def calculate_supertrend(df, period=10, multiplier=3):
    """Calculate SuperTrend"""
    try:
        high, low, close = df['high'], df['low'], df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        hl2 = (high + low) / 2
        basic_upper = hl2 + (multiplier * atr)
        basic_lower = hl2 - (multiplier * atr)
        
        final_upper = pd.Series(index=df.index, dtype=float)
        final_lower = pd.Series(index=df.index, dtype=float)
        trend = pd.Series(index=df.index, dtype=int)
        
        final_upper.iloc[0] = basic_upper.iloc[0]
        final_lower.iloc[0] = basic_lower.iloc[0]
        trend.iloc[0] = 1
        
        for i in range(1, len(df)):
            prev_close = close.iloc[i-1]
            prev_final_upper = final_upper.iloc[i-1]
            prev_final_lower = final_lower.iloc[i-1]
            prev_trend = trend.iloc[i-1]
            
            if basic_upper.iloc[i] < prev_final_upper or prev_close > prev_final_upper:
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = prev_final_upper
                
            if basic_lower.iloc[i] > prev_final_lower or prev_close < prev_final_lower:
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = prev_final_lower
            
            if close.iloc[i] <= final_upper.iloc[i] and prev_trend == 1:
                trend.iloc[i] = -1
            elif close.iloc[i] >= final_lower.iloc[i] and prev_trend == -1:
                trend.iloc[i] = 1
            elif prev_trend == 1:
                trend.iloc[i] = 1
            else:
                trend.iloc[i] = -1
        
        current_trend = trend.iloc[-1]
        current_st = final_upper.iloc[-1] if current_trend == -1 else final_lower.iloc[-1]
        
        return {
            'trend': trend,
            'supertrend': final_upper if current_trend == -1 else final_lower,
            'current_trend': current_trend,
            'current_supertrend': current_st
        }
    except Exception as e:
        print(f"  ❌ SuperTrend calculation error: {e}")
        return None

# ============================================
# 5. ENHANCED SIGNAL DETECTION WITH ALL INDICATORS
# ============================================

def check_enhanced_signal(symbol, df):
    """
    Enhanced signal detection using ALL indicators:
    1. ADX > 25 (trend strength)
    2. RSI (momentum confirmation)
    3. MACD (trend direction)
    4. Bollinger Bands (volatility)
    5. Volume (confirmation)
    6. SuperTrend (trend following)
    """
    try:
        # Calculate all indicators
        adx_data = calculate_adx(df, period=21)
        if adx_data is None:
            return None, None, None
        
        rsi_data = calculate_rsi(df, period=14)
        macd_data = calculate_macd(df, fast=12, slow=26, signal=9)
        bb_data = calculate_bollinger_bands(df, period=20, std_dev=2)
        vol_data = calculate_volume_sma(df, period=20)
        st_data = calculate_supertrend(df, period=10, multiplier=3)
        
        if any(x is None for x in [rsi_data, macd_data, bb_data, vol_data, st_data]):
            return None, None, None
        
        # Get current values
        current_adx = adx_data['current_adx']
        plus_di = adx_data['current_plus_di']
        minus_di = adx_data['current_minus_di']
        direction = adx_data['direction']
        
        current_rsi = rsi_data['current_rsi']
        current_macd = macd_data['current_macd']
        current_signal = macd_data['current_signal']
        current_histogram = macd_data['current_histogram']
        
        current_price = df['close'].iloc[-1]
        bb_upper = bb_data['current_upper']
        bb_lower = bb_data['current_lower']
        bb_middle = bb_data['current_middle']
        
        vol_ratio = vol_data['current_vol_ratio']
        st_trend = st_data['current_trend']
        
        # Get previous ADX for trend confirmation
        adx_series = adx_data['adx']
        prev_adx = adx_series.iloc[-2] if len(adx_series) > 1 else current_adx
        
        # ============================================
        # SIGNAL STRENGTH SCORING SYSTEM
        # ============================================
        
        buy_score = 0
        sell_score = 0
        signals_confirmed = []
        
        # 1. ADX Condition (Primary)
        if current_adx > 25:
            if current_adx > 40:
                buy_score += 3
                sell_score += 3
                signals_confirmed.append(f"ADX Very Strong ({current_adx:.1f})")
            elif current_adx > 30:
                buy_score += 2
                sell_score += 2
                signals_confirmed.append(f"ADX Strong ({current_adx:.1f})")
            else:
                buy_score += 1
                sell_score += 1
                signals_confirmed.append(f"ADX Moderate ({current_adx:.1f})")
        else:
            return None, None, None  # ADX must be > 25
        
        # 2. ADX Increasing (Momentum)
        if current_adx > prev_adx:
            buy_score += 1
            sell_score += 1
            signals_confirmed.append("ADX Increasing")
        
        # 3. RSI Condition (Momentum)
        if direction == 1:  # Bullish
            if 40 < current_rsi < 70:
                buy_score += 2
                signals_confirmed.append(f"RSI Bullish ({current_rsi:.1f})")
            elif current_rsi <= 40:
                buy_score += 1  # Oversold but can stay oversold
                signals_confirmed.append(f"RSI Oversold ({current_rsi:.1f})")
        else:  # Bearish
            if 30 < current_rsi < 60:
                sell_score += 2
                signals_confirmed.append(f"RSI Bearish ({current_rsi:.1f})")
            elif current_rsi >= 60:
                sell_score += 1  # Overbought but can stay overbought
                signals_confirmed.append(f"RSI Overbought ({current_rsi:.1f})")
        
        # 4. MACD Condition (Trend Direction)
        if current_macd > current_signal:
            buy_score += 2
            signals_confirmed.append("MACD Bullish")
            if current_histogram > 0:
                buy_score += 1
                signals_confirmed.append("MACD Histogram Positive")
        else:
            sell_score += 2
            signals_confirmed.append("MACD Bearish")
            if current_histogram < 0:
                sell_score += 1
                signals_confirmed.append("MACD Histogram Negative")
        
        # 5. Bollinger Bands (Volatility/Position)
        if direction == 1:  # Bullish
            if current_price > bb_middle:
                buy_score += 1
                signals_confirmed.append("Price Above BB Middle")
            if current_price > bb_upper:
                buy_score += 1
                signals_confirmed.append("Price Above BB Upper (Strong)")
        else:  # Bearish
            if current_price < bb_middle:
                sell_score += 1
                signals_confirmed.append("Price Below BB Middle")
            if current_price < bb_lower:
                sell_score += 1
                signals_confirmed.append("Price Below BB Lower (Strong)")
        
        # 6. Volume Confirmation
        if vol_ratio > 1.5:
            if direction == 1:
                buy_score += 2
                signals_confirmed.append(f"High Volume ({vol_ratio:.1f}x)")
            else:
                sell_score += 2
                signals_confirmed.append(f"High Volume ({vol_ratio:.1f}x)")
        elif vol_ratio > 1.2:
            if direction == 1:
                buy_score += 1
                signals_confirmed.append(f"Above Avg Volume ({vol_ratio:.1f}x)")
            else:
                sell_score += 1
                signals_confirmed.append(f"Above Avg Volume ({vol_ratio:.1f}x)")
        
        # 7. SuperTrend Confirmation
        if st_trend == 1:  # Bullish
            buy_score += 2
            signals_confirmed.append("SuperTrend Bullish")
        else:  # Bearish
            sell_score += 2
            signals_confirmed.append("SuperTrend Bearish")
        
        # Determine final signal and strength
        min_score_required = 6  # Minimum score for a signal
        
        if buy_score >= min_score_required and buy_score > sell_score:
            # Determine strength
            if buy_score >= 10:
                strength = 'VERY_STRONG'
            elif buy_score >= 8:
                strength = 'STRONG'
            else:
                strength = 'NORMAL'
            
            return 'BUY', strength, {
                'score': buy_score,
                'adx': current_adx,
                'rsi': current_rsi,
                'macd': current_macd,
                'bb_position': 'Above Upper' if current_price > bb_upper else 'Above Middle' if current_price > bb_middle else 'Below Middle',
                'vol_ratio': vol_ratio,
                'st_trend': 'Bullish' if st_trend == 1 else 'Bearish',
                'confirmed_signals': signals_confirmed,
                'total_confirmed': len(signals_confirmed)
            }
        
        elif sell_score >= min_score_required and sell_score > buy_score:
            if sell_score >= 10:
                strength = 'VERY_STRONG'
            elif sell_score >= 8:
                strength = 'STRONG'
            else:
                strength = 'NORMAL'
            
            return 'SELL', strength, {
                'score': sell_score,
                'adx': current_adx,
                'rsi': current_rsi,
                'macd': current_macd,
                'bb_position': 'Below Lower' if current_price < bb_lower else 'Below Middle' if current_price < bb_middle else 'Above Middle',
                'vol_ratio': vol_ratio,
                'st_trend': 'Bullish' if st_trend == 1 else 'Bearish',
                'confirmed_signals': signals_confirmed,
                'total_confirmed': len(signals_confirmed)
            }
        
        return None, None, None
        
    except Exception as e:
        print(f"  ❌ Enhanced signal error for {symbol}: {e}")
        return None, None, None

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
        'VERY_STRONG': '🔥💪🚀',
        'STRONG': '💪',
        'NORMAL': '✅'
    }
    return emojis.get(strength, '✅')

# 7. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    print("\n" + "="*70)
    print("🚀 ENHANCED SIGNAL GENERATOR - 6 INDICATORS")
    print("="*70)
    print(f"📊 Exchange: {EXCHANGE_NAME}")
    print(f"\n📈 INDICATORS USED:")
    print(f"  1. ADX (21) - Trend Strength")
    print(f"  2. RSI (14) - Momentum")
    print(f"  3. MACD (12,26,9) - Trend Direction")
    print(f"  4. Bollinger Bands (20,2) - Volatility")
    print(f"  5. Volume SMA (20) - Volume Confirmation")
    print(f"  6. SuperTrend (10,3) - Trend Following")
    print(f"\n📊 SIGNAL REQUIREMENTS:")
    print(f"  • Minimum Score: 6/14 indicators must agree")
    print(f"  • ADX must be > 25")
    print(f"  • Multiple confirmations required")
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
            f"✅ <b>Enhanced Signal Bot Started</b>\n\n"
            f"📊 <b>Exchange:</b> {EXCHANGE_NAME}\n"
            f"⏱️ <b>Timeframe:</b> 3 Minutes\n"
            f"📊 <b>6 Indicators:</b>\n"
            f"  • ADX (21) - Trend Strength\n"
            f"  • RSI (14) - Momentum\n"
            f"  • MACD - Trend Direction\n"
            f"  • Bollinger Bands - Volatility\n"
            f"  • Volume SMA - Confirmation\n"
            f"  • SuperTrend - Trend Following\n"
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
                        timeframe='3m', 
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 40:
                        print(f"  ⚠️ {symbol}: Insufficient data")
                        continue

                    # Check enhanced signal
                    signal, strength, indicators = check_enhanced_signal(symbol, df)
                    
                    current_price = df['close'].iloc[-1]
                    price_str = format_price(current_price)
                    rating, volume, quality = get_rating(symbol)

                    # Display current status
                    if signal:
                        print(f"  🎯 {rating} {symbol:12} | {price_str:12} | "
                              f"SIGNAL: {signal} {get_strength_emoji(strength)} | "
                              f"Score: {indicators['score']}/14 | "
                              f"Confirmed: {indicators['total_confirmed']} indicators")
                        
                        result = update_signal_state(symbol, signal, strength, indicators)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1
                            signal_tracker[symbol]['alert_sent'] = True

                            # Build detailed alert message
                            confirmed_list = "\n  • ".join(indicators['confirmed_signals'])
                            message = (
                                f"🚨 <b>{signal} SIGNAL CONFIRMED</b> {get_strength_emoji(strength)}\n\n"
                                f"<b>Symbol:</b> {symbol} {rating.split()[0]}\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Strength:</b> {strength} ({indicators['score']}/14)\n"
                                f"<b>Quality:</b> {quality}\n\n"
                                f"<b>📊 Indicator Summary:</b>\n"
                                f"  • ADX: {indicators['adx']:.1f}\n"
                                f"  • RSI: {indicators['rsi']:.1f}\n"
                                f"  • MACD: {indicators['macd']:.4f}\n"
                                f"  • BB Position: {indicators['bb_position']}\n"
                                f"  • Volume: {indicators['vol_ratio']:.1f}x avg\n"
                                f"  • SuperTrend: {indicators['st_trend']}\n\n"
                                f"<b>✅ {indicators['total_confirmed']} Confirmations:</b>\n"
                                f"  • {confirmed_list}\n\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                            )

                            if send_alert(message):
                                print(f"  🚨 ALERT SENT: {symbol} {signal} ({strength})")
                            else:
                                print(f"  ❌ Alert FAILED for {symbol}")
                    else:
                        # Show status for all coins
                        print(f"  {rating} {symbol:12} | {price_str:12} | "
                              f"Waiting for confirmation...")

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

# 8. Start Bot
print("\n🚀 Starting bot...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# 9. Start Flask Server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port)