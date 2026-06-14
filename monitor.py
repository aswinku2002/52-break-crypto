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

SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ADA/USDT',
    'PAXG/USDT', 'XAUT/USDT', 'BEAT/USDT', 'H/USDT',
    'AIO/USDT', 'XRP/USDT', 'LAB/USDT', 'ZEC/USDT',
    'SKYAI/USDT', 'SLVON/USDT', 'DOGE/USDT', 'SIREN/USDT',
    'BNB/USDT', 'LTC/USDT', 'PIPPIN/USDT', 'LINK/USDT',
    'XMR/USDT', 'AIN/USDT', '1000SATS/USDT',
    'PENGU/USDT', 'ARC/USDT', 'DOGS/USDT'
]

EXCHANGE = ccxt.binance({
    'enableRateLimit': True,
})

EXCHANGE.load_markets()

# Prevent repeated alerts
last_combined_alert = {}

def calculate_adx(df, period=14):
    """Manual ADX calculation - no pandas-ta needed"""
    high = df['high']
    low = df['low']
    close = df['close']
    
    # True Range
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Directional Movement
    up_move = high - high.shift()
    down_move = low.shift() - low
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    # Smooth with Wilder's moving average (period)
    atr = tr.rolling(window=period).mean()
    plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr)
    
    # DX and ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(window=period).mean()
    
    result = adx.iloc[-1]
    return result if not pd.isna(result) else 0

def calculate_choppiness_index(df, period=14):
    """Calculate Choppiness Index"""
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
    
    # Choppiness Index formula
    choppiness = 100 * np.log10(sum_tr / (highest_high - lowest_low)) / np.log10(period)
    
    result = choppiness.iloc[-1]
    return result if not pd.isna(result) else 50

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

def run_bot():
    print("Bot loop started...")

    # Startup message
    send_alert("✅ Multi-Indicator Bot Started (DC52 + ADX14 + CHOP14)")

    while True:
        for symbol in SYMBOLS:
            try:
                # Skip symbols not available on Binance
                if symbol not in EXCHANGE.markets:
                    print(f"Skipping unavailable symbol: {symbol}")
                    continue

                # Get enough candles for all indicators
                ohlcv = EXCHANGE.fetch_ohlcv(
                    symbol,
                    timeframe='15m',
                    limit=70
                )

                if len(ohlcv) < 70:
                    print(f"Insufficient data for {symbol}, only {len(ohlcv)} candles")
                    continue

                df = pd.DataFrame(
                    ohlcv,
                    columns=['ts', 'open', 'high', 'low', 'close', 'vol']
                )

                # ============ DONCHIAN CHANNEL (52 candles) ============
                HH = df['high'][-53:-1].max()
                LL = df['low'][-53:-1].min()
                
                bullish_level = HH - ((HH - LL) * 0.025)
                bearish_level = LL + ((HH - LL) * 0.025)
                
                # ============ ADX 14 ============
                adx_value = calculate_adx(df.tail(60), period=14)
                
                # ============ CHOPPINESS INDEX 14 ============
                chop_value = calculate_choppiness_index(df.tail(60), period=14)
                
                # Current market price
                ticker = EXCHANGE.fetch_ticker(symbol)
                current_price = ticker['last']
                
                # Initialize alert tracking
                if symbol not in last_combined_alert:
                    last_combined_alert[symbol] = None
                
                # ============ CHECK FOR BULLISH TREND (All conditions together) ============
                # Condition 1: Price in bullish zone (top 5% of DC52)
                # Condition 2: ADX > 25 (strong trend)
                # Condition 3: CHOP < 40 (non-choppy/trending market)
                
                if current_price >= bullish_level and adx_value > 25 and chop_value < 40:
                    if last_combined_alert[symbol] != "BULLISH_TREND":
                        message = (
                            f"🟢🟢🟢 BULLISH TREND CONFIRMATION 🟢🟢🟢\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.4f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • Price in BULLISH ZONE (top 5%)\n"
                            f"  • HH: ${HH:.4f}\n"
                            f"  • LL: ${LL:.4f}\n"
                            f"  • Level: ${bullish_level:.4f}\n\n"
                            f"📈 ADX (14): {adx_value:.2f} (>25 ✅)\n"
                            f"  → Strong Trend Detected\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value:.2f} (<40 ✅)\n"
                            f"  → Non-Choppy/Trending Market\n\n"
                            f"⚡ VERDICT: STRONG BULLISH ALIGNMENT - All indicators confirm uptrend!"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🟢 BULLISH TREND ALERT: ADX={adx_value:.2f}, CHOP={chop_value:.2f}, Price in bullish zone")
                        last_combined_alert[symbol] = "BULLISH_TREND"
                
                # ============ CHECK FOR BEARISH TREND (All conditions together) ============
                # Condition 1: Price in bearish zone (bottom 5% of DC52)
                # Condition 2: ADX > 25 (strong trend)
                # Condition 3: CHOP < 40 (non-choppy/trending market)
                
                elif current_price <= bearish_level and adx_value > 25 and chop_value < 40:
                    if last_combined_alert[symbol] != "BEARISH_TREND":
                        message = (
                            f"🔴🔴🔴 BEARISH TREND CONFIRMATION 🔴🔴🔴\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.4f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • Price in BEARISH ZONE (bottom 5%)\n"
                            f"  • HH: ${HH:.4f}\n"
                            f"  • LL: ${LL:.4f}\n"
                            f"  • Level: ${bearish_level:.4f}\n\n"
                            f"📈 ADX (14): {adx_value:.2f} (>25 ✅)\n"
                            f"  → Strong Trend Detected\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value:.2f} (<40 ✅)\n"
                            f"  → Non-Choppy/Trending Market\n\n"
                            f"⚡ VERDICT: STRONG BEARISH ALIGNMENT - All indicators confirm downtrend!"
                        )
                        send_alert(message)
                        print(f"{symbol} - 🔴 BEARISH TREND ALERT: ADX={adx_value:.2f}, CHOP={chop_value:.2f}, Price in bearish zone")
                        last_combined_alert[symbol] = "BEARISH_TREND"
                
                # ============ CHECK FOR POTENTIAL REVERSAL (All conditions together) ============
                # Condition 1: Price anywhere (no DC zone restriction)
                # Condition 2: ADX < 25 (weak/no trend)
                # Condition 3: CHOP > 60 (choppy market - potential reversal coming)
                
                elif adx_value < 25 and chop_value > 60:
                    if last_combined_alert[symbol] != "REVERSAL":
                        # Determine if price is near extremes for extra context
                        price_position = "neutral"
                        if current_price >= bullish_level:
                            price_position = "near resistance (bullish zone)"
                        elif current_price <= bearish_level:
                            price_position = "near support (bearish zone)"
                        else:
                            price_position = "middle of channel"
                        
                        message = (
                            f"⚠️⚠️⚠️ POTENTIAL REVERSAL ALERT ⚠️⚠️⚠️\n\n"
                            f"Symbol: {symbol}\n"
                            f"Price: ${current_price:.4f}\n\n"
                            f"📊 DONCHIAN CHANNEL (52):\n"
                            f"  • Price position: {price_position}\n"
                            f"  • HH: ${HH:.4f}\n"
                            f"  • LL: ${LL:.4f}\n\n"
                            f"📉 ADX (14): {adx_value:.2f} (<25 ✅)\n"
                            f"  → Weak/No Trend (Directional strength very low)\n\n"
                            f"🔄 CHOPPINESS INDEX (14): {chop_value:.2f} (>60 ✅)\n"
                            f"  → Choppy/Ranging Market\n\n"
                            f"⚡ VERDICT: Market is choppy with no clear trend!\n"
                            f"  → Potential reversal or breakout imminent\n"
                            f"  → Wait for trend confirmation before entering trades"
                        )
                        send_alert(message)
                        print(f"{symbol} - ⚠️ REVERSAL ALERT: ADX={adx_value:.2f}, CHOP={chop_value:.2f}, Price={price_position}")
                        last_combined_alert[symbol] = "REVERSAL"
                
                # Reset alert when conditions no longer met
                else:
                    if last_combined_alert[symbol] is not None:
                        print(f"{symbol} - Alert reset: {last_combined_alert[symbol]} condition ended")
                        last_combined_alert[symbol] = None
                
                # Optional: Print current indicators for monitoring
                print(f"{symbol} - ADX: {adx_value:.2f}, CHOP: {chop_value:.2f}, Price: ${current_price:.4f}")

            except Exception as e:
                print(f"Error checking {symbol}: {e}")

        # Check every 5 seconds
        time.sleep(5)

# 3. Start bot in background
threading.Thread(target=run_bot, daemon=True).start()

# 4. Start Flask web server
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
