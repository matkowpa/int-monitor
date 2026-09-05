"""Social / alternative source screening via the last30days engine.

The engine (MIT, https://github.com/mvanhorn/last30days-skill) is pure Python
stdlib. It is used headless with keyless sources (Reddit, Hacker News,
YouTube, StockTwits, web). An optional SCRAPECREATORS_API_KEY in the
environment unlocks X/Twitter without any code change.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collect_news import is_relevant, strip_html
from .models import SocialItem, make_id, parse_iso

log = logging.getLogger("int-monitor")

ENGINE_TIMEOUT_S = 480
_LOCAL_SKILL_DIR = Path.home() / ".cline" / "skills" / "last30days"


def ensure_engine(config) -> Path:
    """Return a path to the engine's last30days.py, acquiring it if needed."""
    clone_dir = Path(".last30days")
    engine = clone_dir / "scripts" / "last30days.py"
    if engine.exists():
        return engine

    skill_dir = os.environ.get("LAST30DAYS_SKILL_DIR") or (
        str(_LOCAL_SKILL_DIR) if (_LOCAL_SKILL_DIR / "scripts" / "last30days.py").exists() else ""
    )
    if skill_dir:
        local_engine = Path(skill_dir) / "scripts" / "last30days.py"
        if local_engine.exists():
            log.info("Using local last30days skill: %s", local_engine)
            return local_engine

    if shutil.which("git") is None:
        raise RuntimeError("git is required to clone the last30days engine")
    log.info("Cloning last30days engine from %s@%s", config.last30days_repo, config.last30days_ref)
    subprocess.run(
        ["git", "clone", "--depth", "1", "-b", config.last30days_ref,
         config.last30days_repo, str(clone_dir)],
        check=True,
        timeout=180,
    )
    matches = sorted(clone_dir.rglob("last30days.py"))
    if not matches:
        raise RuntimeError(f"last30days.py not found after cloning into {clone_dir.resolve()}")
    return matches[0]


def run_engine(topic: str, days: int, save_dir: Path, engine_path: Path,
               subreddits: str = "") -> tuple[str, int]:
    """Run the engine headless; return (stdout, returncode). Never raises on engine failure."""
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(engine_path), topic,
        "--emit", "json",
        "--json-profile", "raw",
        "--days", str(days),
        "--no-browser-cookies",
        "--save-dir", str(save_dir),
        "--quick",
    ]
    if subreddits.strip():
        cmd += ["--subreddits", subreddits.strip()]
    env = dict(os.environ)
    log.info("Running last30days engine (this can take a few minutes)...")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=ENGINE_TIMEOUT_S, env=env)
        if proc.returncode != 0:
            log.warning("Engine exited with code %d; stderr tail: %s",
                        proc.returncode, (proc.stderr or "")[-500:])
        return proc.stdout or "", proc.returncode
    except subprocess.TimeoutExpired:
        log.warning("Engine timed out after %ds", ENGINE_TIMEOUT_S)
        return "", -1
    except OSError as exc:
        log.warning("Engine failed to start: %s", exc)
        return "", -1


def load_engine_output(stdout: str, save_dir: Path) -> dict:
    """Get the engine's JSON report: saved file first, stdout fallback."""
    save_dir = Path(save_dir)
    if save_dir.exists():
        candidates = sorted(
            save_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    log.info("Loaded engine output from %s", path)
                    return data
            except (OSError, ValueError):
                continue
    text = (stdout or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {}

def _epoch_to_iso(value) -> str | None:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        if isinstance(value, str) and value.strip():
            parsed = parse_iso(value.strip())
            return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") if parsed else None
    except (OSError, OverflowError, ValueError):
        return None
    return None


def _engagement_str(node: dict) -> str:
    eng = node.get("engagement")
    if isinstance(eng, str) and eng.strip():
        return eng.strip()
    if isinstance(eng, dict):
        node = eng
    parts = []
    for key, label in (("score", "pts"), ("points", "pts"), ("upvotes", "upvotes")):
        if isinstance(node.get(key), (int, float)):
            parts.append(f"{int(node[key])} {label}")
            break
    for key, label in (("num_comments", "comments"), ("comments", "comments")):
        if isinstance(node.get(key), (int, float)):
            parts.append(f"{int(node[key])} {label}")
            break
    for key, label in (("likes", "likes"), ("views", "views")):
        if isinstance(node.get(key), (int, float)):
            parts.append(f"{int(node[key])} {label}")
            break
    return " · ".join(parts)


def social_fallback_search(config, cap: int = 30) -> list[SocialItem]:
    """Keyless direct searches used when the engine's sources come up empty.

    Tries, in order: Reddit public JSON search (works in some environments,
    IP-blocked in others) and Hacker News (Algolia API, keyless and reliable).
    Results are filtered by topic terms and merged.
    """
    merged: list[SocialItem] = []
    merged.extend(_reddit_search(config, cap))
    merged.extend(_hackernews_search(config, cap))
    merged.sort(key=lambda i: i.posted or utcnow_min(), reverse=True)
    # dedupe by id
    seen, out = set(), []
    for item in merged:
        if item.id not in seen:
            seen.add(item.id)
            out.append(item)
    log.info("Social fallback total: %d relevant post(s)", len(out))
    return out[:cap]


def utcnow_min():
    return datetime.min.replace(tzinfo=timezone.utc)


def _http_get_json(url: str, headers: dict | None = None, timeout: int = 30):
    import urllib.request

    merged_headers = {"User-Agent": "int-monitor/1.0 (media monitor)"}
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, headers=merged_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _reddit_search(config, cap: int) -> list[SocialItem]:
    """Direct keyless Reddit search via the public JSON API."""
    import time
    import urllib.parse

    query = f'"{config.company.split()[0]}" OR {config.company}'
    url = (f"https://www.reddit.com/search.json?"
           f"q={urllib.parse.quote(query)}&sort=new&t=month&limit=25")
    try:
        data = _http_get_json(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept": "application/json",
        })
    except (OSError, ValueError) as exc:
        log.warning("Reddit fallback search failed: %s", exc)
        return []

    cutoff = time.time() - config.social_days * 86400
    items: list[SocialItem] = []
    for child in (data.get("data") or {}).get("children") or []:
        post = child.get("data") or {}
        title = strip_html(str(post.get("title") or ""))
        permalink = post.get("permalink")
        if not title or not permalink:
            continue
        created = float(post.get("created_utc") or 0)
        if created and created < cutoff:
            continue
        items.append(SocialItem(
            id=make_id("reddit", title.lower(), str(permalink)[:150]),
            platform=f"reddit r/{post.get('subreddit') or 'unknown'}",
            title=title[:300],
            url="https://www.reddit.com" + str(permalink),
            snippet=strip_html(str(post.get("selftext") or ""))[:400],
            engagement=f"{int(post.get('score') or 0)} pts · "
                       f"{int(post.get('num_comments') or 0)} comments",
            posted=parse_iso(_epoch_to_iso(created)),
        ))
    relevant = [i for i in items if is_relevant(f"{i.title} {i.snippet}", config.topic_terms)]
    log.info("Reddit fallback: %d posts, %d relevant", len(items), len(relevant))
    return relevant[:cap]


def _hackernews_search(config, cap: int) -> list[SocialItem]:
    """Keyless Hacker News search via the public Algolia API."""
    import time
    import urllib.parse

    query = urllib.parse.quote(config.company)
    url = (f"https://hn.algolia.com/api/v1/search_by_date?"
           f"query={query}&tags=story&hitsPerPage=30")
    try:
        data = _http_get_json(url)
    except (OSError, ValueError) as exc:
        log.warning("HN fallback search failed: %s", exc)
        return []

    cutoff = time.time() - config.social_days * 86400
    items: list[SocialItem] = []
    for hit in data.get("hits") or []:
        title = strip_html(str(hit.get("title") or hit.get("story_title") or ""))
        if not title:
            continue
        created = hit.get("created_at_i") or 0
        if created and created < cutoff:
            continue
        hn_id = hit.get("objectID") or ""
        url = str(hit.get("url") or f"https://news.ycombinator.com/item?id={hn_id}")
        points, comments = hit.get("points") or 0, hit.get("num_comments") or 0
        items.append(SocialItem(
            id=make_id("hackernews", title.lower(), str(hn_id)),
            platform="hackernews",
            title=title[:300],
            url=url,
            snippet=strip_html(str(hit.get("story_text") or hit.get("comment_text") or ""))[:400],
            engagement=f"{int(points)} pts · {int(comments)} comments",
            posted=parse_iso(_epoch_to_iso(created)),
        ))
    relevant = [i for i in items if is_relevant(f"{i.title} {i.snippet}", config.topic_terms)]
    log.info("HN fallback: %d posts, %d relevant", len(items), len(relevant))
    return relevant[:cap]


def extract_social_items(raw: dict, terms: list[str], cap: int = 30) -> list[SocialItem]:
    """Tolerantly walk the raw engine output and pull out posts about the company."""
    if not isinstance(raw, dict):
        return []
    found: dict[str, SocialItem] = {}

    def walk(node, platform_hint: str | None = None) -> None:
        if isinstance(node, dict):
            platform = node.get("platform") or node.get("source") or platform_hint
            if not platform and node.get("subreddit"):
                platform = f"reddit r/{node['subreddit']}"
            url = str(node.get("url") or node.get("link") or node.get("permalink") or "").strip()
            title = strip_html(
                str(node.get("title") or node.get("text") or node.get("body") or "")
            )
            if url and title and url.startswith("http"):
                snippet = strip_html(str(node.get("text") or node.get("body")
                                         or node.get("summary") or node.get("snippet") or ""))[:400]
                item = SocialItem(
                    id=make_id(str(platform or "web"), title.lower(), url[:200]),
                    platform=str(platform or "web"),
                    title=title[:300],
                    url=url,
                    snippet=snippet if snippet != title else "",
                    engagement=_engagement_str(node),
                    posted=parse_iso(_epoch_to_iso(
                        node.get("created_utc") or node.get("published_at")
                        or node.get("published") or node.get("created_at") or node.get("date")
                    )),
                )
                if item.id not in found:
                    found[item.id] = item
            for value in node.values():
                walk(value, str(platform) if isinstance(platform, str) else platform_hint)
        elif isinstance(node, list):
            for value in node:
                walk(value, platform_hint)

    walk(raw)
    relevant = [
        item for item in found.values()
        if is_relevant(f"{item.title} {item.snippet}", terms)
    ]
    log.info("Social screening: %d posts collected, %d relevant", len(found), len(relevant))
    return relevant[:cap]

