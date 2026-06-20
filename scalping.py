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
        tuple: (current_dpo, previous_dpo) values
    """
    try:
        close = df['close']
        
        # Ensure we have enough data
        if len(close) < period + 10:
            return (0, 0)
        
        # Calculate simple moving average
        sma = close.rolling(window=period).mean()
        
        # Calculate offset (half the period + 1)
        offset = period // 2 + 1
        
        # Shift SMA forward by offset periods
        # This creates the detrended price
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
    """
    Calculate Average True Range (ATR)
    
    ATR measures market volatility by averaging the true range over a period.
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: Lookback period for ATR (default 14)
    
    Returns:
        float: ATR value for the latest candle, or 0 if calculation fails
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
        
        result = atr.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 0
        return round(result, 4)
    
    except Exception as e:
        print(f"ATR calculation error: {e}")
        return 0

def calculate_atr_sma(df, atr_period=14, sma_period=20):
    """
    Calculate SMA of ATR
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        atr_period: Period for ATR calculation (default 14)
        sma_period: Period for SMA calculation (default 20)
    
    Returns:
        float: SMA of ATR value for the latest candle, or 0 if calculation fails
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
        
        # Calculate ATR
        atr = tr.rolling(window=atr_period).mean()
        
        # Calculate SMA of ATR
        atr_sma = atr.rolling(window=sma_period).mean()
        
        result = atr_sma.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 0
        return round(result, 4)
    
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
    
    print("===== UPDATED STRATEGY WITH DPO ONLY FOR REVERSALS =====")
    print("📊 TIMEFRAME: 15-minute candles")
    print("📈 DONCHIAN CHANNEL: 52 periods")
    print("\n📊 REVERSAL SIGNALS (CHOP > 60) - DPO REQUIRED:")
    print(f"🟢 BUY REVERSAL: Bottom 5% + RSI < 30 + DPO < 0 + ATR < ATR_SMA20")
    print(f"🔴 SELL REVERSAL: Top 5% + RSI > 70 + DPO > 0 + ATR < ATR_SMA20")
    print("\n📈 TREND SIGNALS (CHOP < 40) - NO DPO REQUIRED:")
    print(f"🟢 BUY TREND: Top 2% + RSI > 60 + ATR > ATR_SMA20 (DPO informational only)")
    print(f"🔴 SELL TREND: Bottom 2% + RSI < 40 + ATR > ATR_SMA20 (DPO informational only)")
    print("\n📊 ATR LOGIC:")
    print("  • Reversal: ATR < ATR_SMA20 (Volatility contraction - expecting reversal)")
    print("  • Trend: ATR > ATR_SMA20 (Volatility expansion - trend confirmation)")
    print("============================")
    
    # Startup message
    send_alert(f"✅ Bot Started on {EXCHANGE.name.capitalize()}\n\n"
               f"📊 Donchian Channel (52) + CHOP (14) + RSI (14) + DPO (21) + ATR (14) + ATR_SMA20\n"
               f"⏱️ Timeframe: 15-minute candles\n\n"
               f"🟢 BUY REVERSAL (DPO REQUIRED):\n"
               f"• CHOP > 60 + Bottom 5% + RSI < 30 + DPO < 0\n"
               f"• ATR < ATR_SMA20 (Volatility contraction)\n\n"
               f"🔴 SELL REVERSAL (DPO REQUIRED):\n"
               f"• CHOP > 60 + Top 5% + RSI > 70 + DPO > 0\n"
               f"• ATR < ATR_SMA20 (Volatility contraction)\n\n"
               f"🟢 BUY TREND (NO DPO REQUIRED):\n"
               f"• CHOP < 40 + Top 2% + RSI > 60\n"
               f"• ATR > ATR_SMA20 (Volatility expansion)\n"
               f"• DPO shown for reference only\n\n"
               f"🔴 SELL TREND (NO DPO REQUIRED):\n"
               f"• CHOP < 40 + Bottom 2% + RSI < 40\n"
               f"• ATR > ATR_SMA20 (Volatility expansion)\n"
               f"• DPO shown for reference only")
    
    while True:
        for symbol in available_symbols:
            try:
                # Get enough candles for calculations
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=150  # Increased for ATR SMA calculation
                )
                
                if len(ohlcv) < 100:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # ============ DONCHIAN CHANNEL (52 candles) ============
                HH = df['high'][-53:-1].max()  # Highest high
                LL = df['low'][-53:-1].min()   # Lowest low
                channel_range = HH - LL
                
                # ============ CHOPPINESS INDEX (14) ============
                chop_value = calculate_choppiness_index(df, period=14)
                
                # ============ RSI (14) ============
                rsi_value = calculate_rsi(df, period=14)
                
                # ============ DPO (21) ============
                dpo_current, dpo_previous = calculate_dpo(df, period=21)
                
                # ============ ATR (14) ============
                atr_value = calculate_atr(df, period=14)
                
                # ============ ATR_SMA20 ============
                atr_sma20 = calculate_atr_sma(df, atr_period=14, sma_period=20)
                
                # Current market price
                current_price = df['close'].iloc[-1]
                
                # Calculate position in channel
                channel_percentile = calculate_channel_percentile(HH, LL, current_price)
                
                # ============ ATR Logic ============
                atr_below_sma = atr_value < atr_sma20 if atr_sma20 > 0 else False
                atr_above_sma = atr_value > atr_sma20 if atr_sma20 > 0 else False
                
                # Determine if in alert zones
                # Trend zones (tighter)
                is_top_trend_zone = channel_percentile >= TOP_TREND_ZONE  # Top 2%
                is_bottom_trend_zone = channel_percentile <= BOTTOM_TREND_ZONE  # Bottom 2%
                
                # Reversal zones (wider)
                is_top_reversal_zone = channel_percentile >= TOP_REVERSAL_ZONE  # Top 5%
                is_bottom_reversal_zone = channel_percentile <= BOTTOM_REVERSAL_ZONE  # Bottom 5%
                
                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None
                
                # Debug print with all indicators
                print(f"\n{'='*60}")
                print(f"🔍 {symbol} - 15m Analysis")
                print(f"{'='*60}")
                print(f"📊 Price: ${current_price:.2f}")
                print(f"📍 Channel Position: {channel_percentile}%")
                print(f"📍 Top 2%: {is_top_trend_zone} | Bottom 2%: {is_bottom_trend_zone}")
                print(f"📍 Top 5%: {is_top_reversal_zone} | Bottom 5%: {is_bottom_reversal_zone}")
                print(f"\n📊 Indicators:")
                print(f"  • RSI14: {rsi_value}")
                print(f"  • CHOP14: {chop_value}")
                print(f"  • DPO Current: {dpo_current} | Previous: {dpo_previous} (Required for REVERSALS only)")
                print(f"  • ATR14: {atr_value}")
                print(f"  • ATR_SMA20: {atr_sma20}")
                print(f"  • ATR < ATR_SMA20: {atr_below_sma} (Reversal confirmation - volatility contraction)")
                print(f"  • ATR > ATR_SMA20: {atr_above_sma} (Trend confirmation - volatility expansion)")
                
                # Skip if indicators couldn't be calculated
                if chop_value == 50 or rsi_value == 50 or atr_value == 0 or atr_sma20 == 0:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue
                
                # ==============================================
                # CONDITION 1: BUY REVERSAL (DPO REQUIRED)
                # CHOP > 60 & RSI < 30 & DPO < 0 & Bottom 5% & ATR < ATR_SMA20
                # ==============================================
                if (chop_value > 60 and is_bottom_reversal_zone and rsi_value < 30 and 
                    dpo_current < 0 and atr_below_sma):
                    if last_alert[symbol] != "BUY_REVERSAL_ATR":
                        message = (
                            f"🟢🟢🟢 BUY REVERSAL (DPO + ATR/SMA20 Strategy) 🟢🟢🟢\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value} (<30 - Oversold)\n"
                            f"DPO: {dpo_current} (<0 - Price below SMA) ✅ REQUIRED\n"
                            f"Choppiness Index: {chop_value} (>60 - Ranging Market)\n"
                            f"Channel Position: {channel_percentile}% (Bottom 5% Reversal Zone)\n"
                            f"ATR: ${atr_value:.4f}\n"
                            f"ATR_SMA20: ${atr_sma20:.4f}\n"
                            f"ATR < ATR_SMA20: ✅ (Volatility contraction - reversal expected)\n\n"
                            f"📊 Market Condition: RANGING/CHOPPY MARKET & OVERSOLD\n"
                            f"⚠️ Multiple reversal indicators aligned\n"
                            f"📉 Low volatility suggests reversal setup\n"
                            f"🎯 BUY SIGNAL: Mean-reversion expected\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${current_price - (atr_value * 2):.2f} (ATR×2 below entry)\n"
                            f"💰 Take Profit: ${current_price + (atr_value * 1.5):.2f} (ATR×1.5 above entry)\n"
                            f"📈 Risk/Reward: ~1:0.75"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🟢 BUY REVERSAL TRIGGERED (DPO: {dpo_current})")
                        last_alert[symbol] = "BUY_REVERSAL_ATR"
                
                # ==============================================
                # CONDITION 2: SELL REVERSAL (DPO REQUIRED)
                # CHOP > 60 & RSI > 70 & DPO > 0 & Top 5% & ATR < ATR_SMA20
                # ==============================================
                elif (chop_value > 60 and is_top_reversal_zone and rsi_value > 70 and 
                      dpo_current > 0 and atr_below_sma):
                    if last_alert[symbol] != "SELL_REVERSAL_ATR":
                        message = (
                            f"🔴🔴🔴 SELL REVERSAL (DPO + ATR/SMA20 Strategy) 🔴🔴🔴\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value} (>70 - Overbought)\n"
                            f"DPO: {dpo_current} (>0 - Price above SMA) ✅ REQUIRED\n"
                            f"Choppiness Index: {chop_value} (>60 - Ranging Market)\n"
                            f"Channel Position: {channel_percentile}% (Top 5% Reversal Zone)\n"
                            f"ATR: ${atr_value:.4f}\n"
                            f"ATR_SMA20: ${atr_sma20:.4f}\n"
                            f"ATR < ATR_SMA20: ✅ (Volatility contraction - reversal expected)\n\n"
                            f"📊 Market Condition: RANGING/CHOPPY MARKET & OVERBOUGHT\n"
                            f"⚠️ Multiple reversal indicators aligned\n"
                            f"📉 Low volatility suggests reversal setup\n"
                            f"🎯 SELL SIGNAL: Mean-reversion expected\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${current_price + (atr_value * 2):.2f} (ATR×2 above entry)\n"
                            f"💰 Take Profit: ${current_price - (atr_value * 1.5):.2f} (ATR×1.5 below entry)\n"
                            f"📈 Risk/Reward: ~1:0.75"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🔴 SELL REVERSAL TRIGGERED (DPO: {dpo_current})")
                        last_alert[symbol] = "SELL_REVERSAL_ATR"
                
                # ==============================================
                # CONDITION 3: BUY TREND (NO DPO REQUIRED)
                # CHOP < 40 & RSI > 60 & Top 2% & ATR > ATR_SMA20
                # DPO is informational only
                # ==============================================
                elif (chop_value < 40 and is_top_trend_zone and rsi_value > 60 and atr_above_sma):
                    if last_alert[symbol] != "BUY_TREND_ATR":
                        message = (
                            f"🟢🟢🟢 BUY TREND CONTINUATION (ATR/SMA20 Strategy) 🟢🟢🟢\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value} (>60 - Bullish Momentum)\n"
                            f"DPO: {dpo_current} (INFORMATIONAL ONLY - Not required for trend)\n"
                            f"Choppiness Index: {chop_value} (<40 - Trending Market)\n"
                            f"Channel Position: {channel_percentile}% (Top 2% Trend Zone)\n"
                            f"ATR: ${atr_value:.4f}\n"
                            f"ATR_SMA20: ${atr_sma20:.4f}\n"
                            f"ATR > ATR_SMA20: ✅ (Volatility expansion - trend confirmed)\n\n"
                            f"📊 Market Condition: STRONG TRENDING & BULLISH\n"
                            f"⚠️ Multiple trend continuation indicators aligned\n"
                            f"📈 High volatility confirms trend strength\n"
                            f"🎯 BUY SIGNAL: Trend continuation with high confidence\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${current_price - (atr_value * 2):.2f} (ATR×2 below entry)\n"
                            f"💰 Take Profit: ${current_price + (atr_value * 3):.2f} (ATR×3 above entry)\n"
                            f"📈 Risk/Reward: ~1:1.5"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🟢 BUY TREND TRIGGERED (DPO: {dpo_current} - informational)")
                        last_alert[symbol] = "BUY_TREND_ATR"
                
                # ==============================================
                # CONDITION 4: SELL TREND (NO DPO REQUIRED)
                # CHOP < 40 & RSI < 40 & Bottom 2% & ATR > ATR_SMA20
                # DPO is informational only
                # ==============================================
                elif (chop_value < 40 and is_bottom_trend_zone and rsi_value < 40 and atr_above_sma):
                    if last_alert[symbol] != "SELL_TREND_ATR":
                        message = (
                            f"🔴🔴🔴 SELL TREND CONTINUATION (ATR/SMA20 Strategy) 🔴🔴🔴\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"RSI: {rsi_value} (<40 - Bearish Momentum)\n"
                            f"DPO: {dpo_current} (INFORMATIONAL ONLY - Not required for trend)\n"
                            f"Choppiness Index: {chop_value} (<40 - Trending Market)\n"
                            f"Channel Position: {channel_percentile}% (Bottom 2% Trend Zone)\n"
                            f"ATR: ${atr_value:.4f}\n"
                            f"ATR_SMA20: ${atr_sma20:.4f}\n"
                            f"ATR > ATR_SMA20: ✅ (Volatility expansion - trend confirmed)\n\n"
                            f"📊 Market Condition: STRONG TRENDING & BEARISH\n"
                            f"⚠️ Multiple trend continuation indicators aligned\n"
                            f"📈 High volatility confirms trend strength\n"
                            f"🎯 SELL SIGNAL: Trend continuation with high confidence\n\n"
                            f"📈 RISK MANAGEMENT:\n"
                            f"🛑 Stop Loss: ${current_price + (atr_value * 2):.2f} (ATR×2 above entry)\n"
                            f"💰 Take Profit: ${current_price - (atr_value * 3):.2f} (ATR×3 below entry)\n"
                            f"📈 Risk/Reward: ~1:1.5"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🔴 SELL TREND TRIGGERED (DPO: {dpo_current} - informational)")
                        last_alert[symbol] = "SELL_TREND_ATR"
                
                # Reset alert when conditions no longer met
                else:
                    if last_alert[symbol] is not None:
                        print(f"  → {symbol} - Alert reset: {last_alert[symbol]} condition ended")
                        last_alert[symbol] = None
                
                print(f"{'='*60}\n")
                
            except Exception as e:
                print(f"Error checking {symbol} on {EXCHANGE.name.capitalize()}: {e}")
        
        # Check every 20 seconds
        time.sleep(20)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)