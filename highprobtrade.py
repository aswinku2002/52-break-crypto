'''import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime

# 1. Setup Flask for Render.com
app = Flask(__name__)

@app.route('/')
def home():
    return "CHOP + SuperTrend LIVE Signal Generator is running!"

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Exchange configuration
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'binance').lower()

# Exchange API keys
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Trading pairs to monitor
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

def init_exchange(exchange_name, config):
    """Initialize exchange with error handling"""
    try:
        if exchange_name == 'binance':
            exchange = ccxt.binance(config)
        else:
            return None

        exchange.load_markets()
        print(f"✅ {exchange_name.capitalize()} markets loaded successfully")
        return exchange
    except Exception as e:
        print(f"❌ Error loading {exchange_name.capitalize()} markets: {e}")
        return None

# Initialize Binance with rate limit protection
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,  # CRITICAL: Protects from rate limit bans
    'options': {
        'defaultType': 'spot',
    }
}
EXCHANGE = init_exchange('binance', binance_config)

if not EXCHANGE:
    print("❌ No exchanges available. Please check your configuration.")
    exit(1)

print(f"✅ Using {EXCHANGE.name.capitalize()} as primary exchange")

# Prevent repeated alerts
last_alert = {}
last_price = {}
signal_cooldown = {}  # Tracks when cooldown expires
alert_count = {}  # Track how many alerts per symbol

# Store historical data for each symbol
historical_data = {}
last_ohlcv_update = {}

def calculate_choppiness_index_live(df, period=21):
    """
    Calculate Choppiness Index using live data
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
            return None
        return round(result, 2)
    except Exception as e:
        print(f"Choppiness calculation error: {e}")
        return None

def calculate_supertrend_live(df, current_price, period=10, multiplier=3):
    """
    Calculate SuperTrend using live price data for REAL-TIME detection
    
    This uses the latest candle data + current live price
    to determine if SuperTrend has crossed in real-time
    """
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate ATR
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        # Calculate upper and lower bands
        upper_band = (high + low) / 2 + multiplier * atr
        lower_band = (high + low) / 2 - multiplier * atr
        
        # Initialize SuperTrend
        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)  # 1 for uptrend, -1 for downtrend
        
        for i in range(period, len(df)):
            if i == period:
                # First value
                if close.iloc[i] > upper_band.iloc[i]:
                    supertrend.iloc[i] = lower_band.iloc[i]
                    direction.iloc[i] = 1  # Uptrend
                else:
                    supertrend.iloc[i] = upper_band.iloc[i]
                    direction.iloc[i] = -1  # Downtrend
            else:
                # Check for trend reversal
                if direction.iloc[i-1] == 1:  # Currently in uptrend
                    if close.iloc[i] < lower_band.iloc[i]:
                        # Reversal to downtrend
                        supertrend.iloc[i] = upper_band.iloc[i]
                        direction.iloc[i] = -1
                    else:
                        # Continue uptrend
                        supertrend.iloc[i] = max(upper_band.iloc[i], supertrend.iloc[i-1])
                        direction.iloc[i] = 1
                else:  # Currently in downtrend
                    if close.iloc[i] > upper_band.iloc[i]:
                        # Reversal to uptrend
                        supertrend.iloc[i] = lower_band.iloc[i]
                        direction.iloc[i] = 1
                    else:
                        # Continue downtrend
                        supertrend.iloc[i] = min(lower_band.iloc[i], supertrend.iloc[i-1])
                        direction.iloc[i] = -1
        
        # Get current direction from last completed candle
        current_direction = direction.iloc[-1] if len(direction) > 0 else None
        current_st_value = supertrend.iloc[-1] if len(supertrend) > 0 else None
        
        # NOW CHECK WITH LIVE PRICE FOR REAL-TIME CROSSOVER DETECTION
        live_direction = current_direction
        live_st_value = current_st_value
        
        # Check if live price has crossed the SuperTrend line
        if current_st_value is not None:
            # Determine if price is above or below SuperTrend
            price_above_st = current_price > current_st_value
            
            # If price is above ST and direction was DOWN, potential reversal to UP
            if price_above_st and current_direction == -1:
                live_direction = 1  # Live direction would be UP
                # Update live ST value to lower band
                live_st_value = lower_band.iloc[-1] if len(lower_band) > 0 else current_st_value
            
            # If price is below ST and direction was UP, potential reversal to DOWN
            elif not price_above_st and current_direction == 1:
                live_direction = -1  # Live direction would be DOWN
                # Update live ST value to upper band
                live_st_value = upper_band.iloc[-1] if len(upper_band) > 0 else current_st_value
        
        return {
            'current_direction': current_direction,  # From candles
            'live_direction': live_direction,  # With live price applied
            'current_value': current_st_value,  # From candles
            'live_value': live_st_value,  # With live price applied
            'upper_band': upper_band.iloc[-1] if len(upper_band) > 0 else None,
            'lower_band': lower_band.iloc[-1] if len(lower_band) > 0 else None,
            'price_above_st': current_price > current_st_value if current_st_value is not None else None
        }
    except Exception as e:
        print(f"SuperTrend live calculation error: {e}")
        return None

def send_alert(message):
    """Send alert via Telegram"""
    if TOKEN and CHAT_ID:
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
                print("✅ Alert sent successfully")
            else:
                print(f"❌ Failed to send alert: {response.text}")
        except Exception as e:
            print(f"Telegram error: {e}")

def get_available_symbols(exchange, symbols):
    """Filter symbols to only those available on the exchange"""
    available = []
    for symbol in symbols:
        if symbol in exchange.markets:
            available.append(symbol)
    return available

def format_price(price):
    """Format price based on value"""
    if price < 1:
        return f"${price:.6f}"
    elif price < 100:
        return f"${price:.4f}"
    elif price < 1000:
        return f"${price:.2f}"
    else:
        return f"${price:.2f}"

def update_ohlcv_data(symbol):
    """Update OHLCV data efficiently - only fetch new candles"""
    try:
        # Fetch only the latest 2 candles to minimize weight
        latest = EXCHANGE.fetch_ohlcv(symbol, timeframe='5m', limit=2)
        
        if len(latest) >= 2:
            new_df = pd.DataFrame(
                latest,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            
            # Update historical data
            if symbol in historical_data:
                # Add new candle, keep last 60
                combined = pd.concat([historical_data[symbol], new_df])
                combined = combined.drop_duplicates(subset=['timestamp'], keep='last')
                historical_data[symbol] = combined.tail(60)
            else:
                historical_data[symbol] = new_df
            
            return True
        return False
    except Exception as e:
        print(f"Error updating OHLCV for {symbol}: {e}")
        return False

def check_live_signals():
    """Main function to check signals in real-time with 2-minute cooldown"""
    print("\n" + "="*60)
    print("🔴🟢 LIVE SIGNAL MONITORING - 2 MINUTE COOLDOWN")
    print("="*60)
    print("📊 Monitoring for REAL-TIME SuperTrend Crossovers")
    print("⏱️ Check Frequency: Every 30 seconds")
    print("🛡️ Rate Limit Protection: ENABLED")
    print("⏰ Cooldown: 2 minutes between alerts per symbol")
    print("="*60 + "\n")
    
    # Get available symbols
    available_symbols = get_available_symbols(EXCHANGE, SYMBOLS)
    print(f"✅ Monitoring {len(available_symbols)} symbols\n")
    
    # Initialize historical data for all symbols
    print("📥 Loading initial data...")
    for symbol in available_symbols:
        try:
            ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe='5m', limit=60)
            if len(ohlcv) >= 50:
                df = pd.DataFrame(
                    ohlcv,
                    columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
                )
                historical_data[symbol] = df
                print(f"✅ Loaded data for {symbol}")
            else:
                print(f"⚠️ Insufficient data for {symbol}")
        except Exception as e:
            print(f"❌ Error loading {symbol}: {e}")
        # Small delay between initial loads to prevent rate limit issues
        time.sleep(0.1)
    
    print("\n" + "="*60)
    print("🚀 LIVE MONITORING STARTED! (2-minute cooldown)")
    print("="*60 + "\n")
    
    # Send startup alert
    send_alert(f"🚀 <b>LIVE Signal Generator Started!</b>\n\n"
               f"📊 Strategy: CHOP + SuperTrend (LIVE)\n"
               f"🔍 Monitoring: {len(available_symbols)} pairs\n"
               f"⏱️ Check Frequency: Every 30 seconds\n"
               f"⚡ Real-time crossover detection: <b>ACTIVE</b>\n"
               f"⏰ Cooldown: <b>2 minutes</b> between alerts per symbol\n\n"
               f"📈 <b>SELL:</b> CHOP {'<'} 50 + ST crosses UP→DOWN\n"
               f"📉 <b>BUY:</b> CHOP {'<'} 50 + ST crosses DOWN→UP\n"
               f"🛡️ Rate Limit Protection: ENABLED")
    
    check_count = 0
    
    while True:
        try:
            check_count += 1
            start_time = time.time()
            
            print(f"\n{'='*60}")
            print(f"🔄 CHECK #{check_count} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}")
            
            # Check each symbol with small delays between them
            for idx, symbol in enumerate(available_symbols):
                try:
                    # Get current live price (weight: 1)
                    ticker = EXCHANGE.fetch_ticker(symbol)
                    current_price = ticker['last']
                    
                    # Update OHLCV only every 3rd check to reduce load
                    if check_count % 3 == 0 or symbol not in historical_data:
                        update_ohlcv_data(symbol)
                    
                    df = historical_data.get(symbol)
                    if df is None or len(df) < 50:
                        print(f"⚠️ Insufficient data for {symbol}")
                        continue
                    
                    # Calculate indicators
                    chop_value = calculate_choppiness_index_live(df, period=21)
                    if chop_value is None:
                        continue
                    
                    supertrend_data = calculate_supertrend_live(df, current_price, period=10, multiplier=3)
                    if supertrend_data is None:
                        continue
                    
                    # Extract values
                    live_direction = supertrend_data['live_direction']
                    current_direction = supertrend_data['current_direction']
                    live_st_value = supertrend_data['live_value']
                    current_st_value = supertrend_data['current_value']
                    price_above_st = supertrend_data['price_above_st']
                    
                    # Get previous direction for crossover detection
                    prev_direction = None
                    if len(df) >= 3:
                        prev_close = df['close'].iloc[-2]
                        # Calculate previous SuperTrend (without live price)
                        prev_supertrend = calculate_supertrend_live(df.iloc[:-1], prev_close, period=10, multiplier=3)
                        if prev_supertrend:
                            prev_direction = prev_supertrend['live_direction']
                    
                    # Format price
                    price_str = format_price(current_price)
                    
                    # Direction text
                    direction_text = "🟢 UP" if live_direction == 1 else "🔴 DOWN"
                    
                    # Only show significant price changes
                    if symbol not in last_price or abs(current_price - last_price.get(symbol, 0)) > 0.0001:
                        print(f"{symbol:12} | Price: {price_str:12} | CHOP: {chop_value:6.2f} | ST: {direction_text:8} | ST Value: {live_st_value:.4f} | {'ABOVE' if price_above_st else 'BELOW'}")
                        last_price[symbol] = current_price
                    
                    # ==============================================
                    # REAL-TIME SIGNAL DETECTION WITH 2-MINUTE COOLDOWN
                    # ==============================================
                    
                    sell_signal = False
                    buy_signal = False
                    
                    # SELL: CHOP < 50 AND ST crosses UP → DOWN
                    if (chop_value < 50 and 
                        prev_direction == 1 and 
                        live_direction == -1):
                        sell_signal = True
                    
                    # BUY: CHOP < 50 AND ST crosses DOWN → UP
                    elif (chop_value < 50 and 
                          prev_direction == -1 and 
                          live_direction == 1):
                        buy_signal = True
                    
                    # ==============================================
                    # CHECK COOLDOWN (2 MINUTES = 120 SECONDS)
                    # ==============================================
                    
                    current_time = time.time()
                    
                    # Initialize cooldown for this symbol
                    if symbol not in signal_cooldown:
                        signal_cooldown[symbol] = 0
                    
                    # Check if in cooldown
                    time_since_last = current_time - signal_cooldown[symbol]
                    cooldown_seconds = 120  # 2 minutes
                    
                    if (sell_signal or buy_signal) and time_since_last < cooldown_seconds:
                        remaining = int(cooldown_seconds - time_since_last)
                        print(f"  ⏳ {symbol} - Signal detected but in cooldown ({remaining}s remaining)")
                        # Still update state but don't send alert
                        if sell_signal:
                            last_alert[symbol] = "SELL"
                        elif buy_signal:
                            last_alert[symbol] = "BUY"
                        continue
                    
                    # ==============================================
                    # SEND ALERTS (with cooldown)
                    # ==============================================
                    
                    # SELL Signal
                    if sell_signal:
                        # Track alert count
                        if symbol not in alert_count:
                            alert_count[symbol] = 0
                        alert_count[symbol] += 1
                        
                        message = (
                            f"🔴🔴🔴 <b>SELL SIGNAL #{alert_count[symbol]}</b> 🔴🔴🔴\n\n"
                            f"<b>Symbol:</b> {symbol}\n"
                            f"<b>Price:</b> {price_str}\n"
                            f"<b>CHOP21:</b> {chop_value:.2f} (< 50 - Trending)\n"
                            f"<b>SuperTrend:</b> {direction_text}\n"
                            f"<b>ST Value:</b> {live_st_value:.4f}\n"
                            f"<b>Signal Type:</b> <b>LIVE REAL-TIME</b> ⚡\n"
                            f"<b>Cross:</b> UPTREND → DOWNTREND\n"
                            f"<b>Alert #:</b> {alert_count[symbol]} for this symbol\n"
                            f"<b>Cooldown:</b> 2 minutes\n\n"
                            f"📊 Timeframe: Live (5m candles for calculation)\n"
                            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"⚡ <b>IMMEDIATE ACTION REQUIRED!</b>"
                        )
                        send_alert(message)
                        print(f"🎯🎯🎯 {symbol} - 🔴 SELL SIGNAL #{alert_count[symbol]} SENT! (Cooldown: 2min)")
                        last_alert[symbol] = "SELL"
                        signal_cooldown[symbol] = current_time  # Reset cooldown
                    
                    # BUY Signal
                    elif buy_signal:
                        # Track alert count
                        if symbol not in alert_count:
                            alert_count[symbol] = 0
                        alert_count[symbol] += 1
                        
                        message = (
                            f"🟢🟢🟢 <b>BUY SIGNAL #{alert_count[symbol]}</b> 🟢🟢🟢\n\n"
                            f"<b>Symbol:</b> {symbol}\n"
                            f"<b>Price:</b> {price_str}\n"
                            f"<b>CHOP21:</b> {chop_value:.2f} (< 50 - Trending)\n"
                            f"<b>SuperTrend:</b> {direction_text}\n"
                            f"<b>ST Value:</b> {live_st_value:.4f}\n"
                            f"<b>Signal Type:</b> <b>LIVE REAL-TIME</b> ⚡\n"
                            f"<b>Cross:</b> DOWNTREND → UPTREND\n"
                            f"<b>Alert #:</b> {alert_count[symbol]} for this symbol\n"
                            f"<b>Cooldown:</b> 2 minutes\n\n"
                            f"📊 Timeframe: Live (5m candles for calculation)\n"
                            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"⚡ <b>IMMEDIATE ACTION REQUIRED!</b>"
                        )
                        send_alert(message)
                        print(f"🎯🎯🎯 {symbol} - 🟢 BUY SIGNAL #{alert_count[symbol]} SENT! (Cooldown: 2min)")
                        last_alert[symbol] = "BUY"
                        signal_cooldown[symbol] = current_time  # Reset cooldown
                    
                    # Reset alert if no signal
                    elif not sell_signal and not buy_signal and last_alert.get(symbol) is not None:
                        # Only reset if direction changed back
                        if prev_direction is not None:
                            print(f"  → {symbol} - Alert reset (no signal, CHOP: {chop_value:.2f})")
                            last_alert[symbol] = None
                    
                    # Small delay between symbols to spread out requests
                    if idx < len(available_symbols) - 1:
                        time.sleep(0.15)
                
                except ccxt.RateLimitExceeded as e:
                    print(f"⚠️ Rate limit exceeded for {symbol}: {e}")
                    print("⏳ Waiting 60 seconds before continuing...")
                    time.sleep(60)
                except Exception as e:
                    print(f"❌ Error checking {symbol}: {e}")
            
            # Calculate time taken and wait for next cycle
            elapsed = time.time() - start_time
            sleep_time = max(0, 30 - elapsed)
            
            # Show alert statistics
            total_alerts = sum(alert_count.values()) if alert_count else 0
            active_signals = sum(1 for v in last_alert.values() if v is not None)
            
            print(f"\n⏱️ Check #{check_count} completed in {elapsed:.1f}s | Next check in {sleep_time:.1f}s")
            print(f"📊 Active: {len(available_symbols)} symbols | Signals active: {active_signals} | Total alerts sent: {total_alerts}")
            
            # Show cooldown status for active symbols
            if active_signals > 0:
                print("\n⏰ Cooldown Status:")
                for sym in last_alert:
                    if last_alert[sym] is not None:
                        time_remaining = max(0, 120 - (time.time() - signal_cooldown.get(sym, 0)))
                        if time_remaining > 0:
                            print(f"  • {sym}: {int(time_remaining)}s remaining")
            
            # Show top symbols by alert count (every 5 checks)
            if alert_count and check_count % 5 == 0:
                print("\n📊 Alert Count Summary (Top 5):")
                sorted_alerts = sorted(alert_count.items(), key=lambda x: x[1], reverse=True)[:5]
                for sym, count in sorted_alerts:
                    print(f"  • {sym}: {count} alerts")
            
            time.sleep(sleep_time)
            
        except ccxt.RateLimitExceeded as e:
            print(f"⚠️ Rate limit exceeded: {e}")
            print("⏳ Waiting 60 seconds before continuing...")
            time.sleep(60)
        except Exception as e:
            print(f"❌ Main loop error: {e}")
            time.sleep(30)

# 3. Start bot in background
threading.Thread(target=check_live_signals, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port) '''








import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime

# 1. Setup Flask for Render
app = Flask(__name__)

@app.route('/')
def home():
    return "SEB + CHOP Signal Generator is running!"

# 2. Configuration
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Exchange configuration
PRIMARY_EXCHANGE = os.environ.get('PRIMARY_EXCHANGE', 'binance').lower()

# Exchange API keys
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Trading pairs to monitor
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

def init_exchange(exchange_name, config):
    """Initialize exchange with error handling"""
    try:
        if exchange_name == 'binance':
            exchange = ccxt.binance(config)
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
    'options': {
        'defaultType': 'spot',
    }
}
EXCHANGE = init_exchange('binance', binance_config)

if not EXCHANGE:
    print("❌ No exchanges available. Please check your configuration.")
    exit(1)

print(f"✅ Using {EXCHANGE.name.capitalize()} as primary exchange")

# Prevent repeated alerts
last_alert = {}

def calculate_choppiness_index(df, period=21):
    """
    Calculate Choppiness Index (Period 21)
    
    The Choppiness Index measures whether the market is trending (low values)
    or ranging/choppy (high values).
    
    Formula: CI = 100 * log10(SUM(TR, n) / (MAX(HIGH, n) - MIN(LOW, n))) / log10(n)
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
            return None
        return round(result, 2)
    except Exception as e:
        print(f"Choppiness calculation error: {e}")
        return None

def calculate_standard_error_bands(df, period=52, std_err=2, avg_method='simple', avg_periods=3):
    """
    Calculate Standard Error Bands (Period 52, Standard Error = 2, Average Method = Simple, Average Periods = 3)
    
    Standard Error Bands are similar to Bollinger Bands but use standard error instead of standard deviation.
    
    Args:
        df: DataFrame with 'close' column
        period: Lookback period for regression (52)
        std_err: Number of standard errors to use (2)
        avg_method: Method for averaging (simple)
        avg_periods: Period for moving average of bands (3)
    """
    try:
        close = df['close']
        
        # Ensure we have enough data
        if len(close) < period + 10:
            return None, None, None
        
        # Calculate linear regression for each point
        def linear_regression(y, window=period):
            x = np.arange(window)
            x_mean = x.mean()
            y_mean = y.mean()
            
            # Calculate slope and intercept
            slope = np.sum((x - x_mean) * (y - y_mean)) / np.sum((x - x_mean) ** 2)
            intercept = y_mean - slope * x_mean
            
            # Calculate predicted values
            predicted = intercept + slope * x
            
            # Calculate standard error of the estimate
            residuals = y - predicted
            std_error = np.sqrt(np.sum(residuals ** 2) / (window - 2))
            
            return predicted[-1], std_error
        
        # Calculate rolling regression
        middle_band = []
        upper_band = []
        lower_band = []
        
        for i in range(period - 1, len(close)):
            y = close.iloc[i - period + 1:i + 1].values
            if len(y) == period:
                predicted, std_error = linear_regression(y, period)
                middle_band.append(predicted)
                upper_band.append(predicted + std_err * std_error)
                lower_band.append(predicted - std_err * std_error)
            else:
                middle_band.append(None)
                upper_band.append(None)
                lower_band.append(None)
        
        # Convert to Series with proper indexing
        middle_series = pd.Series(middle_band, index=close.index[period-1:])
        upper_series = pd.Series(upper_band, index=close.index[period-1:])
        lower_series = pd.Series(lower_band, index=close.index[period-1:])
        
        # Apply simple moving average if avg_periods > 1
        if avg_periods > 1 and avg_method.lower() == 'simple':
            middle_series = middle_series.rolling(window=avg_periods).mean()
            upper_series = upper_series.rolling(window=avg_periods).mean()
            lower_series = lower_series.rolling(window=avg_periods).mean()
        
        # Get the latest values
        current_middle = middle_series.iloc[-1]
        current_upper = upper_series.iloc[-1]
        current_lower = lower_series.iloc[-1]
        
        # Previous values for cross detection
        prev_middle = middle_series.iloc[-2] if len(middle_series) >= 2 else None
        prev_upper = upper_series.iloc[-2] if len(upper_series) >= 2 else None
        prev_lower = lower_series.iloc[-2] if len(lower_series) >= 2 else None
        
        # Get previous close price
        prev_close = close.iloc[-2] if len(close) >= 2 else None
        
        return {
            'current': {
                'upper': round(current_upper, 4) if current_upper is not None else None,
                'middle': round(current_middle, 4) if current_middle is not None else None,
                'lower': round(current_lower, 4) if current_lower is not None else None
            },
            'previous': {
                'upper': round(prev_upper, 4) if prev_upper is not None else None,
                'middle': round(prev_middle, 4) if prev_middle is not None else None,
                'lower': round(prev_lower, 4) if prev_lower is not None else None
            },
            'prev_close': prev_close
        }
    except Exception as e:
        print(f"Standard Error Bands calculation error: {e}")
        return None

def send_alert(message):
    """Send alert via Telegram"""
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
    return available

def check_cross_detection(current_price, prev_price, band_current, band_prev, direction='above_to_below'):
    """
    Check if price crossed a band
    
    Args:
        current_price: Current live price
        prev_price: Previous price
        band_current: Current band value
        band_prev: Previous band value
        direction: 'above_to_below' or 'below_to_above' or 'outside_to_inside'
    """
    if None in [current_price, prev_price, band_current, band_prev]:
        return False
    
    if direction == 'above_to_below':
        # Price was above band and is now below
        return prev_price > band_prev and current_price < band_current
    
    elif direction == 'below_to_above':
        # Price was below band and is now above
        return prev_price < band_prev and current_price > band_current
    
    elif direction == 'outside_to_inside_upper':
        # Price was outside (above) upper band and is now inside (below upper band)
        return prev_price > band_prev and current_price <= band_current
    
    elif direction == 'outside_to_inside_lower':
        # Price was outside (below) lower band and is now inside (above lower band)
        return prev_price < band_prev and current_price >= band_current
    
    return False

def run_bot():
    print("Bot loop started...")
    print(f"Exchange: {EXCHANGE.name.capitalize()}")
    print("\n" + "="*50)
    print("STANDARD ERROR BANDS + CHOPPINESS INDEX STRATEGY")
    print("="*50)
    print("\n📊 INDICATORS:")
    print("  • Choppiness Index (Period 21)")
    print("  • Standard Error Bands (Period 52, StdErr=2, Avg Method=SMA, Avg Periods=3)")
    print("\n📈 TRADING SIGNALS:")
    print("  🔴 SELL:")
    print("    Condition A: CHOP 40-60 + Price crosses UPPER band from outside to inside")
    print("    Condition B: CHOP 40-50 + Price crosses MIDDLE band from above to below")
    print("  🟢 BUY:")
    print("    Condition A: CHOP 40-60 + Price crosses LOWER band from outside to inside")
    print("    Condition B: CHOP 40-50 + Price crosses MIDDLE band from below to above")
    print("\n⏱️ CHECKING EVERY 2 MINUTES")
    print("="*50 + "\n")
    
    # Startup message
    send_alert(f"✅ SEB + CHOP Signal Generator Started\n\n"
               f"📊 Strategy: Standard Error Bands + Choppiness Index\n"
               f"🔍 Monitoring: {len(SYMBOLS)} trading pairs\n"
               f"⏱️ Check Frequency: Every 2 minutes\n\n"
               f"📈 Signals Generated on Price Crossings")
    
    # Get available symbols
    available_symbols = get_available_symbols(EXCHANGE, SYMBOLS)
    print(f"✅ Available symbols: {len(available_symbols)}")
    
    while True:
        for symbol in available_symbols:
            try:
                # Get current and previous live price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # Get OHLCV data (need enough for SEB calculation)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='5m',  # Using 5-minute candles
                    limit=100  # Enough for 52-period SEB
                )
                
                if len(ohlcv) < 80:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # Calculate indicators
                chop_value = calculate_choppiness_index(df, period=21)
                seb_values = calculate_standard_error_bands(
                    df, 
                    period=52, 
                    std_err=2, 
                    avg_method='simple', 
                    avg_periods=3
                )
                
                # Skip if indicators couldn't be calculated
                if chop_value is None or seb_values is None:
                    print(f"  → Skipping {symbol} - indicators not ready")
                    continue
                
                # Extract SEB values
                seb_current = seb_values['current']
                seb_previous = seb_values['previous']
                prev_close = seb_values['prev_close']
                
                if None in [seb_current['upper'], seb_current['middle'], seb_current['lower']]:
                    print(f"  → Skipping {symbol} - SEB values incomplete")
                    continue
                
                # Get previous close price (from OHLCV)
                prev_price = df['close'].iloc[-2] if len(df) >= 2 else None
                
                if prev_price is None:
                    print(f"  → Skipping {symbol} - no previous price")
                    continue
                
                # Format current price
                price_str = f"${current_price:.4f}" if current_price < 1000 else f"${current_price:.2f}"
                price_str = f"${current_price:.4f}" if current_price < 100 else price_str
                
                # Debug output
                print(f"{symbol} - Price: {price_str}, CHOP21: {chop_value}, "
                      f"SEB_U: {seb_current['upper']:.4f}, SEB_M: {seb_current['middle']:.4f}, SEB_L: {seb_current['lower']:.4f}")
                
                # Initialize alert tracking
                if symbol not in last_alert:
                    last_alert[symbol] = None
                
                # Check for price crossings
                
                # ==============================================
                # SELL SIGNAL - CONDITION A
                # CHOP > 40 and < 60
                # Price crosses UPPER band from outside to inside
                # ==============================================
                if 40 < chop_value < 60:
                    upper_cross = check_cross_detection(
                        current_price, prev_price,
                        seb_current['upper'], seb_previous['upper'],
                        'outside_to_inside_upper'
                    )
                    
                    if upper_cross and last_alert[symbol] != "SELL_A":
                        message = (
                            f"🔴🔴🔴 SELL SIGNAL 🔴🔴🔴\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: {price_str}\n"
                            f"CHOP21: {chop_value:.2f} (40-60 Range)\n"
                            f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                            f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                            f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                            f"Reason: Condition A\n"
                            f"Price crossed UPPER band from OUTSIDE to INSIDE\n"
                            f"Market is ranging (CHOP 40-60)"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 SELL SIGNAL (Condition A)")
                        last_alert[symbol] = "SELL_A"
                
                # ==============================================
                # SELL SIGNAL - CONDITION B
                # CHOP > 40 and < 50
                # Price crosses MIDDLE band from above to below
                # ==============================================
                elif 40 < chop_value < 50:
                    middle_cross_above_to_below = check_cross_detection(
                        current_price, prev_price,
                        seb_current['middle'], seb_previous['middle'],
                        'above_to_below'
                    )
                    
                    if middle_cross_above_to_below and last_alert[symbol] != "SELL_B":
                        message = (
                            f"🔴🔴🔴 SELL SIGNAL 🔴🔴🔴\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: {price_str}\n"
                            f"CHOP21: {chop_value:.2f} (40-50 Range)\n"
                            f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                            f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                            f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                            f"Reason: Condition B\n"
                            f"Price crossed MIDDLE band from ABOVE to BELOW\n"
                            f"Market is moderately ranging (CHOP 40-50)"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 SELL SIGNAL (Condition B)")
                        last_alert[symbol] = "SELL_B"
                
                # ==============================================
                # BUY SIGNAL - CONDITION A
                # CHOP > 40 and < 60
                # Price crosses LOWER band from outside to inside
                # ==============================================
                if 40 < chop_value < 60:
                    lower_cross = check_cross_detection(
                        current_price, prev_price,
                        seb_current['lower'], seb_previous['lower'],
                        'outside_to_inside_lower'
                    )
                    
                    if lower_cross and last_alert[symbol] != "BUY_A":
                        message = (
                            f"🟢🟢🟢 BUY SIGNAL 🟢🟢🟢\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: {price_str}\n"
                            f"CHOP21: {chop_value:.2f} (40-60 Range)\n"
                            f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                            f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                            f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                            f"Reason: Condition A\n"
                            f"Price crossed LOWER band from OUTSIDE to INSIDE\n"
                            f"Market is ranging (CHOP 40-60)"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BUY SIGNAL (Condition A)")
                        last_alert[symbol] = "BUY_A"
                
                # ==============================================
                # BUY SIGNAL - CONDITION B
                # CHOP > 40 and < 50
                # Price crosses MIDDLE band from below to above
                # ==============================================
                elif 40 < chop_value < 50:
                    middle_cross_below_to_above = check_cross_detection(
                        current_price, prev_price,
                        seb_current['middle'], seb_previous['middle'],
                        'below_to_above'
                    )
                    
                    if middle_cross_below_to_above and last_alert[symbol] != "BUY_B":
                        message = (
                            f"🟢🟢🟢 BUY SIGNAL 🟢🟢🟢\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: {price_str}\n"
                            f"CHOP21: {chop_value:.2f} (40-50 Range)\n"
                            f"SEB52 Upper: {seb_current['upper']:.4f}\n"
                            f"SEB52 Middle: {seb_current['middle']:.4f}\n"
                            f"SEB52 Lower: {seb_current['lower']:.4f}\n\n"
                            f"Reason: Condition B\n"
                            f"Price crossed MIDDLE band from BELOW to ABOVE\n"
                            f"Market is moderately ranging (CHOP 40-50)"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BUY SIGNAL (Condition B)")
                        last_alert[symbol] = "BUY_B"
                
                # Reset alerts if no conditions met
                else:
                    if last_alert[symbol] is not None:
                        # Only reset if chop is outside ranges
                        if not (40 < chop_value < 60) and not (40 < chop_value < 50):
                            print(f"{symbol} - Alert reset: {last_alert[symbol]} condition ended (CHOP: {chop_value:.2f})")
                            last_alert[symbol] = None
                
            except Exception as e:
                print(f"Error checking {symbol}: {e}")
        
        # Check every 30 seconds
        time.sleep(30)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

