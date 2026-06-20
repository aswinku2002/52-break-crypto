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
# 1. Setup Flask for Render 2
# ============================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Binance Futures Scalping Bot is running!"

# ============================================
# 2. Configuration
# ============================================
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Binance API keys (optional for higher rate limits)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# ============================================
# BINANCE FUTURES SYMBOLS
# Using USDT perpetual futures
# ============================================
SYMBOLS = [
    # Major Cryptocurrencies (High Volume)
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

# Initialize Binance Futures
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # perpetual futures
    }
}

EXCHANGE = ccxt.binance(binance_config)

# Enable futures market specifically
try:
    EXCHANGE.load_markets()
    print("✅ Binance markets loaded successfully")
    print(f"📊 Loaded {len(EXCHANGE.markets)} trading pairs")
    print(f"🎯 Monitoring {len(SYMBOLS)} perpetual futures")
except Exception as e:
    print(f"❌ Error loading Binance markets: {e}")
    print("Make sure you have internet connection and Binance is accessible")

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
    Fetch current and historical Open Interest from Binance Futures.
    Returns: (current_oi, previous_oi, oi_change_percent)
    """
    try:
        # Binance uses symbol format like BTCUSDT (without slash)
        symbol_clean = symbol.replace('/', '')
        
        # Fetch current Open Interest
        oi_data = EXCHANGE.public_get_futures_data_openinterest({
            'symbol': symbol_clean
        })

        if 'openInterest' not in oi_data:
            return None, None, 0
            
        current_oi = float(oi_data['openInterest'])
        
        # Fetch historical OI for change calculation
        try:
            # Get OI history for the last 2 periods (10 minutes each with our timeframe)
            oi_history = EXCHANGE.public_get_futures_data_openinterest_hist({
                'symbol': symbol_clean,
                'period': '10m',
                'limit': 2
            })
            
            if oi_history and len(oi_history) >= 2:
                # Historical OI data comes in reverse order (most recent first)
                prev_oi = float(oi_history[1]['sumOpenInterest'])
                current_oi = float(oi_history[0]['sumOpenInterest'])
            else:
                # Fallback: use current OI as previous if history not available
                prev_oi = current_oi
                
        except Exception as e:
            logger.debug(f"OI history fetch failed for {symbol}, using current OI as fallback: {e}")
            prev_oi = current_oi

        # Calculate OI change percentage
        if prev_oi > 0:
            oi_change = ((current_oi - prev_oi) / prev_oi) * 100
        else:
            oi_change = 0

        return current_oi, prev_oi, round(oi_change, 2)

    except Exception as e:
        logger.error(f"Open Interest fetch error for {symbol}: {e}")
        return None, None, 0

def calculate_rsi_slope(df, rsi_values):
    """Calculate RSI direction (rising or falling)"""
    try:
        if len(rsi_values) < 3:
            return "NEUTRAL"
        
        # Compare current RSI with previous RSI
        current_rsi = rsi_values.iloc[-1]
        previous_rsi = rsi_values.iloc[-2]
        
        if current_rsi > previous_rsi:
            return "RISING"
        elif current_rsi < previous_rsi:
            return "FALLING"
        else:
            return "NEUTRAL"
            
    except Exception as e:
        logger.error(f"RSI slope calculation error: {e}")
        return "NEUTRAL"

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
                     channel_percentile, oi_current, oi_change, rsi_direction, 
                     candle_type, prev_rsi=None):
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
        strategy = "Mean-reversion expected at oversold levels"

    elif signal_type == "SELL_REVERSAL":
        emoji = "🔴"
        title = "SELL REVERSAL"
        strategy = "Mean-reversion expected at overbought levels"

    elif signal_type == "BUY_TREND":
        emoji = "🟢"
        title = "BUY TREND CONTINUATION"
        strategy = "Momentum continuation expected"

    elif signal_type == "SELL_TREND":
        emoji = "🔴"
        title = "SELL TREND CONTINUATION"
        strategy = "Momentum continuation expected"

    else:
        return

    # Format OI in millions for readability
    oi_millions = oi_current / 1_000_000 if oi_current else 0
    
    # Get RSI direction emoji
    rsi_emoji = "📈" if rsi_direction == "RISING" else "📉" if rsi_direction == "FALLING" else "➡️"
    
    # Get candle emoji
    candle_emoji = "🟩" if candle_type == "BULLISH" else "🟥" if candle_type == "BEARISH" else "⬜"

    # Build the alert message
    message = (
        f"{emoji}{emoji}{emoji} {title} {emoji}{emoji}{emoji}\n\n"
        f"Exchange: Binance Futures\n"
        f"Symbol: {symbol}\n"
        f"Entry: ${price:,.2f}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"📊 SIGNAL DETAILS:\n"
        f"• CHOP: {chop_value:.1f}\n"
        f"• RSI: {rsi_value:.1f} {rsi_emoji} ({rsi_direction})\n"
        f"• Channel Position: {channel_percentile:.1f}%\n"
        f"• Open Interest: ${oi_millions:,.2f}M\n"
        f"• OI Change: {oi_change:+.2f}% ✅ (Threshold: >1%)\n"
        f"• Candle: {candle_emoji} {candle_type}\n\n"
        f"📈 STRATEGY:\n"
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

def check_buy_reversal(chop_value, rsi_value, rsi_direction, channel_percentile, 
                       oi_change, candle_type):
    """Check for BUY REVERSAL signal"""
    try:
        conditions_met = (
            chop_value > 58 and
            channel_percentile <= 5 and
            rsi_value < 28 and
            rsi_direction == "RISING" and
            candle_type == "BULLISH" and
            oi_change is not None and
            oi_change > 1
        )

        if conditions_met:
            logger.info(f"✅ BUY REVERSAL detected")
            return True

        return False

    except Exception as e:
        logger.error(f"Error checking BUY REVERSAL: {e}")
        return False

def check_sell_reversal(chop_value, rsi_value, rsi_direction, channel_percentile, 
                        oi_change, candle_type):
    """Check for SELL REVERSAL signal"""
    try:
        conditions_met = (
            chop_value > 58 and
            channel_percentile >= 95 and
            rsi_value > 72 and
            rsi_direction == "FALLING" and
            candle_type == "BEARISH" and
            oi_change is not None and
            oi_change > 1
        )

        if conditions_met:
            logger.info(f"✅ SELL REVERSAL detected")
            return True

        return False

    except Exception as e:
        logger.error(f"Error checking SELL REVERSAL: {e}")
        return False

def check_buy_trend(chop_value, rsi_value, channel_percentile, oi_change):
    """Check for BUY TREND CONTINUATION"""
    try:
        conditions_met = (
            chop_value < 42 and
            channel_percentile >= 95 and
            rsi_value > 55 and
            oi_change is not None and
            oi_change > 1
        )

        if conditions_met:
            logger.info(f"✅ BUY TREND CONTINUATION detected")
            return True

        return False

    except Exception as e:
        logger.error(f"Error checking BUY TREND: {e}")
        return False

def check_sell_trend(chop_value, rsi_value, channel_percentile, oi_change):
    """Check for SELL TREND CONTINUATION"""
    try:
        conditions_met = (
            chop_value < 42 and
            channel_percentile <= 5 and
            rsi_value < 45 and
            oi_change is not None and
            oi_change > 1
        )

        if conditions_met:
            logger.info(f"✅ SELL TREND CONTINUATION detected")
            return True

        return False

    except Exception as e:
        logger.error(f"Error checking SELL TREND: {e}")
        return False

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
        connection_status = "✅ Connected to Binance Futures"
    except:
        btc_price = "N/A"
        connection_status = "⚠️ Connection Issue"

    message = (
        f"🚀🚀🚀 BINANCE FUTURES SCALPING BOT STARTED 🚀🚀🚀\n\n"
        f"✅ Bot is ONLINE and RUNNING\n"
        f"📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🔗 Exchange: Binance Futures\n"
        f"📊 Status: {connection_status}\n\n"
        f"📈 MARKET DATA:\n"
        f"• BTC/USDT: ${btc_price:,.2f}\n"
        f"• Active Symbols: {len(SYMBOLS)}\n\n"
        f"📊 STRATEGY CONFIGURATION:\n"
        f"• Timeframe: 10 minutes\n"
        f"• Donchian Channel Period: 78 (13 hours)\n"
        f"• Choppiness Index Period: 14\n"
        f"• RSI Period: 14\n"
        f"• Open Interest (OI) Filter: >1%\n\n"
        f"⚡ SIGNAL TYPES:\n"
        f"🟢 BUY REVERSAL: CHOP>58 + Channel≤5% + RSI<28 + RSI↑ + Bullish Candle + OI↑>1%\n"
        f"🔴 SELL REVERSAL: CHOP>58 + Channel≥95% + RSI>72 + RSI↓ + Bearish Candle + OI↑>1%\n"
        f"🟢 BUY TREND: CHOP<42 + Channel≥95% + RSI>55 + OI↑>1%\n"
        f"🔴 SELL TREND: CHOP<42 + Channel≤5% + RSI<45 + OI↑>1%\n\n"
        f"⏰ Scan Interval: 120 seconds\n"
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
    logger.info("🚀 BINANCE FUTURES SCALPING BOT STARTED")
    logger.info("=" * 60)
    logger.info(f"📊 Total Symbols: {len(SYMBOLS)}")
    logger.info("📊 Strategy: Donchian (78) + CHOP (14) + RSI (14) + OI Filter (>1%)")
    logger.info("⏱ Timeframe: 10m candles")
    logger.info("⏱ Scan Interval: 120 seconds")
    logger.info("⚡ Expected Hold Time: 5-30 minutes")
    logger.info("💬 Alerts: Trading signals ONLY")
    logger.info("=" * 60)

    # Send startup message
    send_startup_message()

    while True:
        for idx, symbol in enumerate(SYMBOLS):
            try:
                # Get OHLCV data (10-minute candles)
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='10m',
                    limit=100  # Need at least 78 + some buffer
                )

                if len(ohlcv) < 80:  # Need at least 78 candles + buffer
                    logger.debug(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'volume']
                )

                # ============ CALCULATE INDICATORS ONCE PER SYMBOL ============
                
                # Calculate RSI
                rsi_value = calculate_rsi(df, period=14)
                
                # Calculate RSI direction (slope)
                rsi_values = df['close'].rolling(14).apply(
                    lambda x: 100 - (100 / (1 + (x.diff().clip(lower=0).mean() / -x.diff().clip(upper=0).mean())))
                )
                rsi_direction = calculate_rsi_slope(df, rsi_values)
                
                # Calculate CHOP
                chop_value = calculate_choppiness_index(df, period=14)
                
                # Donchian Channel (78 candles = ~13 hours)
                HH = df['high'][-79:-1].max()  # Highest high in last 78 candles
                LL = df['low'][-79:-1].min()   # Lowest low in last 78 candles

                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']

                # Calculate channel position
                channel_percentile = calculate_channel_percentile(HH, LL, current_price)
                
                # Get Open Interest data (current and historical for change)
                oi_current, oi_prev, oi_change = get_open_interest(symbol)
                
                # Determine candle type (bullish or bearish)
                last_candle = df.iloc[-1]
                if last_candle['close'] > last_candle['open']:
                    candle_type = "BULLISH"
                elif last_candle['close'] < last_candle['open']:
                    candle_type = "BEARISH"
                else:
                    candle_type = "NEUTRAL"

                # ============ CHECK SIGNALS ============
                
                # Priority: Reversals first, then Trend Continuations
                signal_detected = False

                # 1. Check BUY REVERSAL
                if check_buy_reversal(chop_value, rsi_value, rsi_direction, 
                                     channel_percentile, oi_change, candle_type):
                    if last_alert.get(symbol) != "BUY_REVERSAL":
                        send_signal_alert(symbol, "BUY_REVERSAL", current_price, 
                                        chop_value, rsi_value, channel_percentile, 
                                        oi_current, oi_change, rsi_direction, candle_type)
                        signal_detected = True

                # 2. Check SELL REVERSAL (only if no buy signal detected)
                if not signal_detected:
                    if check_sell_reversal(chop_value, rsi_value, rsi_direction, 
                                          channel_percentile, oi_change, candle_type):
                        if last_alert.get(symbol) != "SELL_REVERSAL":
                            send_signal_alert(symbol, "SELL_REVERSAL", current_price,
                                            chop_value, rsi_value, channel_percentile,
                                            oi_current, oi_change, rsi_direction, candle_type)
                            signal_detected = True

                # 3. Check BUY TREND CONTINUATION (only if no reversal detected)
                if not signal_detected:
                    if check_buy_trend(chop_value, rsi_value, channel_percentile, oi_change):
                        if last_alert.get(symbol) != "BUY_TREND":
                            send_signal_alert(symbol, "BUY_TREND", current_price,
                                            chop_value, rsi_value, channel_percentile,
                                            oi_current, oi_change, rsi_direction, candle_type)
                            signal_detected = True

                # 4. Check SELL TREND CONTINUATION (only if no other signal detected)
                if not signal_detected:
                    if check_sell_trend(chop_value, rsi_value, channel_percentile, oi_change):
                        if last_alert.get(symbol) != "SELL_TREND":
                            send_signal_alert(symbol, "SELL_TREND", current_price,
                                            chop_value, rsi_value, channel_percentile,
                                            oi_current, oi_change, rsi_direction, candle_type)
                            signal_detected = True

                # Reset alert if conditions no longer met
                if symbol in last_alert and last_alert[symbol] is not None:
                    if time.time() - alert_cooldown.get(f"{symbol}_{last_alert[symbol]}", 0) > 300:
                        last_alert[symbol] = None

                # Debug logging (every 5th symbol to reduce noise)
                if idx % 5 == 0:
                    logger.debug(f"{symbol} - Price: ${current_price:,.2f}, "
                               f"CHOP: {chop_value:.1f}, "
                               f"RSI: {rsi_value:.1f} ({rsi_direction}), "
                               f"Channel%: {channel_percentile:.1f}%, "
                               f"OI Change: {oi_change:+.2f}%, "
                               f"Candle: {candle_type}")

            except ccxt.RateLimitExceeded:
                logger.warning(f"⚠️ Rate limit exceeded for {symbol}, waiting...")
                time.sleep(5)
            except ccxt.NetworkError as e:
                logger.error(f"🌐 Network error for {symbol}: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"❌ Error checking {symbol}: {e}")
                time.sleep(2)

        # Check every 120 seconds (2 minutes)
        time.sleep(120)

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