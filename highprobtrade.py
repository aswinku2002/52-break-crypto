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
# Trend signals use 2%
TOP_TREND_ZONE = 98      # Top 2%
BOTTOM_TREND_ZONE = 2    # Bottom 2%

# Reversal signals use 5%
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

def calculate_choppiness_index(df, period=14):
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

def calculate_rsi(df, period=14):
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

def calculate_dpo(df, period=21):
    """
    Calculate Detrended Price Oscillator (DPO)
    
    DPO = Price - SMA(Price, period/2 + 1) shifted by (period/2 + 1)
    Standard DPO shifts the SMA forward by half the period + 1
    
    Args:
        df: DataFrame with 'close' column
        period: Lookback period for DPO (default 21)
    
    Returns:
        tuple: (current_dpo, previous_dpo) - Current and previous DPO values
    """
    try:
        close = df['close']

        # Ensure we have enough data
        if len(close) < period + 10:
            print(f"DPO: Insufficient data. Need {period + 10}, have {len(close)}")
            return (0, 0)

        # Calculate simple moving average
        sma = close.rolling(window=period).mean()

        # Calculate offset (half the period + 1)
        offset = period // 2 + 1

        # Shift SMA forward by offset periods
        shifted_sma = sma.shift(offset)

        # DPO = Price - shifted SMA
        dpo = close - shifted_sma

        # Get current and previous values
        current_dpo = dpo.iloc[-1]
        previous_dpo = dpo.iloc[-2] if len(dpo) >= 2 else 0

        # Handle NaN or infinite values
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

        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Calculate ATR
        atr = tr.rolling(window=period).mean()

        current_atr = atr.iloc[-1]
        if pd.isna(current_atr) or np.isinf(current_atr):
            return 0

        return round(current_atr, 4)
    except Exception as e:
        print(f"ATR calculation error: {e}")
        return 0

def calculate_atr_sma(df, atr_period=14, sma_period=20):
    """Calculate ATR SMA for comparison"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']

        # Calculate True Range
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Calculate ATR
        atr = tr.rolling(window=atr_period).mean()

        # Calculate SMA of ATR
        atr_sma = atr.rolling(window=sma_period).mean()

        current_atr_sma = atr_sma.iloc[-1]
        if pd.isna(current_atr_sma) or np.isinf(current_atr_sma):
            return 0

        return round(current_atr_sma, 4)
    except Exception as e:
        print(f"ATR SMA calculation error: {e}")
        return 0

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

    print("===== UPDATED SIGNAL CONDITIONS =====")
    print("📊 REVERSAL SIGNALS (DPO Cross Confirmation):")
    print("🟢 BUY REVERSAL: CHOP > 65 & RSI < 30 & Bottom 5% & DPO Bullish Cross")
    print("🔴 SELL REVERSAL: CHOP > 65 & RSI > 70 & Top 5% & DPO Bearish Cross")
    print("")
    print("📈 TREND SIGNALS (ATR Validation):")
    print("🟢 BUY TREND: Close > DC52 High & CHOP < 40 & RSI > 55 & DPO > 0 & ATR Rising")
    print("🔴 SELL TREND: Close < DC52 Low & CHOP < 40 & RSI < 45 & DPO < 0 & ATR Rising")
    print("============================")

    # Startup message
    send_alert(f"✅ Bot Started on {EXCHANGE.name.capitalize()}\n\n"
               f"📊 Updated Strategy:\n"
               f"• DC52 + CHOP14 + RSI14 + DPO21 + ATR14\n"
               f"• 10-minute candles\n\n"
               f"🟢 BUY REVERSAL:\n"
               f"• CHOP > 65 + Bottom 5% + RSI < 30\n"
               f"• DPO Bullish Cross (Previous < 0, Current > 0)\n\n"
               f"🔴 SELL REVERSAL:\n"
               f"• CHOP > 65 + Top 5% + RSI > 70\n"
               f"• DPO Bearish Cross (Previous > 0, Current < 0)\n\n"
               f"🟢 BUY TREND:\n"
               f"• Close > DC52 High + CHOP < 40 + RSI > 55\n"
               f"• DPO > 0 + ATR Rising (ATR > ATR from 5 candles ago)\n\n"
               f"🔴 SELL TREND:\n"
               f"• Close < DC52 Low + CHOP < 40 + RSI < 45\n"
               f"• DPO < 0 + ATR Rising (ATR > ATR from 5 candles ago)")

    while True:
        for symbol in available_symbols:
            try:
                # ==============================================
                # STEP 1: Get LIVE TICKER DATA for DC52 (Current Price)
                # ==============================================
                ticker = EXCHANGE.fetch_ticker(symbol)
                live_price = ticker['last']  # Current live price
                live_high = ticker['high']   # Today's high
                live_low = ticker['low']     # Today's low
                
                # ==============================================
                # STEP 2: Get HISTORICAL CANDLE DATA for Indicators
                # ==============================================
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='10m',
                    limit=150  # Need extra for ATR SMA calculation
                )

                if len(ohlcv) < 100:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # ==============================================
                # DONCHIAN CHANNEL (52 candles) - USING LIVE DATA
                # ==============================================
                # Use previous 52 closed candles for the range
                prev_52_high = df['high'][-53:-1].max()  # Highest high from previous 52 candles
                prev_52_low = df['low'][-53:-1].min()    # Lowest low from previous 52 candles
                
                # Update with LIVE data if it's higher/lower
                # DC52 High = Max(previous 52 high, today's live high)
                # DC52 Low = Min(previous 52 low, today's live low)
                HH = max(prev_52_high, live_high) if live_high else prev_52_high
                LL = min(prev_52_low, live_low) if live_low else prev_52_low
                
                # Use LIVE price for current price
                current_price = live_price
                
                dc52_high = HH
                dc52_low = LL
                channel_range = HH - LL

                # ==============================================
                # INDICATORS - Using CLOSED CANDLE DATA only
                # ==============================================
                
                # CHOPPINESS INDEX (14) - from closed candles
                chop_value = calculate_choppiness_index(df, period=14)

                # RSI (14) - from closed candles
                rsi_value = calculate_rsi(df, period=14)

                # DPO (21) - from closed candles
                dpo_current, dpo_previous = calculate_dpo(df, period=21)

                # DPO Cross detection
                dpo_bullish_cross = dpo_previous < 0 and dpo_current > 0
                dpo_bearish_cross = dpo_previous > 0 and dpo_current < 0

                # ATR (14) - from closed candles
                atr_value = calculate_atr(df, period=14)

                # Get ATR from 5 candles ago - from closed candles
                atr_5_candles_ago = 0
                try:
                    # Calculate ATR for all candles
                    high = df['high']
                    low = df['low']
                    close = df['close']
                    tr1 = high - low
                    tr2 = abs(high - close.shift())
                    tr3 = abs(low - close.shift())
                    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                    atr_series = tr.rolling(window=14).mean()

                    # Get ATR from 5 candles ago (index -6 because we want 5 candles before current)
                    if len(atr_series) >= 6:
                        atr_5_candles_ago = atr_series.iloc[-6]
                        if pd.isna(atr_5_candles_ago) or np.isinf(atr_5_candles_ago):
                            atr_5_candles_ago = 0
                except Exception as e:
                    print(f"ATR 5-candle calculation error: {e}")
                    atr_5_candles_ago = 0

                # ATR Rising definition: ATR14 > ATR14 from 5 candles ago
                atr_rising = atr_value > atr_5_candles_ago if atr_5_candles_ago > 0 else False

                # Calculate position in channel using LIVE price
                channel_percentile = calculate_channel_percentile(HH, LL, current_price)

                # Determine if price is in alert zones
                is_top_trend_zone = channel_percentile >= TOP_TREND_ZONE  # Top 2%
                is_bottom_trend_zone = channel_percentile <= BOTTOM_TREND_ZONE  # Bottom 2%
                is_top_reversal_zone = channel_percentile >= TOP_REVERSAL_ZONE  # Top 5%
                is_bottom_reversal_zone = channel_percentile <= BOTTOM_REVERSAL_ZONE  # Bottom 5%

                # Breakout detection (using LIVE price)
                price_above_dc52_high = current_price > dc52_high
                price_below_dc52_low = current_price < dc52_low

                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None

                # ==============================================
                # DEBUG LOGS - All required indicators
                # ==============================================
                print(f"\n🔍 {symbol} - DEBUG LOGS:")
                print(f"  • LIVE Price: ${current_price:.2f}")
                print(f"  • LIVE High: ${live_high:.2f}")
                print(f"  • LIVE Low: ${live_low:.2f}")
                print(f"  • DC52 High (with LIVE): ${dc52_high:.2f}")
                print(f"  • DC52 Low (with LIVE): ${dc52_low:.2f}")
                print(f"  • RSI14: {rsi_value}")
                print(f"  • CHOP14: {chop_value}")
                print(f"  • Current DPO: {dpo_current}")
                print(f"  • Previous DPO: {dpo_previous}")
                print(f"  • DPO Bullish Cross: {dpo_bullish_cross}")
                print(f"  • DPO Bearish Cross: {dpo_bearish_cross}")
                print(f"  • ATR14: {atr_value}")
                print(f"  • ATR from 5 candles ago: {atr_5_candles_ago}")
                print(f"  • ATR Rising (ATR > ATR 5 candles ago): {atr_rising}")
                print(f"  • Channel Position: {channel_percentile}%")
                print(f"  • Price vs DC52 High: {'ABOVE' if price_above_dc52_high else 'BELOW'}")
                print(f"  • Price vs DC52 Low: {'BELOW' if price_below_dc52_low else 'ABOVE'}")

                # Skip if indicators couldn't be calculated
                if chop_value == 50 or rsi_value == 50:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue

                # ==============================================
                # REVERSAL SIGNAL: BUY REVERSAL
                # CHOP > 65 & RSI < 30 & Bottom 5% & DPO Bullish Cross
                # ==============================================
                if chop_value > 65 and is_bottom_reversal_zone and rsi_value < 30 and dpo_bullish_cross:
                    if last_alert[symbol] != "BUY_REVERSAL":
                        message = (
                            f"🟢🟢🟢 BUY REVERSAL (DPO Confirmed) 🟢🟢🟢\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.2f} (LIVE)\n"
                            f"RSI14: {rsi_value} (<30 - Oversold)\n"
                            f"CHOP14: {chop_value} (>65 - Extreme Choppy)\n"
                            f"Current DPO: {dpo_current:.2f}\n"
                            f"Previous DPO: {dpo_previous:.2f}\n"
                            f"ATR14: {atr_value:.4f}\n"
                            f"DC52 High (LIVE): ${dc52_high:.2f}\n"
                            f"DC52 Low (LIVE): ${dc52_low:.2f}\n"
                            f"Channel Position: {channel_percentile}% (Bottom 5% Zone)\n\n"
                            f"📊 DPO Bullish Cross Confirmed!\n"
                            f"✅ Previous DPO < 0 → Current DPO > 0\n"
                            f"🎯 Reversal signal triggered at turn"
                        )
                        send_alert(message)
                        print(f"✅ {symbol} - 🟢 BUY REVERSAL TRIGGERED (DPO Bullish Cross)")
                        last_alert[symbol] = "BUY_REVERSAL"

                # ==============================================
                # REVERSAL SIGNAL: SELL REVERSAL
                # CHOP > 65 & RSI > 70 & Top 5% & DPO Bearish Cross
                # ==============================================
                elif chop_value > 65 and is_top_reversal_zone and rsi_value > 70 and dpo_bearish_cross:
                    if last_alert[symbol] != "SELL_REVERSAL":
                        message = (
                            f"🔴🔴🔴 SELL REVERSAL (DPO Confirmed) 🔴🔴🔴\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.2f} (LIVE)\n"
                            f"RSI14: {rsi_value} (>70 - Overbought)\n"
                            f"CHOP14: {chop_value} (>65 - Extreme Choppy)\n"
                            f"Current DPO: {dpo_current:.2f}\n"
                            f"Previous DPO: {dpo_previous:.2f}\n"
                            f"ATR14: {atr_value:.4f}\n"
                            f"DC52 High (LIVE): ${dc52_high:.2f}\n"
                            f"DC52 Low (LIVE): ${dc52_low:.2f}\n"
                            f"Channel Position: {channel_percentile}% (Top 5% Zone)\n\n"
                            f"📊 DPO Bearish Cross Confirmed!\n"
                            f"✅ Previous DPO > 0 → Current DPO < 0\n"
                            f"🎯 Reversal signal triggered at turn"
                        )
                        send_alert(message)
                        print(f"✅ {symbol} - 🔴 SELL REVERSAL TRIGGERED (DPO Bearish Cross)")
                        last_alert[symbol] = "SELL_REVERSAL"

                # ==============================================
                # TREND SIGNAL: BUY TREND
                # Close > DC52 High & CHOP < 40 & RSI > 55 & DPO > 0 & ATR Rising
                # ==============================================
                elif price_above_dc52_high and chop_value < 40 and rsi_value > 55 and dpo_current > 0 and atr_rising:
                    if last_alert[symbol] != "BUY_TREND":
                        message = (
                            f"🟢🟢🟢 BUY TREND (ATR Validated) 🟢🟢🟢\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.2f} (LIVE)\n"
                            f"RSI14: {rsi_value} (>55 - Bullish Momentum)\n"
                            f"CHOP14: {chop_value} (<40 - Trending)\n"
                            f"Current DPO: {dpo_current:.2f} (>0 - Upward Trend)\n"
                            f"Previous DPO: {dpo_previous:.2f}\n"
                            f"ATR14: {atr_value:.4f} (Rising)\n"
                            f"DC52 High (LIVE): ${dc52_high:.2f}\n"
                            f"DC52 Low (LIVE): ${dc52_low:.2f}\n"
                            f"Channel Position: {channel_percentile}%\n\n"
                            f"📈 Breakout above DC52 High confirmed!\n"
                            f"✅ ATR Rising validates trend strength\n"
                            f"🎯 Trend continuation signal"
                        )
                        send_alert(message)
                        print(f"✅ {symbol} - 🟢 BUY TREND TRIGGERED (ATR Rising)")
                        last_alert[symbol] = "BUY_TREND"

                # ==============================================
                # TREND SIGNAL: SELL TREND
                # Close < DC52 Low & CHOP < 40 & RSI < 45 & DPO < 0 & ATR Rising
                # ==============================================
                elif price_below_dc52_low and chop_value < 40 and rsi_value < 45 and dpo_current < 0 and atr_rising:
                    if last_alert[symbol] != "SELL_TREND":
                        message = (
                            f"🔴🔴🔴 SELL TREND (ATR Validated) 🔴🔴🔴\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.2f} (LIVE)\n"
                            f"RSI14: {rsi_value} (<45 - Bearish Momentum)\n"
                            f"CHOP14: {chop_value} (<40 - Trending)\n"
                            f"Current DPO: {dpo_current:.2f} (<0 - Downward Trend)\n"
                            f"Previous DPO: {dpo_previous:.2f}\n"
                            f"ATR14: {atr_value:.4f} (Rising)\n"
                            f"DC52 High (LIVE): ${dc52_high:.2f}\n"
                            f"DC52 Low (LIVE): ${dc52_low:.2f}\n"
                            f"Channel Position: {channel_percentile}%\n\n"
                            f"📉 Breakdown below DC52 Low confirmed!\n"
                            f"✅ ATR Rising validates trend strength\n"
                            f"🎯 Trend continuation signal"
                        )
                        send_alert(message)
                        print(f"✅ {symbol} - 🔴 SELL TREND TRIGGERED (ATR Rising)")
                        last_alert[symbol] = "SELL_TREND"

                # Reset alert when conditions no longer met
                else:
                    if last_alert[symbol] is not None:
                        print(f"  → {symbol} - Alert reset: {last_alert[symbol]} condition ended")
                        last_alert[symbol] = None

            except Exception as e:
                print(f"Error checking {symbol} on {EXCHANGE.name.capitalize()}: {e}")

        # Check every 2 minutes (10-minute candles)
        time.sleep(180)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)