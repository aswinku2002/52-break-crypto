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
    return "Heikin Ashi RSI/VWMA/Volume Signal Generator is running!"

@app.route('/health')
def health():
    return {
        "status": "ok",
        "exchange": "BINANCE",
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

# Binance API Keys (optional)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Performance Configuration
API_CALL_INTERVAL = 1.0         # Seconds between API calls
CHECK_INTERVAL = 20             # ⚡ 20 SECONDS between full scans
CANDLES_TO_FETCH = 100
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 100

# Signal Settings - INSTANT ALERTS
CONFIRMATION_CYCLES_REQUIRED = 1
RESET_CYCLES_REQUIRED = 2

# Trading pairs - ETH/USDT ONLY
SYMBOLS = [
    'ETH/USDT'
]

# Global variables
last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0

# OHLCV Cache System
ohlcv_cache = {}

def get_cached_ohlcv(exchange, symbol, timeframe='1m', limit=100):
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

# Signal Tracker
signal_tracker = {}

def update_signal_state(symbol, new_signal, strength='NORMAL'):
    """Send alert IMMEDIATELY on first detection"""
    now = datetime.now()

    if symbol not in signal_tracker:
        signal_tracker[symbol] = {
            'current_signal': None,
            'active': False,
            'alert_sent': False,
            'last_signal_time': now,
            'signal_strength': 'NORMAL'
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
                'alert_sent': tracker['alert_sent']
            }
    return active

# 3. Binance Exchange Initialization - BINANCE ONLY
def init_binance():
    """Initialize Binance exchange"""
    try:
        config = {
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        }

        if BINANCE_API_KEY and BINANCE_API_SECRET:
            config['apiKey'] = BINANCE_API_KEY
            config['secret'] = BINANCE_API_SECRET
            print(f"🔑 Binance: Using authenticated endpoints")
        else:
            print(f"🔓 Binance: Using public endpoints (no API keys required)")

        exchange = ccxt.binance(config)
        exchange.load_markets()
        print(f"✅ Connected to Binance successfully")
        return exchange

    except Exception as e:
        print(f"❌ Error initializing Binance: {e}")
        return None

# Initialize Binance exchange
EXCHANGE = init_binance()
if not EXCHANGE:
    print("❌ Failed to connect to Binance. Exiting.")
    exit(1)

# 4. HEIKIN ASHI CALCULATION
def calculate_heikin_ashi(df):
    """
    Convert regular candles to Heikin Ashi candles
    HA_Close = (Open + High + Low + Close) / 4
    HA_Open = (Previous HA_Open + Previous HA_Close) / 2
    HA_High = Max(High, HA_Open, HA_Close)
    HA_Low = Min(Low, HA_Open, HA_Close)
    """
    try:
        ha_df = pd.DataFrame(index=df.index)
        
        # Heikin Ashi Close
        ha_df['ha_close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
        
        # Heikin Ashi Open (uses previous HA values)
        ha_df['ha_open'] = 0.0
        ha_df['ha_open'].iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
        
        for i in range(1, len(df)):
            ha_df['ha_open'].iloc[i] = (ha_df['ha_open'].iloc[i-1] + ha_df['ha_close'].iloc[i-1]) / 2
        
        # Heikin Ashi High
        ha_df['ha_high'] = df[['high']].copy()
        ha_df['ha_high'] = ha_df.apply(lambda x: max(df.loc[x.name, 'high'], 
                                                      ha_df.loc[x.name, 'ha_open'], 
                                                      ha_df.loc[x.name, 'ha_close']), axis=1)
        
        # Heikin Ashi Low
        ha_df['ha_low'] = df[['low']].copy()
        ha_df['ha_low'] = ha_df.apply(lambda x: min(df.loc[x.name, 'low'], 
                                                     ha_df.loc[x.name, 'ha_open'], 
                                                     ha_df.loc[x.name, 'ha_close']), axis=1)
        
        # Keep original volume
        ha_df['vol'] = df['vol']
        
        return ha_df
        
    except Exception as e:
        print(f"  ❌ Heikin Ashi calculation error: {e}")
        return None

# 5. Indicator Calculations ON HEIKIN ASHI
def calculate_indicators(ha_df):
    """
    Calculate RSI(7), Stoch RSI(14), VWMA(26), VWMA(52), Volume MA(20)
    ALL calculations based on HEIKIN ASHI candles
    """
    try:
        close = ha_df['ha_close']
        high = ha_df['ha_high']
        low = ha_df['ha_low']
        open_price = ha_df['ha_open']
        volume = ha_df['vol']

        # RSI(7) on Heikin Ashi close
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=7).mean()
        avg_loss = loss.rolling(window=7).mean()
        rs = avg_gain / avg_loss
        rsi_7 = 100 - (100 / (1 + rs))

        # Stoch RSI(14) on Heikin Ashi
        delta_14 = close.diff()
        gain_14 = delta_14.where(delta_14 > 0, 0)
        loss_14 = -delta_14.where(delta_14 < 0, 0)
        avg_gain_14 = gain_14.rolling(window=14).mean()
        avg_loss_14 = loss_14.rolling(window=14).mean()
        rs_14 = avg_gain_14 / avg_loss_14
        rsi_14 = 100 - (100 / (1 + rs_14))

        lowest_rsi = rsi_14.rolling(window=14).min()
        highest_rsi = rsi_14.rolling(window=14).max()
        stoch_rsi = (rsi_14 - lowest_rsi) / (highest_rsi - lowest_rsi)

        # VWMA (Volume Weighted Moving Average) on Heikin Ashi
        def vwma(period):
            typical_price = (high + low + close) / 3
            vwma_val = (typical_price * volume).rolling(window=period).sum() / volume.rolling(window=period).sum()
            return vwma_val

        vwma_26 = vwma(26)
        vwma_52 = vwma(52)

        # Volume MA(20)
        vol_ma_20 = volume.rolling(window=20).mean()

        # Green/Red Volume based on Heikin Ashi close vs open
        green_volume = volume.where(close > open_price, 0)
        red_volume = volume.where(close < open_price, 0)

        return {
            'rsi_7': rsi_7,
            'stoch_rsi': stoch_rsi,
            'vwma_26': vwma_26,
            'vwma_52': vwma_52,
            'vol_ma_20': vol_ma_20,
            'green_volume': green_volume,
            'red_volume': red_volume,
            # PREVIOUS CANDLE VALUES (Heikin Ashi - index -2)
            'prev_rsi_7': rsi_7.iloc[-2] if len(rsi_7) >= 2 and not pd.isna(rsi_7.iloc[-2]) else 0,
            'prev_stoch_rsi': stoch_rsi.iloc[-2] if len(stoch_rsi) >= 2 and not pd.isna(stoch_rsi.iloc[-2]) else 0,
            'prev_vwma_26': vwma_26.iloc[-2] if len(vwma_26) >= 2 and not pd.isna(vwma_26.iloc[-2]) else 0,
            'prev_vwma_52': vwma_52.iloc[-2] if len(vwma_52) >= 2 and not pd.isna(vwma_52.iloc[-2]) else 0,
            'prev_vol_ma_20': vol_ma_20.iloc[-2] if len(vol_ma_20) >= 2 and not pd.isna(vol_ma_20.iloc[-2]) else 0,
            'prev_green_vol': green_volume.iloc[-2] if len(green_volume) >= 2 and not pd.isna(green_volume.iloc[-2]) else 0,
            'prev_red_vol': red_volume.iloc[-2] if len(red_volume) >= 2 and not pd.isna(red_volume.iloc[-2]) else 0,
            'prev_volume': volume.iloc[-2] if len(volume) >= 2 and not pd.isna(volume.iloc[-2]) else 0,
            'prev_ha_close': close.iloc[-2] if len(close) >= 2 and not pd.isna(close.iloc[-2]) else 0,
            'prev_ha_open': open_price.iloc[-2] if len(open_price) >= 2 and not pd.isna(open_price.iloc[-2]) else 0,
            'prev_ha_high': high.iloc[-2] if len(high) >= 2 and not pd.isna(high.iloc[-2]) else 0,
            'prev_ha_low': low.iloc[-2] if len(low) >= 2 and not pd.isna(low.iloc[-2]) else 0,
            # Current candle values for display only
            'current_rsi_7': rsi_7.iloc[-1] if not pd.isna(rsi_7.iloc[-1]) else 0,
            'current_stoch_rsi': stoch_rsi.iloc[-1] if not pd.isna(stoch_rsi.iloc[-1]) else 0,
            'current_vwma_26': vwma_26.iloc[-1] if not pd.isna(vwma_26.iloc[-1]) else 0,
            'current_vwma_52': vwma_52.iloc[-1] if not pd.isna(vwma_52.iloc[-1]) else 0,
            'current_vol_ma_20': vol_ma_20.iloc[-1] if not pd.isna(vol_ma_20.iloc[-1]) else 0,
            'current_green_vol': green_volume.iloc[-1] if not pd.isna(green_volume.iloc[-1]) else 0,
            'current_red_vol': red_volume.iloc[-1] if not pd.isna(red_volume.iloc[-1]) else 0,
            'current_volume': volume.iloc[-1] if not pd.isna(volume.iloc[-1]) else 0
        }
    except Exception as e:
        print(f"  ❌ Indicator calculation error: {e}")
        return None

# 6. Signal Detection - Conditions 1, 3, 6, 8 (HEIKIN ASHI PREVIOUS CANDLE)
def check_signals(symbol, df, indicators):
    """
    Check ONLY the PREVIOUS (completed) HEIKIN ASHI candle for conditions 1, 3, 6, 8
    
    Condition 1 (BUY): RSI(7)>70, StochRSI>0.8, VWMA26>VWMA52, GreenVol>VolMA20
    Condition 3 (SELL): RSI(7)<30, StochRSI<0.2, VWMA52>VWMA26, RedVol>VolMA20
    Condition 6 (SELL): RSI(7)>70, StochRSI>0.8, VWMA52>VWMA26, RedVol>VolMA20
    Condition 8 (BUY): RSI(7)<30, StochRSI<0.2, VWMA26>VWMA52, GreenVol>VolMA20
    """
    try:
        if indicators is None:
            return None, None, None

        # USE PREVIOUS HEIKIN ASHI CANDLE VALUES (index -2, completed candle)
        rsi_7 = indicators['prev_rsi_7']
        stoch_rsi = indicators['prev_stoch_rsi']
        vwma_26 = indicators['prev_vwma_26']
        vwma_52 = indicators['prev_vwma_52']
        green_vol = indicators['prev_green_vol']
        red_vol = indicators['prev_red_vol']
        vol_ma_20 = indicators['prev_vol_ma_20']

        # Condition 1: Strong Bullish Continuation (Heikin Ashi)
        if (rsi_7 > 70 and 
            stoch_rsi > 0.8 and 
            vwma_26 > vwma_52 and 
            green_vol > vol_ma_20):
            return 'BUY', 'STRONG', 1

        # Condition 8: Bullish Dip Buy (Heikin Ashi)
        if (rsi_7 < 30 and 
            stoch_rsi < 0.2 and 
            vwma_26 > vwma_52 and 
            green_vol > vol_ma_20):
            return 'BUY', 'NORMAL', 8

        # Condition 3: Strong Bearish Continuation (Heikin Ashi)
        if (rsi_7 < 30 and 
            stoch_rsi < 0.2 and 
            vwma_52 > vwma_26 and 
            red_vol > vol_ma_20):
            return 'SELL', 'STRONG', 3

        # Condition 6: Failed Rally in Bear Trend (Heikin Ashi)
        if (rsi_7 > 70 and 
            stoch_rsi > 0.8 and 
            vwma_52 > vwma_26 and 
            red_vol > vol_ma_20):
            return 'SELL', 'STRONG', 6

        return None, None, None

    except Exception as e:
        print(f"  ❌ Signal detection error for {symbol}: {e}")
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

# 8. Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    condition_names = {
        1: "HA Bullish Continuation",
        3: "HA Bearish Continuation",
        6: "HA Failed Rally (Bear)",
        8: "HA Bullish Dip Buy"
    }

    # Track last alert time for re-alerts
    last_alert_time = {}

    print("\n" + "="*70)
    print("🚀 HEIKIN ASHI RSI/VWMA/VOLUME SIGNAL GENERATOR - ETH/USDT")
    print("="*70)
    print(f"📊 Exchange: BINANCE ONLY")
    print(f"\n📈 CONFIGURATION:")
    print(f"  • 🕯️ CANDLE TYPE: HEIKIN ASHI")
    print(f"  • ⚡ INSTANT ALERTS")
    print(f"  • Symbol: ETH/USDT")
    print(f"  • Timeframe: 1 MINUTE")
    print(f"  • Scan Interval: 20 SECONDS ⚡")
    print(f"  • 🔍 CHECKING: PREVIOUS COMPLETED HEIKIN ASHI CANDLE")
    print(f"  • All indicators calculated on Heikin Ashi prices")
    print(f"\n📊 ACTIVE CONDITIONS (1, 3, 6, 8):")
    print(f"  • Cond 1 (BUY): HA RSI(7)>70, HA StochRSI>0.8, HA VWMA26>VWMA52, HA GreenVol>VolMA20")
    print(f"  • Cond 3 (SELL): HA RSI(7)<30, HA StochRSI<0.2, HA VWMA52>VWMA26, HA RedVol>VolMA20")
    print(f"  • Cond 6 (SELL): HA RSI(7)>70, HA StochRSI>0.8, HA VWMA52>VWMA26, HA RedVol>VolMA20")
    print(f"  • Cond 8 (BUY): HA RSI(7)<30, HA StochRSI<0.2, HA VWMA26>VWMA52, HA GreenVol>VolMA20")
    print("="*70 + "\n")

    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)}/{len(SYMBOLS)} symbols on Binance")

    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>Heikin Ashi Bot Started - ETH/USDT</b>\n\n"
            f"📊 <b>Exchange:</b> BINANCE ONLY\n"
            f"🕯️ <b>Candles:</b> HEIKIN ASHI\n"
            f"⏱️ <b>Timeframe:</b> 1 Minute\n"
            f"🔄 <b>Scan Interval:</b> 20 Seconds ⚡\n"
            f"🔍 <b>Checking:</b> PREVIOUS COMPLETED HA CANDLE\n"
            f"⚡ <b>Alert Mode:</b> INSTANT + Re-alert every 3 min\n"
            f"📊 <b>Conditions Active:</b> 1, 3, 6, 8\n"
            f"🕒 <b>Start:</b> {datetime.now().strftime('%H:%M:%S')}"
        )

    while True:
        try:
            cycle_count += 1
            new_signals = 0
            processed = 0

            print(f"\n{'='*70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Every 20s ⚡")
            print(f"{'='*70}")
            print(f"🕯️ HEIKIN ASHI MODE - CHECKING PREVIOUS COMPLETED HA CANDLE")

            if cycle_count % 10 == 0:
                cleanup_cache()

            for i, symbol in enumerate(available_symbols):
                try:
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    # Fetch regular OHLCV
                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='1m',
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 60:
                        print(f"  ⚠️ {symbol}: Insufficient data ({len(df) if df is not None else 0} candles)")
                        continue

                    # CONVERT TO HEIKIN ASHI
                    ha_df = calculate_heikin_ashi(df)
                    
                    if ha_df is None:
                        print(f"  ⚠️ {symbol}: Heikin Ashi conversion failed")
                        continue

                    # Calculate indicators on Heikin Ashi data
                    indicators = calculate_indicators(ha_df)

                    if indicators is None:
                        print(f"  ⚠️ {symbol}: Indicator calculation failed")
                        continue

                    # Get values for display
                    current_price = df['close'].iloc[-1]  # Regular close for reference
                    
                    # Heikin Ashi previous candle values
                    ha_prev_close = indicators['prev_ha_close']
                    ha_prev_open = indicators['prev_ha_open']
                    ha_prev_high = indicators['prev_ha_high']
                    ha_prev_low = indicators['prev_ha_low']
                    
                    price_str = format_price(current_price)
                    ha_prev_close_str = format_price(ha_prev_close)
                    
                    # Previous HA candle values (FOR SIGNAL CHECK)
                    rsi_7_prev = indicators['prev_rsi_7']
                    stoch_rsi_prev = indicators['prev_stoch_rsi']
                    vwma_26_prev = indicators['prev_vwma_26']
                    vwma_52_prev = indicators['prev_vwma_52']
                    green_vol_prev = indicators['prev_green_vol']
                    red_vol_prev = indicators['prev_red_vol']
                    vol_ma_20_prev = indicators['prev_vol_ma_20']
                    prev_volume = indicators['prev_volume']
                    
                    # Current HA candle values (for display)
                    rsi_7_curr = indicators['current_rsi_7']
                    stoch_rsi_curr = indicators['current_stoch_rsi']
                    
                    # Determine trend and candle types
                    trend = "BULL 🟢" if vwma_26_prev > vwma_52_prev else "BEAR 🔴"
                    ha_prev_bullish = ha_prev_close > ha_prev_open
                    ha_prev_candle_type = "HA GREEN 🟢" if ha_prev_bullish else "HA RED 🔴"
                    
                    # Current HA candle
                    ha_curr_close = ha_df['ha_close'].iloc[-1]
                    ha_curr_open = ha_df['ha_open'].iloc[-1]
                    ha_curr_bullish = ha_curr_close > ha_curr_open
                    ha_curr_candle_type = "HA GREEN 🟢" if ha_curr_bullish else "HA RED 🔴"
                    
                    # Display Heikin Ashi info
                    print(f"\n  {'='*60}")
                    print(f"  {symbol} | Current Price: {price_str}")
                    print(f"  {'='*60}")
                    print(f"  🕯️ HEIKIN ASHI CANDLES:")
                    print(f"  ⏮️  PREVIOUS HA CANDLE (SIGNAL CHECK):")
                    print(f"     Close: {ha_prev_close_str} | Open: {format_price(ha_prev_open)} | {ha_prev_candle_type}")
                    print(f"     High: {format_price(ha_prev_high)} | Low: {format_price(ha_prev_low)}")
                    print(f"     HA RSI(7): {rsi_7_prev:7.2f} | HA StochRSI: {stoch_rsi_prev:7.3f}")
                    print(f"     HA VWMA26: {vwma_26_prev:10.4f} | HA VWMA52: {vwma_52_prev:10.4f} | Trend: {trend}")
                    print(f"     GreenVol: {green_vol_prev:8.0f} | RedVol: {red_vol_prev:8.0f} | VolMA20: {vol_ma_20_prev:8.0f}")
                    print(f"     Total Vol: {prev_volume:8.0f}")
                    print(f"  🕯️  CURRENT HA CANDLE (FORMING):")
                    print(f"     Close: {format_price(ha_curr_close)} | Open: {format_price(ha_curr_open)} | {ha_curr_candle_type}")
                    print(f"     HA RSI(7): {rsi_7_curr:7.2f} | HA StochRSI: {stoch_rsi_curr:7.3f}")

                    # Check signals on PREVIOUS HEIKIN ASHI CANDLE
                    signal, strength, condition_num = check_signals(symbol, ha_df, indicators)

                    if signal:
                        cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
                        
                        print(f"\n  🎯 HEIKIN ASHI SIGNAL DETECTED ON PREVIOUS CANDLE!")
                        print(f"     {signal} | Condition #{condition_num} - {cond_name}")
                        print(f"     HA Signal Price: {ha_prev_close_str} | Strength: {strength}")

                        # Create unique key for this signal
                        signal_key = f"{symbol}_HA_{signal}_{condition_num}"
                        
                        # Check if we should alert
                        should_alert = False
                        now = datetime.now()
                        
                        if signal_key not in last_alert_time:
                            should_alert = True
                            print(f"     🆕 NEW HEIKIN ASHI SIGNAL - SENDING ALERT!")
                        else:
                            time_since_last = (now - last_alert_time[signal_key]).total_seconds()
                            if time_since_last > 180:  # Re-alert after 3 minutes
                                should_alert = True
                                print(f"     🔄 RE-ALERT (last alert {time_since_last:.0f}s ago)")
                            else:
                                print(f"     ℹ️ Already alerted {time_since_last:.0f}s ago (re-alert in {180-time_since_last:.0f}s)")
                        
                        if should_alert:
                            new_signals += 1
                            last_alert_time[signal_key] = now

                            # Update signal state
                            result = update_signal_state(symbol, f"{signal}_{condition_num}", strength)
                            if result == 'NEW_SIGNAL':
                                signal_tracker[symbol]['alert_sent'] = True

                            emoji = "🟢" if signal == 'BUY' else "🔴"
                            strength_emoji = "💪" if strength == 'STRONG' else "✅"

                            message = (
                                f"🚨 <b>HEIKIN ASHI {signal} SIGNAL!</b> {strength_emoji}\n\n"
                                f"<b>Symbol:</b> {symbol}\n"
                                f"<b>Exchange:</b> BINANCE\n"
                                f"<b>Signal Price (HA Close):</b> {ha_prev_close_str}\n"
                                f"<b>Current Price:</b> {price_str}\n"
                                f"<b>Condition:</b> #{condition_num} - {cond_name}\n"
                                f"<b>Strength:</b> {strength}\n"
                                f"🕯️ <b>Candles:</b> HEIKIN ASHI\n"
                                f"⏮️ <b>PREVIOUS HA CANDLE:</b> {ha_prev_candle_type}\n\n"
                                f"<b>Heikin Ashi Previous Candle Values:</b>\n"
                                f"• HA Close: {ha_prev_close_str}\n"
                                f"• HA Open: {format_price(ha_prev_open)}\n"
                                f"• HA High: {format_price(ha_prev_high)}\n"
                                f"• HA Low: {format_price(ha_prev_low)}\n"
                                f"• HA RSI(7): {rsi_7_prev:.2f}\n"
                                f"• HA StochRSI(14): {stoch_rsi_prev:.3f}\n"
                                f"• HA VWMA(26): {vwma_26_prev:.4f}\n"
                                f"• HA VWMA(52): {vwma_52_prev:.4f}\n"
                                f"• HA Trend: {trend}\n"
                                f"• Volume: {prev_volume:.0f}\n"
                                f"• VolMA(20): {vol_ma_20_prev:.0f}\n\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"⚡ <b>HEIKIN ASHI - 20s SCAN!</b>"
                            )

                            if send_alert(message):
                                print(f"     ✅ ALERT SENT SUCCESSFULLY!")
                            else:
                                print(f"     ❌ Alert FAILED")
                    else:
                        print(f"\n  ❌ No Heikin Ashi signal on previous candle")

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    traceback.print_exc()
                    continue

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            print(f"\n{'='*70}")
            print(f"📊 Cycle #{cycle_count} Summary (20s scan):")
            print(f"  • Exchange: BINANCE")
            print(f"  • Candles: HEIKIN ASHI 🕯️")
            print(f"  • Timeframe: 1 Minute")
            print(f"  • Checking: PREVIOUS COMPLETED HA CANDLE ⏮️")
            print(f"  • Processed: {processed}/{len(available_symbols)}")
            print(f"  • New Signals: {new_signals}")
            print(f"  • API Calls Saved: {api_calls_saved}")

            if last_alert_time:
                print(f"  • Recent HA Alerts:")
                for key, alert_time in list(last_alert_time.items())[-5:]:
                    print(f"    - {key} @ {alert_time.strftime('%H:%M:%S')}")

            print(f
