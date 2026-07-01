#!/usr/bin/env python3
"""
Cryptocurrency Signal Bot - Aroon Indicator Based
Monitors multiple exchanges and symbols for Aroon signals
"""

import asyncio
import logging
import time
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import requests
import numpy as np
from flask import Flask, jsonify
import ccxt.async_support as ccxt_async
from telegram import Bot
from telegram.error import TelegramError
import asyncio
from concurrent.futures import ThreadPoolExecutor

# ============ CONFIGURATION ============

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Exchange Configuration
EXCHANGES = {
    "binance": {
        "class": ccxt_async.binance,
        "timeframe": "3m",
        "limit": 200,
        "rate_limit": 1200,
    },
    "kraken": {
        "class": ccxt_async.kraken,
        "timeframe": "3m",
        "limit": 200,
        "rate_limit": 1000,
    },
    "coinbase": {
        "class": ccxt_async.coinbase,
        "timeframe": "3m",
        "limit": 200,
        "rate_limit": 1000,
    },
    "kucoin": {
        "class": ccxt_async.kucoin,
        "timeframe": "3m",
        "limit": 200,
        "rate_limit": 1000,
    },
    "bybit": {
        "class": ccxt_async.bybit,
        "timeframe": "3m",
        "limit": 200,
        "rate_limit": 1000,
    }
}

# Symbol Configuration - High priority symbols
SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "MATIC/USDT", "DOT/USDT",
    "LINK/USDT", "UNI/USDT", "ATOM/USDT", "LTC/USDT", "ETC/USDT",
    "FIL/USDT", "APT/USDT", "ARB/USDT", "OP/USDT", "VET/USDT"
]

# Aroon Configuration
AROON_PERIOD = 52  # Period for Aroon calculation
SIGNAL_THRESHOLD = 90  # Signal threshold (>= 90)
SIGNAL_STRONG = 100  # Strong signal threshold

# Flask Configuration
FLASK_PORT = int(os.getenv("PORT", 5000))

# Cache Configuration
CACHE_DURATION = 300  # 5 minutes in seconds
CACHE_CLEANUP_INTERVAL = 3600  # 1 hour

# Signal Tracking
SIGNAL_TRACKING_DURATION = 3600  # 1 hour

# ============ LOGGING SETUP ============

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('aroon_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============ TELEGRAM BOT ============

class TelegramNotifier:
    """Handles Telegram notifications"""
    
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)
        
        if self.enabled:
            logger.info("Telegram notifications enabled")
        else:
            logger.warning("Telegram notifications disabled - missing credentials")
    
    async def send_signal(self, symbol: str, exchange: str, price: float, 
                          aroon_up: float, aroon_down: float, 
                          signal_type: str, cycle_number: int) -> bool:
        """Send signal alert via Telegram"""
        if not self.enabled:
            return False
        
        try:
            # Determine signal strength
            if signal_type == "BUY":
                aroon_value = aroon_up
            else:
                aroon_value = aroon_down
            
            strength = "STRONG" if aroon_value >= SIGNAL_STRONG else "NORMAL"
            price_str = f"${price:,.2f}" if price >= 1 else f"${price:.8f}"
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Build message
            message = f"""
🚨 {signal_type} SIGNAL

Symbol: {symbol}
Exchange: {exchange.title()}
Price: {price_str}
Aroon Up ({AROON_PERIOD}): {aroon_up:.2f}
Aroon Down ({AROON_PERIOD}): {aroon_down:.2f}
Strength: {strength}
Time: {current_time}
Cycle: #{cycle_number}
            """.strip()
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML'
            )
            
            logger.info(f"Telegram signal sent: {symbol} {signal_type} (Strength: {strength})")
            return True
            
        except TelegramError as e:
            logger.error(f"Telegram error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {str(e)}")
            return False

# ============ AROON INDICATOR ============

def calculate_aroon(highs: np.ndarray, lows: np.ndarray, period: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Calculate Aroon Up and Aroon Down indicators
    
    Args:
        highs: Array of high prices
        lows: Array of low prices
        period: Lookback period
        
    Returns:
        Tuple of (aroon_up, aroon_down) arrays
    """
    if len(highs) < period + 1:
        return np.array([]), np.array([])
    
    aroon_up = np.zeros(len(highs))
    aroon_down = np.zeros(len(highs))
    
    for i in range(period, len(highs)):
        # Look back over the period
        window_highs = highs[i-period+1:i+1]
        window_lows = lows[i-period+1:i+1]
        
        # Find position of highest high (most recent first)
        high_max_idx = np.argmax(window_highs[::-1])  # Reversed to find most recent
        high_position = period - high_max_idx
        
        # Find position of lowest low (most recent first)
        low_min_idx = np.argmin(window_lows[::-1])  # Reversed to find most recent
        low_position = period - low_min_idx
        
        # Calculate Aroon values
        aroon_up[i] = ((period - high_position) / period) * 100
        aroon_down[i] = ((period - low_position) / period) * 100
    
    return aroon_up, aroon_down

# ============ SIGNAL TRACKER ============

class SignalTracker:
    """Tracks active signals to prevent duplicates"""
    
    def __init__(self):
        self.active_signals = {}  # key: (symbol, exchange) -> {'type': str, 'timestamp': float, 'cycle': int}
        self.signal_history = defaultdict(list)  # For tracking cycles
        self.cycle_counter = 0
    
    def get_cycle_number(self, symbol: str, exchange: str, signal_type: str) -> int:
        """Get the cycle number for a new signal"""
        key = f"{symbol}_{exchange}_{signal_type}"
        self.signal_history[key].append(datetime.now())
        return len(self.signal_history[key])
    
    def is_duplicate(self, symbol: str, exchange: str, signal_type: str) -> bool:
        """Check if signal is a duplicate"""
        key = (symbol, exchange)
        
        if key not in self.active_signals:
            return False
        
        active = self.active_signals[key]
        # Only prevent duplicates of the same type
        if active['type'] == signal_type:
            # Check if signal is still active
            elapsed = time.time() - active['timestamp']
            if elapsed < SIGNAL_TRACKING_DURATION:
                return True
            else:
                # Signal expired
                self.clear_signal(symbol, exchange)
                return False
        
        return False
    
    def add_signal(self, symbol: str, exchange: str, signal_type: str) -> int:
        """Add a new signal to tracker"""
        key = (symbol, exchange)
        cycle = self.get_cycle_number(symbol, exchange, signal_type)
        
        self.active_signals[key] = {
            'type': signal_type,
            'timestamp': time.time(),
            'cycle': cycle
        }
        
        return cycle
    
    def clear_signal(self, symbol: str, exchange: str) -> None:
        """Clear an active signal"""
        key = (symbol, exchange)
        if key in self.active_signals:
            del self.active_signals[key]
    
    def get_active_signal(self, symbol: str, exchange: str) -> Optional[Dict]:
        """Get the active signal for a symbol/exchange"""
        key = (symbol, exchange)
        return self.active_signals.get(key)
    
    def cleanup_old_signals(self):
        """Remove expired signals"""
        current_time = time.time()
        expired = []
        
        for key, signal in self.active_signals.items():
            if current_time - signal['timestamp'] >= SIGNAL_TRACKING_DURATION:
                expired.append(key)
        
        for key in expired:
            del self.active_signals[key]
            logger.debug(f"Removed expired signal for {key}")

# ============ EXCHANGE HANDLER ============

class ExchangeHandler:
    """Handles exchange interactions and candle fetching"""
    
    def __init__(self, exchange_name: str, config: Dict):
        self.name = exchange_name
        self.config = config
        self.exchange = None
        self.cache = {}
        self.last_fetch_time = {}
    
    async def initialize(self):
        """Initialize the exchange connection"""
        try:
            self.exchange = self.config['class']({
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot'
                }
            })
            await self.exchange.load_markets()
            logger.info(f"Initialized exchange: {self.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize {self.name}: {str(e)}")
            return False
    
    async def get_ohlcv(self, symbol: str) -> Optional[np.ndarray]:
        """Fetch OHLCV data for a symbol"""
        try:
            cache_key = f"{symbol}"
            current_time = time.time()
            
            # Check cache
            if cache_key in self.cache:
                cache_time, data = self.cache[cache_key]
                if current_time - cache_time < CACHE_DURATION:
                    return data
            
            # Fetch new data
            ohlcv = await self.exchange.fetch_ohlcv(
                symbol,
                timeframe=self.config['timeframe'],
                limit=self.config['limit']
            )
            
            if not ohlcv:
                logger.warning(f"No OHLCV data for {symbol} on {self.name}")
                return None
            
            # Convert to numpy array
            data = np.array(ohlcv)
            
            # Update cache
            self.cache[cache_key] = (current_time, data)
            self.last_fetch_time[cache_key] = current_time
            
            return data
            
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} on {self.name}: {str(e)}")
            return None
    
    async def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return ticker['last']
        except Exception as e:
            logger.error(f"Error fetching price for {symbol} on {self.name}: {str(e)}")
            return None
    
    async def close(self):
        """Close the exchange connection"""
        if self.exchange:
            await self.exchange.close()

# ============ SIGNAL BOT ============

class AroonSignalBot:
    """Main bot class for Aroon-based signals"""
    
    def __init__(self):
        self.exchanges = {}
        self.signal_tracker = SignalTracker()
        self.telegram = None
        self.running = False
        self.bot_thread = None
        self.loop = None
        self.executor = ThreadPoolExecutor(max_workers=10)
        
        # Initialize Telegram
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            self.telegram = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        
        # Initialize Flask app
        self.app = Flask(__name__)
        self.setup_flask_routes()
    
    def setup_flask_routes(self):
        """Setup Flask routes"""
        
        @self.app.route('/health', methods=['GET'])
        def health():
            """Health check endpoint"""
            return jsonify({
                'status': 'running' if self.running else 'stopped',
                'timestamp': datetime.now().isoformat(),
                'active_signals': len(self.signal_tracker.active_signals),
                'exchanges': list(self.exchanges.keys()),
                'symbols': len(SYMBOLS)
            })
        
        @self.app.route('/signals', methods=['GET'])
        def get_signals():
            """Get current active signals"""
            signals = []
            for (symbol, exchange), signal in self.signal_tracker.active_signals.items():
                signals.append({
                    'symbol': symbol,
                    'exchange': exchange,
                    'signal': signal['type'],
                    'timestamp': datetime.fromtimestamp(signal['timestamp']).isoformat(),
                    'cycle': signal['cycle']
                })
            return jsonify({
                'count': len(signals),
                'signals': signals
            })
    
    async def initialize_exchanges(self):
        """Initialize all exchange handlers"""
        for name, config in EXCHANGES.items():
            handler = ExchangeHandler(name, config)
            success = await handler.initialize()
            if success:
                self.exchanges[name] = handler
                logger.info(f"Added exchange: {name}")
            else:
                logger.warning(f"Skipping exchange: {name}")
    
    def calculate_aroon_signal(self, ohlcv: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """
        Calculate Aroon and determine signal
        
        Returns:
            Tuple of (aroon_up, aroon_down, signal_type)
            signal_type: 'BUY', 'SELL', or None
        """
        try:
            if len(ohlcv) < AROON_PERIOD + 1:
                return None, None, None
            
            # Extract OHLCV data
            highs = ohlcv[:, 2]  # High prices
            lows = ohlcv[:, 3]   # Low prices
            closes = ohlcv[:, 4]  # Close prices (for reference)
            
            # Calculate Aroon
            aroon_up, aroon_down = calculate_aroon(highs, lows, AROON_PERIOD)
            
            if len(aroon_up) == 0:
                return None, None, None
            
            # Get latest values
            latest_aroon_up = aroon_up[-1]
            latest_aroon_down = aroon_down[-1]
            
            # Determine signal
            signal_type = None
            
            if latest_aroon_up >= SIGNAL_THRESHOLD:
                signal_type = "BUY"
            elif latest_aroon_down >= SIGNAL_THRESHOLD:
                signal_type = "SELL"
            
            return latest_aroon_up, latest_aroon_down, signal_type
            
        except Exception as e:
            logger.error(f"Error calculating Aroon: {str(e)}")
            return None, None, None
    
    async def process_symbol(self, exchange_name: str, symbol: str):
        """Process a single symbol on an exchange"""
        try:
            exchange = self.exchanges[exchange_name]
            
            # Get OHLCV data
            ohlcv = await exchange.get_ohlcv(symbol)
            if ohlcv is None or len(ohlcv) < AROON_PERIOD + 1:
                return
            
            # Get current price
            price = await exchange.get_current_price(symbol)
            if price is None:
                return
            
            # Calculate Aroon and signals
            aroon_up, aroon_down, signal_type = self.calculate_aroon_signal(ohlcv)
            
            if signal_type is None:
                # Clear any active signal if condition no longer true
                if self.signal_tracker.get_active_signal(symbol, exchange_name):
                    self.signal_tracker.clear_signal(symbol, exchange_name)
                    logger.info(f"Signal cleared for {symbol} on {exchange_name}")
                return
            
            # Check for duplicates
            if self.signal_tracker.is_duplicate(symbol, exchange_name, signal_type):
                # Still log the values for monitoring
                if aroon_up is not None and aroon_down is not None:
                    price_str = f"${price:,.2f}" if price >= 1 else f"${price:.8f}"
                    logger.info(f"{symbol} | {price_str} | Aroon Up: {aroon_up:.2f} | Aroon Down: {aroon_down:.2f} | {signal_type} (DUPLICATE)")
                return
            
            # New signal detected
            if aroon_up is not None and aroon_down is not None:
                # Log to console
                price_str = f"${price:,.2f}" if price >= 1 else f"${price:.8f}"
                logger.info(f"{symbol} | {price_str} | Aroon Up: {aroon_up:.2f} | Aroon Down: {aroon_down:.2f} | {signal_type}")
                
                # Add to tracker
                cycle = self.signal_tracker.add_signal(symbol, exchange_name, signal_type)
                
                # Send Telegram alert
                if self.telegram:
                    await self.telegram.send_signal(
                        symbol=symbol,
                        exchange=exchange_name,
                        price=price,
                        aroon_up=aroon_up,
                        aroon_down=aroon_down,
                        signal_type=signal_type,
                        cycle_number=cycle
                    )
                
                logger.info(f"New {signal_type} signal for {symbol} on {exchange_name} (Cycle #{cycle})")
            
        except Exception as e:
            logger.error(f"Error processing {symbol} on {exchange_name}: {str(e)}")
    
    async def scan_all_symbols(self):
        """Scan all symbols across all exchanges"""
        tasks = []
        
        for exchange_name in self.exchanges:
            for symbol in SYMBOLS:
                tasks.append(self.process_symbol(exchange_name, symbol))
        
        # Process all symbols in parallel
        await asyncio.gather(*tasks)
        
        # Cleanup old signals
        self.signal_tracker.cleanup_old_signals()
    
    async def run_bot_loop(self):
        """Main bot loop"""
        logger.info("Starting Aroon Signal Bot...")
        self.running = True
        
        # Initialize exchanges
        await self.initialize_exchanges()
        
        if not self.exchanges:
            logger.error("No exchanges initialized. Bot stopping.")
            self.running = False
            return
        
        logger.info(f"Bot started with {len(self.exchanges)} exchanges and {len(SYMBOLS)} symbols")
        logger.info(f"Monitoring for Aroon {'Up' if SIGNAL_THRESHOLD >= 90 else ''} signals")
        logger.info(f"Aroon period: {AROON_PERIOD}, Signal threshold: {SIGNAL_THRESHOLD}")
        
        while self.running:
            try:
                start_time = time.time()
                
                # Scan all symbols
                await self.scan_all_symbols()
                
                # Calculate sleep time
                elapsed = time.time() - start_time
                # Use a shorter interval for more responsive signals
                sleep_time = max(10, 60 - elapsed)  # Check every ~60 seconds or less
                
                logger.debug(f"Scan completed in {elapsed:.2f}s. Sleeping for {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Error in bot loop: {str(e)}")
                await asyncio.sleep(30)  # Wait before retrying
    
    def run_async(self):
        """Run the bot in a separate thread"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            self.loop.run_until_complete(self.run_bot_loop())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Cleanup resources"""
        self.running = False
        
        # Close exchange connections
        if hasattr(self, 'exchanges'):
            for exchange in self.exchanges.values():
                asyncio.run_coroutine_threadsafe(exchange.close(), self.loop)
        
        if self.loop:
            self.loop.close()
        
        self.executor.shutdown(wait=False)
        logger.info("Bot cleanup completed")
    
    def start(self):
        """Start the bot"""
        if self.bot_thread and self.bot_thread.is_alive():
            logger.warning("Bot is already running")
            return
        
        self.bot_thread = threading.Thread(target=self.run_async, daemon=True)
        self.bot_thread.start()
        logger.info("Bot started in background thread")
    
    def stop(self):
        """Stop the bot"""
        self.running = False
        if self.bot_thread:
            self.bot_thread.join(timeout=5)
        logger.info("Bot stopped")
    
    def run_flask(self):
        """Run the Flask server"""
        logger.info(f"Starting Flask server on port {FLASK_PORT}")
        self.app.run(host='0.0.0.0', port=FLASK_PORT, debug=False, use_reloader=False)

# ============ MAIN ============

def main():
    """Main entry point"""
    # Create bot instance
    bot = AroonSignalBot()
    
    # Start the bot in background
    bot.start()
    
    # Start Flask server (blocking)
    try:
        bot.run_flask()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        bot.stop()

if __name__ == "__main__":
    main()