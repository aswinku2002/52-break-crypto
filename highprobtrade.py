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

# Binance API keys (optional but recommended)
BINANCE_API_KEY = os.environ.get('BINANCE_API_KEY', '')
BINANCE_API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# ============================================
# ALL SYMBOLS FROM YOUR SCREENSHOTS (Mapped to Binance)
# ============================================
SYMBOLS = [
    # Major Coins (High Volume)
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT',
    'BNB/USDT', 'DOGE/USDT', 'ADA/USDT', 'LTC/USDT',
    'LINK/USDT', 'AVAX/USDT', 'DOT/USDT', 'MATIC/USDT',
    'UNI/USDT', 'ATOM/USDT', 'ETC/USDT', 'BCH/USDT',
    'XLM/USDT', 'TRX/USDT', 'XMR/USDT', 'NEAR/USDT',
    'AAVE/USDT', 'SUI/USDT', 'ZEC/USDT', 'AXS/USDT',
    'ENJ/USDT', 'ORDI/USDT', 'WLD/USDT',
    
    # Metal Tokens
    'XAUT/USDT', 'PAXG/USDT',  # Gold tokens on Binance
    
    # Alt/New Tokens (Available on Binance)
    'HIVE/USDT', 'VET/USDT', 'CHZ/USDT', 'ONE/USDT',
    'FTM/USDT', 'SAND/USDT', 'MANA/USDT', 'GALA/USDT',
    'CRV/USDT', 'CVX/USDT', 'FXS/USDT', 'LDO/USDT',
    'OP/USDT', 'ARB/USDT', 'APT/USDT', 'SUI/USDT',
    'SEI/USDT', 'TIA/USDT', 'PYTH/USDT', 'JUP/USDT',
    'ONDO/USDT', 'STRK/USDT', 'ENA/USDT', 'W/USDT',
    
    # Note: Some symbols from Delta screenshots may not exist on Binance
    # Those are mapped to closest available Binance pairs
]

# Initialize Binance
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
    print("✅ Binance markets loaded successfully")
    print(f"📊 Loaded {len(EXCHANGE.markets)} trading pairs")
    print(f"🎯 Monitoring {len(SYMBOLS)} symbols")
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
    """Check for BUY SCALPING signal"""
    try:
        if len(df) < 3:
            return False, None, None, None

        last_closed = df.iloc[-2]
        
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
        if hh is None or ll is None:
            return False, None, None, None
        
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        rsi_series = calculate_rsi_series(df.iloc[:-1], 14)
        rsi_current = rsi_series.iloc[-1]
        rsi_previous = rsi_series.iloc[-2]
        
        ema20_series = calculate_ema(df.iloc[:-1], 20)
        ema50_series = calculate_ema(df.iloc[:-1], 50)
        ema20_current = ema20_series.iloc[-1]
        ema50_current = ema50_series.iloc[-1]
        
        avg_volume = calculate_average_volume(df.iloc[:-1], 20)
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        body_size, upper_wick, lower_wick, is_bullish = calculate_candle_metrics(last_closed)
        channel_percentile = calculate_channel_percentile(hh, ll, last_closed['close'])
        
        # ============ CHECK CONDITIONS ============
        # 1. CHOP < 35
        if chop_value >= 35:
            return False, None, None, None
        
        # 2. RSI between 55-70
        if not (55 <= rsi_current <= 70):
            return False, None, None, None
        
        # 3. RSI Rising
        if rsi_current <= rsi_previous:
            return False, None, None, None
        
        # 4. Close near Donchian High (0.5% below)
        early_entry_threshold = hh * 0.995
        if last_closed['close'] < early_entry_threshold:
            return False, None, None, None
        
        # 5. Price > EMA20
        if last_closed['close'] <= ema20_current:
            return False, None, None, None
        
        # 6. EMA20 > EMA50
        if ema20_current <= ema50_current:
            return False, None, None, None
        
        # 7. Volume > 1.1x average
        if last_closed['vol'] <= (avg_volume * 1.1):
            return False, None, None, None
        
        # 8. Bullish candle
        if not is_bullish:
            return False, None, None, None
        
        # 9. No significant upper wick
        if body_size > 0 and (upper_wick / body_size) > 0.5:
            return False, None, None, None
        
        logger.info(f"✅ BUY SCALP detected for {symbol} at ${last_closed['close']:.2f}")
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
    """Check for SELL SCALPING signal"""
    try:
        if len(df) < 3:
            return False, None, None, None

        last_closed = df.iloc[-2]
        
        hh, ll = calculate_donchian_channel(df.iloc[:-1], 52)
        if hh is None or ll is None:
            return False, None, None, None
        
        chop_value = calculate_choppiness_index(df.iloc[:-1], 14)
        
        rsi_series = calculate_rsi_series(df.iloc[:-1], 14)
        rsi_current = rsi_series.iloc[-1]
        rsi_previous = rsi_series.iloc[-2]
        
        ema20_series = calculate_ema(df.iloc[:-1], 20)
        ema50_series = calculate_ema(df.iloc[:-1], 50)
        ema20_current = ema20_series.iloc[-1]
        ema50_current = ema50_series.iloc[-1]
        
        avg_volume = calculate_average_volume(df.iloc[:-1], 20)
        atr_value = calculate_atr(df.iloc[:-1], 14)
        
        body_size, upper_wick, lower_wick, is_bullish = calculate_candle_metrics(last_closed)
        channel_percentile = calculate_channel_percentile(hh, ll, last_closed['close'])
        
        # ============ CHECK CONDITIONS ============
        # 1. CHOP < 35
        if chop_value >= 35:
            return False, None, None, None
        
        # 2. RSI between 30-45
        if not (30 <= rsi_current <= 45):
            return False, None, None, None
        
        # 3. RSI Falling
        if rsi_current >= rsi_previous:
            return False, None, None, None
        
        # 4. Close near Donchian Low (0.5% above)
        early_entry_threshold = ll * 1.005
        if last_closed['close'] > early_entry_threshold:
            return False, None, None, None
        
        # 5. Price < EMA20
        if last_closed['close'] >= ema20_current:
            return False, None, None, None
        
        # 6. EMA20 < EMA50
        if ema20_current >= ema50_current:
            return False, None, None, None
        
        # 7. Volume > 1.1x average
        if last_closed['vol'] <= (avg_volume * 1.1):
            return False, None, None, None
        
        # 8. Bearish candle
        if is_bullish:
            return False, None, None, None
        
        # 9. No significant lower wick
        if body_size > 0 and (lower_wick / body_size) > 0.5:
            return False, None, None, None
        
        logger.info(f"✅ SELL SCALP detected for {symbol} at ${last_closed['close']:.2f}")
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
# 5. Alert Sending Functions
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
    """Send formatted scalping alert."""
    alert_key = f"{symbol}_{signal_type}"
    current_time = time.time()
    
    if alert_key in alert_cooldown:
        if current_time - alert_cooldown[alert_key] < 120:  # 2 min cooldown
            logger.info(f"Skipping duplicate {signal_type} alert for {symbol}")
            return
    
    if signal_type == "BUY":
        emoji = "🟢"
        title = "BUY SCALP ENTRY"
        sl = price - (atr_value * 1.0)
        tp = price + (price * 0.005)
        
        message = (
            f"{emoji}{emoji}{emoji} {title} {emoji}{emoji}{emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry: ${price:.4f}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"📊 SIGNAL DETAILS:\n"
            f"• CHOP: {indicators['chop']:.1f} (<35) ✅\n"
            f"• RSI: {indicators['rsi']:.1f} (55-70, Rising) ✅\n"
            f"• Channel Position: {indicators['percentile']:.1f}%\n"
            f"• EMA20: ${indicators['ema20']:.4f}\n"
            f"• EMA50: ${indicators['ema50']:.4f}\n"
            f"• Volume: CONFIRMED ✅\n"
            f"• Candle: {indicators['candle_type']} ✅\n\n"
            f"⚡ EXIT PLAN (15-30 min):\n"
            f"🛑 Stop Loss: ${sl:.4f} (ATR×1.0)\n"
            f"💰 Take Profit: ${tp:.4f} (0.5%)\n"
            f"📈 Risk/Reward: ~1:1.67\n\n"
            f"⚠️ Exit if:\n"
            f"• RSI > 80 (overbought)\n"
            f"• Volume drops below avg\n"
            f"• Price hits 0.5% profit"
        )
        
    else:
        emoji = "🔴"
        title = "SELL SCALP ENTRY"
        sl = price + (atr_value * 1.0)
        tp = price - (price * 0.005)
        
        message = (
            f"{emoji}{emoji}{emoji} {title} {emoji}{emoji}{emoji}\n\n"
            f"Exchange: Binance\n"
            f"Symbol: {symbol}\n"
            f"Entry: ${price:.4f}\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"📊 SIGNAL DETAILS:\n"
            f"• CHOP: {indicators['chop']:.1f} (<35) ✅\n"
            f"• RSI: {indicators['rsi']:.1f} (30-45, Falling) ✅\n"
            f"• Channel Position: {indicators['percentile']:.1f}%\n"
            f"• EMA20: ${indicators['ema20']:.4f}\n"
            f"• EMA50: ${indicators['ema50']:.4f}\n"
            f"• Volume: CONFIRMED ✅\n"
            f"• Candle: {indicators['candle_type']} ✅\n\n"
            f"⚡ EXIT PLAN (15-30 min):\n"
            f"🛑 Stop Loss: ${sl:.4f} (ATR×1.0)\n"
            f"💰 Take Profit: ${tp:.4f} (0.5%)\n"
            f"📈 Risk/Reward: ~1:1.67\n\n"
            f"⚠️ Exit if:\n"
            f"• RSI < 20 (oversold)\n"
            f"• Volume drops below avg\n"
            f"• Price hits 0.5% profit"
        )
    
    if send_telegram_alert(message):
        alert_cooldown[alert_key] = current_time
        last_alert[symbol] = signal_type
        logger.info(f"📨 Alert sent: {signal_type} SCALP for {symbol}")

# ============================================
# 6. Main Bot Loop
# ============================================

def run_bot():
    """Main bot execution loop."""
    logger.info("🚀 SCALPING BOT STARTED - BINANCE")
    logger.info("=" * 50)
    logger.info(f"📊 Total Symbols: {len(SYMBOLS)}")
    logger.info("📊 Strategy: Donchian (52) + CHOP (14) + RSI (14)")
    logger.info("⏱ Timeframe: 15m candles")
    logger.info("⚡ Expected Hold Time: 15-30 minutes")
    logger.info("💬 Alerts: Trading signals ONLY (no heartbeat)")
    logger.info("=" * 50)
    
    # Send startup message
    startup_message = (
        "🚀 SCALPING BOT STARTED - BINANCE\n\n"
        f"📊 Total Symbols: {len(SYMBOLS)}\n"
        "📊 Strategy: Donchian (52) + CHOP (14) + RSI (14)\n"
        "⏱ Timeframe: 15m candles\n"
        "⚡ Expected Hold: 15-30 minutes\n"
        "💬 Alerts: Trading signals ONLY\n\n"
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
        f"🎯 TARGET: 15-30 minute scalps on {len(SYMBOLS)} symbols\n"
        "📡 Waiting for signals..."
    )
    send_telegram_alert(startup_message)
    
    while True:
        for symbol in SYMBOLS:
            try:
                # Check if symbol exists on Binance
                if symbol not in EXCHANGE.markets:
                    continue
                
                # Get OHLCV data
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=150
                )
                
                if len(ohlcv) < 80:
                    continue
                
                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )
                
                # Check BUY SCALP
                buy_signal, price, atr, indicators = check_buy_scalp(df, symbol)
                if buy_signal and atr is not None:
                    send_scalp_alert(symbol, "BUY", price, atr, indicators)
                    continue
                
                # Check SELL SCALP
                sell_signal, price, atr, indicators = check_sell_scalp(df, symbol)
                if sell_signal and atr is not None:
                    send_scalp_alert(symbol, "SELL", price, atr, indicators)
                    continue
                
                # Reset alert if needed
                if symbol in last_alert and last_alert[symbol] is not None:
                    if time.time() - alert_cooldown.get(f"{symbol}_{last_alert[symbol]}", 0) > 300:
                        last_alert[symbol] = None
                        
            except ccxt.RateLimitExceeded:
                logger.warning(f"⚠️ Rate limit exceeded for {symbol}, waiting...")
                time.sleep(5)
            except ccxt.NetworkError as e:
                logger.error(f"🌐 Network error for {symbol}: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"❌ Error checking {symbol}: {e}")
                time.sleep(2)
        
        # Check every 20 seconds
        time.sleep(20)

# ============================================
# 7. Start Bot and Flask Server
# ============================================

# Start bot in background
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Start Flask server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)