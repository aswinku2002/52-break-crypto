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

# ============================================================
# 1. Flask Setup for Render
# ============================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "HMA Signal Generator (Bybit ETH/USDT Perp) is running!"

@app.route('/health')
def health():
    return {
        "status": "ok",
        "exchange": "BYBIT",
        "last_check": last_check_time,
        "cycle": cycle_count,
        "exchange_connected": EXCHANGE is not None,
        "active_signals": sum(1 for v in signal_tracker.values() if v['active']),
        "cache_stats": {
            "symbols_cached": len(ohlcv_cache),
            "total_api_calls_saved": api_calls_saved
        }
    }

# ============================================================
# 2. Configuration
# ============================================================
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', '')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', '')

API_CALL_INTERVAL = 1.0
CHECK_INTERVAL = 60
CANDLES_TO_FETCH = 499
CACHE_EXPIRY_SECONDS = 55          # FIX: was 60 → cache was never usable at 60s interval
MAX_CANDLES_IN_CACHE = 499

# FIX: minimum candles needed for HMA(390) to be valid.
# HMA(390) = WMA(2*WMA(195) - WMA(390), sqrt(390)=19) → needs ~409 candles.
# 450 gives safe margin.
MIN_CANDLES_REQUIRED = 450

# FIX: drop the last (unclosed) candle before computing indicators.
# A still-forming candle makes HMA values flip-flop mid-cycle.
USE_CLOSED_CANDLES_ONLY = True

CONFIRMATION_CYCLES_REQUIRED = 1
RESET_CYCLES_REQUIRED = 2

SYMBOLS = ['ETH/USDT:USDT']

last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0

ohlcv_cache = {}

# ============================================================
# 3. OHLCV Cache System
# ============================================================
def get_cached_ohlcv(exchange, symbol, timeframe='1m', limit=CANDLES_TO_FETCH):
    """Smart OHLCV fetcher with caching + incremental updates."""
    global api_calls_saved

    now = datetime.now()
    cache_key = f"{symbol}_{timeframe}"

    if cache_key in ohlcv_cache:
        cache_entry = ohlcv_cache[cache_key]
        age_seconds = (now - cache_entry['last_update']).total_seconds()

        if age_seconds < CACHE_EXPIRY_SECONDS:
            try:
                last_cached_ts = cache_entry['last_timestamp']
                # FIX: advance by one full candle (60_000 ms) instead of 1 ms.
                # +1 ms re-fetched the same candle every time — pointless.
                new_ohlcv = exchange.fetch_ohlcv(
                    symbol,
                    timeframe=timeframe,
                    since=last_cached_ts + 60_000,
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
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

        if ohlcv and len(ohlcv) > 0:
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


# ============================================================
# 4. Signal Tracker
# ============================================================
signal_tracker = {}

def update_signal_state(symbol, new_signal, strength='NORMAL'):
    """Send alert IMMEDIATELY on first detection."""
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


# ============================================================
# 5. Bybit Exchange Initialization (Non-Fatal)
# ============================================================
EXCHANGE = None

def init_bybit():
    try:
        config = {
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'},
        }

        if BYBIT_API_KEY and BYBIT_API_SECRET:
            config['apiKey'] = BYBIT_API_KEY
            config['secret'] = BYBIT_API_SECRET
            print("🔑 Bybit: Using authenticated endpoints")
        else:
            print("🔓 Bybit: Using public endpoints (no API keys required)")

        exchange = ccxt.bybit(config)
        exchange.load_markets()
        print("✅ Connected to Bybit successfully")
        return exchange

    except Exception as e:
        print(f"❌ Error initializing Bybit: {e}")
        return None


def ensure_exchange():
    global EXCHANGE
    if EXCHANGE is not None:
        return EXCHANGE
    EXCHANGE = init_bybit()
    return EXCHANGE


# ============================================================
# 6. HMA Indicator
# ============================================================
def calculate_hma(series, period):
    """HMA = WMA(2 * WMA(n/2) - WMA(n), sqrt(n))"""
    def wma(data, p):
        weights = np.arange(1, p + 1)
        return data.rolling(window=p).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )

    half_period = int(period / 2)
    sqrt_period = int(np.sqrt(period))

    wma_half = wma(series, half_period)
    wma_full = wma(series, period)

    raw_hma = 2 * wma_half - wma_full
    hma = wma(raw_hma, sqrt_period)
    return hma


def calculate_indicators(df):
    """
    Calculate HMA 45, 130, 135, 390.
    FIX: Returns None if ANY HMA is NaN (instead of coercing to 0).
         Coercing to 0 silently broke the "both must agree" rule —
         HMA135 > 0 is always True, so a fake BUY could fire.
    """
    try:
        close = df['close']

        hma_45 = calculate_hma(close, 45)
        hma_130 = calculate_hma(close, 130)
        hma_135 = calculate_hma(close, 135)
        hma_390 = calculate_hma(close, 390)

        last_45 = hma_45.iloc[-1]
        last_130 = hma_130.iloc[-1]
        last_135 = hma_135.iloc[-1]
        last_390 = hma_390.iloc[-1]
        last_price = close.iloc[-1]
        last_vol = df['vol'].iloc[-1]

        # FIX: refuse to evaluate if ANY value is invalid.
        if any(pd.isna(v) for v in (last_45, last_130, last_135, last_390, last_price, last_vol)):
            return None

        return {
            'hma_45': hma_45,
            'hma_130': hma_130,
            'hma_135': hma_135,
            'hma_390': hma_390,
            'current_hma_45': float(last_45),
            'current_hma_130': float(last_130),
            'current_hma_135': float(last_135),
            'current_hma_390': float(last_390),
            'current_price': float(last_price),
            'current_volume': float(last_vol),
        }
    except Exception as e:
        print(f"  ❌ Indicator calculation error: {e}")
        return None


# ============================================================
# 7. Signal Detection (HMA Conditions)
# ============================================================
def check_signals(symbol, df, indicators):
    """
    BULLISH: HMA45 > HMA130 AND HMA135 > HMA390
    BEARISH: HMA45 < HMA130 AND HMA135 < HMA390
    """
    try:
        if indicators is None:
            return None, None, None

        hma_45 = indicators['current_hma_45']
        hma_130 = indicators['current_hma_130']
        hma_135 = indicators['current_hma_135']
        hma_390 = indicators['current_hma_390']

        # Both pairs must agree. No coercion, no free passes.
        if hma_45 > hma_130 and hma_135 > hma_390:
            return 'BUY', 'STRONG', 1

        if hma_45 < hma_130 and hma_135 < hma_390:
            return 'SELL', 'STRONG', 2

        return None, None, None

    except Exception as e:
        print(f"  ❌ Signal detection error for {symbol}: {e}")
        return None, None, None


# ============================================================
# 8. Telegram Alerts
# ============================================================
def escape_html(text):
    """FIX: Telegram HTML parse mode needs & < > escaped, otherwise
       sendMessage returns 400 and the alert is silently dropped."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_alert(message):
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
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.8f}"


# ============================================================
# 9. Main Bot Loop
# ============================================================
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    condition_names = {
        1: "Bullish HMA Alignment (45>130 & 135>390)",
        2: "Bearish HMA Alignment (45<130 & 135<390)"
    }

    print("\n" + "=" * 70)
    print("🚀 HMA SIGNAL GENERATOR — ETH/USDT PERP (BYBIT)")
    print("=" * 70)
    print("📊 Exchange: BYBIT (USDT Perpetual)")
    print("📈 CONFIGURATION:")
    print("  • ⚡ INSTANT ALERTS")
    print("  • Symbol: ETH/USDT:USDT")
    print("  • Timeframe: 1 MINUTE")
    print(f"  • Scan Interval: {CHECK_INTERVAL} SECONDS")
    print("  • Indicators: HMA(45), HMA(130), HMA(135), HMA(390)")
    print(f"  • Closed candles only: {USE_CLOSED_CANDLES_ONLY}")
    print("📊 ACTIVE CONDITIONS:")
    print("  • BULLISH (BUY):  HMA45 > HMA130 AND HMA135 > HMA390")
    print("  • BEARISH (SELL): HMA45 < HMA130 AND HMA135 < HMA390")
    print("=" * 70 + "\n")

    ex = ensure_exchange()
    if ex is None:
        print("⚠️ Initial Bybit connection failed — will keep retrying.")

    if TOKEN and CHAT_ID:
        send_alert(
            "✅ <b>HMA Signal Bot Started — ETH/USDT Perp</b>\n\n"
            "📊 <b>Exchange:</b> BYBIT\n"
            "⏱️ <b>Timeframe:</b> 1 Minute\n"
            f"🔄 <b>Scan Interval:</b> {CHECK_INTERVAL} Seconds\n"
            "⚡ <b>Alert Mode:</b> INSTANT\n"
            "🔍 <b>Monitoring:</b> ETH/USDT:USDT\n"
            "📊 <b>Conditions:</b> HMA 45/130 &amp; 135/390 Alignment\n"
            f"🕒 <b>Start:</b> {datetime.now().strftime('%H:%M:%S')}"
        )

    while True:
        try:
            cycle_count += 1

            ex = ensure_exchange()
            if ex is None:
                print("⏳ Bybit unreachable — retrying in 90s...")
                time.sleep(90)
                continue

            new_signals = 0
            processed = 0

            print(f"\n{'=' * 70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print(f"{'=' * 70}")

            if cycle_count % 10 == 0:
                cleanup_cache()

            available_symbols = [s for s in SYMBOLS if s in ex.markets]
            if not available_symbols:
                print(f"⚠️ No symbols available. Markets loaded: {len(ex.markets)}")

            for i, symbol in enumerate(available_symbols):
                try:
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    df = get_cached_ohlcv(
                        ex, symbol,
                        timeframe='1m',
                        limit=CANDLES_TO_FETCH
                    )

                    # FIX: raise minimum from 400 → 450 so HMA(390) is always valid.
                    if df is None or len(df) < MIN_CANDLES_REQUIRED:
                        print(f"  ⚠️ {symbol}: Insufficient data "
                              f"({len(df) if df is not None else 0} candles, "
                              f"need {MIN_CANDLES_REQUIRED})")
                        continue

                    # FIX: drop the unclosed (still-forming) last candle.
                    # Otherwise HMA values wiggle mid-bar and signals flicker.
                    calc_df = df.iloc[:-1] if USE_CLOSED_CANDLES_ONLY else df
                    if len(calc_df) < MIN_CANDLES_REQUIRED:
                        print(f"  ⚠️ {symbol}: After dropping unclosed candle, "
                              f"only {len(calc_df)} candles remain")
                        continue

                    indicators = calculate_indicators(calc_df)
                    if indicators is None:
                        print(f"  ⚠️ {symbol}: Indicator calculation returned None "
                              f"(NaN values — skipping this cycle)")
                        continue

                    current_price = indicators['current_price']
                    price_str = format_price(current_price)
                    hma_45 = indicators['current_hma_45']
                    hma_130 = indicators['current_hma_130']
                    hma_135 = indicators['current_hma_135']
                    hma_390 = indicators['current_hma_390']

                    trend_short = "BULL" if hma_45 > hma_130 else "BEAR"
                    trend_long = "BULL" if hma_135 > hma_390 else "BEAR"
                    # Candle type based on the last CLOSED candle
                    candle_type = "GREEN" if calc_df['close'].iloc[-1] > calc_df['open'].iloc[-1] else "RED"

                    print(f"  {symbol:18} | {price_str:12} | "
                          f"HMA45:{hma_45:10.4f} | HMA130:{hma_130:10.4f} | {trend_short:4} | "
                          f"HMA135:{hma_135:10.4f} | HMA390:{hma_390:10.4f} | {trend_long:4} | "
                          f"{candle_type:5} | Vol:{indicators['current_volume']:8.0f}")

                    signal, strength, condition_num = check_signals(symbol, calc_df, indicators)

                    if signal:
                        cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
                        print(f"  🎯 {symbol}: {signal} (Cond #{condition_num} - {cond_name})")

                        result = update_signal_state(symbol, f"{signal}_{condition_num}", strength)

                        if result == 'NEW_SIGNAL':
                            new_signals += 1
                            signal_tracker[symbol]['alert_sent'] = True

                            strength_emoji = "💪" if strength == 'STRONG' else "✅"

                            # FIX: escape the condition name before embedding in HTML.
                            cond_name_html = escape_html(cond_name)

                            message = (
                                f"🚨 <b>IMMEDIATE {signal} SIGNAL</b> {strength_emoji}\n\n"
                                f"<b>Symbol:</b> {escape_html(symbol)}\n"
                                f"<b>Exchange:</b> BYBIT (Perp)\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Condition:</b> #{condition_num} - {cond_name_html}\n"
                                f"<b>Strength:</b> {strength}\n\n"
                                f"<b>HMA Indicators:</b>\n"
                                f"• HMA(45):  {hma_45:.4f}\n"
                                f"• HMA(130): {hma_130:.4f}\n"
                                f"• HMA(135): {hma_135:.4f}\n"
                                f"• HMA(390): {hma_390:.4f}\n"
                                f"• Short Trend (45/130): {trend_short}\n"
                                f"• Long Trend (135/390): {trend_long}\n"
                                f"• Candle: {candle_type}\n"
                                f"• Volume: {indicators['current_volume']:.0f}\n\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                                f"⚡ <b>{CHECK_INTERVAL}s SCAN — ALERT SENT IMMEDIATELY!</b>"
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

            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Exchange: BYBIT")
            print(f"  • Timeframe: 1 Minute")
            print(f"  • Processed: {processed}/{len(available_symbols)}")
            print(f"  • New Signals: {new_signals}")
            print(f"  • Active Signals: {len(active)}")
            print(f"  • API Calls Saved: {api_calls_saved}")

            if active:
                for sym, info in active.items():
                    print(f"    • {sym}: {info['signal']} ({info['strength']})")

            print(f"  • Next Scan: "
                  f"{(datetime.now() + timedelta(seconds=CHECK_INTERVAL)).strftime('%H:%M:%S')} UTC")
            print(f"{'=' * 70}\n")

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            if TOKEN and CHAT_ID:
                send_alert("🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            traceback.print_exc()
            time.sleep(30)


# ============================================================
# 10. Start Bot (background thread)
# ============================================================
print("\n🚀 Starting bot thread...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# ============================================================
# 11. Start Flask Server (main thread)
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    # FIX: threaded=True so a slow request can't block /health
    app.run(host='0.0.0.0', port=port, threaded=True) 

    
