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
    return "Heikin Ashi Signal Generator - DEBUG MODE (5min)"

@app.route('/health')
def health():
    active_count = sum(1 for v in signal_tracker.items() if v[1]['active'])
    return {
        "status": "ok",
        "exchange": "BINANCE",
        "last_check": last_check_time,
        "cycle": cycle_count,
        "active_signals": active_count,
        "total_alerts_sent": total_alerts_sent
    }

@app.route('/test_alert')
def test_alert():
    """Manual test endpoint to verify Telegram works"""
    if TOKEN and CHAT_ID:
        test_msg = f"🧪 <b>TEST ALERT</b>\n\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nBot is running on 5min candles!"
        success = send_alert(test_msg)
        return f"Test alert {'SENT ✅' if success else 'FAILED ❌'}"
    return "Telegram not configured"

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

print(f"\n{'='*60}")
print(f"🔧 CONFIGURATION CHECK:")
print(f"  TELEGRAM_TOKEN: {'✅ SET' if TOKEN else '❌ MISSING'}")
print(f"  CHAT_ID: {'✅ SET' if CHAT_ID else '❌ MISSING'}")
print(f"{'='*60}\n")

# Binance API Keys (optional)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Performance Configuration
API_CALL_INTERVAL = 1.0
CHECK_INTERVAL = 30  # Check every 30 seconds for 5min candles
CANDLES_TO_FETCH = 100  # Fetch 100 candles (about 8.3 hours of 5min data)
CACHE_EXPIRY_SECONDS = 120  # Cache for 2 minutes
MAX_CANDLES_IN_CACHE = 150

# Signal Settings
CONFIRMATION_CYCLES_REQUIRED = 1
RESET_CYCLES_REQUIRED = 2

# Trading pairs
SYMBOLS = ['ETH/USDT']

# Global variables
last_check_time = "Never"
cycle_count = 0
api_calls_saved = 0
total_alerts_sent = 0

# OHLCV Cache
ohlcv_cache = {}

def get_cached_ohlcv(exchange, symbol, timeframe='5m', limit=100):
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
                    ohlcv_cache[cache_key] = {'data': combined_df, 'last_update': now, 'last_timestamp': combined_df['ts'].iloc[-1]}
                    api_calls_saved += 1
                    return combined_df
                else:
                    api_calls_saved += 1
                    return cache_entry['data']
            except:
                pass

    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if len(ohlcv) > 0:
            df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
            ohlcv_cache[cache_key] = {'data': df, 'last_update': now, 'last_timestamp': df['ts'].iloc[-1]}
            return df
    except Exception as e:
        print(f"  ❌ Fetch error: {e}")
    return None

def cleanup_cache():
    now = datetime.now()
    expired = [k for k, v in ohlcv_cache.items() if (now - v['last_update']).total_seconds() > 300]
    for k in expired:
        del ohlcv_cache[k]

signal_tracker = {}

def update_signal_state(symbol, new_signal, strength='NORMAL'):
    now = datetime.now()
    if symbol not in signal_tracker:
        signal_tracker[symbol] = {'current_signal': None, 'active': False, 'alert_sent': False, 'last_signal_time': now, 'signal_strength': 'NORMAL'}
    
    tracker = signal_tracker[symbol]
    if new_signal and new_signal != tracker['current_signal']:
        tracker['current_signal'] = new_signal
        tracker['active'] = True
        tracker['alert_sent'] = False
        tracker['last_signal_time'] = now
        tracker['signal_strength'] = strength
        return 'NEW_SIGNAL'
    elif new_signal and new_signal == tracker['current_signal']:
        return 'SAME_SIGNAL'
    else:
        if tracker['active']:
            tracker['active'] = False
            tracker['alert_sent'] = False
            return 'SIGNAL_ENDED'
    return None

def init_binance():
    try:
        config = {'enableRateLimit': True, 'options': {'defaultType': 'spot'}}
        if BINANCE_API_KEY and BINANCE_API_SECRET:
            config['apiKey'] = BINANCE_API_KEY
            config['secret'] = BINANCE_API_SECRET
        exchange = ccxt.binance(config)
        exchange.load_markets()
        print(f"✅ Connected to Binance")
        return exchange
    except Exception as e:
        print(f"❌ Binance error: {e}")
        return None

EXCHANGE = init_binance()
if not EXCHANGE:
    exit(1)

def calculate_heikin_ashi(df):
    """Convert to Heikin Ashi candles"""
    try:
        ha = pd.DataFrame(index=df.index)
        ha['ha_close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4
        ha['ha_open'] = 0.0
        ha['ha_open'].iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
        for i in range(1, len(df)):
            ha['ha_open'].iloc[i] = (ha['ha_open'].iloc[i-1] + ha['ha_close'].iloc[i-1]) / 2
        ha['ha_high'] = df[['high']].copy()
        ha['ha_low'] = df[['low']].copy()
        for i in ha.index:
            ha.loc[i, 'ha_high'] = max(df.loc[i, 'high'], ha.loc[i, 'ha_open'], ha.loc[i, 'ha_close'])
            ha.loc[i, 'ha_low'] = min(df.loc[i, 'low'], ha.loc[i, 'ha_open'], ha.loc[i, 'ha_close'])
        ha['vol'] = df['vol']
        return ha
    except Exception as e:
        print(f"  ❌ HA calculation error: {e}")
        return None

def calculate_indicators(ha_df):
    """Calculate all indicators on Heikin Ashi data"""
    try:
        close = ha_df['ha_close']
        high = ha_df['ha_high']
        low = ha_df['ha_low']
        open_p = ha_df['ha_open']
        volume = ha_df['vol']

        # RSI(7)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(7).mean()
        avg_loss = loss.rolling(7).mean()
        rs = avg_gain / avg_loss
        rsi_7 = 100 - (100 / (1 + rs))

        # RSI(14) for StochRSI
        avg_gain_14 = gain.rolling(14).mean()
        avg_loss_14 = loss.rolling(14).mean()
        rs_14 = avg_gain_14 / avg_loss_14
        rsi_14 = 100 - (100 / (1 + rs_14))
        lowest_rsi = rsi_14.rolling(14).min()
        highest_rsi = rsi_14.rolling(14).max()
        stoch_rsi = (rsi_14 - lowest_rsi) / (highest_rsi - lowest_rsi)

        # VWMA
        def vwma(p):
            tp = (high + low + close) / 3
            return (tp * volume).rolling(p).sum() / volume.rolling(p).sum()
        
        vwma_26 = vwma(26)
        vwma_52 = vwma(52)
        vol_ma_20 = volume.rolling(20).mean()
        green_vol = volume.where(close > open_p, 0)
        red_vol = volume.where(close < open_p, 0)

        idx = -2  # Previous completed candle
        
        return {
            'prev_rsi_7': float(rsi_7.iloc[idx]) if len(rsi_7) > abs(idx) and not pd.isna(rsi_7.iloc[idx]) else 0,
            'prev_stoch_rsi': float(stoch_rsi.iloc[idx]) if len(stoch_rsi) > abs(idx) and not pd.isna(stoch_rsi.iloc[idx]) else 0,
            'prev_vwma_26': float(vwma_26.iloc[idx]) if len(vwma_26) > abs(idx) and not pd.isna(vwma_26.iloc[idx]) else 0,
            'prev_vwma_52': float(vwma_52.iloc[idx]) if len(vwma_52) > abs(idx) and not pd.isna(vwma_52.iloc[idx]) else 0,
            'prev_vol_ma_20': float(vol_ma_20.iloc[idx]) if len(vol_ma_20) > abs(idx) and not pd.isna(vol_ma_20.iloc[idx]) else 0,
            'prev_green_vol': float(green_vol.iloc[idx]) if len(green_vol) > abs(idx) and not pd.isna(green_vol.iloc[idx]) else 0,
            'prev_red_vol': float(red_vol.iloc[idx]) if len(red_vol) > abs(idx) and not pd.isna(red_vol.iloc[idx]) else 0,
            'prev_volume': float(volume.iloc[idx]) if len(volume) > abs(idx) and not pd.isna(volume.iloc[idx]) else 0,
            'prev_ha_close': float(close.iloc[idx]) if len(close) > abs(idx) and not pd.isna(close.iloc[idx]) else 0,
            'prev_ha_open': float(open_p.iloc[idx]) if len(open_p) > abs(idx) and not pd.isna(open_p.iloc[idx]) else 0,
            'prev_ha_high': float(high.iloc[idx]) if len(high) > abs(idx) and not pd.isna(high.iloc[idx]) else 0,
            'prev_ha_low': float(low.iloc[idx]) if len(low) > abs(idx) and not pd.isna(low.iloc[idx]) else 0,
        }
    except Exception as e:
        print(f"  ❌ Indicator error: {e}")
        traceback.print_exc()
        return None

def check_signals_debug(symbol, indicators):
    """
    DEBUG VERSION - Shows exactly why each condition passes or fails
    """
    if indicators is None:
        print(f"  ❌ No indicators data")
        return None, None, None

    rsi = indicators['prev_rsi_7']
    stoch = indicators['prev_stoch_rsi']
    vwma26 = indicators['prev_vwma_26']
    vwma52 = indicators['prev_vwma_52']
    green_vol = indicators['prev_green_vol']
    red_vol = indicators['prev_red_vol']
    vol_ma = indicators['prev_vol_ma_20']
    
    trend = "BULL" if vwma26 > vwma52 else "BEAR"

    print(f"\n  🔍 DEBUG: Checking conditions for {symbol} (5min)")
    print(f"  {'─'*50}")
    print(f"  📊 VALUES:")
    print(f"     RSI(7):          {rsi:8.2f}  (Need >70 for Cond1/6, <30 for Cond3/8)")
    print(f"     StochRSI(14):    {stoch:8.3f}  (Need >0.8 for Cond1/6, <0.2 for Cond3/8)")
    print(f"     VWMA(26):        {vwma26:10.4f}")
    print(f"     VWMA(52):        {vwma52:10.4f}")
    print(f"     Trend:           {trend}")
    print(f"     Green Volume:    {green_vol:8.0f}")
    print(f"     Red Volume:      {red_vol:8.0f}")
    print(f"     Vol MA(20):      {vol_ma:8.0f}")
    print(f"     Total Volume:    {indicators['prev_volume']:8.0f}")
    
    print(f"\n  🧪 CONDITION CHECKS:")

    # Condition 1: BUY - Strong Bullish Continuation
    c1_rsi = rsi > 70
    c1_stoch = stoch > 0.8
    c1_vwma = vwma26 > vwma52
    c1_vol = green_vol > vol_ma
    c1_all = c1_rsi and c1_stoch and c1_vwma and c1_vol
    
    print(f"  Cond 1 (BUY Bullish):")
    print(f"     RSI>70:        {'✅' if c1_rsi else '❌'} ({rsi:.2f})")
    print(f"     Stoch>0.8:     {'✅' if c1_stoch else '❌'} ({stoch:.3f})")
    print(f"     VWMA26>VWMA52: {'✅' if c1_vwma else '❌'} ({vwma26:.4f} vs {vwma52:.4f})")
    print(f"     GreenVol>MA20: {'✅' if c1_vol else '❌'} ({green_vol:.0f} vs {vol_ma:.0f})")
    print(f"     RESULT:        {'🟢 SIGNAL!' if c1_all else '❌ No signal'}")

    # Condition 8: BUY - Bullish Dip Buy
    c8_rsi = rsi < 30
    c8_stoch = stoch < 0.2
    c8_vwma = vwma26 > vwma52
    c8_vol = green_vol > vol_ma
    c8_all = c8_rsi and c8_stoch and c8_vwma and c8_vol
    
    print(f"  Cond 8 (BUY Dip):")
    print(f"     RSI<30:        {'✅' if c8_rsi else '❌'} ({rsi:.2f})")
    print(f"     Stoch<0.2:     {'✅' if c8_stoch else '❌'} ({stoch:.3f})")
    print(f"     VWMA26>VWMA52: {'✅' if c8_vwma else '❌'} ({vwma26:.4f} vs {vwma52:.4f})")
    print(f"     GreenVol>MA20: {'✅' if c8_vol else '❌'} ({green_vol:.0f} vs {vol_ma:.0f})")
    print(f"     RESULT:        {'🟢 SIGNAL!' if c8_all else '❌ No signal'}")

    # Condition 3: SELL - Strong Bearish Continuation
    c3_rsi = rsi < 30
    c3_stoch = stoch < 0.2
    c3_vwma = vwma52 > vwma26
    c3_vol = red_vol > vol_ma
    c3_all = c3_rsi and c3_stoch and c3_vwma and c3_vol
    
    print(f"  Cond 3 (SELL Bearish):")
    print(f"     RSI<30:        {'✅' if c3_rsi else '❌'} ({rsi:.2f})")
    print(f"     Stoch<0.2:     {'✅' if c3_stoch else '❌'} ({stoch:.3f})")
    print(f"     VWMA52>VWMA26: {'✅' if c3_vwma else '❌'} ({vwma52:.4f} vs {vwma26:.4f})")
    print(f"     RedVol>MA20:   {'✅' if c3_vol else '❌'} ({red_vol:.0f} vs {vol_ma:.0f})")
    print(f"     RESULT:        {'🔴 SIGNAL!' if c3_all else '❌ No signal'}")

    # Condition 6: SELL - Failed Rally in Bear Trend
    c6_rsi = rsi > 70
    c6_stoch = stoch > 0.8
    c6_vwma = vwma52 > vwma26
    c6_vol = red_vol > vol_ma
    c6_all = c6_rsi and c6_stoch and c6_vwma and c6_vol
    
    print(f"  Cond 6 (SELL Failed Rally):")
    print(f"     RSI>70:        {'✅' if c6_rsi else '❌'} ({rsi:.2f})")
    print(f"     Stoch>0.8:     {'✅' if c6_stoch else '❌'} ({stoch:.3f})")
    print(f"     VWMA52>VWMA26: {'✅' if c6_vwma else '❌'} ({vwma52:.4f} vs {vwma26:.4f})")
    print(f"     RedVol>MA20:   {'✅' if c6_vol else '❌'} ({red_vol:.0f} vs {vol_ma:.0f})")
    print(f"     RESULT:        {'🔴 SIGNAL!' if c6_all else '❌ No signal'}")

    # Return first matching signal
    if c1_all:
        return 'BUY', 'STRONG', 1
    if c8_all:
        return 'BUY', 'NORMAL', 8
    if c3_all:
        return 'SELL', 'STRONG', 3
    if c6_all:
        return 'SELL', 'STRONG', 6

    return None, None, None

def send_alert(message):
    """Send Telegram alert with error handling"""
    global total_alerts_sent
    
    if not TOKEN or not CHAT_ID:
        print("  ⚠️ Telegram not configured!")
        return False

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        response = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        
        if response.status_code == 200:
            total_alerts_sent += 1
            print(f"  ✅ Alert #{total_alerts_sent} sent!")
            return True
        else:
            print(f"  ❌ Telegram HTTP {response.status_code}: {response.text}")
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

# Main Bot Loop
def run_bot():
    global last_check_time, cycle_count, total_alerts_sent

    condition_names = {
        1: "Bullish Continuation",
        3: "Bearish Continuation",
        6: "Failed Rally (Bear)",
        8: "Bullish Dip Buy"
    }

    print("\n" + "="*70)
    print("🚀 HEIKIN ASHI SIGNAL BOT - ETH/USDT (5min CANDLES)")
    print("="*70)
    print(f"📊 Exchange: BINANCE")
    print(f"🕯️  Candles: HEIKIN ASHI")
    print(f"⏱️  Timeframe: 5 MINUTES")
    print(f"🔄 Scan: Every 30 seconds")
    print(f"🔍 Checking: PREVIOUS COMPLETED CANDLE")
    print(f"📊 Conditions: 1, 3, 6, 8")
    print("="*70)
    
    # TEST ALERT ON STARTUP
    print(f"\n📱 Testing Telegram connection...")
    test_msg = f"✅ <b>Heikin Ashi Bot Started (5min)</b>\n\n📊 ETH/USDT 5m\n🕯️ Heikin Ashi Candles\n🔄 30s Scans\n🕒 {datetime.now().strftime('%H:%M:%S')}"
    if send_alert(test_msg):
        print(f"✅ Startup alert sent successfully!")
    else:
        print(f"❌ Startup alert FAILED - Check TELEGRAM_TOKEN and CHAT_ID!")
    
    last_alert_time = {}
    available_symbols = [s for s in SYMBOLS if s in EXCHANGE.markets]

    while True:
        try:
            cycle_count += 1
            new_signals = 0
            processed = 0

            print(f"\n{'='*70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%H:%M:%S')} | {total_alerts_sent} alerts sent so far")
            print(f"{'='*70}")

            if cycle_count % 10 == 0:
                cleanup_cache()

            for i, symbol in enumerate(available_symbols):
                try:
                    if i > 0:
                        time.sleep(API_CALL_INTERVAL)

                    df = get_cached_ohlcv(EXCHANGE, symbol, timeframe='5m', limit=CANDLES_TO_FETCH)
                    if df is None or len(df) < 60:
                        continue

                    ha_df = calculate_heikin_ashi(df)
                    if ha_df is None:
                        continue

                    indicators = calculate_indicators(ha_df)
                    if indicators is None:
                        continue

                    # Show current state
                    current_price = df['close'].iloc[-1]
                    print(f"\n  {symbol} | Price: {format_price(current_price)} | "
                          f"RSI: {indicators['prev_rsi_7']:.2f} | "
                          f"Stoch: {indicators['prev_stoch_rsi']:.3f} | "
                          f"Trend: {'BULL' if indicators['prev_vwma_26'] > indicators['prev_vwma_52'] else 'BEAR'}")

                    # DEBUG CHECK
                    signal, strength, condition_num = check_signals_debug(symbol, indicators)

                    if signal:
                        cond_name = condition_names.get(condition_num, f"Cond {condition_num}")
                        ha_prev_close = indicators['prev_ha_close']
                        
                        print(f"\n  {'🟢' if signal == 'BUY' else '🔴'} SIGNAL FOUND! {signal} - {cond_name}")
                        print(f"     Signal Price (HA): {format_price(ha_prev_close)}")

                        signal_key = f"{symbol}_{signal}_{condition_num}"
                        now = datetime.now()
                        
                        should_alert = False
                        if signal_key not in last_alert_time:
                            should_alert = True
                        elif (now - last_alert_time[signal_key]).total_seconds() > 300:  # 5 minutes cooldown
                            should_alert = True
                        
                        if should_alert:
                            new_signals += 1
                            last_alert_time[signal_key] = now

                            message = (
                                f"🚨 <b>{'🟢 BUY' if signal == 'BUY' else '🔴 SELL'} SIGNAL! (5min)</b>\n\n"
                                f"<b>Symbol:</b> {symbol}\n"
                                f"<b>Condition:</b> #{condition_num} - {cond_name}\n"
                                f"<b>Price (HA):</b> {format_price(ha_prev_close)}\n"
                                f"<b>Current:</b> {format_price(current_price)}\n\n"
                                f"<b>Heikin Ashi Values:</b>\n"
                                f"• RSI(7): {indicators['prev_rsi_7']:.2f}\n"
                                f"• StochRSI: {indicators['prev_stoch_rsi']:.3f}\n"
                                f"• VWMA26: {indicators['prev_vwma_26']:.4f}\n"
                                f"• VWMA52: {indicators['prev_vwma_52']:.4f}\n"
                                f"• Volume: {indicators['prev_volume']:.0f}\n\n"
                                f"🕒 {now.strftime('%H:%M:%S')}"
                            )

                            if send_alert(message):
                                print(f"  ✅ ALERT SENT!")
                            else:
                                print(f"  ❌ ALERT FAILED!")

                    processed += 1

                except Exception as e:
                    print(f"  ❌ Error: {e}")
                    traceback.print_exc()

            print(f"\n📊 Cycle #{cycle_count} Done | Signals: {new_signals} | Total Alerts: {total_alerts_sent}")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Stopped")
            break
        except Exception as e:
            print(f"❌ Critical: {e}")
            time.sleep(60)

# Start
print("\n🚀 Starting...")
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Server on port {port}")
    print(f"📱 Test alert: http://localhost:{port}/test_alert")
    app.run(host='0.0.0.0', port=port)
