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
import time
import urllib.error
import urllib.request

log = logging.getLogger("int-monitor")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def wait_for_page(url: str, marker: str, timeout_s: int = 240,
                  interval_s: int = 10) -> bool:
    """Poll url until GitHub Pages serves it (HTTP 200 + marker in content).

    Pages deploys asynchronously ~1-2 min after the gh-pages push; the
    notification should go out only when the linked page is actually
    reachable. A cache-busting query parameter avoids a stale CDN-cached 404.
    Returns True when confirmed live, False on timeout (notify anyway then).
    """
    log.info("Waiting for the page to go live: %s", url)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        probe = url + ("&" if "?" in url else "?") + "cb=" + str(int(time.time()))
        try:
            req = urllib.request.Request(probe, headers={"User-Agent": "int-monitor"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200 and marker in resp.read().decode("utf-8", errors="replace"):
                    log.info("Page is live")
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code != 404:  # 404 = not deployed yet, expected
                log.warning("HTTP %s while waiting for %s", exc.code, url)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("Page check failed (%s) - retrying", exc)
        time.sleep(interval_s)
    log.warning("Page %s not confirmed live after %ds - notifying anyway", url, timeout_s)
    return False


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
