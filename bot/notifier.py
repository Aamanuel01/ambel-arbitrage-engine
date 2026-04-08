"""
notifier.py — Sends formatted alerts to Discord or Telegram.

Configured via environment variables.  Only one channel needs to be set.
Internally rate-limited per token pair to avoid webhook spam.
"""

import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

# Minimum seconds between alerts for the same pair
_RATE_LIMIT_SECONDS = 30
_last_sent: dict[str, float] = {}


def _rate_limited(pair: str) -> bool:
    """Return True if we're within the rate limit window for this pair."""
    now = time.monotonic()
    last = _last_sent.get(pair, 0)
    return (now - last) < _RATE_LIMIT_SECONDS


def _mark_sent(pair: str) -> None:
    _last_sent[pair] = time.monotonic()


# ── Discord ───────────────────────────────────────────────────────────────────

def _send_discord(message: str) -> bool:
    if not config.DISCORD_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(
            config.DISCORD_WEBHOOK_URL,
            json={"content": message},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info('"Discord alert sent"')
        return True
    except Exception as exc:
        logger.error('"Discord send failed: %s"', exc)
        return False


# ── Telegram ──────────────────────────────────────────────────────────────────

def _send_telegram(message: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info('"Telegram alert sent"')
        return True
    except Exception as exc:
        logger.error('"Telegram send failed: %s"', exc)
        return False


# ── Public interface ──────────────────────────────────────────────────────────

def send(message: str, pair: str) -> bool:
    """
    Send *message* to the configured notification channel.
    Returns True if at least one channel succeeded.
    Silently skips if within rate-limit window for *pair*.
    """
    if _rate_limited(pair):
        logger.debug('"Rate limit active for %s — skipping notification"', pair)
        return False

    sent = _send_discord(message) or _send_telegram(message)

    if sent:
        _mark_sent(pair)
    else:
        logger.warning(
            '"No notification channel configured or all sends failed. '
            'Set DISCORD_WEBHOOK_URL or TELEGRAM_BOT_TOKEN+TELEGRAM_CHAT_ID in .env"'
        )

    return sent
