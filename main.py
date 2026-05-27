"""
GOLD XAUUSD Renko Alert System — Main Runner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Data:      Twelve Data WebSocket (free)
Renko:     Custom Python engine (100% accurate)
Strategies: 4 custom strategies
Alerts:    Telegram Bot → your phone
Cloud:     Runs 24/7 on Render.com (free)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import time
import json
import logging
import requests
import websocket
import threading
from datetime import datetime, timezone

from renko_engine import RenkoEngine
from strategies  import (Strategy1_MAPullback, Strategy2_StochVWAP,
                          Strategy3_VWAPBreakout, Strategy4_DarvasBox)
from telegram_bot import TelegramBot

# ─────────────────────────────────────────────
# CONFIG — Set these in Render environment vars
# ─────────────────────────────────────────────
TWELVE_DATA_KEY  = os.getenv("TWELVE_DATA_KEY",  "YOUR_API_KEY_HERE")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")
BRICK_SIZE       = float(os.getenv("BRICK_SIZE", "3.0"))   # Default $3

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# GLOBAL STATE
# ─────────────────────────────────────────────
renko     = RenkoEngine(brick_size=BRICK_SIZE)
telegram  = TelegramBot(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

s1 = Strategy1_MAPullback(max_pullback=3)
s2 = Strategy2_StochVWAP()
s3 = Strategy3_VWAPBreakout()
s4 = Strategy4_DarvasBox(box_length=5)

price_volumes: list[float] = []   # volume proxy for VWAP
last_heartbeat = time.time()
last_session_date = None          # track UTC day for VWAP reset
daily_highs: list[float] = []
daily_lows:  list[float] = []
current_day_high = 0.0
current_day_low  = float("inf")
prev_brick_count = 0


def get_daily_candles_history():
    """
    Fetch last 10 daily candles on startup to initialize DBOX.
    Uses Twelve Data REST endpoint.
    """
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol=XAU/USD&interval=1day&outputsize=10"
            f"&apikey={TWELVE_DATA_KEY}"
        )
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if "values" in data:
            candles = data["values"]
            highs = [float(c["high"]) for c in reversed(candles)]
            lows  = [float(c["low"])  for c in reversed(candles)]
            logger.info(f"✅ Loaded {len(highs)} daily candles for DBOX")
            return highs, lows
    except Exception as e:
        logger.error(f"Failed to fetch daily candles: {e}")
    return [], []


def preload_historical_renko():
    """
    Fetch last 500 x 1-min candles on startup to pre-build Renko bricks.
    This ensures 200 SMA (Strategy 2) and other indicators have enough data.
    Uses ~2 REST API calls from free Twelve Data quota.
    """
    global price_volumes, last_session_date
    logger.info("📥 Preloading historical 1-min data to warm up Renko engine...")

    try:
        # Fetch 500 x 1-min candles (covers ~8 hours)
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol=XAU/USD&interval=1min&outputsize=500"
            f"&apikey={TWELVE_DATA_KEY}"
        )
        resp = requests.get(url, timeout=20)
        data = resp.json()

        if "values" not in data:
            logger.error(f"Historical data error: {data.get('message', 'unknown')}")
            return

        candles = list(reversed(data["values"]))  # oldest first
        logger.info(f"✅ Fetched {len(candles)} historical 1-min candles")

        today_utc = datetime.now(tz=timezone.utc).date()
        session_started = False

        for candle in candles:
            ts    = datetime.fromisoformat(candle["datetime"]).replace(tzinfo=timezone.utc)
            close = float(candle["close"])
            high  = float(candle["high"])
            low   = float(candle["low"])

            # Track VWAP session reset at UTC midnight
            if ts.date() == today_utc and not session_started:
                s3.new_session(len(renko.bricks))
                session_started = True
                logger.info(f"✅ VWAP session reset at {ts} UTC (today)")

            new_bricks = renko.process_price(close, ts)
            for _ in new_bricks:
                price_volumes.append(1.0)

        brick_count = len(renko.bricks)
        logger.info(f"✅ Renko preload complete — {brick_count} bricks built")

        if brick_count < 200:
            logger.warning(
                f"⚠️ Only {brick_count} bricks after preload. "
                f"Strategy 2 (200 SMA) needs 200. Will activate once enough bricks form."
            )
        else:
            logger.info(f"✅ All strategies ready — enough bricks for 200 SMA")

        telegram.send(
            f"📊 *Renko Preload Complete*\n"
            f"Historical bricks built: {brick_count}\n"
            f"Strategy 2 (200 SMA): {'✅ Ready' if brick_count >= 200 else f'⏳ Need {200-brick_count} more bricks'}\n"
            f"All systems monitoring live now!"
        )

    except Exception as e:
        logger.error(f"Preload failed: {e}")
        telegram.send(f"⚠️ Historical preload failed: {e}\nSystem running on live data only.")


def check_new_session(timestamp: datetime):
    """Reset VWAP and update daily box at UTC midnight"""
    global last_session_date, daily_highs, daily_lows
    global current_day_high, current_day_low

    current_date = timestamp.date()
    if last_session_date != current_date:
        logger.info(f"🌅 New session: {current_date} UTC — Resetting VWAP")

        # Save yesterday's candle data for DBOX
        if last_session_date is not None and current_day_high > 0:
            daily_highs.append(current_day_high)
            daily_lows.append(current_day_low)
            if len(daily_highs) > 20:
                daily_highs = daily_highs[-20:]
                daily_lows  = daily_lows[-20:]
            s4.update_daily_box(daily_highs, daily_lows)

        # Reset session tracking
        current_day_high = 0.0
        current_day_low  = float("inf")
        last_session_date = current_date

        # Reset VWAP session
        s3.new_session(len(renko.bricks))


def on_new_bricks(new_bricks):
    """Called every time new Renko bricks form — run all 4 strategies"""
    global prev_brick_count

    for brick in new_bricks:
        logger.info(f"🧱 New brick: {brick}")

        # Append volume proxy (1.0 as we don't have real volume from WebSocket)
        price_volumes.append(1.0)

        # ── Run all 4 strategies ──
        signals = []

        sig1 = s1.check(renko)
        sig2 = s2.check(renko)
        sig3 = s3.check(renko, price_volumes)
        sig4 = s4.check(renko)

        for sig in [sig1, sig2, sig3, sig4]:
            if sig:
                signals.append(sig)
                logger.info(f"🚨 SIGNAL: {sig.strategy_name} — {sig.signal_type} @ ${sig.price}")
                telegram.send(sig.message)

    prev_brick_count = len(renko.bricks)


def on_message(ws, message):
    """Handle incoming WebSocket price tick from Twelve Data"""
    global current_day_high, current_day_low, last_heartbeat

    try:
        data = json.loads(message)

        # Twelve Data sends: {"event": "price", "symbol": "XAU/USD", "price": "...", "timestamp": ...}
        if data.get("event") != "price":
            return

        price = float(data["price"])
        ts    = datetime.fromtimestamp(data["timestamp"], tz=timezone.utc)

        # Track daily high/low for DBOX
        current_day_high = max(current_day_high, price)
        current_day_low  = min(current_day_low,  price)

        # Check for new session (UTC midnight)
        check_new_session(ts)

        # Feed price into Renko engine
        new_bricks = renko.process_price(price, ts)

        if new_bricks:
            on_new_bricks(new_bricks)

        # Hourly heartbeat
        if time.time() - last_heartbeat > 3600:
            telegram.send_heartbeat(price, len(renko.bricks))
            last_heartbeat = time.time()

    except Exception as e:
        logger.error(f"on_message error: {e}")


def on_error(ws, error):
    logger.error(f"WebSocket error: {error}")


def on_close(ws, close_status_code, close_msg):
    logger.warning(f"WebSocket closed: {close_status_code} — {close_msg}")
    logger.info("Reconnecting in 5 seconds...")
    time.sleep(5)
    start_websocket()


def on_open(ws):
    logger.info("✅ WebSocket connected to Twelve Data")
    # Subscribe to XAUUSD
    subscribe = {
        "action": "subscribe",
        "params": {
            "symbols": "XAU/USD"
        }
    }
    ws.send(json.dumps(subscribe))


def start_websocket():
    """Start Twelve Data WebSocket connection"""
    url = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={TWELVE_DATA_KEY}"
    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever(ping_interval=30, ping_timeout=10)


def main():
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("  GOLD XAUUSD Renko Alert System v1.0")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"  Brick Size : ${BRICK_SIZE}")
    logger.info(f"  Strategies : 4 active")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Load daily candles for DBOX initialization
    global daily_highs, daily_lows
    daily_highs, daily_lows = get_daily_candles_history()
    if daily_highs:
        s4.update_daily_box(daily_highs, daily_lows)
        logger.info(f"✅ DBOX initialized — Top: {s4.top_box}, Bottom: {s4.bottom_box}")

    # Preload historical data — this also handles VWAP session reset internally
    preload_historical_renko()

    # Send startup notification
    telegram.send_startup(BRICK_SIZE)

    # Start WebSocket in main thread (auto-reconnects on close)
    start_websocket()


if __name__ == "__main__":
    main()
