import os
import time
import ccxt
import pandas as pd
import numpy as np
import requests
import threading
from flask import Flask
from datetime import datetime
import logging

# ============================================
# 1. Setup Flask for Render
# ============================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Delta Exchange Futures Scalping Bot is running!"

# ============================================
# 2. Configuration
# ============================================
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Delta Exchange API keys (optional for higher rate limits)
DELTA_API_KEY = os.environ.get('DELTA_API_KEY', '')
DELTA_API_SECRET = os.environ.get('DELTA_API_SECRET', '')

# ============================================
# DELTA EXCHANGE INDIA FUTURES SYMBOLS
# Based on your screenshots
# ============================================
SYMBOLS = [
    # Major Cryptocurrencies (High Volume)
    'BTCUSD', 'ETHUSD', 'SOLUSD', 'XRPUSD',
    'DOGEUSD', 'BNBUSD', 'LTCUSD', 'LINKUSD',
    'AVAXUSD', 'ADAUSD', 'SUIUSD', 'TRXUSD',
    'BCHUSD', 'AAVEUSD', 'ETCUSD', 'NEARUSD',
    'ORDIUSD', 'WLDUSD', 'HYPEUSD', 'XLMUSD',
    
    # Metal Tokens
    'XAUTUSD', 'PAXGUSD',
    
    # Additional Altcoins from screenshots
    'UNIUSD', 'ZECUSD', 'ENJUSD', 'XMRUSD',
    'AXSUSD', 'JTOUSD', 'IOUSD', 'ALTUSD',
    
    # New/Recent Tokens
    'ACTUSD', 'EVAUSD', 'SLVONUSD', 'EDENUSD',
    'SKYAIUSD', 'EIGENUSD', 'SIRENUSD', 'VVVUSD',
    'WCTUSD', 'SPCXXUSD', 'AIOUSD', 'SWARMSUSD',
    'ALLOUSD', 'RIVERUSD', 'PIPPINUSD', 'BILLUSD',
    'MUSD', 'XPLUSD', 'COAIUSD', 'QQQXUSD',
    'RAVEUSD', 'BASEDUSD', 'BLESSUSD', 'VELVETUSD',
    'LABUSD', 'BEATUSD', 'HUSD'
]

# Map to Delta Exchange format (add /USDT for trading pairs)
DELTA_SYMBOLS = [f"{symbol}/USDT" for symbol in SYMBOLS]

# Initialize Delta Exchange
delta_config = {
    'apiKey': DELTA_API_KEY,
    'secret': DELTA_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # perpetual futures
    }
}

EXCHANGE = ccxt.delta(delta_config)

try:
    EXCHANGE.load_markets()
    print("✅ Delta Exchange markets loaded successfully")
    print(f"📊 Loaded {len(EXCHANGE.markets)} trading pairs")
    print(f"🎯 Monitoring {len(SYMBOLS)} perpetual futures")
except Exception as e:
    print(f"❌ Error loading Delta Exchange markets: {e}")
    print("Make sure you have internet connection and Delta Exchange is accessible")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Prevent repeated alerts
last_alert = {}
alert_cooldown = {}
bot_started_message_sent = False

# ============================================
# 3. Core Indicator Functions
# ============================================

def calculate_choppiness_index(df, period=14):
    """Calculate Choppiness Index - measures trend vs range market"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']

        # Calculate True Range (TR)
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

        # Choppiness Index formula: 100 * log10(sum(TR) / (HH - LL)) / log10(period)
        choppiness = 100 * np.log10(sum_tr / price_range) / np.log10(period)

        result = choppiness.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)

    except Exception as e:
        logger.error(f"Choppiness calculation error: {e}")
        return 50

def calculate_rsi(df, period=14):
    """Calculate RSI (Relative Strength Index) - measures momentum"""
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
        logger.error(f"RSI calculation error: {e}")
        return 50

def calculate_channel_percentile(HH, LL, current_price):
    """Calculate where price sits in the Donchian Channel (0% = LL, 100% = HH)"""
    if HH == LL:
        return 50
    percentile = ((current_price - LL) / (HH - LL)) * 100
    return round(percentile, 2)

def get_open_interest(symbol):
    """
    Fetch current Open Interest from Delta Exchange.
    Returns: (current_oi, previous_oi, oi_change_percent)
    """
    try:
        # Get the Delta format symbol (e.g., BTC/USDT)
        delta_symbol = f"{symbol}/USDT"
        
        # Fetch Open Interest from Delta Exchange
        oi_data = EXCHANGE.public_get_public_oi({
            'symbol': delta_symbol
        })
        
        if 'result' in oi_data and 'oi' in oi_data['result']:
            current_oi = float(oi_data['result']['oi'])
            
            # Try to get OI history for change calculation
            try:
                oi_history = EXCHANGE.public_get_public_oi_history({
                    'symbol': delta_symbol,
                    'resolution': '15m',
                    'limit': 2
                })
                
                if 'result' in oi_history and len(oi_history['result']) >= 2:
                    prev_oi = float(oi_history['result'][0]['oi'])
                    current_oi = float(oi_history['result'][1]['oi'])
                else:
                    prev_oi = current_oi
            except:
                prev_oi = current_oi
            
            # Calculate OI change percentage
            if prev_oi > 0:
                oi_change = ((current_oi - prev_oi) / prev_oi) * 100
            else:
                oi_change = 0
                
            return current_oi, prev_oi, round(oi_change, 2)
        
        return None, None, 0
    
    except Exception as e:
        logger.error(f"Open Interest fetch error for {symbol}: {e}")
        return None, None, 0

# ============================================
# 4. Alert Sending Functions
# ============================================

def send_telegram_alert(message):
    """Send alert to Telegram"""
    if TOKEN and CHAT_ID:
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                params={
                    "chat_id": CHAT_ID,
                    "text": message
                },
                timeout=10
            )
            if response.status_code != 200:
                logger.error(f"Telegram error: {response.status_code} - {response.text}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    else:
        logger.error("❌ TELEGRAM_TOKEN or CHAT_ID not set!")
        return False

def send_signal_alert(symbol, signal_type, price, chop_value, rsi_value, 
                     channel_percentile, oi_current, oi_change, indicators):
    """Send formatted signal alert to Telegram"""
    
    alert_key = f"{symbol}_{signal_type}"
    current_time = time.time()
    
    # Cooldown: 5 minutes per symbol per signal type
    if alert_key in alert_cooldown:
        if current_time - alert_cooldown[alert_key] < 300:
            logger.info(f"⏳ Skipping duplicate {signal_type} alert for {symbol} (cooldown)")
            return
    
    # Format the message based on signal type
    if signal_type == "BUY_REVERSAL":
        emoji = "🟢"
        title = "BUY REVERSAL"
        market_condition = "RANGING/CHOPPY MARKET"
        strategy = "Mean-reversion expected"
        
    elif signal_type == "SELL_REVERSAL":
        emoji = "🔴"
        title = "SELL REVERSAL"
        market_condition = "RANGING/CHOPPY MARKET"
        strategy = "Mean-reversion expected"
        
    elif signal_type == "BUY_TREND":
        emoji = "🟢"
        title = "BUY TREND CONTINUATION"
        market_condition = "STRONG TRENDING MARKET"
        strategy = "Momentum continuation expected"
        
    elif signal_type == "SELL_TREND":
        emoji = "🔴"
        title = "SELL TREND CONTINUATION"
        market_condition = "STRONG TRENDING MARKET"
        strategy = "Momentum continuation expected"
        
    else:
        return
    
    # Format OI in millions for readability
    oi_millions = oi_current / 1_000_000 if oi_current else 0
    
    # Build the alert message
    message = (
        f"{emoji}{emoji}{emoji} {title} {emoji}{emoji}{emoji}\n\n"
        f"Exchange: Delta Exchange (Futures)\n"
        f"Symbol: {symbol}\n"
        f"Entry: ${price:,.2f}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"📊 SIGNAL DETAILS:\n"
        f"• CHOP: {chop_value:.1f}\n"
        f"• RSI: {rsi_value:.1f}\n"
        f"• Channel Position: {channel_percentile:.1f}%\n"
        f"• Open Interest: ${oi_millions:,.2f}M\n"
        f"• OI Change: {oi_change:+.2f}% ✅ (Threshold: >1%)\n\n"
        f"📈 MARKET CONDITION:\n"
        f"• {market_condition}\n"
        f"• {strategy}\n\n"
        f"⏰ Expected Hold Time: 5-30 minutes\n"
        f"⚠️ No SL/TP provided - Manage manually"
    )
    
    if send_telegram_alert(message):
        alert_cooldown[alert_key] = current_time
        last_alert[symbol] = signal_type
        logger.info(f"📨 Alert sent: {title} for {symbol}")

# ============================================
# 5. Signal Detection Functions
# ============================================

def check_buy_reversal(df, symbol, current_price, HH, LL):
    """Check for BUY REVERSAL signal: CHOP > 60, Channel <= 5%, RSI < 30, OI increasing > 1%"""
    try:
        chop_value = calculate_choppiness_index(df, period=14)
        rsi_value = calculate_rsi(df, period=14)
        channel_percentile = calculate_channel_percentile(HH, LL, current_price)
        
        # Get Open Interest data
        oi_current, oi_prev, oi_change = get_open_interest(symbol)
        
        # Check all conditions - OI threshold changed to 1%
        conditions_met = (
            chop_value > 60 and
            channel_percentile <= 5 and
            rsi_value < 30 and
            oi_current is not None and
            oi_change > 1  # OI increasing > 1% (changed from 2%)
        )
        
        if conditions_met:
            logger.info(f"✅ BUY REVERSAL detected for {symbol}")
            return True, chop_value, rsi_value, channel_percentile, oi_current, oi_change
        
        return False, None, None, None, None, None
        
    except Exception as e:
        logger.error(f"Error checking BUY REVERSAL for {symbol}: {e}")
        return False, None, None, None, None, None

def check_sell_reversal(df, symbol, current_price, HH, LL):
    """Check for SELL REVERSAL signal: CHOP > 60, Channel >= 95%, RSI > 70, OI increasing > 1%"""
    try:
        chop_value = calculate_choppiness_index(df, period=14)
        rsi_value = calculate_rsi(df, period=14)
        channel_percentile = calculate_channel_percentile(HH, LL, current_price)
        
        # Get Open Interest data
        oi_current, oi_prev, oi_change = get_open_interest(symbol)
        
        # Check all conditions - OI threshold changed to 1%
        conditions_met = (
            chop_value > 60 and
            channel_percentile >= 95 and
            rsi_value > 70 and
            oi_current is not None and
            oi_change > 1  # OI increasing > 1% (changed from 2%)
        )
        
        if conditions_met:
            logger.info(f"✅ SELL REVERSAL detected for {symbol}")
            return True, chop_value, rsi_value, channel_percentile, oi_current, oi_change
        
        return False, None, None, None, None, None
        
    except Exception as e:
        logger.error(f"Error checking SELL REVERSAL for {symbol}: {e}")
        return False, None, None, None, None, None

def check_buy_trend(df, symbol, current_price, HH, LL):
    """Check for BUY TREND CONTINUATION: CHOP < 40, Channel >= 95%, RSI > 55, OI increasing > 1%"""
    try:
        chop_value = calculate_choppiness_index(df, period=14)
        rsi_value = calculate_rsi(df, period=14)
        channel_percentile = calculate_channel_percentile(HH, LL, current_price)
        
        # Get Open Interest data
        oi_current, oi_prev, oi_change = get_open_interest(symbol)
        
        # Check all conditions - OI threshold changed to 1%
        conditions_met = (
            chop_value < 40 and
            channel_percentile >= 95 and
            rsi_value > 55 and
            oi_current is not None and
            oi_change > 1  # OI increasing > 1% (changed from 2%)
        )
        
        if conditions_met:
            logger.info(f"✅ BUY TREND CONTINUATION detected for {symbol}")
            return True, chop_value, rsi_value, channel_percentile, oi_current, oi_change
        
        return False, None, None, None, None, None
        
    except Exception as e:
        logger.error(f"Error checking BUY TREND for {symbol}: {e}")
        return False, None, None, None, None, None

def check_sell_trend(df, symbol, current_price, HH, LL):
    """Check for SELL TREND CONTINUATION: CHOP < 40, Channel <= 5%, RSI < 45, OI increasing > 1%"""
    try:
        chop_value = calculate_choppiness_index(df, period=14)
        rsi_value = calculate_rsi(df, period=14)
        channel_percentile = calculate_channel_percentile(HH, LL, current_price)
        
        # Get Open Interest data
        oi_current, oi_prev, oi_change = get_open_interest(symbol)
        
        # Check all conditions - OI threshold changed to 1%
        conditions_met = (
            chop_value < 40 and
            channel_percentile <= 5 and
            rsi_value < 45 and
            oi_current is not None and
            oi_change > 1  # OI increasing > 1% (changed from 2%)
        )
        
        if conditions_met:
            logger.info(f"✅ SELL TREND CONTINUATION detected for {symbol}")
            return True, chop_value, rsi_value, channel_percentile, oi_current, oi_change
        
        return False, None, None, None, None, None
        
    except Exception as e:
        logger.error(f"Error checking SELL TREND for {symbol}: {e}")
        return False, None, None, None, None, None

# ============================================
# 6. Startup Message
# ============================================

def send_startup_message():
    """Send a one-time startup confirmation message."""
    global bot_started_message_sent
    
    if bot_started_message_sent:
        return
    
    # Test connection
    try:
        ticker = EXCHANGE.fetch_ticker('BTC/USDT')
        btc_price = ticker['last']
        connection_status = "✅ Connected to Delta Exchange"
    except:
        btc_price = "N/A"
        connection_status = "⚠️ Connection Issue"
    
    message = (
        f"🚀🚀🚀 DELTA EXCHANGE SCALPING BOT STARTED 🚀🚀🚀\n\n"
        f"✅ Bot is ONLINE and RUNNING\n"
        f"📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🔗 Exchange: Delta Exchange (Futures)\n"
        f"📊 Status: {connection_status}\n\n"
        f"📈 MARKET DATA:\n"
        f"• BTC/USDT: ${btc_price:,.2f}\n"
        f"• Active Symbols: {len(SYMBOLS)}\n\n"
        f"📊 STRATEGY:\n"
        f"• Donchian Channel (52)\n"
        f"• Choppiness Index (14)\n"
        f"• RSI (14)\n"
        f"• Open Interest (OI) Filter: >1%\n\n"
        f"⚡ SIGNAL TYPES:\n"
        f"🟢 BUY REVERSAL: CHOP>60 + Channel≤5% + RSI<30 + OI↑>1%\n"
        f"🔴 SELL REVERSAL: CHOP>60 + Channel≥95% + RSI>70 + OI↑>1%\n"
        f"🟢 BUY TREND: CHOP<40 + Channel≥95% + RSI>55 + OI↑>1%\n"
        f"🔴 SELL TREND: CHOP<40 + Channel≤5% + RSI<45 + OI↑>1%\n\n"
        f"⏰ Scan Interval: 60 seconds\n"
        f"⏰ Expected Hold Time: 5-30 minutes\n"
        f"💡 No auto-trading - Alerts only\n\n"
        f"🟢 All systems operational. Waiting for signals..."
    )
    
    if send_telegram_alert(message):
        bot_started_message_sent = True
        logger.info("✅ Startup confirmation message sent to Telegram")

# ============================================
# 7. Main Bot Loop
# ============================================

def run_bot():
    """Main bot execution loop."""
    logger.info("🚀 DELTA EXCHANGE SCALPING BOT STARTED")
    logger.info("=" * 60)
    logger.info(f"📊 Total Symbols: {len(SYMBOLS)}")
    logger.info("📊 Strategy: Donchian (52) + CHOP (14) + RSI (14) + OI Filter (>1%)")
    logger.info("⏱ Timeframe: 15m candles")
    logger.info("⏱ Scan Interval: 60 seconds")
    logger.info("⚡ Expected Hold Time: 5-30 minutes")
    logger.info("💬 Alerts: Trading signals ONLY")
    logger.info("=" * 60)
    
    # Send startup message
    send_startup_message()
    
    while True:
        for idx, symbol in enumerate(SYMBOLS):
            try:
                # Map to Delta Exchange symbol format
                delta_symbol = f"{symbol}/USDT"
                
                # Check if symbol exists on Delta Exchange
                if delta_symbol not in EXCHANGE.markets:
                    logger.debug(f"Skipping unavailable symbol: {delta_symbol}")
                    continue
                
                # Get OHLCV data (15-minute candles)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    delta_symbol,
                    timeframe='15m',
                    limit=100
                )
                
                if len(ohlcv) < 70:
                    logger.debug(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'volume']
                )
                
                # ============ DONCHIAN CHANNEL (52 candles) ============
                HH = df['high'][-53:-1].max()  # Highest high in last 52 candles
                LL = df['low'][-53:-1].min()   # Lowest low in last 52 candles
                
                # Current market price
                ticker = EXCHANGE.fetch_ticker(delta_symbol)
                current_price = ticker['last']
                
                # Calculate channel position
                channel_percentile = calculate_channel_percentile(HH, LL, current_price)
                
                # Check for signals in priority order
                # Priority: Reversals first, then Trend Continuations
                
                # 1. Check BUY REVERSAL
                buy_rev_signal, chop_val, rsi_val, ch_pct, oi_curr, oi_chg = check_buy_reversal(
                    df, symbol, current_price, HH, LL
                )
                
                if buy_rev_signal and last_alert.get(symbol) != "BUY_REVERSAL":
                    send_signal_alert(symbol, "BUY_REVERSAL", current_price, 
                                    chop_val, rsi_val, ch_pct, oi_curr, oi_chg, {})
                    continue  # Skip other signals for this cycle
                
                # 2. Check SELL REVERSAL
                sell_rev_signal, chop_val, rsi_val, ch_pct, oi_curr, oi_chg = check_sell_reversal(
                    df, symbol, current_price, HH, LL
                )
                
                if sell_rev_signal and last_alert.get(symbol) != "SELL_REVERSAL":
                    send_signal_alert(symbol, "SELL_REVERSAL", current_price,
                                    chop_val, rsi_val, ch_pct, oi_curr, oi_chg, {})
                    continue
                
                # 3. Check BUY TREND CONTINUATION
                buy_trend_signal, chop_val, rsi_val, ch_pct, oi_curr, oi_chg = check_buy_trend(
                    df, symbol, current_price, HH, LL
                )
                
                if buy_trend_signal and last_alert.get(symbol) != "BUY_TREND":
                    send_signal_alert(symbol, "BUY_TREND", current_price,
                                    chop_val, rsi_val, ch_pct, oi_curr, oi_chg, {})
                    continue
                
                # 4. Check SELL TREND CONTINUATION
                sell_trend_signal, chop_val, rsi_val, ch_pct, oi_curr, oi_chg = check_sell_trend(
                    df, symbol, current_price, HH, LL
                )
                
                if sell_trend_signal and last_alert.get(symbol) != "SELL_TREND":
                    send_signal_alert(symbol, "SELL_TREND", current_price,
                                    chop_val, rsi_val, ch_pct, oi_curr, oi_chg, {})
                    continue
                
                # Reset alert if conditions no longer met
                if symbol in last_alert and last_alert[symbol] is not None:
                    if time.time() - alert_cooldown.get(f"{symbol}_{last_alert[symbol]}", 0) > 300:
                        last_alert[symbol] = None
                
                # Debug logging (every 5th symbol to reduce noise)
                if idx % 5 == 0:
                    logger.debug(f"{symbol} - Price: ${current_price:,.2f}, "
                               f"CHOP: {chop_val if chop_val else 'N/A'}, "
                               f"RSI: {rsi_val if rsi_val else 'N/A'}, "
                               f"Channel%: {channel_percentile:.1f}%")
                
            except ccxt.RateLimitExceeded:
                logger.warning(f"⚠️ Rate limit exceeded for {symbol}, waiting...")
                time.sleep(5)
            except ccxt.NetworkError as e:
                logger.error(f"🌐 Network error for {symbol}: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"❌ Error checking {symbol}: {e}")
                time.sleep(2)
        
        # Check every 60 seconds (changed from 20 seconds)
        time.sleep(60)

# ============================================
# 8. Start Bot and Flask Server
# ============================================

# Start bot in background thread
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Start Flask server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)