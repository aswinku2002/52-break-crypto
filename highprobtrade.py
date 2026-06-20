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
    return "Crypto Scalping Bot is running!"

# ============================================
# 2. Configuration
# ============================================
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Binance API keys
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT',
    'PAXG/USDT', 'XAUT/USDT', 'XRP/USDT', 'DOGE/USDT',
    'BNB/USDT', 'LTC/USDT', 'LINK/USDT', 'MATIC/USDT',
    'DOT/USDT', 'AVAX/USDT', 'UNI/USDT', 'ATOM/USDT'
]

# Initialize Binance
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
}

EXCHANGE = ccxt.binance(binance_config)

try:
    EXCHANGE.load_markets()
    print("Binance markets loaded successfully")
except Exception as e:
    print(f"Error loading Binance markets: {e}")

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

# ============================================
# 3. Core Indicator Functions
# ============================================

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
        logger.error(f"Choppiness calculation error: {e}")
        return 50

def calculate_atr(df, period=14):
    """Calculate ATR"""
    try:
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr = tr.ewm(span=period, adjust=False).mean()
        result = atr.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return None
        return round(result, 2)
    except Exception as e:
        logger.error(f"ATR calculation error: {e}")
        return None

def calculate_rsi_series(df, period=14):
    """Calculate RSI series"""
    try:
        close = df['close']
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    except Exception as e:
        logger.error(f"RSI series calculation error: {e}")
        return pd.Series([50] * len(df))

def calculate_rsi_current(df, period=14):
    """Get latest RSI"""
    try:
        rsi_series = calculate_rsi_series(df, period)
        result = rsi_series.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return 50
        return round(result, 2)
    except Exception as e:
        logger.error(f"RSI current value calculation error: {e}")
        return 50

def calculate_ema(df, period):
    """Calculate EMA"""
    try:
        return df['close'].ewm(span=period, adjust=False).mean()
    except Exception as e:
        logger.error(f"EMA {period} calculation error: {e}")
        return pd.Series([0] * len(df))

def calculate_average_volume(df, period=20):
    """Calculate average volume"""
    try:
        avg_volume = df['vol'].rolling(window=period).mean()
        return avg_volume.iloc[-1] if not pd.isna(avg_volume.iloc[-1]) else 0
    except Exception as e:
        logger.error(f"Average volume calculation error: {e}")
        return 0

def calculate_donchian_channel(df, period=52):
    """Calculate Donchian Channel"""
    try:
        if len(df) <= period:
            hh = df['high'].iloc[:-1].max()
            ll = df['low'].iloc[:-1].min()
        else:
            hh = df['high'].iloc[-(period+1):-1].max()
            ll = df['low'].iloc[-(period+1):-1].min()
        return hh, ll
    except Exception as e:
        logger.error(f"Donchian channel calculation error: {e}")
        return None, None

def calculate_channel_percentile(HH, LL, current_price):
    """Calculate channel position"""
    if HH == LL:
        return 50
    percentile = ((current_price - LL) / (HH - LL)) * 100
    return round(percentile, 2)

def calculate_candle_metrics(row):
    """Calculate candle metrics"""
    body_size = abs(row['close'] - row['open'])
    upper_wick = row['high'] - max(row['open'], row['close'])
    lower_wick = min(row['open'], row['close']) - row['low']
    is_bullish = row['close'] > row['open']
    return body_size, upper_wick, lower_wick, is_bullish

# ============================================
# 4. SCALPING SIGNAL FUNCTIONS
# ============================================

def check_buy_scalp(df, symbol):
    """
    Check for BUY SCALPING signal - Fast entry, high probability.
    
    Conditions:
    - CHOP < 35 (trending enough for momentum)
    - RSI between 55-70 (momentum with room to run)
    - RSI Rising (current > previous)
    - Close near Donchian High (early breakout entry)
    - Price > EMA20 (short-term trend)
    - EMA20 > EMA50 (trend confirmation)
    - Volume > 1.1x average
    - Bullish candle
    - No significant upper wick (avoid rejection)
    """
    try:
        if len(df) < 3:
            return False, None, None, None

        # Get last closed candle
        last_closed = df.iloc[-2]
        
        # ============ CALCULATE INDICATORS ============
        # Donchian Channel
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
        if hh is None or ll is None:
            return False, None, None, None
        
        # CHOP
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        # RSI series
        rsi_series = calculate_rsi_series(df.iloc[:-1], 14)
        rsi_current = rsi_series.iloc[-1]
        rsi_previous = rsi_series.iloc[-2]
        
        # EMAs (scalping version - faster)
        ema20_series = calculate_ema(df.iloc[:-1], 20)
        ema50_series = calculate_ema(df.iloc[:-1], 50)
        ema20_current = ema20_series.iloc[-1]
        ema50_current = ema50_series.iloc[-1]
        
        # Volume
        avg_volume = calculate_average_volume(df.iloc[:-1], 20)
        
        # ATR
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        # Candle metrics
        body_size, upper_wick, lower_wick, is_bullish = calculate_candle_metrics(last_closed)
        
        # Channel percentile
        channel_percentile = calculate_channel_percentile(hh, ll, last_closed['close'])
        
        # ============ CHECK CONDITIONS ============
        # 1. CHOP < 35 (trending, but not too extreme)
        if chop_value >= 35:
            logger.debug(f"{symbol} BUY SCALP: CHOP {chop_value} >= 35")
            return False, None, None, None
        
        # 2. RSI between 55-70
        if not (55 <= rsi_current <= 70):
            logger.debug(f"{symbol} BUY SCALP: RSI {rsi_current} outside 55-70")
            return False, None, None, None
        
        # 3. RSI Rising
        if rsi_current <= rsi_previous:
            logger.debug(f"{symbol} BUY SCALP: RSI not rising")
            return False, None, None, None
        
        # 4. Close near Donchian High (early entry - 0.5% below HH)
        early_entry_threshold = hh * 0.995
        if last_closed['close'] < early_entry_threshold:
            logger.debug(f"{symbol} BUY SCALP: Close ${last_closed['close']:.2f} below threshold ${early_entry_threshold:.2f}")
            return False, None, None, None
        
        # 5. Price > EMA20
        if last_closed['close'] <= ema20_current:
            logger.debug(f"{symbol} BUY SCALP: Price below EMA20")
            return False, None, None, None
        
        # 6. EMA20 > EMA50 (trend confirmation)
        if ema20_current <= ema50_current:
            logger.debug(f"{symbol} BUY SCALP: EMA20 {ema20_current:.2f} <= EMA50 {ema50_current:.2f}")
            return False, None, None, None
        
        # 7. Volume > 1.1x average (less strict for scalping)
        if last_closed['vol'] <= (avg_volume * 1.1):
            logger.debug(f"{symbol} BUY SCALP: Volume {last_closed['vol']:.2f} <= {avg_volume * 1.1:.2f}")
            return False, None, None, None
        
        # 8. Bullish candle
        if not is_bullish:
            logger.debug(f"{symbol} BUY SCALP: Not bullish")
            return False, None, None, None
        
        # 9. No significant upper wick (avoid rejection)
        if body_size > 0 and (upper_wick / body_size) > 0.5:
            logger.debug(f"{symbol} BUY SCALP: Upper wick too large")
            return False, None, None, None
        
        # All conditions met!
        logger.info(f"BUY SCALP signal detected for {symbol} at ${last_closed['close']:.2f}")
        return True, last_closed['close'], atr_value, {
            'chop': chop_value,
            'rsi': rsi_current,
            'percentile': channel_percentile,
            'ema20': ema20_current,
            'ema50': ema50_current,
            'volume_confirmed': True,
            'candle_type': 'Bullish'
        }
        
    except Exception as e:
        logger.error(f"Error checking BUY SCALP for {symbol}: {e}")
        return False, None, None, None

def check_sell_scalp(df, symbol):
    """
    Check for SELL SCALPING signal - Fast entry, high probability.
    
    Conditions:
    - CHOP < 35
    - RSI between 30-45
    - RSI Falling
    - Close near Donchian Low
    - Price < EMA20
    - EMA20 < EMA50
    - Volume > 1.1x average
    - Bearish candle
    - No significant lower wick
    """
    try:
        if len(df) < 3:
            return False, None, None, None

        # Get last closed candle
        last_closed = df.iloc[-2]
        
        # ============ CALCULATE INDICATORS ============
        # Donchian Channel
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
        if hh is None or ll is None:
            return False, None, None, None
        
        # CHOP
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        # RSI series
        rsi_series = calculate_rsi_series(df.iloc[:-1], 14)
        rsi_current = rsi_series.iloc[-1]
        rsi_previous = rsi_series.iloc[-2]
        
        # EMAs
        ema20_series = calculate_ema(df.iloc[:-1], 20)
        ema50_series = calculate_ema(df.iloc[:-1], 50)
        ema20_current = ema20_series.iloc[-1]
        ema50_current = ema50_series.iloc[-1]
        
        # Volume
        avg_volume = calculate_average_volume(df.iloc[:-1], 20)
        
        # ATR
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        # Candle metrics
        body_size, upper_wick, lower_wick, is_bullish = calculate_candle_metrics(last_closed)
        
        # Channel percentile
        channel_percentile = calculate_channel_percentile(hh, ll, last_closed['close'])
        
        # ============ CHECK CONDITIONS ============
        # 1. CHOP < 35
        if chop_value >= 35:
            logger.debug(f"{symbol} SELL SCALP: CHOP {chop_value} >= 35")
            return False, None, None, None
        
        # 2. RSI between 30-45
        if not (30 <= rsi_current <= 45):
            logger.debug(f"{symbol} SELL SCALP: RSI {rsi_current} outside 30-45")
            return False, None, None, None
        
        # 3. RSI Falling
        if rsi_current >= rsi_previous:
            logger.debug(f"{symbol} SELL SCALP: RSI not falling")
            return False, None, None, None
        
        # 4. Close near Donchian Low (early entry - 0.5% above LL)
        early_entry_threshold = ll * 1.005
        if last_closed['close'] > early_entry_threshold:
            logger.debug(f"{symbol} SELL SCALP: Close ${last_closed['close']:.2f} above threshold ${early_entry_threshold:.2f}")
            return False, None, None, None
        
        # 5. Price < EMA20
        if last_closed['close'] >= ema20_current:
            logger.debug(f"{symbol} SELL SCALP: Price above EMA20")
            return False, None, None, None
        
        # 6. EMA20 < EMA50
        if ema20_current >= ema50_current:
            logger.debug(f"{symbol} SELL SCALP: EMA20 {ema20_current:.2f} >= EMA50 {ema50_current:.2f}")
            return False, None, None, None
        
        # 7. Volume > 1.1x average
        if last_closed['vol'] <= (avg_volume * 1.1):
            logger.debug(f"{symbol} SELL SCALP: Volume {last_closed['vol']:.2f} <= {avg_volume * 1.1:.2f}")
            return False, None, None, None
        
        # 8. Bearish candle
        if is_bullish:
            logger.debug(f"{symbol} SELL SCALP: Not bearish")
            return False, None, None, None
        
        # 9. No significant lower wick
        if body_size > 0 and (lower_wick / body_size) > 0.5:
            logger.debug(f"{symbol} SELL SCALP: Lower wick too large")
            return False, None, None, None
        
        # All conditions met!
        logger.info(f"SELL SCALP signal detected for {symbol} at ${last_closed['close']:.2f}")
        return True, last_closed['close'], atr_value, {
            'chop': chop_value,
            'rsi': rsi_current,
            'percentile': channel_percentile,
            'ema20': ema20_current,
            'ema50': ema50_current,
            'volume_confirmed': True,
            'candle_type': 'Bearish'
        }
        
    except Exception as e:
        logger.error(f"Error checking SELL SCALP for {symbol}: {e}")
        return False, None, None, None

# ============================================
# 5. EXIT SIGNAL FUNCTIONS (NEW)
# ============================================

def check_scalp_exit(position_type, entry_price, current_price, rsi_current, volume_ratio, atr_value):
    """
    Check if we should exit a scalp trade.
    
    Returns: (should_exit, exit_reason)
    """
    profit_pct = ((current_price - entry_price) / entry_price) * 100
    
    # For BUY positions
    if position_type == "BUY":
        # 1. Take profit at 0.5% (fast exit)
        if profit_pct >= 0.5:
            return True, "TP 0.5%"
        
        # 2. RSI overbought
        if rsi_current > 80:
            return True, "RSI > 80"
        
        # 3. Stop loss at 0.3%
        if profit_pct <= -0.3:
            return True, "SL 0.3%"
        
        # 4. Volume drying up
        if volume_ratio < 0.8:
            return True, "Volume dropping"
    
    # For SELL positions
    elif position_type == "SELL":
        # 1. Take profit at 0.5%
        if profit_pct >= 0.5:
            return True, "TP 0.5%"
        
        # 2. RSI oversold
        if rsi_current < 20:
            return True, "RSI < 20"
        
        # 3. Stop loss at 0.3%
        if profit_pct <= -0.3:
            return True, "SL 0.3%"
        
        # 4. Volume drying up
        if volume_ratio < 0.8:
            return True, "Volume dropping"
    
    return False, None

# ============================================
# 6. Alert Sending Functions
# ============================================

def send_telegram_alert(message):
    """Send alert to Telegram"""
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
            if response.status_code != 200:
                logger.error(f"Telegram error: {response.status_code}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    return False

def send_scalp_alert(symbol, signal_type, price, atr_value, indicators):
    """
    Send formatted scalping alert.
    """
    # Check cooldown
    alert_key = f"{symbol}_{signal_type}"
    current_time = time.time()
    
    if alert_key in alert_cooldown:
        if current_time - alert_cooldown[alert_key] < 120:  # 2 min cooldown for scalping
            logger.info(f"Skipping duplicate {signal_type} alert for {symbol}")
            return
    
    if signal_type == "BUY":
        emoji = "🟢"
        title = "BUY SCALP ENTRY"
        sl_mult = 1.0  # Tighter for scalping
        tp_mult = 0.5  # 0.5% profit target
        sl = price - (atr_value * sl_mult)
        tp = price + (price * 0.005)  # 0.5% take profit
        
        message = (
            f"{emoji}{emoji}{emoji} {title} {emoji}{emoji}{emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry: ${price:.2f}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"📊 SIGNAL DETAILS:\n"
            f"• CHOP: {indicators['chop']:.1f} (<35) ✅\n"
            f"• RSI: {indicators['rsi']:.1f} (55-70, Rising) ✅\n"
            f"• Channel Position: {indicators['percentile']:.1f}%\n"
            f"• EMA20: ${indicators['ema20']:.2f}\n"
            f"• EMA50: ${indicators['ema50']:.2f}\n"
            f"• Volume: CONFIRMED ✅\n"
            f"• Candle: {indicators['candle_type']} ✅\n\n"
            f"⚡ EXIT PLAN (15-30 min):\n"
            f"🛑 Stop Loss: ${sl:.2f} (ATR×1.0)\n"
            f"💰 Take Profit: ${tp:.2f} (0.5%)\n"
            f"📈 Risk/Reward: ~1:1.67\n\n"
            f"⚠️ Exit if:\n"
            f"• RSI > 80 (overbought)\n"
            f"• Volume drops below avg\n"
            f"• Price hits 0.5% profit"
        )
        
    else:  # SELL
        emoji = "🔴"
        title = "SELL SCALP ENTRY"
        sl_mult = 1.0
        tp_mult = 0.5
        sl = price + (atr_value * sl_mult)
        tp = price - (price * 0.005)
        
        message = (
            f"{emoji}{emoji}{emoji} {title} {emoji}{emoji}{emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry: ${price:.2f}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"📊 SIGNAL DETAILS:\n"
            f"• CHOP: {indicators['chop']:.1f} (<35) ✅\n"
            f"• RSI: {indicators['rsi']:.1f} (30-45, Falling) ✅\n"
            f"• Channel Position: {indicators['percentile']:.1f}%\n"
            f"• EMA20: ${indicators['ema20']:.2f}\n"
            f"• EMA50: ${indicators['ema50']:.2f}\n"
            f"• Volume: CONFIRMED ✅\n"
            f"• Candle: {indicators['candle_type']} ✅\n\n"
            f"⚡ EXIT PLAN (15-30 min):\n"
            f"🛑 Stop Loss: ${sl:.2f} (ATR×1.0)\n"
            f"💰 Take Profit: ${tp:.2f} (0.5%)\n"
            f"📈 Risk/Reward: ~1:1.67\n\n"
            f"⚠️ Exit if:\n"
            f"• RSI < 20 (oversold)\n"
            f"• Volume drops below avg\n"
            f"• Price hits 0.5% profit"
        )
    
    # Send alert
    if send_telegram_alert(message):
        alert_cooldown[alert_key] = current_time
        last_alert[symbol] = signal_type
        logger.info(f"Alert sent: {signal_type} SCALP for {symbol}")

# ============================================
# 7. Main Bot Loop
# ============================================

def run_bot():
    """Main bot execution loop."""
    logger.info("🚀 SCALPING BOT STARTED")
    logger.info("=" * 50)
    logger.info("Strategy: Donchian (52) + CHOP (14) + RSI (14)")
    logger.info("Timeframe: 15m candles")
    logger.info("Expected Hold Time: 15-30 minutes")
    logger.info("=" * 50)
    
    # Startup message
    startup_message = (
        "🚀 SCALPING BOT STARTED\n\n"
        "📊 Strategy: Donchian (52) + CHOP (14) + RSI (14)\n"
        "⏱ Timeframe: 15m candles\n"
        "⚡ Expected Hold: 15-30 minutes\n\n"
        "🟢 BUY SCALP CONDITIONS:\n"
        "• CHOP < 35 + RSI 55-70 (Rising)\n"
        "• Near Donchian High + EMA20 > EMA50\n"
        "• Volume > 1.1x + Bullish Candle\n\n"
        "🔴 SELL SCALP CONDITIONS:\n"
        "• CHOP < 35 + RSI 30-45 (Falling)\n"
        "• Near Donchian Low + EMA20 < EMA50\n"
        "• Volume > 1.1x + Bearish Candle\n\n"
        "⚡ EXIT RULES:\n"
        "• TP: 0.5% profit\n"
        "• SL: ATR × 1.0\n"
        "• RSI extremes\n"
        "• Volume drop\n\n"
        "🎯 TARGET: 15-30 minute scalps"
    )
    send_telegram_alert(startup_message)
    
    while True:
        for symbol in SYMBOLS:
            try:
                if symbol not in EXCHANGE.markets:
                    continue
                
                # Get 15m candles
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=150  # Enough for indicators
                )
                
                if len(ohlcv) < 80:
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # ============ CHECK BUY SCALP ============
                buy_signal, price, atr, indicators = check_buy_scalp(df, symbol)
                if buy_signal and atr is not None:
                    send_scalp_alert(symbol, "BUY", price, atr, indicators)
                    continue
                
                # ============ CHECK SELL SCALP ============
                sell_signal, price, atr, indicators = check_sell_scalp(df, symbol)
                if sell_signal and atr is not None:
                    send_scalp_alert(symbol, "SELL", price, atr, indicators)
                    continue
                
                # Reset alert if needed
                if symbol in last_alert and last_alert[symbol] is not None:
                    if time.time() - alert_cooldown.get(f"{symbol}_{last_alert[symbol]}", 0) > 300:
                        last_alert[symbol] = None
                        
            except ccxt.RateLimitExceeded:
                logger.warning(f"Rate limit exceeded for {symbol}, waiting...")
                time.sleep(5)
            except ccxt.NetworkError as e:
                logger.error(f"Network error for {symbol}: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Error checking {symbol}: {e}")
                time.sleep(2)
        
        # Check every 20 seconds (faster for scalping)
        time.sleep(20)

# ============================================
# 8. Start Bot and Flask Server
# ============================================

# Start bot in background
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Start Flask server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)