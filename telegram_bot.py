"""
Telegram Alert Bot
Sends formatted trade signals to your phone instantly
"""

import requests
import logging

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send(self, message: str) -> bool:
        """Send a message to Telegram. Returns True if successful."""
        try:
            url  = f"{self.base_url}/sendMessage"
            data = {
                "chat_id":    self.chat_id,
                "text":       message,
                "parse_mode": "Markdown"
            }
            resp = requests.post(url, data=data, timeout=10)
            if resp.status_code == 200:
                logger.info(f"✅ Telegram alert sent: {message[:60]}...")
                return True
            else:
                logger.error(f"❌ Telegram error {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Telegram exception: {e}")
            return False

    def send_startup(self, brick_size: float):
        """Send system startup notification"""
        msg = (
            f"🚀 *GOLD Alert System Started*\n"
            f"Symbol: XAUUSD\n"
            f"Brick Size: ${brick_size}\n"
            f"Strategies Active: 4\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ S1: MA Pullback (8/21 SMA)\n"
            f"✅ S2: 200 SMA + Stochastic\n"
            f"✅ S3: VWAP Breakout\n"
            f"✅ S4: Darvas Box\n"
            f"━━━━━━━━━━━━━━\n"
            f"System is live and monitoring!"
        )
        self.send(msg)

    def send_heartbeat(self, price: float, bricks_count: int):
        """Optional: send hourly heartbeat so you know system is alive"""
        msg = (
            f"💓 *System Heartbeat*\n"
            f"XAUUSD: ${price:.3f}\n"
            f"Renko Bricks: {bricks_count}\n"
            f"System: Running ✅"
        )
        self.send(msg)
