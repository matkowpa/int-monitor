"""Pipeline orchestrator for the Intrum AB daily monitor.

Usage:
  python -m src.run [--date YYYY-MM-DD] [--dry-run] [--skip-social] [--mock] [--push]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import analyze as analyze_mod
from . import collect_news, collect_social, site as site_mod
from .config import load_config
from .models import NewsItem, State, Story

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
STATE_PATH = DATA_DIR / "state.json"
STORIES_PATH = DATA_DIR / "stories.json"
SITE_DIR = ROOT / "site"
ENGINE_RUN_DIR = ROOT / ".last30days-run"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
SOCIAL_FALLBACK_MIN = 3  # below this, the direct Reddit search fallback kicks in

log = logging.getLogger("int-monitor")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s (%s) - using default", path, exc)
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_state() -> State:
    return State.from_dict(_load_json(STATE_PATH, {"last_run": None, "seen_ids": []}))


def _load_stories() -> list[Story]:
    return [Story.from_dict(s) for s in _load_json(STORIES_PATH, []) if isinstance(s, dict)]


def _prev_headline() -> str | None:
    metas = site_mod.load_report_metas(REPORTS_DIR)
    return str(metas[0].get("headline")) if metas else None


def _week_context(stories: list[Story]) -> dict:
    """Weekly/monthly context for the report, built from prior daily reports.

    Excludes today (the report being written) — strictly previous days.
    """
    metas = site_mod.load_report_metas(REPORTS_DIR)
    today = date.today().isoformat()
    prior = [m for m in metas if str(m.get("date", "")) < today]

    def _entry(meta: dict) -> dict:
        sentiment = meta.get("sentiment") or {}
        return {
            "date": meta.get("date", ""),
            "headline": meta.get("headline", ""),
            "highlights": [str(h) for h in (meta.get("highlights") or [])][:4],
            "sentiment": sentiment.get("score"),
            "sentiment_label": sentiment.get("label", ""),
            "top_threats": [str(t.get("title") or "") for t in (meta.get("threats") or [])][:3],
        }

    last_week = [_entry(m) for m in prior[:7]]
    month = prior[:30]
    scores = [
        {"date": m.get("date", ""), "score": (m.get("sentiment") or {}).get("score")}
        for m in month
        if isinstance((m.get("sentiment") or {}).get("score"), (int, float))
    ]
    top_stories = [
        {"id": s.id, "title": s.title, "status": s.status, "summary": s.summary}
        for s in stories if s.status in ("active", "updated")
    ][:10]
    return {
        "last_week": last_week,
        "last_month": {
            "reports_count": len(month),
            "sentiment_series": scores,
            "top_stories": top_stories,
        },
    }


# --- git publishing helpers -------------------------------------------------

def _git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True,
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(res.stderr or res.stdout).strip()}")
    return res


def _in_git_repo() -> bool:
    return (ROOT / ".git").exists()


def _ensure_ci_identity() -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    if not _git(["config", "user.name"], check=False).stdout.strip():
        _git(["config", "user.name", "github-actions[bot]"])
    if not _git(["config", "user.email"], check=False).stdout.strip():
        _git(["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])


def git_commit_state(report_date: str) -> bool:
    """Commit reports/ and data/ to the current branch and push. Returns True if pushed."""
    if not _in_git_repo():
        log.warning("Not a git repository - skipping state commit")
        return False
    _ensure_ci_identity()
    _git(["add", "reports", "data"])
    status = _git(["status", "--porcelain", "reports", "data"])
    if not status.stdout.strip():
        log.info("No report/state changes to commit")
        return False
    _git(["commit", "-m", f"Daily report {report_date}"])
    _git(["push", "origin", "HEAD"])
    log.info("Committed and pushed report state for %s", report_date)
    return True


def publish_site_ghpages(site_dir: Path, report_date: str) -> bool:
    """Publish site_dir contents to the gh-pages branch via a temporary worktree."""
    if not _in_git_repo():
        log.warning("Not a git repository - skipping site publish")
        return False
    _ensure_ci_identity()
    site_dir = Path(site_dir)
    if not (site_dir / "index.html").exists():
        log.warning("No built site at %s - skipping publish", site_dir)
        return False

    worktree = ROOT / ".gh-pages-wt"
    if worktree.exists():
        _git(["worktree", "remove", "--force", str(worktree)], check=False)
        shutil.rmtree(worktree, ignore_errors=True)
    _git(["worktree", "prune"], check=False)

    remote_has_ghpages = bool(_git(["ls-remote", "--heads", "origin", "gh-pages"]).stdout.strip())
    try:
        if remote_has_ghpages:
            _git(["worktree", "add", "--detach", str(worktree), "origin/gh-pages"])
            _git(["checkout", "-B", "gh-pages", "origin/gh-pages"], cwd=worktree)
        else:
            log.info("gh-pages branch does not exist yet - creating it")
            _git(["worktree", "add", "--detach", str(worktree), "HEAD"])
            _git(["checkout", "--orphan", "gh-pages"], cwd=worktree)
            _git(["rm", "-rf", "--ignore-unmatch", "."], cwd=worktree, check=False)
            for entry in worktree.iterdir():
                if entry.name == ".git":
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()

        for entry in site_dir.iterdir():
            dest = worktree / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, dest)

        _git(["add", "-A"], cwd=worktree)
        if not _git(["status", "--porcelain"], cwd=worktree).stdout.strip():
            log.info("Site unchanged - nothing to publish")
            return False
        _git(["commit", "-m", f"Publish site {report_date}"], cwd=worktree)
        _git(["push", "origin", "gh-pages"], cwd=worktree)
        log.info("Published site to gh-pages branch")
        return True
    finally:
        _git(["worktree", "remove", "--force", str(worktree)], check=False)
        _git(["worktree", "prune"], check=False)

def _mock_inputs() -> tuple[list[NewsItem], dict]:
    news = [NewsItem.from_dict(d) for d in _load_json(FIXTURES_DIR / "news_items.json", [])]
    social = _load_json(FIXTURES_DIR / "social_raw.json", {})
    log.info("Mock mode: %d fixture news items, %s fixture social payload",
             len(news), "loaded" if social else "missing")
    return news, social


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="int-monitor", description=__doc__)
    parser.add_argument("--date", help="report date (YYYY-MM-DD); default: today (UTC)")
    parser.add_argument("--dry-run", action="store_true",
                        help="do not persist state changes (state/stories stay untouched)")
    parser.add_argument("--skip-social", action="store_true",
                        help="skip the last30days social screening")
    parser.add_argument("--mock", action="store_true",
                        help="use tests/fixtures instead of live collection (no network for sources)")
    parser.add_argument("--push", action="store_true",
                        help="commit reports/data to the current branch and publish site to gh-pages")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config(ROOT / "config.yml")
    report_date = args.date or date.today().isoformat()

    # 1. Collect
    state = _load_state()
    social_raw: dict = {}
    if args.mock:
        new_items, social_raw = _mock_inputs()
    else:
        new_items, state = collect_news.collect_new_items(config, state)
        if not args.skip_social:
            engine = collect_social.ensure_engine(config)
            stdout, _rc = collect_social.run_engine(
                config.social_topic, config.social_days, ENGINE_RUN_DIR, engine,
                config.subreddits,
            )
            social_raw = collect_social.load_engine_output(stdout, ENGINE_RUN_DIR)

    social_items = []
    if not args.skip_social:
        social_items = collect_social.extract_social_items(
            social_raw, config.topic_terms, config.social_max_items
        )
        if len(social_items) < SOCIAL_FALLBACK_MIN:
            social_items = collect_social.social_fallback_search(
                config, config.social_max_items,
            )

    # 2. Analyze
    stories = _load_stories()
    result = analyze_mod.analyze(config, new_items, social_items, stories, _prev_headline(),
                                 _week_context(stories))

    # 3. Write report artifacts
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{report_date}.md").write_text(result.report_md + "\n", encoding="utf-8")
    meta = {
        "date": report_date,
        "headline": result.headline,
        "highlights": result.highlights,
        "sentiment": result.sentiment.to_dict(),
        "threats": [t.to_dict() for t in result.threats],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (REPORTS_DIR / f"{report_date}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 4. Persist state
    if not args.dry_run:
        updated = analyze_mod.prune_stories(result.stories, report_date)
        _save_json(STORIES_PATH, [s.to_dict() for s in updated])
        _save_json(STATE_PATH, state.to_dict())
    else:
        log.info("Dry run: state/stories files left untouched")

    # 5. Build site
    site_mod.build_site(REPORTS_DIR, SITE_DIR, config)

    # 6. Publish
    if args.push:
        git_commit_state(report_date)
        publish_site_ghpages(SITE_DIR, report_date)

    log.info(
        "Done: %d new item(s), %d social post(s), sentiment %s (%+.2f) - %s",
        len(new_items), len(social_items),
        result.sentiment.label, result.sentiment.score, result.headline,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

