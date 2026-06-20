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
    return "Crypto Alert Bot is running!"

# ============================================
# 2. Configuration
# ============================================
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# Binance API keys (optional but recommended for higher rate limits)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT',
    'PAXG/USDT', 'XAUT/USDT', 'XRP/USDT', 'DOGE/USDT',
    'BNB/USDT', 'LTC/USDT', 'LINK/USDT', 'MATIC/USDT',
    'DOT/USDT', 'AVAX/USDT', 'UNI/USDT', 'ATOM/USDT'
]

# Initialize Binance exchange
binance_config = {
    'apiKey': BINANCE_API_KEY,
    'secret': BINANCE_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
}

EXCHANGE = ccxt.binance(binance_config)

try:
    EXCHANGE.load_markets()
    print("Binance markets loaded successfully")
    print(f"Loaded {len(EXCHANGE.markets)} trading pairs")
except Exception as e:
    print(f"Error loading Binance markets: {e}")
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
alert_cooldown = {}  # Track when each alert type was last sent for each symbol

# ============================================
# 3. Core Indicator Functions
# ============================================

def calculate_choppiness_index(df, period=14):
    """
    Calculate Choppiness Index - measures market ranging vs trending.
    Higher values (>60) indicate ranging/choppy markets.
    Lower values (<30) indicate strong trending markets.
    
    Args:
        df: DataFrame with OHLCV data
        period: Lookback period (default: 14)
    
    Returns:
        float: Choppiness Index value (0-100)
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
            return 50
        return round(result, 2)
        
    except Exception as e:
        logger.error(f"Choppiness calculation error: {e}")
        return 50

def calculate_atr(df, period=14):
    """
    Calculate Average True Range (ATR) - measures market volatility.
    
    Args:
        df: DataFrame with OHLCV data
        period: Lookback period (default: 14)
    
    Returns:
        float: ATR value
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
        
        # Calculate ATR using exponential moving average
        atr = tr.ewm(span=period, adjust=False).mean()
        
        result = atr.iloc[-1]
        if pd.isna(result) or np.isinf(result):
            return None
        return round(result, 2)
        
    except Exception as e:
        logger.error(f"ATR calculation error: {e}")
        return None

def calculate_rsi_series(df, period=14):
    """
    Calculate RSI series for the entire DataFrame.
    
    Args:
        df: DataFrame with OHLCV data
        period: Lookback period (default: 14)
    
    Returns:
        Series: RSI values for the entire DataFrame
    """
    try:
        close = df['close']
        delta = close.diff()
        
        # Separate gains and losses
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        # Calculate RS and RSI
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
        
    except Exception as e:
        logger.error(f"RSI series calculation error: {e}")
        return pd.Series([50] * len(df))

def calculate_rsi_current(df, period=14):
    """
    Get the latest RSI value.
    
    Args:
        df: DataFrame with OHLCV data
        period: Lookback period (default: 14)
    
    Returns:
        float: Latest RSI value
    """
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
    """
    Calculate Exponential Moving Average.
    
    Args:
        df: DataFrame with OHLCV data
        period: EMA period
    
    Returns:
        Series: EMA values
    """
    try:
        return df['close'].ewm(span=period, adjust=False).mean()
    except Exception as e:
        logger.error(f"EMA {period} calculation error: {e}")
        return pd.Series([0] * len(df))

def calculate_average_volume(df, period=20):
    """
    Calculate average volume over the specified period.
    
    Args:
        df: DataFrame with OHLCV data
        period: Lookback period (default: 20)
    
    Returns:
        float: Average volume
    """
    try:
        avg_volume = df['vol'].rolling(window=period).mean()
        return avg_volume.iloc[-1] if not pd.isna(avg_volume.iloc[-1]) else 0
    except Exception as e:
        logger.error(f"Average volume calculation error: {e}")
        return 0

def calculate_donchian_channel(df, period=52):
    """
    Calculate Donchian Channel.
    
    Args:
        df: DataFrame with OHLCV data
        period: Channel period (default: 52)
    
    Returns:
        tuple: (HH, LL) - Highest high and lowest low for the period
    """
    try:
        # Use last 'period' candles, excluding the current (latest) candle
        if len(df) <= period:
            # If not enough data, use available data
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
    """Calculate where price sits in the channel (0% = LL, 100% = HH)"""
    if HH == LL:
        return 50
    percentile = ((current_price - LL) / (HH - LL)) * 100
    return round(percentile, 2)

# ============================================
# 4. Signal Detection Functions
# ============================================

def check_buy_trend_continuation(df, symbol, last_alert):
    """
    Check for BUY TREND CONTINUATION signal.
    
    Conditions:
    - CHOP < 30 (strong trending market)
    - RSI between 60 and 75
    - RSI rising (current > previous)
    - Close breaks above Donchian High (confirmed breakout)
    - Volume > 1.2 × 20-period average volume
    - Price > EMA50
    - EMA50 > EMA200
    - Uses last CLOSED candle (no repainting)
    """
    try:
        # Extract the last closed candle (index -2) and previous (index -3)
        if len(df) < 3:
            return False, None, None
        
        # Get the last closed candle (excluding the most recent, possibly incomplete)
        last_closed = df.iloc[-2]
        prev_closed = df.iloc[-3]
        
        # Current price (for reference only, not used for signal)
        current_price = last_closed['close']
        
        # ============ CALCULATE INDICATORS ============
        # Donchian Channel (52 periods) - using last 52 closed candles
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)  # Exclude current candle
        if hh is None or ll is None:
            return False, None, None
        
        # Choppiness Index (14) - using closed candles
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        # RSI series for rising check
        rsi_series = calculate_rsi_series(df.iloc[:-1], 14)
        rsi_current = rsi_series.iloc[-1]
        rsi_previous = rsi_series.iloc[-2]
        
        # EMAs
        ema50_series = calculate_ema(df.iloc[:-1], 50)
        ema200_series = calculate_ema(df.iloc[:-1], 200)
        
        ema50_current = ema50_series.iloc[-1]
        ema200_current = ema200_series.iloc[-1]
        
        # Average Volume (20 periods)
        avg_volume = calculate_average_volume(df.iloc[:-1], 20)
        
        # ATR (14)
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        # ============ CHECK CONDITIONS ============
        # 1. CHOP < 30
        if chop_value >= 30:
            return False, None, None
        
        # 2. RSI between 60 and 75
        if not (60 <= rsi_current <= 75):
            return False, None, None
        
        # 3. RSI rising (current > previous)
        if rsi_current <= rsi_previous:
            return False, None, None
        
        # 4. Close breaks above Donchian High (confirmed breakout)
        if last_closed['close'] <= hh:
            return False, None, None
        
        # 5. Volume > 1.2 × 20-period average volume
        if last_closed['vol'] <= (avg_volume * 1.2):
            return False, None, None
        
        # 6. Price above EMA50
        if last_closed['close'] <= ema50_current:
            return False, None, None
        
        # 7. EMA50 above EMA200
        if ema50_current <= ema200_current:
            return False, None, None
        
        # All conditions met!
        logger.info(f"BUY TREND signal detected for {symbol} at ${last_closed['close']:.2f}")
        return True, last_closed['close'], atr_value
        
    except Exception as e:
        logger.error(f"Error checking BUY TREND for {symbol}: {e}")
        return False, None, None

def check_sell_trend_continuation(df, symbol, last_alert):
    """
    Check for SELL TREND CONTINUATION signal.
    
    Conditions:
    - CHOP < 30 (strong trending market)
    - RSI between 25 and 40
    - RSI falling (current < previous)
    - Close breaks below Donchian Low (confirmed breakout)
    - Volume > 1.2 × 20-period average volume
    - Price below EMA50
    - EMA50 below EMA200
    - Uses last CLOSED candle (no repainting)
    """
    try:
        # Extract the last closed candle (index -2) and previous (index -3)
        if len(df) < 3:
            return False, None, None
        
        # Get the last closed candle
        last_closed = df.iloc[-2]
        prev_closed = df.iloc[-3]
        
        # Current price
        current_price = last_closed['close']
        
        # ============ CALCULATE INDICATORS ============
        # Donchian Channel (52 periods)
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
        if hh is None or ll is None:
            return False, None, None
        
        # Choppiness Index (14)
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        # RSI series for falling check
        rsi_series = calculate_rsi_series(df.iloc[:-1], 14)
        rsi_current = rsi_series.iloc[-1]
        rsi_previous = rsi_series.iloc[-2]
        
        # EMAs
        ema50_series = calculate_ema(df.iloc[:-1], 50)
        ema200_series = calculate_ema(df.iloc[:-1], 200)
        
        ema50_current = ema50_series.iloc[-1]
        ema200_current = ema200_series.iloc[-1]
        
        # Average Volume (20 periods)
        avg_volume = calculate_average_volume(df.iloc[:-1], 20)
        
        # ATR (14)
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        # ============ CHECK CONDITIONS ============
        # 1. CHOP < 30
        if chop_value >= 30:
            return False, None, None
        
        # 2. RSI between 25 and 40
        if not (25 <= rsi_current <= 40):
            return False, None, None
        
        # 3. RSI falling (current < previous)
        if rsi_current >= rsi_previous:
            return False, None, None
        
        # 4. Close breaks below Donchian Low (confirmed breakout)
        if last_closed['close'] >= ll:
            return False, None, None
        
        # 5. Volume > 1.2 × 20-period average volume
        if last_closed['vol'] <= (avg_volume * 1.2):
            return False, None, None
        
        # 6. Price below EMA50
        if last_closed['close'] >= ema50_current:
            return False, None, None
        
        # 7. EMA50 below EMA200
        if ema50_current >= ema200_current:
            return False, None, None
        
        # All conditions met!
        logger.info(f"SELL TREND signal detected for {symbol} at ${last_closed['close']:.2f}")
        return True, last_closed['close'], atr_value
        
    except Exception as e:
        logger.error(f"Error checking SELL TREND for {symbol}: {e}")
        return False, None, None

def check_buy_reversal(df, symbol, last_alert):
    """Check for BUY REVERSAL signal (mean-reversion)."""
    try:
        if len(df) < 3:
            return False, None, None
        
        # Get the last closed candle
        last_closed = df.iloc[-2]
        
        # Donchian Channel
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
        if hh is None or ll is None:
            return False, None, None
        
        # Choppiness Index
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        # Channel percentile
        channel_percentile = calculate_channel_percentile(hh, ll, last_closed['close'])
        
        # ATR
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        # Check conditions
        if chop_value > 60 and channel_percentile <= 5:
            return True, last_closed['close'], atr_value
        else:
            return False, None, None
            
    except Exception as e:
        logger.error(f"Error checking BUY REVERSAL for {symbol}: {e}")
        return False, None, None

def check_sell_reversal(df, symbol, last_alert):
    """Check for SELL REVERSAL signal (mean-reversion)."""
    try:
        if len(df) < 3:
            return False, None, None
        
        # Get the last closed candle
        last_closed = df.iloc[-2]
        
        # Donchian Channel
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
        if hh is None or ll is None:
            return False, None, None
        
        # Choppiness Index
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        # Channel percentile
        channel_percentile = calculate_channel_percentile(hh, ll, last_closed['close'])
        
        # ATR
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        # Check conditions
        if chop_value > 60 and channel_percentile >= 95:
            return True, last_closed['close'], atr_value
        else:
            return False, None, None
            
    except Exception as e:
        logger.error(f"Error checking SELL REVERSAL for {symbol}: {e}")
        return False, None, None

# ============================================
# 5. Alert Sending Functions
# ============================================

def send_telegram_alert(message):
    """Send alert to Telegram with error handling."""
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
                logger.error(f"Telegram error: {response.status_code} - {response.text}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    return False

def send_alert(symbol, signal_type, price, atr_value, chop_value, rsi_value, channel_percentile, ema50=None, ema200=None):
    """
    Send formatted alert based on signal type.
    
    Args:
        symbol: Trading pair
        signal_type: 'BUY_TREND', 'SELL_TREND', 'BUY_REVERSAL', 'SELL_REVERSAL'
        price: Entry price
        atr_value: ATR value for risk management
        chop_value: Choppiness Index value
        rsi_value: RSI value
        channel_percentile: Channel position percentage
        ema50: EMA50 value (optional)
        ema200: EMA200 value (optional)
    """
    # Check for duplicate alerts with cooldown
    alert_key = f"{symbol}_{signal_type}"
    current_time = time.time()
    
    if alert_key in alert_cooldown:
        if current_time - alert_cooldown[alert_key] < 300:  # 5 minute cooldown
            logger.info(f"Skipping duplicate {signal_type} alert for {symbol} (cooldown)")
            return
    
    # Format message based on signal type
    if signal_type == "BUY_TREND":
        emoji = "🟢🟢🟢"
        title = "BUY TREND CONTINUATION"
        sl_mult = 2
        tp_mult = 3
        sl = price - (atr_value * sl_mult)
        tp = price + (atr_value * tp_mult)
        
        message = (
            f"{emoji} {title} {emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry Price: ${price:.2f}\n"
            f"RSI: {rsi_value:.2f}\n"
            f"Choppiness Index: {chop_value:.2f}\n"
            f"Channel Position: {channel_percentile:.2f}%\n"
            f"ATR: ${atr_value:.2f}\n"
            f"EMA50: ${ema50:.2f}\n"
            f"EMA200: ${ema200:.2f}\n\n"
            f"📊 Market Condition: STRONG TRENDING MARKET\n"
            f"⚠️ Strong uptrend detected, momentum expected to continue\n"
            f"🎯 BUY SIGNAL: Trend continuation\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${sl:.2f} (ATR×{sl_mult} below entry)\n"
            f"💰 Take Profit: ${tp:.2f} (ATR×{tp_mult} above entry)\n"
            f"📈 Risk/Reward: ~1:{tp_mult/sl_mult:.1f}"
        )
        
    elif signal_type == "SELL_TREND":
        emoji = "🔴🔴🔴"
        title = "SELL TREND CONTINUATION"
        sl_mult = 2
        tp_mult = 3
        sl = price + (atr_value * sl_mult)
        tp = price - (atr_value * tp_mult)
        
        message = (
            f"{emoji} {title} {emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry Price: ${price:.2f}\n"
            f"RSI: {rsi_value:.2f}\n"
            f"Choppiness Index: {chop_value:.2f}\n"
            f"Channel Position: {channel_percentile:.2f}%\n"
            f"ATR: ${atr_value:.2f}\n"
            f"EMA50: ${ema50:.2f}\n"
            f"EMA200: ${ema200:.2f}\n\n"
            f"📊 Market Condition: STRONG TRENDING MARKET\n"
            f"⚠️ Strong downtrend detected, momentum expected to continue\n"
            f"🎯 SELL SIGNAL: Trend continuation\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${sl:.2f} (ATR×{sl_mult} above entry)\n"
            f"💰 Take Profit: ${tp:.2f} (ATR×{tp_mult} below entry)\n"
            f"📈 Risk/Reward: ~1:{tp_mult/sl_mult:.1f}"
        )
        
    elif signal_type == "BUY_REVERSAL":
        emoji = "🟢🟢🟢"
        title = "BUY REVERSAL"
        sl_mult = 2
        tp_mult = 1.5
        sl = price - (atr_value * sl_mult)
        tp = price + (atr_value * tp_mult)
        
        message = (
            f"{emoji} {title} {emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry Price: ${price:.2f}\n"
            f"RSI: {rsi_value:.2f}\n"
            f"Choppiness Index: {chop_value:.2f}\n"
            f"Channel Position: {channel_percentile:.2f}%\n"
            f"ATR: ${atr_value:.2f}\n\n"
            f"📊 Market Condition: RANGING/CHOPPY MARKET\n"
            f"⚠️ Price in bottom 5% of channel in choppy market\n"
            f"🎯 BUY SIGNAL: Mean-reversion expected\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${sl:.2f} (ATR×{sl_mult} below entry)\n"
            f"💰 Take Profit: ${tp:.2f} (ATR×{tp_mult} above entry)\n"
            f"📈 Risk/Reward: ~1:{tp_mult/sl_mult:.1f}"
        )
        
    elif signal_type == "SELL_REVERSAL":
        emoji = "🔴🔴🔴"
        title = "SELL REVERSAL"
        sl_mult = 2
        tp_mult = 1.5
        sl = price + (atr_value * sl_mult)
        tp = price - (atr_value * tp_mult)
        
        message = (
            f"{emoji} {title} {emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry Price: ${price:.2f}\n"
            f"RSI: {rsi_value:.2f}\n"
            f"Choppiness Index: {chop_value:.2f}\n"
            f"Channel Position: {channel_percentile:.2f}%\n"
            f"ATR: ${atr_value:.2f}\n\n"
            f"📊 Market Condition: RANGING/CHOPPY MARKET\n"
            f"⚠️ Price in top 5% of channel in choppy market\n"
            f"🎯 SELL SIGNAL: Mean-reversion expected\n\n"
            f"📈 RISK MANAGEMENT:\n"
            f"🛑 Stop Loss: ${sl:.2f} (ATR×{sl_mult} above entry)\n"
            f"💰 Take Profit: ${tp:.2f} (ATR×{tp_mult} below entry)\n"
            f"📈 Risk/Reward: ~1:{tp_mult/sl_mult:.1f}"
        )
    else:
        logger.error(f"Unknown signal type: {signal_type}")
        return
    
    # Send alert
    if send_telegram_alert(message):
        # Update cooldown
        alert_cooldown[alert_key] = current_time
        last_alert[symbol] = signal_type
        logger.info(f"Alert sent: {signal_type} for {symbol}")
    else:
        logger.error(f"Failed to send alert for {symbol}")

# ============================================
# 6. Main Bot Loop
# ============================================

def run_bot():
    """Main bot execution loop."""
    logger.info("Bot loop started...")
    logger.info("Exchange: Binance (Global)")
    logger.info("===== ALERT CONDITIONS =====")
    logger.info("1️⃣ CHOP > 60 & Price in TOP 5% → SELL REVERSAL")
    logger.info("2️⃣ CHOP > 60 & Price in BOTTOM 5% → BUY REVERSAL")
    logger.info("3️⃣ CHOP < 30 & RSI 60-75 & Rising & Breakout & Volume & EMA → BUY TREND")
    logger.info("4️⃣ CHOP < 30 & RSI 25-40 & Falling & Breakout & Volume & EMA → SELL TREND")
    logger.info("============================")
    
    # Send startup message
    startup_message = (
        "✅ Bot Started on Binance\n\n"
        "📊 Donchian Channel (52) + Choppiness Index (14) + RSI (14)\n"
        "🎯 Alert Zone: Top 5% / Bottom 5% of Channel\n\n"
        "🔴 SELL REVERSAL:\n"
        "• CHOP > 60 + Top 5%\n"
        "• SL: ATR × 2 | TP: ATR × 1.5\n\n"
        "🟢 BUY REVERSAL:\n"
        "• CHOP > 60 + Bottom 5%\n"
        "• SL: ATR × 2 | TP: ATR × 1.5\n\n"
        "🟢 BUY TREND:\n"
        "• CHOP < 30 + RSI 60-75 + Rising + Breakout + Volume + EMA\n"
        "• SL: ATR × 2 | TP: ATR × 3\n\n"
        "🔴 SELL TREND:\n"
        "• CHOP < 30 + RSI 25-40 + Falling + Breakout + Volume + EMA\n"
        "• SL: ATR × 2 | TP: ATR × 3"
    )
    send_telegram_alert(startup_message)
    
    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    logger.debug(f"Skipping unavailable symbol: {symbol}")
                    continue
                
                # Get enough candles for calculations
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=200  # Increased for EMA200 calculation
                )
                
                if len(ohlcv) < 120:  # Need enough for all indicators
                    logger.debug(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # ============ CHECK FOR SIGNALS ============
                
                # Check BUY TREND
                buy_trend, price, atr = check_buy_trend_continuation(df, symbol, last_alert)
                if buy_trend and atr is not None:
                    # Get additional indicator values for the alert
                    hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
                    chop = calculate_choppiness_index(df.iloc[:-1], 14)
                    rsi = calculate_rsi_current(df.iloc[:-1], 14)
                    percent = calculate_channel_percentile(hh, ll, price)
                    ema50 = calculate_ema(df.iloc[:-1], 50).iloc[-1]
                    ema200 = calculate_ema(df.iloc[:-1], 200).iloc[-1]
                    
                    send_alert(symbol, "BUY_TREND", price, atr, chop, rsi, percent, ema50, ema200)
                    continue  # Skip other signals for this symbol
                
                # Check SELL TREND
                sell_trend, price, atr = check_sell_trend_continuation(df, symbol, last_alert)
                if sell_trend and atr is not None:
                    hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
                    chop = calculate_choppiness_index(df.iloc[:-1], 14)
                    rsi = calculate_rsi_current(df.iloc[:-1], 14)
                    percent = calculate_channel_percentile(hh, ll, price)
                    ema50 = calculate_ema(df.iloc[:-1], 50).iloc[-1]
                    ema200 = calculate_ema(df.iloc[:-1], 200).iloc[-1]
                    
                    send_alert(symbol, "SELL_TREND", price, atr, chop, rsi, percent, ema50, ema200)
                    continue
                
                # Check BUY REVERSAL
                buy_reversal, price, atr = check_buy_reversal(df, symbol, last_alert)
                if buy_reversal and atr is not None:
                    hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
                    chop = calculate_choppiness_index(df.iloc[:-1], 14)
                    rsi = calculate_rsi_current(df.iloc[:-1], 14)
                    percent = calculate_channel_percentile(hh, ll, price)
                    
                    send_alert(symbol, "BUY_REVERSAL", price, atr, chop, rsi, percent)
                    continue
                
                # Check SELL REVERSAL
                sell_reversal, price, atr = check_sell_reversal(df, symbol, last_alert)
                if sell_reversal and atr is not None:
                    hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
                    chop = calculate_choppiness_index(df.iloc[:-1], 14)
                    rsi = calculate_rsi_current(df.iloc[:-1], 14)
                    percent = calculate_channel_percentile(hh, ll, price)
                    
                    send_alert(symbol, "SELL_REVERSAL", price, atr, chop, rsi, percent)
                    continue
                
                # Reset alert if no signal (optional)
                # if symbol in last_alert and last_alert[symbol] is not None:
                #     if time.time() - alert_cooldown.get(f"{symbol}_{last_alert[symbol]}", 0) > 3600:
                #         last_alert[symbol] = None
                
            except ccxt.RateLimitExceeded:
                logger.warning(f"Rate limit exceeded for {symbol}, waiting...")
                time.sleep(5)
            except ccxt.NetworkError as e:
                logger.error(f"Network error for {symbol}: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Error checking {symbol}: {e}")
                time.sleep(2)
        
        # Check every 30 seconds (reduced from 20 to avoid rate limits)
        time.sleep(30)

# ============================================
# 7. Start Bot and Flask Server
# ============================================

# Start bot in background thread
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)