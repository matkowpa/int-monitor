"""Telegram notifications for published site updates.

Enabled by two environment variables: TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID (repo secrets in CI; plain env vars locally). Missing
credentials or any delivery failure only logs and skips — a notification
must never break a publish.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("int-monitor")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def notify_publish(config, run_id: str, news_count: int, evidence_chars: int) -> bool:
    """Send a Telegram message about a newly published site page."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.info("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) - skipping")
        return False

    base = config.base_url.rstrip("/")
    text = (
        f"🌐 {config.site_title} — new brief {run_id} is live\n"
        f"{base}/reports/{run_id}.html\n"
        f"news: {news_count} · engine evidence: {evidence_chars} chars"
    )
    req = urllib.request.Request(
        TELEGRAM_API.format(token=token),
        data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            log.warning("Telegram API returned ok=false: %s", data)
            return False
        log.info("Telegram notification sent")
        return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("Telegram notification failed: %s", exc)
        return False
