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
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import collect_news, collect_social, notify as notify_mod
from . import site as site_mod
from . import synthesize as synth_mod
from .config import load_config
from .models import NewsItem, State

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
STATE_PATH = DATA_DIR / "state.json"
SITE_DIR = ROOT / "site"
ENGINE_RUN_DIR = ROOT / ".last30days-run"
FIXTURES_DIR = ROOT / "tests" / "fixtures"

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
    _git(["commit", "-m", f"Daily brief {report_date}"])
    _git(["push", "origin", "HEAD"])
    log.info("Committed and pushed report state for %s", report_date)
    return True


def publish_site_ghpages(site_dir: Path, report_date: str) -> bool:
    """Publish site_dir contents to the gh-pages branch via a temporary worktree.

    The worktree is mirrored to match the new build exactly: files that are no
    longer part of the site (e.g. pages from the previous site concept) are
    removed from gh-pages.
    """
    if not _in_git_repo():
        log.warning("Not a git repository - skipping site publish")
        return False
    _ensure_ci_identity()
    site_dir = Path(site_dir)
    if not (site_dir / "index.html").exists():
        log.warning("No built site at %s - skipping publish", site_dir)
        return False

    # Use a worktree OUTSIDE the repo dir: the repo lives in OneDrive, whose
    # file locks make git worktrees flaky there (partial rmtree -> non-empty
    # directory -> "worktree add" refuses).
    worktree = Path(tempfile.gettempdir()) / "int-monitor-gh-pages-wt"
    if worktree.exists():
        _git(["worktree", "remove", "--force", str(worktree)], check=False)
        for _ in range(3):
            shutil.rmtree(worktree, ignore_errors=True)
            if not worktree.exists():
                break
            time.sleep(1)
        _git(["worktree", "prune"], check=False)
        if worktree.exists():
            # Transient file locks (AV/indexer) can block the cleanup; fall
            # back to a fresh, uniquely named worktree instead of failing.
            worktree = Path(tempfile.gettempdir()) / (
                "int-monitor-gh-pages-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            )
            log.warning("Falling back to a fresh publish worktree: %s", worktree)

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

        # Mirror semantics: drop files absent from the new build.
        for entry in list(worktree.rglob("*")):
            if entry.name.startswith(".") or entry.is_dir():
                continue
            rel = entry.relative_to(worktree)
            if not (site_dir / rel).exists():
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

def _mock_inputs() -> tuple[list[NewsItem], str]:
    news = [NewsItem.from_dict(d) for d in _load_json(FIXTURES_DIR / "news_items.json", [])]
    evidence = ""
    evidence_path = FIXTURES_DIR / "evidence_pack.md"
    if evidence_path.exists():
        evidence = evidence_path.read_text(encoding="utf-8")
    log.info("Mock mode: %d fixture news items, %s fixture evidence pack",
             len(news), "loaded" if evidence else "missing")
    return news, evidence


def _next_run_id(base: str) -> str:
    """Unique report id: the first run of a day keeps the plain date; later
    runs that day get a UTC `HHMM` suffix so nothing is ever overwritten."""
    if not (REPORTS_DIR / f"{base}.md").exists():
        return base
    stamp = datetime.now(timezone.utc).strftime("%H%M")
    run_id = f"{base}-{stamp}"
    n = 2
    while (REPORTS_DIR / f"{run_id}.md").exists():
        run_id = f"{base}-{stamp}-{n}"
        n += 1
    return run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="int-monitor", description=__doc__)
    parser.add_argument("--date", help="report date (YYYY-MM-DD); default: today (UTC)")
    parser.add_argument("--dry-run", action="store_true",
                        help="do not persist state changes (state stays untouched)")
    parser.add_argument("--skip-social", action="store_true",
                        help="skip the last30days evidence collection")
    parser.add_argument("--mock", action="store_true",
                        help="use tests/fixtures instead of live collection (no source network)")
    parser.add_argument("--push", action="store_true",
                        help="commit reports/data to the current branch and publish site to gh-pages")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = load_config(ROOT / "config.yml")
    report_date = args.date or date.today().isoformat()
    run_id = _next_run_id(report_date)

    # 1. Collect
    state = _load_state()
    if args.mock:
        new_items, evidence = _mock_inputs()
    else:
        new_items, state = collect_news.collect_new_items(config, state)
        evidence = ""
        if not args.skip_social:
            engine = collect_social.ensure_engine(config)
            plan_path = str(ROOT / config.social_plan) if config.social_plan else ""
            evidence = collect_social.run_evidence_pack(
                config.social_topic, config.social_days, ENGINE_RUN_DIR, engine,
                config.social_search, config.subreddits, plan_path,
            )
    if evidence and len(evidence) > config.evidence_max_chars:
        evidence = evidence[: config.evidence_max_chars] + "\n\n[... evidence truncated ...]"

    # 2. Synthesize (the badge line is handled programmatically)
    brief_md = synth_mod.synthesize(config, evidence, new_items)

    # 3. Write report artifacts (never overwrite: run_id is unique per run)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / f"{run_id}.md").write_text(brief_md + "\n", encoding="utf-8")
    meta = {
        "date": report_date,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "news_count": len(new_items),
        "evidence_chars": len(evidence),
    }
    (REPORTS_DIR / f"{run_id}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # 4. Persist state
    if not args.dry_run:
        _save_json(STATE_PATH, state.to_dict())
    else:
        log.info("Dry run: state file left untouched")

    # 5. Build site
    site_mod.build_site(REPORTS_DIR, SITE_DIR, config)

    # 6. Publish
    if args.push:
        git_commit_state(report_date)
        if publish_site_ghpages(SITE_DIR, run_id):
            notify_mod.notify_publish(config, run_id, len(new_items), len(evidence))

    log.info("Done: %d new item(s), evidence %d chars -> %s",
             len(new_items), len(evidence), (REPORTS_DIR / f"{run_id}.md").name)
    return 0


if __name__ == "__main__":
    sys.exit(main())


