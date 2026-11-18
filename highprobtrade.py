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
    return "NSE Dividend-Multiple Price Alert Bot is running!"


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

# Watchlist: symbol -> dividend per share (₹)
# Alert fires when price reaches an INTEGER multiple of this dividend.
WATCHLIST = {
    "SAGILITY":   0.25,
    "SYNCOMF":    0.10,
    "IIFL":       4.00,
    "IDFCFIRSTB": 0.25,
    "COALINDIA":  26.50,
    "BANKINDIA":  4.65,
    "TATASTEEL":  4.00,
    "ASHOKLEY":   2.50,
    "MAHABANK":   2.20,
    "PNB":        3.00,
    "INDOCO":     0.20,
    "IREDA":      1.35,
    "FCL":        0.05,
    "HDFCBANK":   13.00,
    "ANGELONE":   6.45,
    "MANAPPURAM": 3.00,
    "SBIN":       17.35,
    "CANBK":      4.20,
    "GAIL":       5.50,
    "SAIL":       2.35,
    "RELIANCE":   6.00,
    "TMPV":       3.00,
    "NMDC":       3.50,
    "MOTHERSON":  0.60,
}

# State per stock: {"prev": float, "last_alert": datetime, "last_level": float}
price_state = {}


# ============================================================
# 3. Market Hours (IST)
# ============================================================
def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:  # Sat / Sun
        return False

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
# 5. Price Fetch (NSE)
# ============================================================
def get_current_price(symbol, exchange="NSE"):
    try:
        q = dalal.quote(symbol, exchange=exchange)
        return float(q['ltp'])
    except Exception as e:
        print(f"  ❌ {symbol} fetch failed: {e}")
        return None


# ============================================================
# 6. Integer-Multiple Cross Detection
# ============================================================
def find_crossed_multiple(symbol, curr_price, dividend):
    """
    Detect if the price crossed an integer multiple of the dividend
    since the last check.
    Returns the crossed multiple level or None.
    """
    if dividend <= 0:
        return None

    if symbol not in price_state:
        price_state[symbol] = {
            "prev": curr_price,
            "last_alert": None,
            "last_level": None
        }
        return None

    prev = price_state[symbol]["prev"]

    # Which integer multiple(s) lie between prev and curr?
    # Multiple n such that n*dividend is between prev and curr
    if prev == curr_price:
        price_state[symbol]["prev"] = curr_price
        return None

    lo = min(prev, curr_price)
    hi = max(prev, curr_price)

    # n_lo = smallest integer n with n*dividend > lo
    n_lo = int(lo // dividend) + 1
    # n_hi = largest integer n with n*dividend <= hi
    n_hi = int(hi // dividend)

    crossed = []
    for n in range(n_lo, n_hi + 1):
        level = round(n * dividend, 2)
        # Confirm it's really strictly between the two prices
        if lo < level <= hi:
            crossed.append(level)

    price_state[symbol]["prev"] = curr_price

    if crossed:
        return crossed[0]  # report the first crossed level
    return None


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
    print("🚀 NSE DIVIDEND-MULTIPLE PRICE ALERT BOT")
    print("=" * 70)
    print(f"  • Stocks: {len(WATCHLIST)}")
    print(f"  • Rule: alert when price = n × dividend (n = integer)")
    print(f"  • Scan interval: {CHECK_INTERVAL}s")
    print(f"  • Alert cooldown: {ALERT_COOLDOWN}s")
    print("=" * 70 + "\n")

    if TOKEN and CHAT_ID:
        send_alert(
            "✅ <b>NSE Dividend-Multiple Alert Bot Started</b>\n\n"
            f"📈 Stocks: {len(WATCHLIST)}\n"
            f"🧮 Rule: alert when price reaches an integer multiple of dividend\n"
            f"⏱️ Scan: {CHECK_INTERVAL}s\n"
            f"🕒 Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST"
        )

    # Initialise first prices
    for sym in WATCHLIST:
        p = get_current_price(sym)
        if p:
            price_state[sym] = {
                "prev": p,
                "last_alert": None,
                "last_level": None
            }
            print(f"  📌 {sym} initial: ₹{p:.2f}  (dividend ₹{WATCHLIST[sym]:.2f})")

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

            for sym, dividend in WATCHLIST.items():
                curr = get_current_price(sym)
                if curr is None:
                    continue

                crossed_level = find_crossed_multiple(sym, curr, dividend)

                print(f"  {sym:12} | ₹{curr:10.2f} | Div ₹{dividend:.2f} | "
                      f"n={int(curr // dividend) if dividend > 0 else '-'}")

                if crossed_level is not None and can_alert(sym):
                    n = int(round(crossed_level / dividend))
                    direction = "上穿 📈" if curr >= crossed_level else "下穿 📉"
                    msg = (
                        f"🚨 <b>Dividend-Multiple Alert</b>\n\n"
                        f"<b>Stock:</b> {sym}\n"
                        f"<b>Dividend/share:</b> ₹{dividend:.2f}\n"
                        f"<b>Multiple:</b> {n}×\n"
                        f"<b>Level:</b> ₹{crossed_level:.2f}\n"
                        f"<b>Current price:</b> ₹{curr:.2f}\n"
                        f"<b>Direction:</b> {direction}\n"
                        f"<b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST"
                    )
                    send_alert(msg)
                    print(f"  🎯 {sym} crossed ₹{crossed_level:.2f} ({direction})")
                    price_state[sym]["last_alert"] = datetime.now()
                    price_state[sym]["last_level"] = crossed_level

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
