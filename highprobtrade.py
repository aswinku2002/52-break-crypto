import os
import time
import math
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime, timedelta
import traceback

# ============================================================
# 1. Flask Setup for Render
# ============================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Multi-HMA Diff Signal Generator (Bybit ETH/USDT Perp) [HA] is running!"

@app.route('/health')
def health():
    return {
        "status": "ok",
        "exchange": "BYBIT",
        "candle_type": "HEIKIN_ASHI",
        "strategy": "MULTI_HMA_DIFF",
        "hma_periods": HMA_PERIODS,
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

# HMA(390) needs ~390 + sqrt(390) ~= 410 bars to stabilize.
# We fetch the max Bybit allows (1000 for many endpoints; 499 is safe).
CANDLES_TO_FETCH = 1000
MAX_CANDLES_IN_CACHE = 1000

# Must be > CHECK_INTERVAL or the incremental cache path never runs.
CACHE_EXPIRY_SECONDS = 90

MIN_CANDLES_REQUIRED = 450   # was 200; HMA(390) needs headroom

USE_CLOSED_CANDLES_ONLY = True

# Signal debounce: number of consecutive cycles a signal must persist
# before an alert fires. 1 = fire immediately. 2 = wait one extra cycle.
CONFIRMATION_CYCLES_REQUIRED = 2

# Deadband around zero (as fraction of HA close price).
DEADBAND_PCT = 0.0002   # 0.02%

# "STRONG" if the smaller-magnitude diff exceeds this % of price.
STRONG_THRESHOLD_PCT = 0.0005   # 0.05%

SYMBOLS = ['ETH/USDT:USDT']

# The four HMA periods driving the strategy
HMA_PERIODS = [45, 130, 135, 390]

# Kept for backward-compat display/logging labels
HMA_SHORT = HMA_PERIODS[0]
HMA_LONG = HMA_PERIODS[-1]

last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0

ohlcv_cache = {}

# ============================================================
# 3. OHLCV Cache System (caches RAW candles, not Heikin Ashi)
# ============================================================
def get_cached_ohlcv(exchange, symbol, timeframe='1m', limit=CANDLES_TO_FETCH):
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
# 4. Heikin Ashi Conversion
# ============================================================
def to_heikin_ashi(df):
    if df is None or len(df) == 0:
        return df

    ha = df.copy().reset_index(drop=True)

    raw_open  = df['open'].to_numpy(dtype=float)
    raw_high  = df['high'].to_numpy(dtype=float)
    raw_low   = df['low'].to_numpy(dtype=float)
    raw_close = df['close'].to_numpy(dtype=float)

    n = len(df)
    ha_close = (raw_open + raw_high + raw_low + raw_close) / 4.0
    ha_open = np.empty(n, dtype=float)

    ha_open[0] = (raw_open[0] + raw_close[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

    ha_high = np.maximum.reduce([raw_high, ha_open, ha_close])
    ha_low  = np.minimum.reduce([raw_low,  ha_open, ha_close])

    ha['open']  = ha_open
    ha['high']  = ha_high
    ha['low']   = ha_low
    ha['close'] = ha_close

    return ha


# ============================================================
# 5. Signal Tracker (with confirmation debounce)
# ============================================================
signal_tracker = {}

def update_signal_state(symbol, new_signal, strength='NORMAL'):
    now = datetime.now()

    if symbol not in signal_tracker:
        signal_tracker[symbol] = {
            'current_signal': None,
            'pending_signal': None,
            'pending_count': 0,
            'active': False,
            'alert_sent': False,
            'last_signal_time': now,
            'signal_strength': 'NORMAL',
        }

    tracker = signal_tracker[symbol]

    if not new_signal:
        tracker['pending_signal'] = None
        tracker['pending_count'] = 0

        if tracker['active']:
            tracker['active'] = False
            tracker['alert_sent'] = False
            tracker['current_signal'] = None
            return 'SIGNAL_ENDED'
        return None

    if tracker['active'] and new_signal == tracker['current_signal']:
        tracker['pending_signal'] = new_signal
        tracker['pending_count'] = 0
        return 'SAME_SIGNAL'

    if new_signal == tracker['pending_signal']:
        tracker['pending_count'] += 1
    else:
        tracker['pending_signal'] = new_signal
        tracker['pending_count'] = 1

    if tracker['pending_count'] >= CONFIRMATION_CYCLES_REQUIRED:
        if tracker['active'] and tracker['current_signal'] != new_signal:
            print(f"  ⚠️ {symbol}: {tracker['current_signal']} signal ended")

        tracker['current_signal'] = new_signal
        tracker['active'] = True
        tracker['alert_sent'] = False
        tracker['last_signal_time'] = now
        tracker['signal_strength'] = strength
        tracker['pending_signal'] = None
        tracker['pending_count'] = 0
        return 'NEW_SIGNAL'

    return 'PENDING'


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
# 6. Bybit Exchange Initialization
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
# 7. HMA + Multi-Period Diff Indicators (Pine-parity rounding)
# ============================================================
def _pine_round(x):
    if x >= 0:
        return int(math.floor(x + 0.5))
    return int(math.ceil(x - 0.5))


def calculate_hma(series, period):
    """
    HMA = WMA(2 * WMA(n/2) - WMA(n), sqrt(n))
    Pine uses math.round() for both n/2 and sqrt(n).
    """
    def wma(data, p):
        weights = np.arange(1, p + 1, dtype=float)
        wsum = weights.sum()
        return data.rolling(window=p).apply(
            lambda x: np.dot(x, weights) / wsum, raw=True
        )

    half_period = _pine_round(period / 2.0)
    sqrt_period = _pine_round(math.sqrt(period))

    wma_half = wma(series, half_period)
    wma_full = wma(series, period)

    raw_hma = 2 * wma_half - wma_full
    hma = wma(raw_hma, sqrt_period)
    return hma


def calculate_indicators(df):
    """
    Multi-period version.

        src    = HA_Close
        hma(p) = HMA(src, p)  for p in HMA_PERIODS
        diff(p) = src - hma(p)
    """
    try:
        if df is None or len(df) == 0:
            return None

        src = df['close']  # HA_Close

        hmas = {}
        diffs = {}
        for p in HMA_PERIODS:
            hmas[p] = calculate_hma(src, p)
            diffs[p] = src - hmas[p]

        last_src = src.iloc[-1]
        last_vol = df['vol'].iloc[-1]

        last_hmas = {p: hmas[p].iloc[-1] for p in HMA_PERIODS}
        last_diffs = {p: diffs[p].iloc[-1] for p in HMA_PERIODS}

        # NaN guard across everything we will use
        vals = [last_src, last_vol] + list(last_hmas.values()) + list(last_diffs.values())
        if any(pd.isna(v) for v in vals):
            return None

        return {
            'hmas': hmas,
            'diffs': diffs,
            'current_src': float(last_src),
            'current_hmas': {p: float(last_hmas[p]) for p in HMA_PERIODS},
            'current_diffs': {p: float(last_diffs[p]) for p in HMA_PERIODS},
            'current_volume': float(last_vol),
        }
    except Exception as e:
        print(f"  ❌ Indicator calculation error: {e}")
        return None


# ============================================================
# 8. Signal Detection — 4-HMA Diff Logic + Deadband
# ============================================================
def check_signals(symbol, df, indicators):
    """
    BUY  when diff(45), diff(130), diff(135), diff(390) are ALL > +deadband
    SELL when diff(45), diff(130), diff(135), diff(390) are ALL < -deadband

    deadband = |HA close| * DEADBAND_PCT  (0 disables it)
    """
    try:
        if indicators is None:
            return None, None, None

        src = indicators['current_src']
        diffs = indicators['current_diffs']
        deadband = abs(src) * DEADBAND_PCT
        strong_threshold = abs(src) * STRONG_THRESHOLD_PCT

        d_vals = [diffs[p] for p in HMA_PERIODS]

        if all(d > deadband for d in d_vals):
            # STRONG if even the weakest diff still clears the strong threshold
            smaller = min(d_vals)
            strength = 'STRONG' if smaller > strong_threshold else 'NORMAL'
            return 'BUY', strength, 1

        if all(d < -deadband for d in d_vals):
            # Closest-to-zero (least negative) determines how "strong" the move is
            closer_to_zero = max(d_vals)
            strength = 'STRONG' if abs(closer_to_zero) > strong_threshold else 'NORMAL'
            return 'SELL', strength, 2

        return None, None, None

    except Exception as e:
        print(f"  ❌ Signal detection error for {symbol}: {e}")
        return None, None, None


# ============================================================
# 9. Telegram Alerts (with one retry)
# ============================================================
def escape_html(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_alert(message, retries=1):
    if not TOKEN or not CHAT_ID:
        print("  ⚠️ No Telegram credentials configured!")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    params = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                print("  ✅ Telegram alert sent successfully!")
                return True
            else:
                print(f"  ❌ Telegram error: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"  ❌ Telegram error (attempt {attempt + 1}): {e}")

        if attempt < retries:
            time.sleep(2)

    return False


def format_price(price):
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.8f}"


def format_diff(value):
    return f"{value:+.4f}"


# ============================================================
# 10. Main Bot Loop
# ============================================================
def run_bot():
    global last_check_time, cycle_count, api_calls_saved

    condition_names = {
        1: "All Diffs Above Zero (45, 130, 135, 390)",
        2: "All Diffs Below Zero (45, 130, 135, 390)"
    }

    periods_label = "/".join(str(p) for p in HMA_PERIODS)

    print("\n" + "=" * 70)
    print(f"🚀 MULTI-HMA ({periods_label}) DIFF SIGNAL GENERATOR — ETH/USDT PERP")
    print("📊 CANDLE TYPE: HEIKIN ASHI")
    print("=" * 70)
    print("📊 Exchange: BYBIT (USDT Perpetual)")
    print("📈 CONFIGURATION:")
    print("  • ⚡ INSTANT ALERTS (debounced)")
    print("  • Symbol: ETH/USDT:USDT")
    print("  • Timeframe: 1 MINUTE")
    print(f"  • Scan Interval: {CHECK_INTERVAL} SECONDS")
    print(f"  • Indicators: HMA{tuple(HMA_PERIODS)} on HA_Close")
    for p in HMA_PERIODS:
        print(f"     diff({p}) = HA_Close - HMA({p})")
    print(f"  • Confirmation cycles: {CONFIRMATION_CYCLES_REQUIRED}")
    print(f"  • Deadband: {DEADBAND_PCT * 100:.4f}% of price")
    print(f"  • Strong threshold: {STRONG_THRESHOLD_PCT * 100:.4f}% of price")
    print("📊 ACTIVE CONDITIONS:")
    print(f"  • BULLISH (BUY):  ALL diffs > 0  for periods {HMA_PERIODS}")
    print(f"  • BEARISH (SELL): ALL diffs < 0  for periods {HMA_PERIODS}")
    print("=" * 70 + "\n")

    ex = ensure_exchange()
    if ex is None:
        print("⚠️ Initial Bybit connection failed — will keep retrying.")

    if TOKEN and CHAT_ID:
        send_alert(
            f"✅ <b>Multi-HMA Diff Bot Started — ETH/USDT Perp</b>\n\n"
            "📊 <b>Exchange:</b> BYBIT\n"
            "🕯️ <b>Candles:</b> Heikin Ashi\n"
            "⏱️ <b>Timeframe:</b> 1 Minute\n"
            f"🔄 <b>Scan Interval:</b> {CHECK_INTERVAL} Seconds\n"
            f"⏳ <b>Confirmation:</b> {CONFIRMATION_CYCLES_REQUIRED} cycles\n"
            "⚡ <b>Alert Mode:</b> INSTANT (debounced)\n"
            "🔍 <b>Monitoring:</b> ETH/USDT:USDT\n"
            f"📊 <b>HMA Periods:</b> {periods_label}\n"
            f"📊 <b>Logic:</b> all diffs same side of zero\n"
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

                    raw_df = get_cached_ohlcv(
                        ex, symbol,
                        timeframe='1m',
                        limit=CANDLES_TO_FETCH
                    )

                    if raw_df is None or len(raw_df) < MIN_CANDLES_REQUIRED:
                        print(f"  ⚠️ {symbol}: Insufficient raw data "
                              f"({len(raw_df) if raw_df is not None else 0} candles, "
                              f"need {MIN_CANDLES_REQUIRED})")
                        continue

                    # Drop the still-forming last bar (HA is recursive).
                    if USE_CLOSED_CANDLES_ONLY and len(raw_df) > MIN_CANDLES_REQUIRED:
                        closed_df = raw_df.iloc[:-1]
                    else:
                        closed_df = raw_df

                    if len(closed_df) < MIN_CANDLES_REQUIRED:
                        print(f"  ⚠️ {symbol}: After dropping unclosed candle, "
                              f"only {len(closed_df)} candles remain")
                        continue

                    ha_df = to_heikin_ashi(closed_df)
                    if ha_df is None or len(ha_df) == 0:
                        print(f"  ⚠️ {symbol}: Heikin Ashi conversion produced empty frame")
                        continue

                    indicators = calculate_indicators(ha_df)
                    if indicators is None:
                        print(f"  ⚠️ {symbol}: Indicator calculation returned None "
                              f"(NaN values — skipping this cycle)")
                        continue

                    src     = indicators['current_src']
                    hmas    = indicators['current_hmas']
                    diffs   = indicators['current_diffs']

                    price_str = format_price(src)
                    deadband = abs(src) * DEADBAND_PCT

                    # Determine ABOVE / BELOW / FLAT per period
                    diff_states = {}
                    for p in HMA_PERIODS:
                        d = diffs[p]
                        if d > deadband:
                            diff_states[p] = "ABOVE"
                        elif d < -deadband:
                            diff_states[p] = "BELOW"
                        else:
                            diff_states[p] = "FLAT"

                    ha_candle_type = "GREEN" if ha_df['close'].iloc[-1] > ha_df['open'].iloc[-1] else "RED"

                    # Compact one-line indicator dump
                    hmas_str  = " ".join(f"HMA{p}:{hmas[p]:.2f}" for p in HMA_PERIODS)
                    diffs_str = " ".join(
                        f"d{p}:{format_diff(diffs[p])}({diff_states[p][0]})"
                        for p in HMA_PERIODS
                    )

                    print(f"  {symbol:18} | HA:{price_str:12} | {hmas_str}")
                    print(f"     {diffs_str} | HA-{ha_candle_type:5} | "
                          f"Vol:{indicators['current_volume']:8.0f}")

                    signal, strength, condition_num = check_signals(symbol, ha_df, indicators)

                    # Feed the state machine whether or not a raw signal fired
                    if signal:
                        result = update_signal_state(symbol, f"{signal}_{condition_num}", strength)
                    else:
                        result = update_signal_state(symbol, None, 'NORMAL')

                    if result == 'PENDING':
                        print(f"  ⏳ {symbol}: {signal} pending confirmation "
                              f"({signal_tracker[symbol]['pending_count']}/"
                              f"{CONFIRMATION_CYCLES_REQUIRED})")

                    elif result == 'NEW_SIGNAL' and signal:
                        new_signals += 1
                        signal_tracker[symbol]['alert_sent'] = True

                        cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
                        cond_name_html = escape_html(cond_name)
                        strength_emoji = "💪" if strength == 'STRONG' else "✅"

                        indicator_lines = "\n".join(
                            f"• HMA({p}): {hmas[p]:.4f}  →  diff({p}) = "
                            f"{format_diff(diffs[p])}  ({diff_states[p]} zero)"
                            for p in HMA_PERIODS
                        )

                        message = (
                            f"🚨 <b>IMMEDIATE {signal} SIGNAL</b> {strength_emoji}\n"
                            f"🕯️ <b>Heikin Ashi · Multi-HMA Diff</b>\n\n"
                            f"<b>Symbol:</b> {escape_html(symbol)}\n"
                            f"<b>Exchange:</b> BYBIT (Perp)\n"
                            f"<b>HA Close:</b> {price_str}\n"
                            f"<b>Condition:</b> #{condition_num} - {cond_name_html}\n"
                            f"<b>Strength:</b> {strength}\n"
                            f"<b>Confirmed after:</b> {CONFIRMATION_CYCLES_REQUIRED} cycles\n\n"
                            f"<b>Indicator Values (on HA_Close):</b>\n"
                            f"{indicator_lines}\n"
                            f"• HA Candle: {ha_candle_type}\n"
                            f"• Volume: {indicators['current_volume']:.0f}\n\n"
                            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                            f"⚡ <b>{CHECK_INTERVAL}s SCAN — ALERT SENT IMMEDIATELY!</b>"
                        )

                        send_alert(message)
                        print(f"  🚨 ALERT SENT: {symbol} {signal} (Cond #{condition_num})")

                    elif result == 'SIGNAL_ENDED':
                        print(f"  🔻 {symbol}: Active signal ended")

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    traceback.print_exc()
                    continue

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            active = get_active_signals()

            print(f"\n📊 Cycle #{cycle_count} Summary:")
            print(f"  • Exchange: BYBIT")
            print(f"  • Candles: Heikin Ashi")
            print(f"  • Strategy: Multi-HMA {periods_label} Difference")
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
# 11. Start Bot (background thread)
# ============================================================
print("\n🚀 Starting bot thread...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# ============================================================
# 12. Start Flask Server (main thread)
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port, threaded=True)
