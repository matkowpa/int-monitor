"""Social / alternative source screening via the last30days engine.

The engine (MIT, https://github.com/mvanhorn/last30days-skill) is pure Python
stdlib. It is run headless with ``--emit compact``, producing an "evidence
pack": a mandatory badge line, per-source evidence blocks for the synthesizing
model, and a stats footer. Keyless sources (Reddit, Hacker News, YouTube,
StockTwits, Polymarket, web) work out of the box; an optional
SCRAPECREATORS_API_KEY in the environment unlocks X/Twitter without any code
change.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

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


def run_evidence_pack(topic: str, days: int, save_dir: Path, engine_path: Path,
                      search: str = "", subreddits: str = "",
                      plan_path: str = "") -> str:
    """Run the engine headless and return its stdout (the evidence pack).

    Returns "" on failure — a broken engine must never abort the run.
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(engine_path), topic,
        "--emit", "compact",
        "--days", str(days),
        "--no-browser-cookies",
        "--save-dir", str(save_dir),
        "--quick",
    ]
    if search.strip():
        cmd += ["--search", search.strip()]
    if subreddits.strip():
        cmd += ["--subreddits", subreddits.strip()]
    if plan_path and os.path.isfile(plan_path):
        # Fixed query plan (engine-plan.json): deterministic subqueries that
        # skip the engine's internal LLM planner, so every run covers community
        # chatter, corporate/financials and transaction stories.
        cmd += ["--plan", plan_path]
    env = dict(os.environ)
    log.info("Running last30days engine (this can take a few minutes)...")
    try:
        # The engine emits UTF-8 regardless of platform; without an explicit
        # encoding Windows decodes with the ANSI codepage and crashes.
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=ENGINE_TIMEOUT_S, env=env,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            log.warning("Engine exited with code %d; stderr tail: %s",
                        proc.returncode, (proc.stderr or "")[-500:])
        return proc.stdout or ""
    except subprocess.TimeoutExpired:
        log.warning("Engine timed out after %ds", ENGINE_TIMEOUT_S)
    except OSError as exc:
        log.warning("Engine failed to start: %s", exc)
    return ""
