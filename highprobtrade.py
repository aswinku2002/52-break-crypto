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

# Channel zones
TOP_ZONE = 95  # Top 5% of channel
BOTTOM_ZONE = 5  # Bottom 5% of channel

# RSI thresholds for TREND signals only
RSI_TREND_BUY = 55     # For BUY TREND
RSI_TREND_SELL = 45    # For SELL TREND

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
    """Calculate Detrended Price Oscillator (DPO) - CONSERVED"""
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
    return available

def check_conditions_and_alert(symbol, live_price, df):
    """Check all trading conditions and send alerts if triggered"""

    # ==============================================
    # DONCHIAN CHANNEL (52 candles)
    # ==============================================
    dc52_high = df['high'].iloc[-53:-1].max()
    dc52_low = df['low'].iloc[-53:-1].min()
    channel_range = dc52_high - dc52_low

    # ==============================================
    # INDICATORS - ALL CALCULATED
    # ==============================================
    chop_value = calculate_choppiness_index(df, period=14)
    rsi_value = calculate_rsi(df, period=14)
    dpo_current, dpo_previous = calculate_dpo(df, period=21)
    atr_value = calculate_atr(df, period=14)
    atr_sma20 = calculate_atr_sma(df, atr_period=14, sma_period=20)

    dpo_bullish_cross = dpo_previous < 0 and dpo_current > 0
    dpo_bearish_cross = dpo_previous > 0 and dpo_current < 0
    atr_above_sma = atr_value > atr_sma20 if atr_sma20 > 0 else False
    atr_below_sma = atr_value < atr_sma20 if atr_sma20 > 0 else False

    channel_percentile = calculate_channel_percentile(dc52_high, dc52_low, live_price)

    # Zone checks
    is_top_zone = channel_percentile >= TOP_ZONE
    is_bottom_zone = channel_percentile <= BOTTOM_ZONE

    # Initialize alert tracking
    if symbol not in last_alert:
        last_alert[symbol] = None

    # ==============================================
    # CONDITION CHECKS - UPDATED: RSI REMOVED FROM REVERSAL
    # ==============================================

    # SELL REVERSAL: CHOP > 60 & Top 5% & ATR < ATR_SMA20 (NO RSI)
    sell_reversal_trigger = (
        chop_value > 60 and 
        is_top_zone and 
        atr_below_sma
    )

    # BUY REVERSAL: CHOP > 60 & Bottom 5% & ATR < ATR_SMA20 (NO RSI)
    buy_reversal_trigger = (
        chop_value > 60 and 
        is_bottom_zone and 
        atr_below_sma
    )

    # BUY TREND: CHOP < 40 & Top 5% & RSI > 55 & ATR > ATR_SMA20
    buy_trend_trigger = (
        chop_value < 40 and 
        is_top_zone and 
        rsi_value > RSI_TREND_BUY and
        atr_above_sma
    )

    # SELL TREND: CHOP < 40 & Bottom 5% & RSI < 45 & ATR > ATR_SMA20
    sell_trend_trigger = (
        chop_value < 40 and 
        is_bottom_zone and 
        rsi_value < RSI_TREND_SELL and
        atr_above_sma
    )

    # ==============================================
    # DEBUG LOGS - ENHANCED WITH REJECTION REASONS
    # ==============================================
    print(f"\n{'='*60}")
    print(f"🔍 {symbol} - Live Analysis")
    print(f"{'='*60}")
    print(f"📊 Live Price: ${live_price:.2f}")
    print(f"📈 DC52 High: ${dc52_high:.2f} | DC52 Low: ${dc52_low:.2f}")
    print(f"📍 Channel Position: {channel_percentile}%")
    print(f"📍 Top Zone (≥{TOP_ZONE}%): {is_top_zone} | Bottom Zone (≤{BOTTOM_ZONE}%): {is_bottom_zone}")
    print(f"\n📊 Indicators:")
    print(f"  • RSI14: {rsi_value} (BUY TREND: >{RSI_TREND_BUY} | SELL TREND: <{RSI_TREND_SELL})")
    print(f"  • CHOP14: {chop_value}")
    print(f"  • Current DPO: {dpo_current} (Conserved)")
    print(f"  • Previous DPO: {dpo_previous} (Conserved)")
    print(f"  • DPO Bullish Cross: {dpo_bullish_cross} (Conserved)")
    print(f"  • DPO Bearish Cross: {dpo_bearish_cross} (Conserved)")
    print(f"  • ATR14: {atr_value}")
    print(f"  • ATR14_SMA20: {atr_sma20}")
    print(f"  • ATR > ATR_SMA20: {atr_above_sma} (Trend Confirmation - Volatility Expansion)")
    print(f"  • ATR < ATR_SMA20: {atr_below_sma} (Reversal Confirmation - Volatility Contraction)")

    print(f"\n🎯 SELL REVERSAL: CHOP > 60 & Top {TOP_ZONE}% & ATR < ATR_SMA20 (NO RSI)")
    print(f"  • CHOP ({chop_value}) > 60: {chop_value > 60}")
    print(f"  • Top Zone: {is_top_zone}")
    print(f"  • ATR < ATR_SMA20 ({atr_value} < {atr_sma20}): {atr_below_sma}")
    
    # Show rejection reason
    if not sell_reversal_trigger:
        reasons = []
        if not (chop_value > 60):
            reasons.append(f"CHOP ({chop_value}) not > 60")
        if not is_top_zone:
            reasons.append(f"Not in top {TOP_ZONE}% zone (position: {channel_percentile}%)")
        if not atr_below_sma:
            reasons.append(f"ATR ({atr_value}) not < ATR_SMA20 ({atr_sma20}) - volatility expansion detected")
        print(f"  ❌ REJECTED: {', '.join(reasons)}")
    else:
        print(f"  ✅ ALL CONDITIONS PASSED")
    print(f"  ✅ TRIGGER: {sell_reversal_trigger}")

    print(f"\n🎯 BUY REVERSAL: CHOP > 60 & Bottom {BOTTOM_ZONE}% & ATR < ATR_SMA20 (NO RSI)")
    print(f"  • CHOP ({chop_value}) > 60: {chop_value > 60}")
    print(f"  • Bottom Zone: {is_bottom_zone}")
    print(f"  • ATR < ATR_SMA20 ({atr_value} < {atr_sma20}): {atr_below_sma}")
    
    # Show rejection reason
    if not buy_reversal_trigger:
        reasons = []
        if not (chop_value > 60):
            reasons.append(f"CHOP ({chop_value}) not > 60")
        if not is_bottom_zone:
            reasons.append(f"Not in bottom {BOTTOM_ZONE}% zone (position: {channel_percentile}%)")
        if not atr_below_sma:
            reasons.append(f"ATR ({atr_value}) not < ATR_SMA20 ({atr_sma20}) - volatility expansion detected")
        print(f"  ❌ REJECTED: {', '.join(reasons)}")
    else:
        print(f"  ✅ ALL CONDITIONS PASSED")
    print(f"  ✅ TRIGGER: {buy_reversal_trigger}")

    print(f"\n🎯 BUY TREND: CHOP < 40 & Top {TOP_ZONE}% & RSI > {RSI_TREND_BUY} & ATR > ATR_SMA20")
    print(f"  • CHOP ({chop_value}) < 40: {chop_value < 40}")
    print(f"  • Top Zone: {is_top_zone}")
    print(f"  • RSI ({rsi_value}) > {RSI_TREND_BUY}: {rsi_value > RSI_TREND_BUY}")
    print(f"  • ATR > ATR_SMA20 ({atr_value} > {atr_sma20}): {atr_above_sma}")
    
    # Show rejection reason
    if not buy_trend_trigger:
        reasons = []
        if not (chop_value < 40):
            reasons.append(f"CHOP ({chop_value}) not < 40")
        if not is_top_zone:
            reasons.append(f"Not in top {TOP_ZONE}% zone (position: {channel_percentile}%)")
        if not (rsi_value > RSI_TREND_BUY):
            reasons.append(f"RSI ({rsi_value}) not > {RSI_TREND_BUY}")
        if not atr_above_sma:
            reasons.append(f"ATR ({atr_value}) not > ATR_SMA20 ({atr_sma20}) - insufficient volatility")
        print(f"  ❌ REJECTED: {', '.join(reasons)}")
    else:
        print(f"  ✅ ALL CONDITIONS PASSED")
    print(f"  ✅ TRIGGER: {buy_trend_trigger}")

    print(f"\n🎯 SELL TREND: CHOP < 40 & Bottom {BOTTOM_ZONE}% & RSI < {RSI_TREND_SELL} & ATR > ATR_SMA20")
    print(f"  • CHOP ({chop_value}) < 40: {chop_value < 40}")
    print(f"  • Bottom Zone: {is_bottom_zone}")
    print(f"  • RSI ({rsi_value}) < {RSI_TREND_SELL}: {rsi_value < RSI_TREND_SELL}")
    print(f"  • ATR > ATR_SMA20 ({atr_value} > {atr_sma20}): {atr_above_sma}")
    
    # Show rejection reason
    if not sell_trend_trigger:
        reasons = []
        if not (chop_value < 40):
            reasons.append(f"CHOP ({chop_value}) not < 40")
        if not is_bottom_zone:
            reasons.append(f"Not in bottom {BOTTOM_ZONE}% zone (position: {channel_percentile}%)")
        if not (rsi_value < RSI_TREND_SELL):
            reasons.append(f"RSI ({rsi_value}) not < {RSI_TREND_SELL}")
        if not atr_above_sma:
            reasons.append(f"ATR ({atr_value}) not > ATR_SMA20 ({atr_sma20}) - insufficient volatility")
        print(f"  ❌ REJECTED: {', '.join(reasons)}")
    else:
        print(f"  ✅ ALL CONDITIONS PASSED")
    print(f"  ✅ TRIGGER: {sell_trend_trigger}")
    print(f"{'='*60}\n")

    # ==============================================
    # SEND ALERTS - SELL REVERSAL
    # ==============================================
    if sell_reversal_trigger and last_alert[symbol] != "SELL_REVERSAL":
        message = (
            f"🔴🔴🔴 SELL REVERSAL 🔴🔴🔴\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Current Price: ${live_price:.2f}\n"
            f"RSI: {rsi_value} (For reference only)\n"
            f"Choppiness Index: {chop_value} (>60 - Ranging Market)\n"
            f"Channel Position: {channel_percentile}% (Top {TOP_ZONE}% Zone)\n"
            f"ATR: ${atr_value:.4f} (Reversal Confirmation - Volatility Contraction)\n"
            f"ATR SMA20: ${atr_sma20:.4f}\n"
            f"ATR < ATR_SMA20: ✅ (Volatility compression - reversal expected)\n"
            f"DPO Current: {dpo_current} (Conserved)\n"
            f"DPO Previous: {dpo_previous} (Conserved)\n"
            f"DPO {'Bullish' if dpo_bullish_cross else 'Bearish'} Cross: {'✅' if dpo_bullish_cross or dpo_bearish_cross else 'No Cross'}\n\n"
            f"📊 Market Condition: RANGING/CHOPPY MARKET\n"
            f"⚠️ Price in top {TOP_ZONE}% of channel in choppy market\n"
            f"📉 Low volatility (ATR < ATR_SMA20) suggests reversal setup\n"
            f"🎯 SELL SIGNAL: Mean-reversion expected\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${live_price + (atr_value * 2):.2f} (ATR×2 above entry)\n"
            f"💰 Take Profit: ${live_price - (atr_value * 1.5):.2f} (ATR×1.5 below entry)\n"
            f"📈 Risk/Reward: ~1:0.75"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🔴 SELL REVERSAL TRIGGERED")
        last_alert[symbol] = "SELL_REVERSAL"

    # ==============================================
    # SEND ALERTS - BUY REVERSAL
    # ==============================================
    elif buy_reversal_trigger and last_alert[symbol] != "BUY_REVERSAL":
        message = (
            f"🟢🟢🟢 BUY REVERSAL 🟢🟢🟢\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Current Price: ${live_price:.2f}\n"
            f"RSI: {rsi_value} (For reference only)\n"
            f"Choppiness Index: {chop_value} (>60 - Ranging Market)\n"
            f"Channel Position: {channel_percentile}% (Bottom {BOTTOM_ZONE}% Zone)\n"
            f"ATR: ${atr_value:.4f} (Reversal Confirmation - Volatility Contraction)\n"
            f"ATR SMA20: ${atr_sma20:.4f}\n"
            f"ATR < ATR_SMA20: ✅ (Volatility compression - reversal expected)\n"
            f"DPO Current: {dpo_current} (Conserved)\n"
            f"DPO Previous: {dpo_previous} (Conserved)\n"
            f"DPO {'Bullish' if dpo_bullish_cross else 'Bearish'} Cross: {'✅' if dpo_bullish_cross or dpo_bearish_cross else 'No Cross'}\n\n"
            f"📊 Market Condition: RANGING/CHOPPY MARKET\n"
            f"⚠️ Price in bottom {BOTTOM_ZONE}% of channel in choppy market\n"
            f"📉 Low volatility (ATR < ATR_SMA20) suggests reversal setup\n"
            f"🎯 BUY SIGNAL: Mean-reversion expected\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${live_price - (atr_value * 2):.2f} (ATR×2 below entry)\n"
            f"💰 Take Profit: ${live_price + (atr_value * 1.5):.2f} (ATR×1.5 above entry)\n"
            f"📈 Risk/Reward: ~1:0.75"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🟢 BUY REVERSAL TRIGGERED")
        last_alert[symbol] = "BUY_REVERSAL"

    # ==============================================
    # SEND ALERTS - BUY TREND
    # ==============================================
    elif buy_trend_trigger and last_alert[symbol] != "BUY_TREND":
        message = (
            f"🟢🟢🟢 BUY TREND CONTINUATION 🟢🟢🟢\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Current Price: ${live_price:.2f}\n"
            f"RSI: {rsi_value} (> {RSI_TREND_BUY} - Bullish Momentum)\n"
            f"Choppiness Index: {chop_value} (<40 - Trending Market)\n"
            f"Channel Position: {channel_percentile}% (Top {TOP_ZONE}% Zone)\n"
            f"ATR: ${atr_value:.4f} (Trend Confirmation - Volatility Expansion)\n"
            f"ATR SMA20: ${atr_sma20:.4f}\n"
            f"ATR > ATR_SMA20: ✅ (Volatility expansion - trend confirmed)\n"
            f"DPO Current: {dpo_current} (Conserved)\n"
            f"DPO Previous: {dpo_previous} (Conserved)\n"
            f"DPO {'Bullish' if dpo_bullish_cross else 'Bearish'} Cross: {'✅' if dpo_bullish_cross or dpo_bearish_cross else 'No Cross'}\n\n"
            f"📊 Market Condition: STRONG TRENDING MARKET\n"
            f"⚠️ Strong uptrend detected, momentum expected to continue\n"
            f"🟢 RSI confirms bullish momentum\n"
            f"📈 High volatility (ATR > ATR_SMA20) confirms trend strength\n"
            f"🎯 BUY SIGNAL: Trend continuation\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${live_price - (atr_value * 2):.2f} (ATR×2 below entry)\n"
            f"💰 Take Profit: ${live_price + (atr_value * 3):.2f} (ATR×3 above entry)\n"
            f"📈 Risk/Reward: ~1:1.5"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🟢 BUY TREND TRIGGERED")
        last_alert[symbol] = "BUY_TREND"

    # ==============================================
    # SEND ALERTS - SELL TREND
    # ==============================================
    elif sell_trend_trigger and last_alert[symbol] != "SELL_TREND":
        message = (
            f"🔴🔴🔴 SELL TREND CONTINUATION 🔴🔴🔴\n\n"
            f"Exchange: {EXCHANGE.name.capitalize()}\n"
            f"Symbol: {symbol}\n"
            f"Current Price: ${live_price:.2f}\n"
            f"RSI: {rsi_value} (< {RSI_TREND_SELL} - Bearish Momentum)\n"
            f"Choppiness Index: {chop_value} (<40 - Trending Market)\n"
            f"Channel Position: {channel_percentile}% (Bottom {BOTTOM_ZONE}% Zone)\n"
            f"ATR: ${atr_value:.4f} (Trend Confirmation - Volatility Expansion)\n"
            f"ATR SMA20: ${atr_sma20:.4f}\n"
            f"ATR > ATR_SMA20: ✅ (Volatility expansion - trend confirmed)\n"
            f"DPO Current: {dpo_current} (Conserved)\n"
            f"DPO Previous: {dpo_previous} (Conserved)\n"
            f"DPO {'Bullish' if dpo_bullish_cross else 'Bearish'} Cross: {'✅' if dpo_bullish_cross or dpo_bearish_cross else 'No Cross'}\n\n"
            f"📊 Market Condition: STRONG TRENDING MARKET\n"
            f"⚠️ Strong downtrend detected, momentum expected to continue\n"
            f"🔴 RSI confirms bearish momentum\n"
            f"📈 High volatility (ATR > ATR_SMA20) confirms trend strength\n"
            f"🎯 SELL SIGNAL: Trend continuation\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${live_price + (atr_value * 2):.2f} (ATR×2 above entry)\n"
            f"💰 Take Profit: ${live_price - (atr_value * 3):.2f} (ATR×3 below entry)\n"
            f"📈 Risk/Reward: ~1:1.5"
        )
        send_alert(message)
        print(f"✅ {symbol} - 🔴 SELL TREND TRIGGERED")
        last_alert[symbol] = "SELL_TREND"

    # Reset alert when conditions no longer met
    else:
        if last_alert[symbol] is not None:
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
    print("\n📊 REVERSAL SIGNALS (CHOP > 60) - NO RSI:")
    print(f"🔴 SELL REVERSAL: Top {TOP_ZONE}% & ATR < ATR_SMA20 (Volatility Contraction)")
    print(f"🟢 BUY REVERSAL: Bottom {BOTTOM_ZONE}% & ATR < ATR_SMA20 (Volatility Contraction)")
    print("\n📈 TREND SIGNALS (CHOP < 40):")
    print(f"🟢 BUY TREND: Top {TOP_ZONE}% & RSI > {RSI_TREND_BUY} & ATR > ATR_SMA20")
    print(f"🔴 SELL TREND: Bottom {BOTTOM_ZONE}% & RSI < {RSI_TREND_SELL} & ATR > ATR_SMA20")
    print("\n📊 CONSERVED INDICATORS (For Reference):")
    print("  • DPO21 (Bullish/Bearish Cross)")
    print("  • RSI14 (Only used for Trend signals)")
    print("============================\n")

    send_alert(f"✅ Bot Started on {EXCHANGE.name.capitalize()}\n\n"
               f"📊 Strategy: DC52 + CHOP14 + ATR14 + RSI14 (Trend only)\n"
               f"⏱️ Timeframe: 10-minute candles\n\n"
               f"📊 REVERSAL SIGNALS (CHOP > 60) - NO RSI:\n"
               f"🔴 SELL REVERSAL: Top {TOP_ZONE}% & ATR < ATR_SMA20\n"
               f"🟢 BUY REVERSAL: Bottom {BOTTOM_ZONE}% & ATR < ATR_SMA20\n\n"
               f"📈 TREND SIGNALS (CHOP < 40):\n"
               f"🟢 BUY TREND: Top {TOP_ZONE}% & RSI > {RSI_TREND_BUY} & ATR > ATR_SMA20\n"
               f"🔴 SELL TREND: Bottom {BOTTOM_ZONE}% & RSI < {RSI_TREND_SELL} & ATR > ATR_SMA20\n\n"
               f"📊 CONSERVED INDICATORS:\n"
               f"  • DPO21 (Bullish/Bearish Cross) - For informational purposes\n"
               f"  • RSI14 - Used for Trend signals only")

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