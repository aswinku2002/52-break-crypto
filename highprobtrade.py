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

# Donchian Channel period
DONCHIAN_PERIOD = 52

# Choppiness Index period
CHOP_PERIOD = 21

# Historical Volatility period
HV_PERIOD = 21

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

def calculate_historical_volatility(df, period=21):
    """
    Calculate Historical Volatility (HV) using log returns
    
    HV = rolling standard deviation of log returns * sqrt(period)
    """
    try:
        close = df['close']
        
        # Calculate log returns
        log_returns = np.log(close / close.shift())
        
        # Calculate rolling standard deviation of log returns
        rolling_std = log_returns.rolling(window=period).std()
        
        # Annualize (or periodize) the volatility
        hv = rolling_std * np.sqrt(period)
        
        # Get current HV (latest complete candle)
        hv_current = hv.iloc[-1]
        
        # Get HV 5 candles ago
        hv_5_ago = hv.iloc[-6] if len(hv) >= 6 else None
        
        # Calculate if HV is rising
        hv_rising = False
        if hv_current is not None and hv_5_ago is not None:
            hv_rising = hv_current > hv_5_ago
        
        # Handle NaN or infinite values
        if pd.isna(hv_current) or np.isinf(hv_current):
            hv_current = 0
        
        if hv_5_ago is None or pd.isna(hv_5_ago) or np.isinf(hv_5_ago):
            hv_5_ago = 0
        
        return (round(hv_current, 4), round(hv_5_ago, 4), hv_rising)
    
    except Exception as e:
        print(f"Historical Volatility calculation error: {e}")
        return (0, 0, False)

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
    
    print("===== STRATEGY: DONCHIAN CHANNEL BREAKOUT WITH VOLATILITY CONFIRMATION =====")
    print("📊 TIMEFRAME: 15-minute candles")
    print(f"📈 DONCHIAN CHANNEL: {DONCHIAN_PERIOD} periods")
    print(f"📊 CHOPPINESS INDEX: {CHOP_PERIOD} periods")
    print(f"📊 HISTORICAL VOLATILITY: {HV_PERIOD} periods (log returns)")
    print("\n📈 BUY CONDITIONS:")
    print(f"  • Close >= Donchian High({DONCHIAN_PERIOD})")
    print(f"  • CHOP{CHOP_PERIOD} < 35")
    print(f"  • HV{HV_PERIOD} > HV{HV_PERIOD}[5] (Volatility Expanding)")
    print("\n📉 SELL CONDITIONS:")
    print(f"  • Close <= Donchian Low({DONCHIAN_PERIOD})")
    print(f"  • CHOP{CHOP_PERIOD} < 35")
    print(f"  • HV{HV_PERIOD} > HV{HV_PERIOD}[5] (Volatility Expanding)")
    print("============================")
    
    # Startup message
    send_alert(f"✅ Bot Started on {EXCHANGE.name.capitalize()}\n\n"
               f"📊 Donchian Channel ({DONCHIAN_PERIOD}) + CHOP ({CHOP_PERIOD}) + HV ({HV_PERIOD})\n"
               f"⏱️ Timeframe: 15-minute candles\n\n"
               f"📈 BUY CONDITIONS:\n"
               f"• Close >= Donchian High({DONCHIAN_PERIOD})\n"
               f"• CHOP{CHOP_PERIOD} < 35\n"
               f"• HV{HV_PERIOD} > HV{HV_PERIOD}[5]\n\n"
               f"📉 SELL CONDITIONS:\n"
               f"• Close <= Donchian Low({DONCHIAN_PERIOD})\n"
               f"• CHOP{CHOP_PERIOD} < 35\n"
               f"• HV{HV_PERIOD} > HV{HV_PERIOD}[5]")
    
    while True:
        for symbol in available_symbols:
            try:
                # Get enough candles for calculations (need extra for HV 5 candles ago)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=150
                )
                
                if len(ohlcv) < 100:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # ============ DONCHIAN CHANNEL (52 periods) ============
                # Use last DONCHIAN_PERIOD candles (excluding current incomplete candle)
                donchian_high = df['high'].iloc[-(DONCHIAN_PERIOD+1):-1].max()
                donchian_low = df['low'].iloc[-(DONCHIAN_PERIOD+1):-1].min()
                
                # ============ CHOPPINESS INDEX (21) ============
                chop_value = calculate_choppiness_index(df, period=CHOP_PERIOD)
                
                # ============ HISTORICAL VOLATILITY (21) ============
                hv_current, hv_5_ago, hv_rising = calculate_historical_volatility(df, period=HV_PERIOD)
                
                # Current market price (completed candle)
                current_price = df['close'].iloc[-1]
                
                # ============ CONDITIONS CHECK ============
                # Buy condition: Close >= Donchian High & CHOP < 35 & HV Rising
                buy_condition = (
                    current_price >= donchian_high and
                    chop_value < 35 and
                    hv_rising
                )
                
                # Sell condition: Close <= Donchian Low & CHOP < 35 & HV Rising
                sell_condition = (
                    current_price <= donchian_low and
                    chop_value < 35 and
                    hv_rising
                )
                
                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None
                
                # Debug print with all indicators
                print(f"\n{'='*60}")
                print(f"🔍 {symbol} - 15m Analysis")
                print(f"{'='*60}")
                print(f"📊 Price: ${current_price:.2f}")
                print(f"📈 Donchian High: ${donchian_high:.2f}")
                print(f"📉 Donchian Low: ${donchian_low:.2f}")
                print(f"📍 Price vs High: {((current_price/donchian_high - 1)*100):.2f}%")
                print(f"📍 Price vs Low: {((current_price/donchian_low - 1)*100):.2f}%")
                print(f"\n📊 Indicators:")
                print(f"  • CHOP{CHOP_PERIOD}: {chop_value}")
                print(f"  • HV{HV_PERIOD} Current: {hv_current:.4f}")
                print(f"  • HV{HV_PERIOD} 5 candles ago: {hv_5_ago:.4f}")
                print(f"  • HV Rising: {hv_rising}")
                print(f"  • Market Regime: {'TRENDING' if chop_value < 35 else 'RANGING'}")
                
                # Skip if indicators couldn't be calculated
                if chop_value == 50 or hv_current == 0 or hv_5_ago == 0:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue
                
                # ==============================================
                # CONDITION 1: BUY SIGNAL
                # Close >= Donchian High & CHOP < 35 & HV Rising
                # ==============================================
                if buy_condition:
                    if last_alert[symbol] != "BUY":
                        message = (
                            f"🟢🟢🟢 BUY SIGNAL 🟢🟢🟢\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"Donchian High: ${donchian_high:.2f}\n"
                            f"CHOP{CHOP_PERIOD}: {chop_value} (<35 - Trending Market)\n"
                            f"HV{HV_PERIOD}: {hv_current:.4f}\n"
                            f"HV{HV_PERIOD}[5]: {hv_5_ago:.4f}\n"
                            f"Volatility Expanding: YES\n\n"
                            f"📊 Market Condition: STRONG TRENDING & VOLATILITY EXPANDING\n"
                            f"✅ Price broke above Donchian High\n"
                            f"🎯 BUY SIGNAL: Trend continuation expected\n\n"
                            f"⚠️ IMPORTANT: Use proper risk management\n"
                            f"Consider stop loss below recent swing low"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🟢 BUY TRIGGERED")
                        last_alert[symbol] = "BUY"
                
                # ==============================================
                # CONDITION 2: SELL SIGNAL
                # Close <= Donchian Low & CHOP < 35 & HV Rising
                # ==============================================
                elif sell_condition:
                    if last_alert[symbol] != "SELL":
                        message = (
                            f"🔴🔴🔴 SELL SIGNAL 🔴🔴🔴\n\n"
                            f"Exchange: {EXCHANGE.name.capitalize()}\n"
                            f"Symbol: {symbol}\n"
                            f"Current Price: ${current_price:.2f}\n"
                            f"Donchian Low: ${donchian_low:.2f}\n"
                            f"CHOP{CHOP_PERIOD}: {chop_value} (<35 - Trending Market)\n"
                            f"HV{HV_PERIOD}: {hv_current:.4f}\n"
                            f"HV{HV_PERIOD}[5]: {hv_5_ago:.4f}\n"
                            f"Volatility Expanding: YES\n\n"
                            f"📊 Market Condition: STRONG TRENDING & VOLATILITY EXPANDING\n"
                            f"✅ Price broke below Donchian Low\n"
                            f"🎯 SELL SIGNAL: Trend continuation expected\n\n"
                            f"⚠️ IMPORTANT: Use proper risk management\n"
                            f"Consider stop loss above recent swing high"
                        )
                        send_alert(message)
                        print(f"  ✅ {symbol} - 🔴 SELL TRIGGERED")
                        last_alert[symbol] = "SELL"
                
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