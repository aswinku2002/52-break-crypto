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
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'binance').lower()  # binance, kraken, coinbase, kucoin, bybit

# API keys for different exchanges
# Binance
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Kraken
KRAKEN_API_KEY = os.environ.get('KRAKEN_API_KEY', '')
KRAKEN_API_SECRET = os.environ.get('KRAKEN_API_SECRET', '')

# Coinbase
COINBASE_API_KEY = os.environ.get('COINBASE_API_KEY', '')
COINBASE_API_SECRET = os.environ.get('COINBASE_API_SECRET', '')

# KuCoin
KUCOIN_API_KEY = os.environ.get('KUCOIN_API_KEY', '')
KUCOIN_API_SECRET = os.environ.get('KUCOIN_API_SECRET', '')
KUCOIN_API_PASSPHRASE = os.environ.get('KUCOIN_API_PASSPHRASE', '')

# Bybit
BYBIT_API_KEY = os.environ.get('BYBIT_API_KEY', '')
BYBIT_API_SECRET = os.environ.get('BYBIT_API_SECRET', '')

SYMBOLS = [
    # Major Cryptocurrencies
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT',
    'DOGE/USDT', 'BNB/USDT', 'LTC/USDT', 'LINK/USDT',
    'AVAX/USDT', 'ADA/USDT', 'SUI/USDT', 'TRX/USDT',
    'BCH/USDT', 'AAVE/USDT', 'ETC/USDT', 'NEAR/USDT',
    'ORDI/USDT', 'WLD/USDT', 'HYPE/USDT', 'XLM/USDT',

    # Metal Tokens
    'XAUT/USDT', 'PAXG/USDT',

    # Additional Altcoins
    'UNI/USDT', 'ZEC/USDT', 'ENJ/USDT', 'XMR/USDT',
    'AXS/USDT', 'JTO/USDT', 'IO/USDT', 'ALT/USDT',

    # New/Recent Tokens
    'ACT/USDT', 'EVA/USDT', 'SLVON/USDT', 'EDEN/USDT',
    'SKYAI/USDT', 'EIGEN/USDT', 'SIREN/USDT', 'VVV/USDT',
    'WCT/USDT', 'SPCXX/USDT', 'AIO/USDT', 'SWARMS/USDT',
    'ALLO/USDT', 'RIVER/USDT', 'PIPPIN/USDT', 'BILL/USDT',
    'M/USDT', 'XPL/USDT', 'COAI/USDT', 'QQQX/USDT',
    'RAVE/USDT', 'BASED/USDT', 'BLESS/USDT', 'VELVET/USDT',
    'LAB/USDT', 'BEAT/USDT', 'H/USDT'
]

# Donchian Channel Zone Definitions
# Trend signals use tighter zones (2%)
TOP_TREND_ZONE = 98      # Top 2%
BOTTOM_TREND_ZONE = 2    # Bottom 2%

# Reversal signals use wider zones (5%)
TOP_REVERSAL_ZONE = 95   # Top 5%
BOTTOM_REVERSAL_ZONE = 5 # Bottom 5%

# Initialize exchanges
exchanges = {}

def init_exchange(exchange_name, config):
    """Initialize an exchange with error handling"""
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
        print(f"   Loaded {len(exchange.markets)} trading pairs")
        return exchange
    except Exception as e:
        print(f"❌ Error loading {exchange_name.capitalize()} markets: {e}")
        return None

# Initialize Binance
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
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
    'options': {
        'defaultType': 'spot',
    }
}
bybit_exchange = init_exchange('bybit', bybit_config)
if bybit_exchange:
    exchanges['bybit'] = bybit_exchange

# Select primary exchange for data fetching
EXCHANGE = exchanges.get(PRIMARY_EXCHANGE)
if not EXCHANGE:
    print(f"⚠️ Primary exchange '{PRIMARY_EXCHANGE}' not available. Using first available exchange.")
    EXCHANGE = next(iter(exchanges.values())) if exchanges else None

if not EXCHANGE:
    print("❌ No exchanges available. Please check your configuration.")
    exit(1)

print(f"✅ Using {EXCHANGE.name.capitalize()} as primary exchange")

# Prevent repeated alerts
last_alert = {}

def calculate_choppiness_index(df, period=21):
    """Calculate Choppiness Index"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']

        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Sum of True Range over period
        sum_tr = tr.rolling(window=period).sum()

        # Highest high and lowest low over period
        highest_high = high.rolling(window=period).max()
        lowest_low = low.rolling(window=period).min()

        # Avoid division by zero
        price_range = highest_high - lowest_low
        price_range = price_range.replace(0, np.nan)

        # Choppiness Index formula
        choppiness = 100 * np.log10(sum_tr / price_range) / np.log10(period)

        result = choppiness.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
    except Exception as e:
        print(f"Choppiness calculation error: {e}")
        return 50

def calculate_rsi(df, period=18):
    """Calculate RSI (Relative Strength Index)"""
    try:
        close = df['close']
        delta = close.diff()

        # Separate gains and losses
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        # Calculate RS and RSI
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        result = rsi.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
    except Exception as e:
        print(f"RSI calculation error: {e}")
        return 50

def calculate_atr(df, period=21):
    """
    Calculate Average True Range (ATR)
    
    ATR measures market volatility by averaging the true range over a period.
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: Lookback period for ATR (default 21)
    
    Returns:
        tuple: (current ATR value, ATR_SMA6 value)
    """
    try:
        high = df['high']
        low = df['low']
        close = df['close']

        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Calculate ATR (SMA of True Range)
        atr = tr.rolling(window=period).mean()

        # Calculate ATR21_SMA6 (6-period SMA of ATR21) - CHANGED FROM 10 TO 6
        atr_sma6 = atr.rolling(window=6).mean()

        # Get current values
        current_atr = atr.iloc[-1]
        current_atr_sma6 = atr_sma6.iloc[-1]

        return (round(current_atr, 4) if not pd.isna(current_atr) else 0, 
                round(current_atr_sma6, 4) if not pd.isna(current_atr_sma6) else 0)

    except Exception as e:
        print(f"ATR calculation error: {e}")
        return (0, 0)

def send_alert(message):
    if TOKEN and CHAT_ID:
        try:
            requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={
                    "chat_id": CHAT_ID,
                    "text": message
                },
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
            # Try alternative format (e.g., BTC/USD instead of BTC/USDT)
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

def run_bot():
    print("Bot loop started...")
    print(f"Exchange: {EXCHANGE.name.capitalize()} (Global)")

    # Get available symbols for this exchange
    available_symbols = get_available_symbols(EXCHANGE, SYMBOLS)
    print(f"✅ Available symbols on {EXCHANGE.name.capitalize()}: {len(available_symbols)}")

    print("\n===== MODIFIED STRATEGY WITH 10-MINUTE TIMEFRAME =====")
    print("📊 TIMEFRAME: 10-minute candles")
    print("📈 DONCHIAN CHANNEL: 78 periods")
    print("📊 RSI: 18 periods")
    print("📊 CHOPPINESS INDEX: 21 periods")
    print("📊 ATR: 21 periods with 6-period SMA")  # Updated
    print("\n📈 TREND ZONES (2%):")
    print(f"🟢 BUY TREND: Top 2% + RSI18 > 60 + CHOP21 < 40 + ATR21 > ATR21_SMA6")  # Updated
    print(f"🔴 SELL TREND: Bottom 2% + RSI18 < 40 + CHOP21 < 40 + ATR21 > ATR21_SMA6")  # Updated
    print("\n📊 REVERSAL ZONES (5%):")
    print(f"🟢 BUY REVERSAL: Bottom 5% + RSI18 < 30 + CHOP21 > 60 + ATR21 < ATR21_SMA6")  # Updated
    print(f"🔴 SELL REVERSAL: Top 5% + RSI18 > 70 + CHOP21 > 60 + ATR21 < ATR21_SMA6")  # Updated
    print("\n📊 ATR FILTER:")
    print("  • ATR21 > ATR21_SMA6 → Trend confirmation (increasing volatility)")
    print("  • ATR21 < ATR21_SMA6 → Reversal confirmation (decreasing volatility)")
    print("  • ATR21_SMA6 = 6-period SMA of ATR21 (MORE RESPONSIVE THAN SMA10)")  # Updated
    print("\n🎯 OBJECTIVE: Earlier signals with faster ATR response")
    print("============================\n")

    # Startup message
    send_alert(f"✅ Bot Started on {EXCHANGE.name.capitalize()}\n\n"
               f"📊 Donchian Channel (78) + CHOP (21) + RSI (18) + ATR (21)\n"
               f"⏱️ Timeframe: 10-minute candles\n"
               f"📈 ATR Filter: ATR21 vs ATR21_SMA6 (FASTER RESPONSE)\n\n"  # Updated
               f"🟢 BUY TREND:\n"
               f"• CHOP < 40 + Top 2% + RSI > 60\n"
               f"• ATR21 > ATR21_SMA6 (Volatility increasing)\n\n"  # Updated
               f"🔴 SELL TREND:\n"
               f"• CHOP < 40 + Bottom 2% + RSI < 40\n"
               f"• ATR21 > ATR21_SMA6 (Volatility increasing)\n\n"  # Updated
               f"🟢 BUY REVERSAL:\n"
               f"• CHOP > 60 + Bottom 5% + RSI < 30\n"
               f"• ATR21 < ATR21_SMA6 (Volatility decreasing)\n\n"  # Updated
               f"🔴 SELL REVERSAL:\n"
               f"• CHOP > 60 + Top 5% + RSI > 70\n"
               f"• ATR21 < ATR21_SMA6 (Volatility decreasing)")  # Updated

    while True:
        for symbol in available_symbols:
            try:
                # Get enough candles for calculations (minimum 150)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='10m',
                    limit=180  # Get more than 150 for reliable calculations
                )

                if len(ohlcv) < 150:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # ============ DONCHIAN CHANNEL (78 periods) ============
                HH = df['high'][-79:-1].max()  # Highest high of last 78 candles
                LL = df['low'][-79:-1].min()   # Lowest low of last 78 candles
                channel_range = HH - LL

                # ============ CHOPPINESS INDEX (21 periods) ============
                chop_value = calculate_choppiness_index(df, period=21)

                # ============ RSI (18 periods) ============
                rsi_value = calculate_rsi(df, period=18)

                # ============ ATR (21 periods) with SMA6 ============  # Updated
                atr_current, atr_sma6 = calculate_atr(df, period=21)  # Updated variable name

                # Current market price
                current_price = df['close'].iloc[-1]

                # Calculate position in channel
                channel_percentile = calculate_channel_percentile(HH, LL, current_price)

                # ============ ATR Logic with ATR21_SMA6 ============  # Updated
                atr_below_sma = atr_current < atr_sma6 if atr_sma6 > 0 else False
                atr_above_sma = atr_current > atr_sma6 if atr_sma6 > 0 else False
                atr_diff_pct = ((atr_current - atr_sma6) / atr_sma6 * 100) if atr_sma6 > 0 else 0

                # Determine if in alert zones
                # Trend zones (2%)
                is_top_trend_zone = channel_percentile >= TOP_TREND_ZONE  # Top 2%
                is_bottom_trend_zone = channel_percentile <= BOTTOM_TREND_ZONE  # Bottom 2%

                # Reversal zones (5%)
                is_top_reversal_zone = channel_percentile >= TOP_REVERSAL_ZONE  # Top 5%
                is_bottom_reversal_zone = channel_percentile <= BOTTOM_REVERSAL_ZONE  # Bottom 5%

                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None

                # Debug print with all indicators
                print(f"\n{'='*60}")
                print(f"🔍 {symbol} - 10m Analysis")
                print(f"{'='*60}")
                print(f"📊 Price: ${current_price:.4f}")
                print(f"📍 Channel Position: {channel_percentile}%")
                print(f"📍 Top 2%: {is_top_trend_zone} | Bottom 2%: {is_bottom_trend_zone}")
                print(f"📍 Top 5%: {is_top_reversal_zone} | Bottom 5%: {is_bottom_reversal_zone}")
                print(f"\n📊 Indicators:")
                print(f"  • RSI18: {rsi_value}")
                print(f"  • CHOP21: {chop_value}")
                print(f"  • ATR21 Current: {atr_current:.4f}")
                print(f"  • ATR21_SMA6: {atr_sma6:.4f}")  # Updated
                print(f"  • ATR Difference: {atr_diff_pct:.2f}%")
                print(f"  • ATR21 < ATR21_SMA6: {atr_below_sma} (Reversal confirmation)")  # Updated
                print(f"  • ATR21 > ATR21_SMA6: {atr_above_sma} (Trend confirmation)")  # Updated

                # Skip if indicators couldn't be calculated
                if chop_value == 50 or rsi_value == 50 or atr_current == 0 or atr_sma6 == 0:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue

                # ==============================================
                # CONDITION 1: BUY REVERSAL
                # CHOP > 60 & RSI < 30 & Bottom 5% & ATR < ATR_SMA6
                # ==============================================
                if (chop_value > 60 and is_bottom_reversal_zone and rsi_value < 30 and atr_below_sma):
                    if last_alert[symbol] != "BUY_REVERSAL":
                        # Calculate stop loss and take profit
                        stop_loss = current_price - (atr_current * 2)
                        take_profit = current_price + (atr_current * 1.5)

                        message = (
                            f"🟢🟢🟢 BUY REVERSAL 🟢🟢🟢\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Timeframe: 10-minute\n"
                            f"Current Price: ${current_price:.4f}\n"
                            f"RSI18: {rsi_value} (<30 - Oversold)\n"
                            f"CHOP21: {chop_value} (>60 - Ranging Market)\n"
                            f"Channel Position: {channel_percentile}% (Bottom 5% Reversal Zone)\n"
                            f"ATR21 Current: ${atr_current:.4f}\n"
                            f"ATR21_SMA6: ${atr_sma6:.4f}\n"  # Updated
                            f"ATR Difference: {atr_diff_pct:.2f}%\n"
                            f"ATR21 < ATR21_SMA6: ✅ (Volatility decreasing - reversal expected)\n\n"  # Updated
                            f"📊 Market Condition: RANGING/CHOPPY & OVERSOLD\n"
                            f"⚠️ Multiple reversal indicators aligned\n"
                            f"📉 Volatility decreasing - reversal setup\n"
                            f"🎯 BUY SIGNAL: Mean-reversion expected\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.4f} (ATR×2 below entry)\n"
                            f"💰 Take Profit: ${take_profit:.4f} (ATR×1.5 above entry)\n"
                            f"📈 Risk/Reward: ~1:0.75"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🟢 BUY REVERSAL TRIGGERED")
                        last_alert[symbol] = "BUY_REVERSAL"

                # ==============================================
                # CONDITION 2: SELL REVERSAL
                # CHOP > 60 & RSI > 70 & Top 5% & ATR < ATR_SMA6
                # ==============================================
                elif (chop_value > 60 and is_top_reversal_zone and rsi_value > 70 and atr_below_sma):
                    if last_alert[symbol] != "SELL_REVERSAL":
                        # Calculate stop loss and take profit
                        stop_loss = current_price + (atr_current * 2)
                        take_profit = current_price - (atr_current * 1.5)

                        message = (
                            f"🔴🔴🔴 SELL REVERSAL 🔴🔴🔴\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Timeframe: 10-minute\n"
                            f"Current Price: ${current_price:.4f}\n"
                            f"RSI18: {rsi_value} (>70 - Overbought)\n"
                            f"CHOP21: {chop_value} (>60 - Ranging Market)\n"
                            f"Channel Position: {channel_percentile}% (Top 5% Reversal Zone)\n"
                            f"ATR21 Current: ${atr_current:.4f}\n"
                            f"ATR21_SMA6: ${atr_sma6:.4f}\n"  # Updated
                            f"ATR Difference: {atr_diff_pct:.2f}%\n"
                            f"ATR21 < ATR21_SMA6: ✅ (Volatility decreasing - reversal expected)\n\n"  # Updated
                            f"📊 Market Condition: RANGING/CHOPPY & OVERBOUGHT\n"
                            f"⚠️ Multiple reversal indicators aligned\n"
                            f"📉 Volatility decreasing - reversal setup\n"
                            f"🎯 SELL SIGNAL: Mean-reversion expected\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.4f} (ATR×2 above entry)\n"
                            f"💰 Take Profit: ${take_profit:.4f} (ATR×1.5 below entry)\n"
                            f"📈 Risk/Reward: ~1:0.75"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🔴 SELL REVERSAL TRIGGERED")
                        last_alert[symbol] = "SELL_REVERSAL"

                # ==============================================
                # CONDITION 3: BUY TREND
                # CHOP < 40 & RSI > 60 & Top 2% & ATR > ATR_SMA6
                # ==============================================
                elif (chop_value < 40 and is_top_trend_zone and rsi_value > 60 and atr_above_sma):
                    if last_alert[symbol] != "BUY_TREND":
                        # Calculate stop loss and take profit
                        stop_loss = current_price - (atr_current * 2)
                        take_profit = current_price + (atr_current * 3)

                        message = (
                            f"🟢🟢🟢 BUY TREND CONTINUATION 🟢🟢🟢\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Timeframe: 10-minute\n"
                            f"Current Price: ${current_price:.4f}\n"
                            f"RSI18: {rsi_value} (>60 - Bullish Momentum)\n"
                            f"CHOP21: {chop_value} (<40 - Trending Market)\n"
                            f"Channel Position: {channel_percentile}% (Top 2% Trend Zone)\n"
                            f"ATR21 Current: ${atr_current:.4f}\n"
                            f"ATR21_SMA6: ${atr_sma6:.4f}\n"  # Updated
                            f"ATR Difference: {atr_diff_pct:.2f}%\n"
                            f"ATR21 > ATR21_SMA6: ✅ (Volatility increasing - trend confirmed)\n\n"  # Updated
                            f"📊 Market Condition: STRONG TRENDING & BULLISH\n"
                            f"⚠️ Multiple trend continuation indicators aligned\n"
                            f"📈 Volatility increasing - trend strength confirmed\n"
                            f"🎯 BUY SIGNAL: Trend continuation with high confidence\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.4f} (ATR×2 below entry)\n"
                            f"💰 Take Profit: ${take_profit:.4f} (ATR×3 above entry)\n"
                            f"📈 Risk/Reward: ~1:1.5"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🟢 BUY TREND TRIGGERED")
                        last_alert[symbol] = "BUY_TREND"

                # ==============================================
                # CONDITION 4: SELL TREND
                # CHOP < 40 & RSI < 40 & Bottom 2% & ATR > ATR_SMA6
                # ==============================================
                elif (chop_value < 40 and is_bottom_trend_zone and rsi_value < 40 and atr_above_sma):
                    if last_alert[symbol] != "SELL_TREND":
                        # Calculate stop loss and take profit
                        stop_loss = current_price + (atr_current * 2)
                        take_profit = current_price - (atr_current * 3)

                        message = (
                            f"🔴🔴🔴 SELL TREND CONTINUATION 🔴🔴🔴\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Timeframe: 10-minute\n"
                            f"Current Price: ${current_price:.4f}\n"
                            f"RSI18: {rsi_value} (<40 - Bearish Momentum)\n"
                            f"CHOP21: {chop_value} (<40 - Trending Market)\n"
                            f"Channel Position: {channel_percentile}% (Bottom 2% Trend Zone)\n"
                            f"ATR21 Current: ${atr_current:.4f}\n"
                            f"ATR21_SMA6: ${atr_sma6:.4f}\n"  # Updated
                            f"ATR Difference: {atr_diff_pct:.2f}%\n"
                            f"ATR21 > ATR21_SMA6: ✅ (Volatility increasing - trend confirmed)\n\n"  # Updated
                            f"📊 Market Condition: STRONG TRENDING & BEARISH\n"
                            f"⚠️ Multiple trend continuation indicators aligned\n"
                            f"📈 Volatility increasing - trend strength confirmed\n"
                            f"🎯 SELL SIGNAL: Trend continuation with high confidence\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${stop_loss:.4f} (ATR×2 above entry)\n"
                            f"💰 Take Profit: ${take_profit:.4f} (ATR×3 below entry)\n"
                            f"📈 Risk/Reward: ~1:1.5"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🔴 SELL TREND TRIGGERED")
                        last_alert[symbol] = "SELL_TREND"

                # Reset alert when conditions no longer met
                else:
                    if last_alert[symbol] is not None:
                        print(f"  → {symbol} - Alert reset: {last_alert[symbol]} condition ended")
                        last_alert[symbol] = None

                print(f"{'='*60}\n")

            except Exception as e:
                print(f"Error checking {symbol} on {EXCHANGE.name.capitalize()}: {e}")

        # Check every 15 seconds (faster than 15-minute version)
        time.sleep(20)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)