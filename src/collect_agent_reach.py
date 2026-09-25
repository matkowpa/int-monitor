"""Semantic news search via agent-reach (Exa MCP via mcporter).

Complements RSS feeds with direct semantic web search for Intrum AB.
Output items are converted into NewsItem models with source="agent-reach (Exa)"
and deduped against state.seen_ids in the same way as RSS news items.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .models import NewsItem, make_id, parse_iso

log = logging.getLogger("int-monitor")

ROOT = Path(__file__).resolve().parent.parent
MCPORTER_CONFIG = ROOT / "config" / "mcporter.json"
TOOL = "exa.web_search_exa"
TIMEOUT_S = 120

RECORDS = re.compile(r"(?m)^---\s*$")
FIELDS = {
    "title": re.compile(r"(?m)^Title:\s*(.*)$"),
    "url": re.compile(r"(?m)^URL:\s*(\S+)$"),
    "published": re.compile(r"(?m)^Published:\s*(\S+)$"),
}


def _clean(text: str) -> str:
    text = re.sub(r"(?m)^\.\.\.$", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _field(pattern: re.Pattern, record: str) -> str:
    match = pattern.search(record)
    return match.group(1).strip() if match else ""


def parse_results(text: str, source_name: str = "agent-reach (Exa)") -> list[NewsItem]:
    """Parse Exa text response into NewsItem objects."""
    items: list[NewsItem] = []
    for record in RECORDS.split(text or ""):
        url = _field(FIELDS["url"], record)
        title = _clean(_field(FIELDS["title"], record))
        if not url or not title:
            continue
        parts = record.split("Highlights:", 1)
        snippet = _clean(parts[1]) if len(parts) == 2 else ""
        pub_str = _field(FIELDS["published"], record)
        published = None
        if pub_str and pub_str.upper() != "N/A":
            published = parse_iso(pub_str)
        items.append(
            NewsItem(
                id=make_id(source_name, title.lower()),
                title=title,
                url=url,
                source=source_name,
                published=published,
                snippet=snippet[:500],
            )
        )
    return items


def _find_mcporter_command() -> list[str] | None:
    """Find a runnable command for mcporter (local binary or npx)."""
    bin_path = shutil.which("mcporter")
    if bin_path:
        return [bin_path]
    npx_path = shutil.which("npx")
    if npx_path:
        return [npx_path, "--yes", "mcporter"]
    return None


def run_search(query: str, num_results: int,
               objective: str = "Find latest news and corporate developments about Intrum AB",
               runner=None) -> str:
    """Execute a single Exa search query via mcporter. Returns response text."""
    cmd_prefix = _find_mcporter_command()
    if not cmd_prefix and runner is None:
        raise RuntimeError("Neither mcporter nor npx found in PATH")

    config_args = ["--config", str(MCPORTER_CONFIG)] if MCPORTER_CONFIG.exists() else []
    full_cmd = (cmd_prefix or ["mcporter"]) + config_args + [
        "call", TOOL,
        f"query={query}",
        f"objective={objective}",
        f"numResults={num_results}",
        "--output", "json",
    ]

    if runner is not None:
        return runner(full_cmd)

    proc = subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
        shell=sys.platform == "win32",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mcporter exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:200]}")

    raw = proc.stdout or ""
    # Extract JSON object (mcporter may print npm output before JSON)
    idx = raw.find("{")
    if idx == -1:
        return ""
    data = json.loads(raw[idx:])
    blocks = [b.get("text", "") for b in data.get("content") or []
              if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(blocks)


def collect_agent_reach_news(config, search_fn=None) -> list[NewsItem]:
    """Collect news items via agent-reach (Exa semantic search).

    Returns a list of NewsItem objects. Any query errors are logged and skipped
    without failing the run.
    """
    if not getattr(config, "agent_reach_enabled", True):
        log.info("Agent reach is disabled in config")
        return []

    queries = getattr(config, "agent_reach_queries", [])
    if not queries:
        log.info("No agent reach queries configured")
        return []

    num_results = getattr(config, "agent_reach_results_per_query", 6)
    search_fn = search_fn or run_search

    collected: list[NewsItem] = []
    seen_urls: set[str] = set()

    for q in queries:
        try:
            text = search_fn(q, num_results)
            parsed = parse_results(text)
            for it in parsed:
                norm_url = it.url.split("?")[0].rstrip("/").lower()
                if norm_url in seen_urls:
                    continue
                seen_urls.add(norm_url)
                collected.append(it)
            log.info("Agent reach query '%s': found %d items", q[:50], len(parsed))
        except Exception as exc:
            log.warning("Agent reach query '%s' failed: %s", q[:50], exc)

    return collected
