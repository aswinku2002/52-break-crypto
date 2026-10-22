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
    return "HMA Signal Generator (Delta Exchange ETH/USDT Perp) is running!"

@app.route('/health')
def health():
    return {
        "status": "ok",
        "exchange": "DELTA",
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

DELTA_API_KEY = os.environ.get('DELTA_API_KEY', '')
DELTA_API_SECRET = os.environ.get('DELTA_API_SECRET', '')

API_CALL_INTERVAL = 1.0
CHECK_INTERVAL = 60
CANDLES_TO_FETCH = 499
CACHE_EXPIRY_SECONDS = 55

MAX_CANDLES_IN_CACHE = 499

MIN_CANDLES_REQUIRED = 340

USE_CLOSED_CANDLES_ONLY = True

CONFIRMATION_CYCLES_REQUIRED = 1
RESET_CYCLES_REQUIRED = 2

# Delta Exchange perpetual futures symbol in CCXT format
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
# 4b. HMA 9/26 Trigger State Tracker (NEW)
# ============================================================
# Tracks the short-term HMA9/26 direction independently, so we only
# alert on FRESH flips that agree with the higher-timeframe trend.
trigger_state = {}
# Structure:
#   trigger_state[symbol] = {
#       'direction': 'BULL' | 'BEAR' | None,   # current HMA9/26 state
#       'last_change': datetime | None,
#       'last_alerted_direction': 'BULL' | 'BEAR' | None,
#   }

def reset_trigger_state(symbol):
    trigger_state[symbol] = {
        'direction': None,
        'last_change': None,
        'last_alerted_direction': None,
    }


# ============================================================
# 5. Delta Exchange Initialization (Non-Fatal)
# ============================================================
EXCHANGE = None

def init_delta():
    try:
        config = {
            'enableRateLimit': True,
        }

        if DELTA_API_KEY and DELTA_API_SECRET:
            config['apiKey'] = DELTA_API_KEY
            config['secret'] = DELTA_API_SECRET
            print("🔑 Delta Exchange: Using authenticated endpoints")
        else:
            print("🔓 Delta Exchange: Using public endpoints (no API keys required)")

        exchange = ccxt.delta(config)
        exchange.load_markets()
        print("✅ Connected to Delta Exchange successfully")
        return exchange

    except Exception as e:
        print(f"❌ Error initializing Delta Exchange: {e}")
        return None


def ensure_exchange():
    global EXCHANGE
    if EXCHANGE is not None:
        return EXCHANGE
    EXCHANGE = init_delta()
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
    Calculate HMA 9, 26, 45, 130, 135, 390.
    Returns None if ANY HMA is NaN.
    """
    try:
        close = df['close']

        hma_9 = calculate_hma(close, 9)
        hma_26 = calculate_hma(close, 26)
        hma_45 = calculate_hma(close, 45)
        hma_130 = calculate_hma(close, 130)
        hma_135 = calculate_hma(close, 135)
        hma_390 = calculate_hma(close, 390)

        last_9 = hma_9.iloc[-1]
        last_26 = hma_26.iloc[-1]
        last_45 = hma_45.iloc[-1]
        last_130 = hma_130.iloc[-1]
        last_135 = hma_135.iloc[-1]
        last_390 = hma_390.iloc[-1]
        last_price = close.iloc[-1]
        last_vol = df['vol'].iloc[-1]

        if any(pd.isna(v) for v in (last_9, last_26, last_45, last_130,
                                    last_135, last_390, last_price, last_vol)):
            return None

        return {
            'hma_9': hma_9,
            'hma_26': hma_26,
            'hma_45': hma_45,
            'hma_130': hma_130,
            'hma_135': hma_135,
            'hma_390': hma_390,
            'current_hma_9': float(last_9),
            'current_hma_26': float(last_26),
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
def detect_trend_and_trigger(indicators):
    """
    Returns:
        trend:   'BULL' | 'BEAR' | None   (HMA 45/130 & 135/390 alignment)
        trigger: 'BULL' | 'BEAR'          (HMA 9/26 direction)
    """
    if indicators is None:
        return None, None

    hma_9 = indicators['current_hma_9']
    hma_26 = indicators['current_hma_26']
    hma_45 = indicators['current_hma_45']
    hma_130 = indicators['current_hma_130']
    hma_135 = indicators['current_hma_135']
    hma_390 = indicators['current_hma_390']

    # Higher-timeframe trend (existing logic)
    if hma_45 > hma_130 and hma_135 > hma_390:
        trend = 'BULL'
    elif hma_45 < hma_130 and hma_135 < hma_390:
        trend = 'BEAR'
    else:
        trend = None

    # Short-term trigger (HMA 9 vs 26)
    trigger = 'BULL' if hma_9 > hma_26 else 'BEAR'

    return trend, trigger


def process_trigger(symbol, trend, trigger):
    """
    Decide whether to alert based on:
      - higher-timeframe trend alignment
      - FRESH flip of HMA 9/26 trigger
      - suppression of counter-trend flips (tracked silently)

    Returns:
      ('BUY' | 'SELL' | None, condition_num | None, note_str)
    """
    if symbol not in trigger_state:
        reset_trigger_state(symbol)

    st = trigger_state[symbol]
    prev_dir = st['direction']
    now = datetime.now()

    # No trend → nothing to do, but keep tracking trigger direction
    if trend is None:
        if prev_dir != trigger:
            st['direction'] = trigger
            st['last_change'] = now
        return None, None, "no-trend"

    # Trend is set. Determine if this is a fresh flip of the trigger.
    is_fresh_flip = (prev_dir != trigger)

    # Always update stored direction (this is how counter-trend flips
    # get "silently noted" — they reset the state without alerting).
    if is_fresh_flip:
        st['direction'] = trigger
        st['last_change'] = now

    # Alert only when fresh flip agrees with trend AND we haven't
    # already alerted for this same direction/trend combination.
    if is_fresh_flip and trigger == trend:
        # Prevent duplicate alert for the same trigger direction
        # if no opposite flip occurred in between.
        if st['last_alerted_direction'] == trigger:
            return None, None, "already-alerted"

        st['last_alerted_direction'] = trigger

        if trend == 'BULL' and trigger == 'BULL':
            return 'BUY', 3, "aligned-bull"
        if trend == 'BEAR' and trigger == 'BEAR':
            return 'SELL', 4, "aligned-bear"

    # Fresh flip but against the trend → suppressed (noted silently)
    if is_fresh_flip and trigger != trend:
        return None, None, "suppressed-counter-trend"

    return None, None, "no-fresh-flip"


# ============================================================
# 8. Telegram Alerts
# ============================================================
def escape_html(text):
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
        2: "Bearish HMA Alignment (45<130 & 135<390)",
        3: "Bullish Trigger (HMA9>HMA26) on Bullish Trend",
        4: "Bearish Trigger (HMA9<HMA26) on Bearish Trend",
    }

    print("\n" + "=" * 70)
    print("🚀 HMA SIGNAL GENERATOR — ETH/USDT PERP (DELTA EXCHANGE)")
    print("=" * 70)
    print("📊 Exchange: DELTA EXCHANGE (Perpetual Futures)")
    print("📈 CONFIGURATION:")
    print("  • ⚡ INSTANT ALERTS")
    print("  • Symbol: ETH/USDT:USDT")
    print("  • Timeframe: 1 MINUTE")
    print(f"  • Scan Interval: {CHECK_INTERVAL} SECONDS")
    print("  • Trend Filter: HMA(45), HMA(130), HMA(135), HMA(390)")
    print("  • Entry Trigger: HMA(9), HMA(26)")
    print(f"  • Closed candles only: {USE_CLOSED_CANDLES_ONLY}")
    print("📊 ACTIVE CONDITIONS:")
    print("  • BULLISH TREND:  HMA45 > HMA130 AND HMA135 > HMA390")
    print("  • BEARISH TREND:  HMA45 < HMA130 AND HMA135 < HMA390")
    print("  • BUY TRIGGER:    Trend BULLISH + HMA9 flips > HMA26")
    print("  • SELL TRIGGER:   Trend BEARISH + HMA9 flips < HMA26")
    print("  • Counter-trend trigger flips are noted silently (no alert)")
    print("=" * 70 + "\n")

    ex = ensure_exchange()
    if ex is None:
        print("⚠️ Initial Delta Exchange connection failed — will keep retrying.")

    if TOKEN and CHAT_ID:
        send_alert(
            "✅ <b>HMA Signal Bot Started — ETH/USDT Perp</b>\n\n"
            "📊 <b>Exchange:</b> DELTA EXCHANGE\n"
            "⏱️ <b>Timeframe:</b> 1 Minute\n"
            f"🔄 <b>Scan Interval:</b> {CHECK_INTERVAL} Seconds\n"
            "⚡ <b>Alert Mode:</b> INSTANT\n"
            "🔍 <b>Monitoring:</b> ETH/USDT:USDT\n"
            "📊 <b>Trend Filter:</b> HMA 45/130 &amp; 135/390\n"
            "🎯 <b>Entry Trigger:</b> HMA 9/26 (aligned with trend)\n"
            f"🕒 <b>Start:</b> {datetime.now().strftime('%H:%M:%S')}"
        )

    while True:
        try:
            cycle_count += 1

            ex = ensure_exchange()
            if ex is None:
                print("⏳ Delta Exchange unreachable — retrying in 90s...")
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

                    if df is None or len(df) < MIN_CANDLES_REQUIRED:
                        print(f"  ⚠️ {symbol}: Insufficient data "
                              f"({len(df) if df is not None else 0} candles, "
                              f"need {MIN_CANDLES_REQUIRED})")
                        continue

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
                    hma_9 = indicators['current_hma_9']
                    hma_26 = indicators['current_hma_26']
                    hma_45 = indicators['current_hma_45']
                    hma_130 = indicators['current_hma_130']
                    hma_135 = indicators['current_hma_135']
                    hma_390 = indicators['current_hma_390']

                    trend, trigger = detect_trend_and_trigger(indicators)

                    trend_short = "BULL" if hma_45 > hma_130 else "BEAR"
                    trend_long = "BULL" if hma_135 > hma_390 else "BEAR"
                    trigger_dir = "BULL" if hma_9 > hma_26 else "BEAR"
                    candle_type = "GREEN" if calc_df['close'].iloc[-1] > calc_df['open'].iloc[-1] else "RED"

                    print(f"  {symbol:18} | {price_str:12} | "
                          f"HMA9:{hma_9:9.4f} HMA26:{hma_26:9.4f} {trigger_dir:4} | "
                          f"HMA45:{hma_45:9.4f} HMA130:{hma_130:9.4f} {trend_short:4} | "
                          f"HMA135:{hma_135:9.4f} HMA390:{hma_390:9.4f} {trend_long:4} | "
                          f"Trend:{str(trend):4} | {candle_type:5} | Vol:{indicators['current_volume']:8.0f}")

                    signal, condition_num, note = process_trigger(symbol, trend, trigger)

                    if note == "suppressed-counter-trend":
                        print(f"  🔕 {symbol}: Trigger flipped {trigger} but trend is {trend} → noted silently, no alert")
                    elif note == "already-alerted":
                        print(f"  ⏸️ {symbol}: Trigger still {trigger} (already alerted) — no duplicate")
                    elif signal:
                        cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
                        print(f"  🎯 {symbol}: {signal} (Cond #{condition_num} - {cond_name})")

                        # Update signal tracker (keeps /health active-signal count useful)
                        result = update_signal_state(symbol, f"{signal}_{condition_num}", 'STRONG')

                        if result == 'NEW_SIGNAL':
                            new_signals += 1
                            signal_tracker[symbol]['alert_sent'] = True

                            cond_name_html = escape_html(cond_name)

                            message = (
                                f"🚨 <b>IMMEDIATE {signal} SIGNAL</b> 💪\n\n"
                                f"<b>Symbol:</b> {escape_html(symbol)}\n"
                                f"<b>Exchange:</b> DELTA EXCHANGE (Perp)\n"
                                f"<b>Price:</b> {price_str}\n"
                                f"<b>Condition:</b> #{condition_num} - {cond_name_html}\n"
                                f"<b>Strength:</b> STRONG\n\n"
                                f"<b>Entry Trigger (HMA 9/26):</b>\n"
                                f"• HMA(9):  {hma_9:.4f}\n"
                                f"• HMA(26): {hma_26:.4f}\n"
                                f"• Trigger: {trigger_dir}\n\n"
                                f"<b>Trend Filter (HMA 45/130 &amp; 135/390):</b>\n"
                                f"• HMA(45):  {hma_45:.4f}\n"
                                f"• HMA(130): {hma_130:.4f}\n"
                                f"• HMA(135): {hma_135:.4f}\n"
                                f"• HMA(390): {hma_390:.4f}\n"
                                f"• Short Trend (45/130): {trend_short}\n"
                                f"• Long Trend (135/390): {trend_long}\n"
                                f"• Overall Trend: {trend}\n\n"
                                f"<b>Candle:</b> {candle_type}\n"
                                f"<b>Volume:</b> {indicators['current_volume']:.0f}\n\n"
                                f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                                f"⚡ <b>{CHECK_INTERVAL}s SCAN — ALERT SENT IMMEDIATELY!</b>"
                            )

                            send_alert(message)
                            print(f"  🚨 ALERT SENT: {symbol} {signal} (Cond #{condition_num})")
                        else:
                            print(f"  ⏸️ {symbol}: Signal {signal} suppressed by signal tracker (already active)")

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    traceback.print_exc()
                    continue

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            active = get_active_signals()

            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Exchange: DELTA EXCHANGE")
            print(f"  • Timeframe: 1 Minute")
            print(f"  • Processed: {processed}/{len(available_symbols)}")
            print(f"  • New Signals: {new_signals}")
            print(f"  • Active Signals: {len(active)}")
            print(f"  • API Calls Saved: {api_calls_saved}")

            # Show trigger state per symbol
            for sym, st in trigger_state.items():
                print(f"  • Trigger state {sym}: "
                      f"dir={st['direction']} last_alerted={st['last_alerted_direction']}")

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
    app.run(host='0.0.0.0', port=port, threaded=True)
