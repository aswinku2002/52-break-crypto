import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime, timedelta
import traceback

# ============================================================================
# 1. FLASK SETUP
# ============================================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Heikin Ashi Signal Generator - v3.0 (No StochRSI)"

@app.route('/health')
def health():
    active_signals = {k: v['signal'] for k, v in signal_tracker.items() if v['active']}
    return {
        "status": "ok",
        "exchange": "BINANCE",
        "last_check": last_check_time,
        "cycle": cycle_count,
        "active_signals": active_signals,
        "total_alerts_sent": total_alerts_sent
    }

@app.route('/test_alert')
def test_alert():
    """Manual test endpoint to verify Telegram works"""
    if TOKEN and CHAT_ID:
        test_msg = f"🧪 <b>TEST ALERT</b>\n\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nBot is running!"
        success = send_telegram_alert(test_msg)
        return f"Test alert {'SENT ✅' if success else 'FAILED ❌'}"
    return "Telegram not configured"

# ============================================================================
# 2. CONFIGURATION
# ============================================================================
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

print(f"\n{'='*60}")
print(f"🔧 CONFIGURATION CHECK:")
print(f"  TELEGRAM_TOKEN: {'✅ SET' if TOKEN else '❌ MISSING'}")
print(f"  CHAT_ID: {'✅ SET' if CHAT_ID else '❌ MISSING'}")
print(f"{'='*60}\n")

BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Performance Configuration
API_CALL_INTERVAL = 1.0
CHECK_INTERVAL = 20
CANDLES_TO_FETCH = 100
CACHE_EXPIRY_SECONDS = 60
MAX_CANDLES_IN_CACHE = 100

# Trading pairs
SYMBOLS = ['ETH/USDT']

# ============================================================================
# 3. GLOBAL STATE VARIABLES
# ============================================================================
last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0
total_alerts_sent = 0

# OHLCV Cache
ohlcv_cache = {}

# Signal Tracker - SINGLE SOURCE OF TRUTH
# Format: {symbol: {'signal': 'BUY_1', 'active': True/False, 'last_alert_time': datetime}}
signal_tracker = {}

# ============================================================================
# 4. OHLCV CACHE SYSTEM
# ============================================================================
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
                new_ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=last_cached_ts + 1, limit=5)
                if new_ohlcv and len(new_ohlcv) > 0:
                    new_df = pd.DataFrame(new_ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
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
            except Exception:
                pass

    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if len(ohlcv) > 0:
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            ohlcv_cache[cache_key] = {
                'data': df,
                'last_update': now,
                'last_timestamp': df['ts'].iloc[-1]
            }
            return df
    except Exception as e:
        print(f"  ❌ Fetch error: {e}")
    return None

def cleanup_cache():
    """Remove expired cache entries"""
    now = datetime.now()
    expired = [k for k, v in ohlcv_cache.items() if (now - v['last_update']).total_seconds() > 300]
    for k in expired:
        del ohlcv_cache[k]
    if expired:
        print(f"  🧹 Cleaned {len(expired)} expired cache entries")

# ============================================================================
# 5. BINANCE EXCHANGE INITIALIZATION
# ============================================================================
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
        print(f"❌ Binance error: {e}")
        return None

EXCHANGE = init_binance()
if not EXCHANGE:
    print("❌ Failed to connect to Binance. Exiting.")
    exit(1)

# ============================================================================
# 6. HEIKIN ASHI CALCULATION
# ============================================================================
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
        
        # Heikin Ashi Open
        ha_df['ha_open'] = 0.0
        ha_df['ha_open'].iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
        for i in range(1, len(df)):
            ha_df['ha_open'].iloc[i] = (ha_df['ha_open'].iloc[i-1] + ha_df['ha_close'].iloc[i-1]) / 2
        
        # Heikin Ashi High
        ha_df['ha_high'] = 0.0
        for i in ha_df.index:
            ha_df.loc[i, 'ha_high'] = max(df.loc[i, 'high'], ha_df.loc[i, 'ha_open'], ha_df.loc[i, 'ha_close'])
        
        # Heikin Ashi Low
        ha_df['ha_low'] = 0.0
        for i in ha_df.index:
            ha_df.loc[i, 'ha_low'] = min(df.loc[i, 'low'], ha_df.loc[i, 'ha_open'], ha_df.loc[i, 'ha_close'])
        
        # Preserve volume
        ha_df['vol'] = df['vol']
        
        return ha_df
    except Exception as e:
        print(f"  ❌ Heikin Ashi calculation error: {e}")
        return None

# ============================================================================
# 7. INDICATOR CALCULATIONS (Stochastic RSI REMOVED)
# ============================================================================
def calculate_indicators(ha_df):
    """
    Calculate indicators on Heikin Ashi data:
    - RSI(7)
    - VWMA(26) and VWMA(52)
    - Volume MA(20)
    (Stochastic RSI removed - redundant with RSI(7))
    """
    try:
        close = ha_df['ha_close']
        high = ha_df['ha_high']
        low = ha_df['ha_low']
        open_price = ha_df['ha_open']
        volume = ha_df['vol']

        # RSI(7) only - no StochRSI needed
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=7).mean()
        avg_loss = loss.rolling(window=7).mean()
        rs = avg_gain / avg_loss
        rsi_7 = 100 - (100 / (1 + rs))

        # VWMA (Volume Weighted Moving Average)
        def vwma(period):
            typical_price = (high + low + close) / 3
            return (typical_price * volume).rolling(window=period).sum() / volume.rolling(window=period).sum()
        
        vwma_26 = vwma(26)
        vwma_52 = vwma(52)
        
        # Volume MA(20)
        vol_ma_20 = volume.rolling(window=20).mean()
        
        # Green/Red Volume (based on Heikin Ashi close vs open)
        green_volume = volume.where(close > open_price, 0)
        red_volume = volume.where(close < open_price, 0)

        # Return previous completed candle values (index -2)
        idx = -2
        
        return {
            'prev_rsi_7': float(rsi_7.iloc[idx]) if len(rsi_7) > abs(idx) and not pd.isna(rsi_7.iloc[idx]) else 0,
            'prev_vwma_26': float(vwma_26.iloc[idx]) if len(vwma_26) > abs(idx) and not pd.isna(vwma_26.iloc[idx]) else 0,
            'prev_vwma_52': float(vwma_52.iloc[idx]) if len(vwma_52) > abs(idx) and not pd.isna(vwma_52.iloc[idx]) else 0,
            'prev_vol_ma_20': float(vol_ma_20.iloc[idx]) if len(vol_ma_20) > abs(idx) and not pd.isna(vol_ma_20.iloc[idx]) else 0,
            'prev_green_vol': float(green_volume.iloc[idx]) if len(green_volume) > abs(idx) and not pd.isna(green_volume.iloc[idx]) else 0,
            'prev_red_vol': float(red_volume.iloc[idx]) if len(red_volume) > abs(idx) and not pd.isna(red_volume.iloc[idx]) else 0,
            'prev_volume': float(volume.iloc[idx]) if len(volume) > abs(idx) and not pd.isna(volume.iloc[idx]) else 0,
            'prev_ha_close': float(close.iloc[idx]) if len(close) > abs(idx) and not pd.isna(close.iloc[idx]) else 0,
            'prev_ha_open': float(open_price.iloc[idx]) if len(open_price) > abs(idx) and not pd.isna(open_price.iloc[idx]) else 0,
            'prev_ha_high': float(high.iloc[idx]) if len(high) > abs(idx) and not pd.isna(high.iloc[idx]) else 0,
            'prev_ha_low': float(low.iloc[idx]) if len(low) > abs(idx) and not pd.isna(low.iloc[idx]) else 0,
            # Current candle for display
            'curr_rsi_7': float(rsi_7.iloc[-1]) if len(rsi_7) > 0 and not pd.isna(rsi_7.iloc[-1]) else 0,
        }
    except Exception as e:
        print(f"  ❌ Indicator error: {e}")
        traceback.print_exc()
        return None

# ============================================================================
# 8. SIGNAL DETECTION - CONDITIONS 1, 3, 6, 8 (NO StochRSI)
# ============================================================================
def detect_signal(indicators):
    """
    Check previous completed Heikin Ashi candle for conditions 1, 3, 6, 8
    
    Simplified - only 3 filters per condition:
    1. RSI(7) for momentum
    2. VWMA cross for trend
    3. Volume > MA20 for participation
    
    Returns: (signal_type, strength, condition_number) or (None, None, None)
    """
    if indicators is None:
        return None, None, None

    rsi = indicators['prev_rsi_7']
    vwma26 = indicators['prev_vwma_26']
    vwma52 = indicators['prev_vwma_52']
    green_vol = indicators['prev_green_vol']
    red_vol = indicators['prev_red_vol']
    vol_ma = indicators['prev_vol_ma_20']

    # Condition 1: BUY - Strong Bullish Continuation
    # RSI(7) > 70 AND VWMA26 > VWMA52 AND Green Volume > Volume MA20
    if rsi > 70 and vwma26 > vwma52 and green_vol > vol_ma:
        return 'BUY', 'STRONG', 1

    # Condition 8: BUY - Bullish Dip Buy
    # RSI(7) < 30 AND VWMA26 > VWMA52 AND Green Volume > Volume MA20
    if rsi < 30 and vwma26 > vwma52 and green_vol > vol_ma:
        return 'BUY', 'NORMAL', 8

    # Condition 3: SELL - Strong Bearish Continuation
    # RSI(7) < 30 AND VWMA52 > VWMA26 AND Red Volume > Volume MA20
    if rsi < 30 and vwma52 > vwma26 and red_vol > vol_ma:
        return 'SELL', 'STRONG', 3

    # Condition 6: SELL - Failed Rally in Bear Trend
    # RSI(7) > 70 AND VWMA52 > VWMA26 AND Red Volume > Volume MA20
    if rsi > 70 and vwma52 > vwma26 and red_vol > vol_ma:
        return 'SELL', 'STRONG', 6

    return None, None, None

# ============================================================================
# 9. SIMPLIFIED SIGNAL TRACKING & NOTIFICATION SYSTEM
# ============================================================================
def process_signal(symbol, signal_type, strength, condition_num, indicators, current_price, ha_prev_close):
    """
    Clean signal processing logic:
    - If signal is NEW (not currently active): Send alert immediately
    - If signal is SAME as currently active: Do nothing
    - If signal is DIFFERENT from currently active: End old, start new, send alert
    
    Signal key format: "BUY_1", "SELL_3", etc.
    """
    global total_alerts_sent
    
    signal_key = f"{signal_type}_{condition_num}"
    now = datetime.now()
    
    # Initialize tracker for this symbol if not exists
    if symbol not in signal_tracker:
        signal_tracker[symbol] = {
            'signal': None,
            'active': False,
            'last_alert_time': None
        }
    
    tracker = signal_tracker[symbol]
    current_active_signal = tracker['signal']
    
    # CASE 1: Same signal still active - no action needed
    if tracker['active'] and current_active_signal == signal_key:
        print(f"  ℹ️  Signal {signal_key} already active (alerted at {tracker['last_alert_time'].strftime('%H:%M:%S') if tracker['last_alert_time'] else 'N/A'})")
        return False
    
    # CASE 2: Different signal was active - end it
    if tracker['active'] and current_active_signal != signal_key:
        print(f"  ⚠️  Previous signal {current_active_signal} ended")
    
    # CASE 3: New signal - send alert
    tracker['signal'] = signal_key
    tracker['active'] = True
    tracker['last_alert_time'] = now
    
    # Send the alert
    condition_names = {
        1: "Bullish Continuation",
        3: "Bearish Continuation",
        6: "Failed Rally (Bear)",
        8: "Bullish Dip Buy"
    }
    
    cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
    emoji = "🟢" if signal_type == 'BUY' else "🔴"
    strength_emoji = "💪" if strength == 'STRONG' else "✅"
    trend = "BULL" if indicators['prev_vwma_26'] > indicators['prev_vwma_52'] else "BEAR"
    
    message = (
        f"🚨 <b>{emoji} {signal_type} SIGNAL!</b> {strength_emoji}\n\n"
        f"<b>Symbol:</b> {symbol}\n"
        f"<b>Exchange:</b> BINANCE\n"
        f"<b>Condition:</b> #{condition_num} - {cond_name}\n"
        f"<b>Strength:</b> {strength}\n"
        f"<b>HA Signal Price:</b> {format_price(ha_prev_close)}\n"
        f"<b>Current Price:</b> {format_price(current_price)}\n"
        f"🕯️ <b>Candles:</b> HEIKIN ASHI\n\n"
        f"<b>Previous HA Candle Values:</b>\n"
        f"• RSI(7): {indicators['prev_rsi_7']:.2f}\n"
        f"• VWMA(26): {indicators['prev_vwma_26']:.4f}\n"
        f"• VWMA(52): {indicators['prev_vwma_52']:.4f}\n"
        f"• Trend: {trend}\n"
        f"• Volume: {indicators['prev_volume']:.0f}\n"
        f"• VolMA(20): {indicators['prev_vol_ma_20']:.0f}\n\n"
        f"<b>Time:</b> {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"⚡ <b>HEIKIN ASHI - 20s SCAN</b>"
    )
    
    success = send_telegram_alert(message)
    if success:
        total_alerts_sent += 1
        print(f"  ✅ ALERT #{total_alerts_sent} SENT: {signal_key}")
    else:
        print(f"  ❌ ALERT FAILED for {signal_key}")
    
    return True

def clear_signal(symbol):
    """
    Called when a signal condition is no longer met.
    Resets the tracker so the same signal can trigger a new alert later.
    """
    if symbol in signal_tracker and signal_tracker[symbol]['active']:
        old_signal = signal_tracker[symbol]['signal']
        signal_tracker[symbol]['active'] = False
        signal_tracker[symbol]['signal'] = None
        print(f"  ⚠️  Signal {old_signal} ended for {symbol} - reset complete")

# ============================================================================
# 10. TELEGRAM NOTIFICATION SYSTEM
# ============================================================================
def send_telegram_alert(message, retry_on_failure=True):
    """
    Send Telegram alert using POST request.
    Retries once after 2-second delay on failure.
    """
    if not TOKEN or not CHAT_ID:
        print("  ⚠️  Telegram not configured! Set TELEGRAM_TOKEN and CHAT_ID environment variables.")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            return True
        else:
            print(f"  ❌ Telegram HTTP {response.status_code}: {response.text}")
            
            # Retry once after 2 seconds
            if retry_on_failure:
                print(f"  🔄 Retrying in 2 seconds...")
                time.sleep(2)
                try:
                    response = requests.post(url, json=payload, timeout=10)
                    if response.status_code == 200:
                        print(f"  ✅ Retry successful!")
                        return True
                    else:
                        print(f"  ❌ Retry failed: HTTP {response.status_code}: {response.text}")
                except Exception as retry_error:
                    print(f"  ❌ Retry error: {retry_error}")
            
            return False
            
    except requests.exceptions.Timeout:
        print(f"  ❌ Telegram timeout")
        if retry_on_failure:
            print(f"  🔄 Retrying after timeout...")
            time.sleep(2)
            return send_telegram_alert(message, retry_on_failure=False)
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

# ============================================================================
# 11. DEBUG OUTPUT (SIMPLIFIED - NO StochRSI)
# ============================================================================
def print_debug_info(symbol, indicators):
    """Print detailed debug information for each condition"""
    if indicators is None:
        return
    
    rsi = indicators['prev_rsi_7']
    vwma26 = indicators['prev_vwma_26']
    vwma52 = indicators['prev_vwma_52']
    green_vol = indicators['prev_green_vol']
    red_vol = indicators['prev_red_vol']
    vol_ma = indicators['prev_vol_ma_20']
    
    trend = "BULL 🟢" if vwma26 > vwma52 else "BEAR 🔴"
    
    print(f"\n  🔍 DEBUG: {symbol}")
    print(f"  {'─'*50}")
    print(f"  📊 VALUES (Previous HA Candle):")
    print(f"     RSI(7):          {rsi:8.2f}  (>70 overbought, <30 oversold)")
    print(f"     VWMA(26):        {vwma26:10.4f}")
    print(f"     VWMA(52):        {vwma52:10.4f}")
    print(f"     Trend:           {trend}")
    print(f"     Green Volume:    {green_vol:8.0f}")
    print(f"     Red Volume:      {red_vol:8.0f}")
    print(f"     Vol MA(20):      {vol_ma:8.0f}")
    print(f"     Total Volume:    {indicators['prev_volume']:8.0f}")
    
    print(f"\n  🧪 CONDITION CHECKS (3 filters each):")
    
    # Condition 1: BUY - RSI>70 + VWMA26>52 + GreenVol>MA
    c1 = [rsi > 70, vwma26 > vwma52, green_vol > vol_ma]
    print(f"  Cond 1 (BUY Bullish):  RSI>70:{'✅' if c1[0] else '❌'} | VWMA26>52:{'✅' if c1[2] else '❌'} | GreenVol>MA:{'✅' if c1[3] else '❌'} | {'🟢 SIGNAL' if all(c1) else '❌'}")
    
    # Condition 8: BUY - RSI<30 + VWMA26>52 + GreenVol>MA
    c8 = [rsi < 30, vwma26 > vwma52, green_vol > vol_ma]
    print(f"  Cond 8 (BUY Dip):     RSI<30:{'✅' if c8[0] else '❌'} | VWMA26>52:{'✅' if c8[1] else '❌'} | GreenVol>MA:{'✅' if c8[2] else '❌'} | {'🟢 SIGNAL' if all(c8) else '❌'}")
    
    # Condition 3: SELL - RSI<30 + VWMA52>26 + RedVol>MA
    c3 = [rsi < 30, vwma52 > vwma26, red_vol > vol_ma]
    print(f"  Cond 3 (SELL Bearish): RSI<30:{'✅' if c3[0] else '❌'} | VWMA52>26:{'✅' if c3[1] else '❌'} | RedVol>MA:{'✅' if c3[2] else '❌'} | {'🔴 SIGNAL' if all(c3) else '❌'}")
    
    # Condition 6: SELL - RSI>70 + VWMA52>26 + RedVol>MA
    c6 = [rsi > 70, vwma52 > vwma26, red_vol > vol_ma]
    print(f"  Cond 6 (SELL Fail):   RSI>70:{'✅' if c6[0] else '❌'} | VWMA52>26:{'✅' if c6[1] else '❌'} | RedVol>MA:{'✅' if c6[2] else '❌'} | {'🔴 SIGNAL' if all(c6) else '❌'}")

# ============================================================================
# 12. MAIN BOT LOOP
# ============================================================================
def run_bot():
    global last_check_time, cycle_count, total_alerts_sent, api_calls_saved

    print("\n" + "="*70)
    print("🚀 HEIKIN ASHI SIGNAL BOT v3.0 - ETH/USDT (NO StochRSI)")
    print("="*70)
    print(f"📊 Exchange: BINANCE ONLY")
    print(f"🕯️  Candles: HEIKIN ASHI")
    print(f"⏱️  Timeframe: 1 MINUTE")
    print(f"🔄 Scan Interval: {CHECK_INTERVAL} SECONDS")
    print(f"🔍 Checking: PREVIOUS COMPLETED HA CANDLE")
    print(f"\n📊 CONDITIONS ACTIVE (3 filters each):")
    print(f"  • Cond 1 (BUY):  RSI(7)>70  +  VWMA26>VWMA52  +  GreenVol>VolMA20")
    print(f"  • Cond 3 (SELL): RSI(7)<30  +  VWMA52>VWMA26  +  RedVol>VolMA20")
    print(f"  • Cond 6 (SELL): RSI(7)>70  +  VWMA52>VWMA26  +  RedVol>VolMA20")
    print(f"  • Cond 8 (BUY):  RSI(7)<30  +  VWMA26>VWMA52  +  GreenVol>VolMA20")
    print(f"\n📱 NOTIFICATION SYSTEM:")
    print(f"  • Single source of truth: signal_tracker")
    print(f"  • No duplicate alerts while signal active")
    print(f"  • Auto-reset when signal disappears")
    print(f"  • New alert when signal reappears")
    print(f"  • Retry once on Telegram failure")
    print(f"\n⚡ IMPROVEMENTS in v3.0:")
    print(f"  • Removed Stochastic RSI (redundant with RSI(7))")
    print(f"  • Faster signals - 3 filters instead of 4")
    print(f"  • No lag from StochRSI smoothing")
    print("="*70 + "\n")

    # Test Telegram on startup
    print(f"📱 Testing Telegram connection...")
    test_msg = (
        f"✅ <b>Heikin Ashi Bot v3.0 Started</b>\n\n"
        f"📊 <b>ETH/USDT</b> | 1m | HA Candles\n"
        f"🔄 20s Scans | Conditions 1,3,6,8\n"
        f"⚡ StochRSI removed - faster signals\n"
        f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    if send_telegram_alert(test_msg):
        total_alerts_sent += 1
        print(f"✅ Startup alert sent!\n")
    else:
        print(f"❌ Startup alert FAILED - Check TELEGRAM_TOKEN and CHAT_ID!\n")

    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]
    print(f"✅ Monitoring {len(available_symbols)} symbols: {', '.join(available_symbols)}\n")

    while True:
        try:
            cycle_count += 1
            signals_found = 0
            processed = 0

            print(f"\n{'='*70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Alerts: {total_alerts_sent}")
            print(f"{'='*70}")

            # Periodic cache cleanup
            if cycle_count % 10 == 0:
                cleanup_cache()

            for i, symbol in enumerate(available_symbols):
                try:
                    # Rate limiting between symbols
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    # Fetch data
                    df = get_cached_ohlcv(EXCHANGE, symbol, timeframe='1m', limit=CANDLES_TO_FETCH)
                    if df is None or len(df) < 60:
                        print(f"  ⚠️  {symbol}: Insufficient data ({len(df) if df is not None else 0} candles)")
                        continue

                    # Convert to Heikin Ashi
                    ha_df = calculate_heikin_ashi(df)
                    if ha_df is None:
                        continue

                    # Calculate indicators
                    indicators = calculate_indicators(ha_df)
                    if indicators is None:
                        continue

                    # Get current prices
                    current_price = df['close'].iloc[-1]
                    ha_prev_close = indicators['prev_ha_close']

                    # Brief status line
                    trend = "BULL 🟢" if indicators['prev_vwma_26'] > indicators['prev_vwma_52'] else "BEAR 🔴"
                    print(f"\n  {symbol} | Price: {format_price(current_price)} | "
                          f"HA RSI(7): {indicators['prev_rsi_7']:.2f} | "
                          f"Trend: {trend}")

                    # Print debug info
                    print_debug_info(symbol, indicators)

                    # Detect signal
                    signal_type, strength, condition_num = detect_signal(indicators)

                    if signal_type:
                        # Signal found - process it
                        condition_names = {
                            1: "Bullish Continuation",
                            3: "Bearish Continuation",
                            6: "Failed Rally (Bear)",
                            8: "Bullish Dip Buy"
                        }
                        cond_name = condition_names.get(condition_num, f"Condition {condition_num}")
                        print(f"\n  🎯 SIGNAL DETECTED: {signal_type} #{condition_num} - {cond_name}")
                        print(f"     HA Signal Price: {format_price(ha_prev_close)} | Strength: {strength}")
                        
                        was_sent = process_signal(
                            symbol, signal_type, strength, condition_num,
                            indicators, current_price, ha_prev_close
                        )
                        if was_sent:
                            signals_found += 1
                    else:
                        # No signal - clear any existing active signal
                        if symbol in signal_tracker and signal_tracker[symbol]['active']:
                            clear_signal(symbol)

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error processing {symbol}: {e}")
                    traceback.print_exc()
                    continue

            # Update last check time
            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

            # Cycle summary
            active_signals = {k: v['signal'] for k, v in signal_tracker.items() if v['active']}
            print(f"\n{'='*70}")
            print(f"📊 Cycle #{cycle_count} Summary:")
            print(f"  • Processed: {processed} symbols")
            print(f"  • New Signals Sent: {signals_found}")
            print(f"  • Total Alerts: {total_alerts_sent}")
            print(f"  • Active Signals: {len(active_signals)}")
            if active_signals:
                for sym, sig in active_signals.items():
                    print(f"    - {sym}: {sig}")
            print(f"  • API Calls Saved: {api_calls_saved}")
            print(f"  • Next Scan: {(datetime.now() + timedelta(seconds=CHECK_INTERVAL)).strftime('%H:%M:%S')}")
            print(f"{'='*70}\n")

            # Wait for next scan
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            if TOKEN and CHAT_ID:
                send_telegram_alert(f"🛑 <b>Bot Stopped</b>\n\nTotal alerts sent: {total_alerts_sent}\nTime: {datetime.now().strftime('%H:%M:%S')}")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            traceback.print_exc()
            time.sleep(CHECK_INTERVAL)

# ============================================================================
# 13. STARTUP
# ============================================================================
if __name__ == "__main__":
    print("\n🚀 Starting Heikin Ashi Signal Bot v3.0 (No StochRSI)...")
    
    # Start bot in background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Start Flask web server
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    print(f"📱 Test alert: http://localhost:{port}/test_alert")
    print(f"❤️  Health check: http://localhost:{port}/health")
    print()
    app.run(host='0.0.0.0', port=port)
