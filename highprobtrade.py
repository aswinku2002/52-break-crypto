import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask

# 1. Setup Flask for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Exchange selection (set in environment variables)
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'binance').lower()

# API keys for different exchanges
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')
KRAKEN_API_KEY = os.environ.get('KRAKEN_API_KEY', '')
KRAKEN_API_SECRET = os.environ.get('KRAKEN_API_SECRET', '')
COINBASE_API_KEY = os.environ.get('COINBASE_API_KEY', '')
COINBASE_API_SECRET = os.environ.get('COINBASE_API_SECRET', '')
KUCOIN_API_KEY = os.environ.get('KUCOIN_API_KEY', '')
KUCOIN_API_SECRET = os.environ.get('KUCOIN_API_SECRET', '')
KUCOIN_API_PASSPHRASE = os.environ.get('KUCOIN_API_PASSPHRASE', '')
BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', '')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', '')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT',
    'DOGE/USDT', 'BNB/USDT', 'LTC/USDT', 'LINK/USDT',
    'AVAX/USDT', 'ADA/USDT', 'SUI/USDT', 'TRX/USDT',
    'BCH/USDT', 'AAVE/USDT', 'ETC/USDT', 'NEAR/USDT',
    'ORDI/USDT', 'WLD/USDT', 'HYPE/USDT', 'XLM/USDT',
    'XAUT/USDT', 'PAXG/USDT',
    'UNI/USDT', 'ZEC/USDT', 'ENJ/USDT', 'XMR/USDT',
    'AXS/USDT', 'JTO/USDT', 'IO/USDT', 'ALT/USDT',
    'ACT/USDT', 'EVA/USDT', 'SLVON/USDT', 'EDEN/USDT',
    'SKYAI/USDT', 'EIGEN/USDT', 'SIREN/USDT', 'VVV/USDT',
    'WCT/USDT', 'SPCXX/USDT', 'AIO/USDT', 'SWARMS/USDT',
    'ALLO/USDT', 'RIVER/USDT', 'PIPPIN/USDT', 'BILL/USDT',
    'M/USDT', 'XPL/USDT', 'COAI/USDT', 'QQQX/USDT',
    'RAVE/USDT', 'BASED/USDT', 'BLESS/USDT', 'VELVET/USDT',
    'LAB/USDT', 'BEAT/USDT', 'H/USDT'
]

TOP_TREND_ZONE = 98
BOTTOM_TREND_ZONE = 2
TOP_REVERSAL_ZONE = 95
BOTTOM_REVERSAL_ZONE = 5

exchanges = {}

def init_exchange(exchange_name, config):
    try:
        if exchange_name == 'binance':
            exchange = ccxt.binance(config)
        elif exchange_name == 'kraken':
            exchange = ccxt.kraken(config)
        elif exchange_name == 'coinbase':
            exchange = ccxt.coinbase(config)
        elif exchange_name == 'kucoin':
            exchange = ccxt.kucoin(config)
        elif exchange_name == 'bybit':
            exchange = ccxt.bybit(config)
        else:
            return None

        exchange.load_markets()
        print(f"✅ {exchange_name.capitalize()} markets loaded successfully")
        return exchange
    except Exception as e:
        print(f"❌ Error loading {exchange_name.capitalize()} markets: {e}")
        return None

# Initialize Binance
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
}
binance_exchange = init_exchange('binance', binance_config)
if binance_exchange:
    exchanges['binance'] = binance_exchange

# Initialize Kraken
kraken_config = {
    'apiKey': KRAKEN_API_KEY,
    'secret': KRAKEN_API_SECRET,
    'enableRateLimit': True,
}
kraken_exchange = init_exchange('kraken', kraken_config)
if kraken_exchange:
    exchanges['kraken'] = kraken_exchange

# Initialize Coinbase
coinbase_config = {
    'apiKey': COINBASE_API_KEY,
    'secret': COINBASE_API_SECRET,
    'enableRateLimit': True,
}
coinbase_exchange = init_exchange('coinbase', coinbase_config)
if coinbase_exchange:
    exchanges['coinbase'] = coinbase_exchange

# Initialize KuCoin
kucoin_config = {
    'apiKey': KUCOIN_API_KEY,
    'secret': KUCOIN_API_SECRET,
    'password': KUCOIN_API_PASSPHRASE,
    'enableRateLimit': True,
}
kucoin_exchange = init_exchange('kucoin', kucoin_config)
if kucoin_exchange:
    exchanges['kucoin'] = kucoin_exchange

# Initialize Bybit
bybit_config = {
    'apiKey': BYBIT_API_KEY,
    'secret': BYBIT_API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
}
bybit_exchange = init_exchange('bybit', bybit_config)
if bybit_exchange:
    exchanges['bybit'] = bybit_exchange

# Select primary exchange
EXCHANGE = exchanges.get(PRIMARY_EXCHANGE)
if not EXCHANGE:
    print(f"⚠️ Primary exchange '{PRIMARY_EXCHANGE}' not available. Using first available exchange.")
    EXCHANGE = next(iter(exchanges.values())) if exchanges else None

if not EXCHANGE:
    print("❌ No exchanges available. Please check your configuration.")
    exit(1)

print(f"✅ Using {EXCHANGE.name.capitalize()} as primary exchange")

# Track state
last_alert = {}
last_candle_ts = {}
breakout_alerted = {}  # Track if we've alerted for current breakout level

def calculate_choppiness_index(df, period=14):
    """Calculate Choppiness Index"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        sum_tr = tr.rolling(window=period).sum()
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()
        price_range = highest_high - lowest_low
        price_range = price_range.replace(0, np.nan)
        choppiness = 100 * np.log10(sum_tr / price_range) / np.log10(period)
        result = choppiness.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
    except Exception as e:
        print(f"Choppiness calculation error: {e}")
        return 50

def calculate_rsi(df, period=14):
    """Calculate RSI (Relative Strength Index)"""
    try:
        close = df['close']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        result = rsi.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
    except Exception as e:
        print(f"RSI calculation error: {e}")
        return 50

def calculate_dpo(df, period=21):
    """
    Calculate Detrended Price Oscillator (DPO)
    DPO = Price - SMA(Price, period/2 + 1) shifted by (period/2 + 1)
    """
    try:
        close = df['close']
        if len(close) < period + 10:
            return (0, 0)
        sma = close.rolling(window=period).mean()
        offset = period // 2 + 1
        shifted_sma = sma.shift(offset)
        dpo = close - shifted_sma
        current_dpo = dpo.iloc[-1]
        previous_dpo = dpo.iloc[-2] if len(dpo) >= 2 else 0
        if pd.isna(current_dpo) or np.isinf(current_dpo):
            current_dpo = 0
        if pd.isna(previous_dpo) or np.isinf(previous_dpo):
            previous_dpo = 0
        return (round(current_dpo, 2), round(previous_dpo, 2))
    except Exception as e:
        print(f"DPO calculation error: {e}")
        return (0, 0)

def calculate_atr(df, period=14):
    """Calculate Average True Range (ATR)"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        current_atr = atr.iloc[-1]
        if pd.isna(current_atr) or np.isinf(current_atr):
            return 0
        return round(current_atr, 4)
    except Exception as e:
        print(f"ATR calculation error: {e}")
        return 0

def calculate_atr_sma(df, atr_period=14, sma_period=20):
    """Calculate SMA of ATR"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=atr_period).mean()
        atr_sma = atr.rolling(window=sma_period).mean()
        current_atr_sma = atr_sma.iloc[-1]
        if pd.isna(current_atr_sma) or np.isinf(current_atr_sma):
            return 0
        return round(current_atr_sma, 4)
    except Exception as e:
        print(f"ATR SMA calculation error: {e}")
        return 0

def send_alert(message):
    """Send alert via Telegram"""
    if TOKEN and CHAT_ID:
        try:
            requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={"chat_id": CHAT_ID, "text": message},
                timeout=10
            )
        except Exception as e:
            print(f"Telegram error: {e}")

def calculate_channel_percentile(HH, LL, current_price):
    """Calculate where price sits in the channel (0% = LL, 100% = HH)"""
    if HH == LL:
        return 50
    percentile = ((current_price - LL) / (HH - LL)) * 100
    return round(percentile, 2)

def get_available_symbols(exchange, symbols):
    """Filter symbols to only those available on the exchange"""
    available = []
    for symbol in symbols:
        if symbol in exchange.markets:
            available.append(symbol)
        else:
            base, quote = symbol.split('/')
            alternatives = [
                f"{base}/USD",
                f"{base}/USDT",
                f"{base}/USDC",
                f"{base}/BTC",
                f"{base}/ETH"
            ]
            for alt in alternatives:
                if alt in exchange.markets:
                    available.append(alt)
                    break
    return available

def check_conditions_and_alert(symbol, live_price, df):
    """Check all trading conditions and send alerts if triggered"""
    
    # Get current candle timestamp
    current_ts = df['ts'].iloc[-1]
    
    # Initialize tracking for this symbol
    if symbol not in last_candle_ts:
        last_candle_ts[symbol] = current_ts
        breakout_alerted[symbol] = {"high": False, "low": False}
    
    # Check if new candle formed using timestamp
    if current_ts != last_candle_ts[symbol]:
        print(f"📊 New candle formed for {symbol} at timestamp {current_ts}")
        last_candle_ts[symbol] = current_ts
        # Reset breakout alerts on new candle
        breakout_alerted[symbol] = {"high": False, "low": False}
    
    # ==============================================
    # DONCHIAN CHANNEL (52 candles) - Direct calculation
    # ==============================================
    dc52_high = df['high'].iloc[-53:-1].max()
    dc52_low = df['low'].iloc[-53:-1].min()
    
    # ==============================================
    # INDICATORS
    # ==============================================
    chop_value = calculate_choppiness_index(df, period=14)
    rsi_value = calculate_rsi(df, period=14)
    dpo_current, dpo_previous = calculate_dpo(df, period=21)
    
    dpo_bullish_cross = dpo_previous < 0 and dpo_current > 0
    dpo_bearish_cross = dpo_previous > 0 and dpo_current < 0
    
    atr_value = calculate_atr(df, period=14)
    atr_sma20 = calculate_atr_sma(df, atr_period=14, sma_period=20)
    atr_above_sma = atr_value > atr_sma20 if atr_sma20 > 0 else False
    atr_below_sma = atr_value < atr_sma20 if atr_sma20 > 0 else False
    
    channel_percentile = calculate_channel_percentile(dc52_high, dc52_low, live_price)
    
    # Zone checks
    is_top_trend_zone = channel_percentile >= TOP_TREND_ZONE
    is_bottom_trend_zone = channel_percentile <= BOTTOM_TREND_ZONE
    is_top_reversal_zone = channel_percentile >= TOP_REVERSAL_ZONE
    is_bottom_reversal_zone = channel_percentile <= BOTTOM_REVERSAL_ZONE
    
    # Breakout detection
    high_breakout = live_price >= dc52_high
    low_breakout = live_price <= dc52_low
    
    should_alert_high = high_breakout and not breakout_alerted[symbol]["high"]
    should_alert_low = low_breakout and not breakout_alerted[symbol]["low"]
    
    # ==============================================
    # CONDITION CHECKS
    # ==============================================
    
    # BUY TREND: Live Price >= DC52 High & CHOP < 40 & RSI > 60 & DPO > 0 & ATR > ATR_SMA20
    buy_trend_conditions = {
        'price_above_dc52': should_alert_high,
        'chop_lt_40': chop_value < 40,
        'rsi_gt_60': rsi_value > 60,
        'dpo_gt_0': dpo_current > 0,
        'atr_above_sma': atr_above_sma
    }
    buy_trend_trigger = all(buy_trend_conditions.values())
    
    # SELL TREND: Live Price <= DC52 Low & CHOP < 40 & RSI < 40 & DPO < 0 & ATR > ATR_SMA20
    sell_trend_conditions = {
        'price_below_dc52': should_alert_low,
        'chop_lt_40': chop_value < 40,
        'rsi_lt_40': rsi_value < 40,
        'dpo_lt_0': dpo_current < 0,
        'atr_above_sma': atr_above_sma
    }
    sell_trend_trigger = all(sell_trend_conditions.values())
    
    # BUY REVERSAL: CHOP > 65 & RSI < 30 & Bottom 5% & DPO Bullish Cross & ATR < ATR_SMA20
    buy_reversal_conditions = {
        'chop_gt_65': chop_value > 65,
        'rsi_lt_30': rsi_value < 30,
        'bottom_5_percent': is_bottom_reversal_zone,
        'dpo_bullish_cross': dpo_bullish_cross,
        'atr_below_sma': atr_below_sma
    }
    buy_reversal_trigger = all(buy_reversal_conditions.values())
    
    # SELL REVERSAL: CHOP > 65 & RSI > 70 & Top 5% & DPO Bearish Cross & ATR < ATR_SMA20
    sell_reversal_conditions = {
        'chop_gt_65': chop_value > 65,
        'rsi_gt_70': rsi_value > 70,
        'top_5_percent': is_top_reversal_zone,
        'dpo_bearish_cross': dpo_bearish_cross,
        'atr_below_sma': atr_below_sma
    }
    sell_reversal_trigger = all(sell_reversal_conditions.values())
    
    # ==============================================
    # DEBUG LOGS
    # ==============================================
    print(f"\n{'='*60}")
    print(f"🔍 {symbol} - Live Analysis")
    print(f"{'='*60}")
    print(f"📊 Live Price: ${live_price:.2f}")
    print(f"📈 DC52 High: ${dc52_high:.2f} | DC52 Low: ${dc52_low:.2f}")
    print(f"📍 Channel Position: {channel_percentile}%")
    print(f"🔄 Current Candle Timestamp: {current_ts}")
    print(f"📌 Last Candle Timestamp: {last_candle_ts[symbol]}")
    print(f"🔔 Breakout Alerted - High: {breakout_alerted[symbol]['high']} | Low: {breakout_alerted[symbol]['low']}")
    print(f"\n📊 Indicators:")
    print(f"  • RSI14: {rsi_value}")
    print(f"  • CHOP14: {chop_value}")
    print(f"  • Current DPO: {dpo_current}")
    print(f"  • Previous DPO: {dpo_previous}")
    print(f"  • DPO Bullish Cross: {dpo_bullish_cross}")
    print(f"  • DPO Bearish Cross: {dpo_bearish_cross}")
    print(f"  • ATR14: {atr_value}")
    print(f"  • ATR14_SMA20: {atr_sma20}")
    print(f"  • ATR > ATR_SMA20 (Trend): {atr_above_sma}")
    print(f"  • ATR < ATR_SMA20 (Reversal): {atr_below_sma}")
    
    print(f"\n🎯 BUY TREND Conditions:")
    for key, value in buy_trend_conditions.items():
        print(f"  • {key}: {value}")
    print(f"  ✅ TRIGGER: {buy_trend_trigger}")
    
    print(f"\n🎯 SELL TREND Conditions:")
    for key, value in sell_trend_conditions.items():
        print(f"  • {key}: {value}")
    print(f"  ✅ TRIGGER: {sell_trend_trigger}")
    
    print(f"\n🎯 BUY REVERSAL Conditions:")
    for key, value in buy_reversal_conditions.items():
        print(f"  • {key}: {value}")
    print(f"  ✅ TRIGGER: {buy_reversal_trigger}")
    
    print(f"\n🎯 SELL REVERSAL Conditions:")
    for key, value in sell_reversal_conditions.items():
        print(f"  • {key}: {value}")
    print(f"  ✅ TRIGGER: {sell_reversal_trigger}")
    print(f"{'='*60}\n")
    
    # ==============================================
    # SEND ALERTS
    # ==============================================
    
    # Initialize alert tracking
    if symbol not in last_alert:
        last_alert[symbol] = None
    
    # BUY TREND Alert
    if buy_trend_trigger and last_alert[symbol] != "BUY_TREND":
        message = (
            f"🟢🟢🟢 BUY TREND (ATR Validated) 🟢🟢🟢\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Price: ${live_price:.2f} (LIVE)\n"
            f"DC52 High: ${dc52_high:.2f}\n"
            f"DC52 Low: ${dc52_low:.2f}\n"
            f"Channel Position: {channel_percentile}%\n"
            f"RSI14: {rsi_value} (>60)\n"
            f"CHOP14: {chop_value} (<40)\n"
            f"DPO: {dpo_current} (>0)\n"
            f"ATR14: {atr_value:.4f}\n"
            f"ATR14_SMA20: {atr_sma20:.4f}\n"
            f"ATR > ATR_SMA20: ✅\n\n"
            f"📈 Breakout above 52-candle high detected!\n"
            f"🎯 Trend continuation signal confirmed"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🟢 BUY TREND TRIGGERED")
        last_alert[symbol] = "BUY_TREND"
        breakout_alerted[symbol]["high"] = True
    
    # SELL TREND Alert
    elif sell_trend_trigger and last_alert[symbol] != "SELL_TREND":
        message = (
            f"🔴🔴🔴 SELL TREND (ATR Validated) 🔴🔴🔴\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Price: ${live_price:.2f} (LIVE)\n"
            f"DC52 High: ${dc52_high:.2f}\n"
            f"DC52 Low: ${dc52_low:.2f}\n"
            f"Channel Position: {channel_percentile}%\n"
            f"RSI14: {rsi_value} (<40)\n"
            f"CHOP14: {chop_value} (<40)\n"
            f"DPO: {dpo_current} (<0)\n"
            f"ATR14: {atr_value:.4f}\n"
            f"ATR14_SMA20: {atr_sma20:.4f}\n"
            f"ATR > ATR_SMA20: ✅\n\n"
            f"📉 Breakdown below 52-candle low detected!\n"
            f"🎯 Trend continuation signal confirmed"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🔴 SELL TREND TRIGGERED")
        last_alert[symbol] = "SELL_TREND"
        breakout_alerted[symbol]["low"] = True
    
    # BUY REVERSAL Alert
    elif buy_reversal_trigger and last_alert[symbol] != "BUY_REVERSAL":
        message = (
            f"🟢🟢🟢 BUY REVERSAL (DPO & ATR Confirmed) 🟢🟢🟢\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Price: ${live_price:.2f} (LIVE)\n"
            f"DC52 High: ${dc52_high:.2f}\n"
            f"DC52 Low: ${dc52_low:.2f}\n"
            f"Channel Position: {channel_percentile}% (Bottom 5% Zone)\n"
            f"RSI14: {rsi_value} (<30 - Oversold)\n"
            f"CHOP14: {chop_value} (>65 - Extreme Choppy)\n"
            f"Current DPO: {dpo_current:.2f}\n"
            f"Previous DPO: {dpo_previous:.2f}\n"
            f"DPO Bullish Cross: ✅\n"
            f"ATR14: {atr_value:.4f}\n"
            f"ATR14_SMA20: {atr_sma20:.4f}\n"
            f"ATR < ATR_SMA20: ✅\n\n"
            f"📊 DPO Bullish Cross Confirmed!\n"
            f"✅ Previous DPO < 0 → Current DPO > 0\n"
            f"🎯 Reversal signal triggered at turn"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🟢 BUY REVERSAL TRIGGERED")
        last_alert[symbol] = "BUY_REVERSAL"
    
    # SELL REVERSAL Alert
    elif sell_reversal_trigger and last_alert[symbol] != "SELL_REVERSAL":
        message = (
            f"🔴🔴🔴 SELL REVERSAL (DPO & ATR Confirmed) 🔴🔴🔴\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Price: ${live_price:.2f} (LIVE)\n"
            f"DC52 High: ${dc52_high:.2f}\n"
            f"DC52 Low: ${dc52_low:.2f}\n"
            f"Channel Position: {channel_percentile}% (Top 5% Zone)\n"
            f"RSI14: {rsi_value} (>70 - Overbought)\n"
            f"CHOP14: {chop_value} (>65 - Extreme Choppy)\n"
            f"Current DPO: {dpo_current:.2f}\n"
            f"Previous DPO: {dpo_previous:.2f}\n"
            f"DPO Bearish Cross: ✅\n"
            f"ATR14: {atr_value:.4f}\n"
            f"ATR14_SMA20: {atr_sma20:.4f}\n"
            f"ATR < ATR_SMA20: ✅\n\n"
            f"📊 DPO Bearish Cross Confirmed!\n"
            f"✅ Previous DPO > 0 → Current DPO < 0\n"
            f"🎯 Reversal signal triggered at turn"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🔴 SELL REVERSAL TRIGGERED")
        last_alert[symbol] = "SELL_REVERSAL"
    
    # Reset alerts when conditions no longer met (except for trend alerts)
    else:
        if last_alert[symbol] is not None:
            # Don't reset trend alerts while price is still in breakout zone
            if not (last_alert[symbol] in ["BUY_TREND", "SELL_TREND"] and 
                    (high_breakout or low_breakout)):
                print(f"  → {symbol} - Alert reset: {last_alert[symbol]} condition ended")
                last_alert[symbol] = None

def run_bot():
    print("Bot loop started...")
    print(f"Exchange: {EXCHANGE.name.capitalize()} (Global)")

    # Get available symbols
    available_symbols = get_available_symbols(EXCHANGE, SYMBOLS)
    print(f"✅ Available symbols on {EXCHANGE.name.capitalize()}: {len(available_symbols)}")

    print("\n===== STRATEGY CONFIGURATION =====")
    print("📊 TIMEFRAME: 10-minute candles")
    print("📈 DONCHIAN CHANNEL: 52 periods")
    print("\n📈 TREND SIGNALS (ATR > ATR_SMA20):")
    print("🟢 BUY TREND: Price >= DC52 High & CHOP < 40 & RSI > 60 & DPO > 0")
    print("🔴 SELL TREND: Price <= DC52 Low & CHOP < 40 & RSI < 40 & DPO < 0")
    print("\n📊 REVERSAL SIGNALS (ATR < ATR_SMA20):")
    print("🟢 BUY REVERSAL: CHOP > 65 & RSI < 30 & Bottom 5% & DPO Bullish Cross")
    print("🔴 SELL REVERSAL: CHOP > 65 & RSI > 70 & Top 5% & DPO Bearish Cross")
    print("============================\n")

    send_alert(f"✅ Bot Started on {EXCHANGE.name.capitalize()}\n\n"
               f"📊 Strategy: DC52 + CHOP14 + RSI14 + DPO21 + ATR14\n"
               f"⏱️ Timeframe: 10-minute candles\n\n"
               f"📈 TREND SIGNALS (ATR > ATR_SMA20):\n"
               f"🟢 BUY: Price >= DC52 High & CHOP < 40 & RSI > 60 & DPO > 0\n"
               f"🔴 SELL: Price <= DC52 Low & CHOP < 40 & RSI < 40 & DPO < 0\n\n"
               f"📊 REVERSAL SIGNALS (ATR < ATR_SMA20):\n"
               f"🟢 BUY: CHOP > 65 & RSI < 30 & Bottom 5% & DPO Bullish Cross\n"
               f"🔴 SELL: CHOP > 65 & RSI > 70 & Top 5% & DPO Bearish Cross")

    while True:
        for symbol in available_symbols:
            try:
                # Get LIVE price
                ticker = EXCHANGE.fetch_ticker(symbol)
                live_price = ticker['last']
                
                # Get candle data
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='10m',
                    limit=150
                )

                if len(ohlcv) < 100:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # Check conditions and send alerts
                check_conditions_and_alert(symbol, live_price, df)
                
            except Exception as e:
                print(f"Error checking {symbol} on {EXCHANGE.name.capitalize()}: {e}")
        
        # Check every 2 minutes
        time.sleep(120)

# Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)