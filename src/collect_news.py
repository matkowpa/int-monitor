"""News collection: RSS feeds, relevance filtering, dedupe against seen state."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone

import feedparser

from .models import NewsItem, State, iso, make_id, utcnow

log = logging.getLogger("int-monitor")

SEEN_IDS_CAP = 3000
_SNIPPET_MAX = 500


def strip_html(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_feed(name: str, url: str) -> list[NewsItem]:
    parsed = feedparser.parse(url)
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"feed parse error: {parsed.bozo_exception}")
    items: list[NewsItem] = []
    for entry in parsed.entries:
        title = strip_html(getattr(entry, "title", ""))
        link = str(getattr(entry, "link", "") or "").strip()
        if not title or not link:
            continue
        summary = strip_html(
            getattr(entry, "summary", "") or getattr(entry, "description", "")
        )[:_SNIPPET_MAX]
        published = None
        for attr in ("published_parsed", "updated_parsed"):
            struct = getattr(entry, attr, None)
            if struct:
                published = datetime(*struct[:6], tzinfo=timezone.utc)
                break
        items.append(
            NewsItem(
                id=make_id(name, title.lower()),
                title=title,
                url=link,
                source=name,
                published=published,
                snippet=summary,
            )
        )
    return items


def is_relevant(text: str, terms: list[str]) -> bool:
    hay = (text or "").lower()
    return any(t.lower() in hay for t in terms)


def collect_new_items(config, state: State) -> tuple[list[NewsItem], State]:
    """Fetch all configured feeds and return the items not yet seen.

    Mutates and returns the state (seen_ids, last_run).
    """
    now = utcnow()
    first_run = not state.seen_ids
    if first_run:
        cutoff = now - timedelta(days=config.first_run_days)
        log.info("First run: taking items from the last %d days", config.first_run_days)
    else:
        cutoff = now - timedelta(hours=config.lookback_hours)

    candidates: list[NewsItem] = []
    for feed in config.rss_sources:
        name = str(feed.get("name") or feed.get("url"))
        url = str(feed.get("url") or "")
        try:
            items = fetch_feed(name, url)
        except Exception as exc:  # noqa: BLE001 - a broken feed must not abort the run
            log.warning("Feed '%s' failed: %s", name, exc)
            continue
        log.info("Feed '%s': %d items", name, len(items))
        always = bool(feed.get("always_relevant"))
        for item in items:
            if item.id in state.seen_ids:
                continue
            if not always and not is_relevant(f"{item.title} {item.snippet}", config.topic_terms):
                continue
            if item.published and item.published < cutoff:
                continue
            candidates.append(item)

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    candidates.sort(key=lambda x: x.published or epoch, reverse=True)
    new_items = candidates[: config.max_new_items]

    seen = set(state.seen_ids)
    seen.update(item.id for item in new_items)
    state.seen_ids = list(seen)[-SEEN_IDS_CAP:]
    state.last_run = iso(now)
    return new_items, state
