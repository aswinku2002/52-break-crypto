import os
import time
import threading
import requests
from flask import Flask
from datetime import datetime, time as dtime
import dalal

# ============================================================
# 1. Flask Setup for Render
# ============================================================
app = Flask(__name__)
last_check_time = "Never"
cycle_count = 0


@app.route('/')
def home():
    return "NSE Price Cross Alert Bot is running!"


@app.route('/health')
def health():
    return {
        "status": "ok",
        "source": "NSE (dalal)",
        "last_check": last_check_time,
        "cycle": cycle_count,
        "market_open": is_market_open(),
        "stocks_monitored": len(WATCHLIST)
    }


# ============================================================
# 2. Configuration
# ============================================================
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

CHECK_INTERVAL = 30          # seconds between scans
ALERT_COOLDOWN = 300         # seconds before re-alerting same stock

# Stock watchlist: symbol -> { "base": base price, "multipliers": [list] }
# Target alert price = base * multiplier
WATCHLIST = {
    "RELIANCE":  {"base": 1400.0, "multipliers": [0.95, 1.00, 1.05, 1.10]},
    "HDFCBANK":  {"base": 1700.0, "multipliers": [0.98, 1.02, 1.05]},
    "TCS":       {"base": 3800.0, "multipliers": [0.95, 1.00, 1.05]},
    "INFY":      {"base": 1500.0, "multipliers": [0.95, 1.00, 1.05]},
    "SBIN":      {"base": 800.0,  "multipliers": [0.95, 1.00, 1.05]},
    "TATAMOTORS":{"base": 900.0,  "multipliers": [0.95, 1.00, 1.05]},
    # Add more from your screenshot as needed
}

# State per stock: {"prev": float, "last_alert": datetime}
price_state = {}


# ============================================================
# 3. Market Hours (IST)
# ============================================================
def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:  # Sat / Sun
        return False

    # NSE 2026 holidays (add more as needed)
    holidays_2026 = {
        "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28",
        "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20",
        "2026-11-10", "2026-11-24", "2026-12-25"
    }
    if now.strftime("%Y-%m-%d") in holidays_2026:
        return False

    return dtime(9, 15) <= now.time() <= dtime(15, 30)


# ============================================================
# 4. Telegram Alert
# ============================================================
def send_alert(message):
    if not TOKEN or not CHAT_ID:
        print("  ⚠️ No Telegram credentials configured!")
        return False

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            params={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=10
        )
        if r.status_code == 200:
            print("  ✅ Telegram alert sent")
            return True
        else:
            print(f"  ❌ Telegram error: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")
        return False


# ============================================================
# 5. Price Fetch (NSE / BSE)
# ============================================================
def get_current_price(symbol, exchange="NSE"):
    try:
        q = dalal.quote(symbol, exchange=exchange)
        return float(q['ltp'])
    except Exception as e:
        print(f"  ❌ {symbol} fetch failed: {e}")
        return None


# ============================================================
# 6. Cross Detection
# ============================================================
def check_cross(symbol, curr_price, levels):
    """Return list of levels crossed since last check."""
    if symbol not in price_state:
        price_state[symbol] = {"prev": curr_price, "last_alert": None}
        return []

    prev = price_state[symbol]["prev"]
    crossed = []

    for level in levels:
        # Upward cross OR downward cross
        if (prev < level <= curr_price) or (prev > level >= curr_price):
            crossed.append(level)

    price_state[symbol]["prev"] = curr_price
    return crossed


def can_alert(symbol):
    last = price_state[symbol].get("last_alert")
    if last is None:
        return True
    return (datetime.now() - last).total_seconds() >= ALERT_COOLDOWN


# ============================================================
# 7. Main Bot Loop
# ============================================================
def run_bot():
    global last_check_time, cycle_count

    print("\n" + "=" * 70)
    print("🚀 NSE / BSE PRICE CROSS ALERT BOT")
    print("=" * 70)
    print(f"  • Stocks: {len(WATCHLIST)}")
    print(f"  • Scan interval: {CHECK_INTERVAL}s")
    print(f"  • Alert cooldown: {ALERT_COOLDOWN}s")
    print("=" * 70 + "\n")

    if TOKEN and CHAT_ID:
        send_alert(
            "✅ <b>NSE Price Cross Alert Bot Started</b>\n\n"
            f"📈 Stocks monitored: {len(WATCHLIST)}\n"
            f"⏱️ Scan interval: {CHECK_INTERVAL}s\n"
            f"🕒 Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST"
        )

    # Initialise first prices
    for sym in WATCHLIST:
        p = get_current_price(sym)
        if p:
            price_state[sym] = {"prev": p, "last_alert": None}
            print(f"  📌 {sym} initial: ₹{p:.2f}")

    while True:
        try:
            cycle_count += 1

            if not is_market_open():
                print(f"⏸️ Market closed at {datetime.now().strftime('%H:%M')} IST — waiting...")
                time.sleep(60)
                continue

            print(f"\n{'=' * 70}")
            print(f"🔄 Cycle #{cycle_count} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST")
            print(f"{'=' * 70}")

            for sym, cfg in WATCHLIST.items():
                curr = get_current_price(sym)
                if curr is None:
                    continue

                levels = [round(cfg["base"] * m, 2) for m in cfg["multipliers"]]
                crossed = check_cross(sym, curr, levels)

                print(f"  {sym:12} | ₹{curr:10.2f} | Levels: {levels}")

                if crossed and can_alert(sym):
                    for lv in crossed:
                        direction = "上穿 📈" if curr >= lv else "下穿 📉"
                        msg = (
                            f"🚨 <b>Price Cross Alert</b>\n\n"
                            f"<b>Stock:</b> {sym}\n"
                            f"<b>Level:</b> ₹{lv:.2f}\n"
                            f"<b>Current price:</b> ₹{curr:.2f}\n"
                            f"<b>Direction:</b> {direction}\n"
                            f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST"
                        )
                        send_alert(msg)
                        print(f"  🎯 {sym} crossed ₹{lv:.2f} ({direction})")
                        price_state[sym]["last_alert"] = datetime.now()
                        break  # one alert per cycle per stock

                time.sleep(1)  # throttle between stocks

            last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
            print(f"\n📊 Cycle #{cycle_count} complete. Next scan in {CHECK_INTERVAL}s.")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n👋 Bot stopped by user")
            if TOKEN and CHAT_ID:
                send_alert("🛑 Bot stopped by user")
            break
        except Exception as e:
            print(f"❌ Critical error: {e}")
            time.sleep(30)


# ============================================================
# 8. Start Bot (background thread)
# ============================================================
print("\n🚀 Starting bot thread...")
threading.Thread(target=run_bot, daemon=True).start()


# ============================================================
# 9. Start Flask Server (main thread)
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Web server on port {port}")
    app.run(host='0.0.0.0', port=port, threaded=True)
