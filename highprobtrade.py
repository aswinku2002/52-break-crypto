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
    return "RSI/VWMA/Volume Signal Generator is running!"

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

# 4. Indicator Calculations
def calculate_indicators(df):
    """
    Calculate RSI(7), Stoch RSI(14), VWMA(26), VWMA(52), Volume MA(20)
    """
    try:
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['vol']

        # RSI(7)
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=7).mean()
        avg_loss = loss.rolling(window=7).mean()
        rs = avg_gain / avg_loss
        rsi_7 = 100 - (100 / (1 + rs))

        # Stoch RSI(14)
        rsi_14 = pd.Series(index=df.index, dtype=float)
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

        # VWMA (Volume Weighted Moving Average)
        def vwma(period):
            typical_price = (high + low + close) / 3
            vwma_val = (typical_price * volume).rolling(window=period).sum() / volume.rolling(window=period).sum()
            return vwma_val

        vwma_26 = vwma(26)
        vwma_52 = vwma(52)

        # Volume MA(20)
        vol_ma_20 = volume.rolling(window=20).mean()

        # Green/Red Volume
        green_volume = volume.where(close > open, 0)
        red_volume = volume.where(close < open, 0)

        return {
            'rsi_7': rsi_7,
            'stoch_rsi': stoch_rsi,
            'vwma_26': vwma_26,
            'vwma_52': vwma_52,
            'vol_ma_20': vol_ma_20,
            'green_volume': green_volume,
            'red_volume': red_volume,
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

# 5. Signal Detection - Conditions 1, 3, 6, 8
def check_signals(symbol, df, indicators):
    """
    Check for conditions 1, 3, 6, 8
    
    Condition 1 (BUY): RSI(7)>70, StochRSI>0.8, VWMA26>VWMA52, GreenVol>VolMA20
    Condition 3 (SELL): RSI(7)<30, StochRSI<0.2, VWMA52>VWMA26, RedVol>VolMA20
    Condition 6 (SELL): RSI(7)>70, StochRSI>0.8, VWMA52>VWMA26, RedVol>VolMA20
    Condition 8 (BUY): RSI(7)<30, StochRSI<0.2, VWMA26>VWMA52, GreenVol>VolMA20
    """
    try:
        if indicators is None:
            return None, None, None

        rsi_7 = indicators['current_rsi_7']
        stoch_rsi = indicators['current_stoch_rsi']
        vwma_26 = indicators['current_vwma_26']
        vwma_52 = indicators['current_vwma_52']
        green_vol = indicators['current_green_vol']
        red_vol = indicators['current_red_vol']
        vol_ma_20 = indicators['current_vol_ma_20']

        # Condition 1: Strong Bullish Continuation
        if (rsi_7 > 70 and 
            stoch_rsi > 0.8 and 
            vwma_26 > vwma_52 and 
            green_vol > vol_ma_20):
            return 'BUY', 'STRONG', 1

        # Condition 8: Bullish Dip Buy
        if (rsi_7 < 30 and 
            stoch_rsi < 0.2 and 
            vwma_26 > vwma_52 and 
            green_vol > vol_ma_20):
            return 'BUY', 'NORMAL', 8

        # Condition 3: Strong Bearish Continuation
        if (rsi_7 < 30 and 
            stoch_rsi < 0.2 and 
            vwma_52 > vwma_26 and 
            red_vol > vol_ma_20):
            return 'SELL', 'STRONG', 3

        # Condition 6: Failed Rally in Bear Trend
        if (rsi_7 > 70 and 
            stoch_rsi > 0.8 and 
            vwma_52 > vwma_26 and 
            red_vol > vol_ma_20):
            return 'SELL', 'STRONG', 6

        return None, None, None

    except Exception as e:
        print(f"  ❌ Signal detection error for {symbol}: {e}")
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

    condition_names = {
        1: "Bullish Continuation (Trend+Volume)",
        3: "Bearish Continuation (Trend+Volume)",
        6: "Failed Rally in Bear Trend",
        8: "Bullish Dip Buy"
    }

    print("\n" + "="*70)
    print("🚀 RSI/VWMA/VOLUME SIGNAL GENERATOR - ETH/USDT")
    print("="*70)
    print(f"📊 Exchange: BINANCE ONLY")
    print(f"\n📈 CONFIGURATION:")
    print(f"  • ⚡ INSTANT ALERTS")
    print(f"  • Symbol: ETH/USDT")
    print(f"  • Timeframe: 1 MINUTE")
    print(f"  • Scan Interval: 20 SECONDS ⚡")
    print(f"  • Indicators: RSI(7), StochRSI(14), VWMA(26/52), VolMA(20)")
    print(f"\n📊 ACTIVE CONDITIONS (1, 3, 6, 8):")
    print(f"  • Cond 1 (BUY): RSI(7)>70, StochRSI>0.8, VWMA26>VWMA52, GreenVol>VolMA20")
    print(f"  • Cond 3 (SELL): RSI(7)<30, StochRSI<0.2, VWMA52>VWMA26, RedVol>VolMA20")
    print(f"  • Cond 6 (SELL): RSI(7)>70, StochRSI>0.8, VWMA52>VWMA26, RedVol>VolMA20")
    print(f"  • Cond 8 (BUY): RSI(7)<30, StochRSI<0.2, VWMA26>VWMA52, GreenVol>VolMA20")
    print("="*70 + "\n")

    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)}/{len(SYMBOLS)} symbols on Binance")

    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>RSI/VWMA/Vol Bot Started - ETH/USDT</b>\n\n"
            f"📊 <b>Exchange:</b> BINANCE ONLY\n"
            f"⏱️ <b>Timeframe:</b> 1 Minute\n"
            f"🔄 <b>Scan Interval:</b> 20 Seconds ⚡\n"
            f"⚡ <b>Alert Mode:</b> INSTANT\n"
            f"🔍 <b>Monitoring:</b> ETH/USDT only\n"
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

            if cycle_count % 10 == 0:
                cleanup_cache()

            for i, symbol in enumerate(available_symbols):
                try:
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    df = get_cached_ohlcv(
                        EXCHANGE, 
                        symbol, 
                        timeframe='1m',
                        limit=CANDLES_TO_FETCH
                    )

                    if df is None or len(df) < 60:
                        print(f"  ⚠️ {symbol}: Insufficient data ({len(df) if df is not None else 0} candles)")
                        continue

                    # Calculate indicators
                    indicators = calculate_indicators(df)

                    if indicators is None:
                        print(f"  ⚠️ {symbol}: Indicator calculation failed")
                        continue

                    # Get current values
                    current_price = df['close'].iloc[-1]
                    price_str = format_price(current_price)
                    rsi_7 = indicators['current_rsi_7']
                    stoch_rsi = indicators['current_stoch_rsi']
                    vwma_26 = indicators['current_vwma_26']
                    vwma_52 = indicators['current_vwma_52']
                    green_vol = indicators['current_green_vol']
                    red_vol = indicators['current_red_vol']
                    vol_ma_20 = indicators['current_vol_ma_20']

                    # Display current values
                    trend = "BULL" if vwma_26 > vwma_52 else "BEAR"
                    candle_type = "GREEN" if current_price > df['open'].iloc[-1] else "RED"
                    
                    print(f"  {symbol:12} | {price_str:12} | "
                          f"RSI7:{rsi_7:6.2f} | StochRSI:{stoch_rsi:6.3f} | "
                          f"VWMA26:{vwma_26:10.4f} | VWMA52:{vwma_52:10.4f} | "
                          f"{trend:4} | {candle_type:5} | "
                          f"Vol:{indicators['current_volume']:8.0f} | VolMA20:{vol_ma_20:8.0f}")

                    # Check signals
                    signal, strength, condition_num = check_signals(symbol, df, indicators)

                    if signal:
                        cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
                        print(f"  🎯 {symbol}: {signal} (Cond #{condition_num} - {cond_name})")

                        result = update_signal_state(symbol, f"{signal}_{condition_num}", strength)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1
                            signal_tracker[symbol]['alert_sent'] = True

                            emoji = "🟢" if signal == 'BUY' else "🔴"
                            strength_emoji = "💪" if strength == 'STRONG' else "✅"

                            message = (
                                f"🚨 <b>IMMEDIATE {signal} SIGNAL</b> {strength_emoji}\n\n"
                                f"<b>Symbol:</b> {symbol}\n"
                                f"<b>Exchange:</b> BINANCE\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Condition:</b> #{condition_num} - {cond_name}\n"
                                f"<b>Strength:</b> {strength}\n\n"
                                f"<b>Indicators:</b>\n"
                                f"• RSI(7): {rsi_7:.2f}\n"
                                f"• StochRSI(14): {stoch_rsi:.3f}\n"
                                f"• VWMA(26): {vwma_26:.4f}\n"
                                f"• VWMA(52): {vwma_52:.4f}\n"
                                f"• Trend: {trend}\n"
                                f"• Candle: {candle_type}\n"
                                f"• Volume: {indicators['current_volume']:.0f}\n"
                                f"• VolMA(20): {vol_ma_20:.0f}\n\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"⚡ <b>20 SECOND SCAN - ALERT SENT IMMEDIATELY!</b>"
                            )

                            send_alert(message)
                            print(f"  🚨 ALERT SENT: {symbol} {signal} (Cond #{condition_num})")

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    traceback.print_exc()
                    continue

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            active = get_active_signals()

            print(f"\n📊 Cycle #{cycle_count} Summary (20s scan):")
            print(f"  • Exchange: BINANCE")
            print(f"  • Timeframe: 1 Minute")
            print(f"  • Processed: {processed}/{len(available_symbols)}")
            print(f"  • New Signals: {new_signals}")
            print(f"  • Active Signals: {len(active)}")
            print(f"  • API Calls Saved: {api_calls_saved}")

            if active:
                for sym, info in active.items():
                    print(f"    • {sym}: {info['signal']} ({info['strength']})")

            print(f"  • Next Scan: {(datetime.now() + timedelta(seconds=CHECK_INTERVAL)).strftime('%H:%M:%S')}")
            print(f"{'='*70}\n")

            # ⚡ SLEEP FOR 20 SECONDS
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            if TOKEN and CHAT_ID:
                send_alert("🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            traceback.print_exc()
            time.sleep(20)  # ⚡ Also 20 seconds on error

# 8. Start Bot
print("\n🚀 Starting bot...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# 9. Start Flask Server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port)
