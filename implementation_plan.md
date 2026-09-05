# Implementation Plan — Intrum AB Daily Monitor

## [Overview]

Build a fully automated daily media-monitoring pipeline for **Intrum AB** (Swedish debt-collection company) that runs on GitHub Actions, collects news and social-media mentions, uses an OpenRouter LLM to write an English daily intelligence report (new items since last publish + updates on ongoing stories + threat/sentiment screening via the `last30days` engine), and publishes it to a Python-generated static website served by **GitHub Pages** from a public repository.

**Scope & context:**
- Workspace is empty (only `TODOs.txt`) — greenfield project, no legacy constraints.
- Daily run is **fully automated** (user decision): GitHub Actions cron → collect → analyze → publish. Cline is only used to bootstrap the repo and for maintenance.
- Reports are in **English** (user decision).
- Publishing: **GitHub Pages from a public repo, Python-generated static site** (user decision). No `gh` CLI on this machine — repo is created via github.com in the browser; `git` (2.55) and Python 3.12 are available.
- `OPENROUTER_API_KEY` exists at user level (no OpenAI/ScrapeCreators/Perplexity/Brave keys). It must be added as a GitHub repo secret. LLM cost ≈ $0.01–0.05/day depending on model (default: `google/gemini-2.5-flash`, configurable in `config.yml`).
- The `last30days` skill (MIT, https://github.com/mvanhorn/last30days-skill) is **not vendored into the repo**; the workflow (or `src/collect_social.py`) clones a pinned ref at runtime into `.last30days/` and runs its Python engine headless (`--emit json --json-profile raw --days 30 --no-browser-cookies --quick`). The engine is pure Python stdlib — no pip install needed for it.
- **Limitation:** X/Twitter coverage requires a paid `SCRAPECREATORS_API_KEY`; initially the social screening runs on keyless sources (Reddit, Hacker News, YouTube, StockTwits, keyless web). The workflow forwards optional secrets if they are ever added — no code change needed.
- The site must still update on a "quiet day": if no new items are found or the LLM call fails, a fallback raw-items/"quiet day" report is published.

## [Types]

All types are `@dataclass` definitions in `src/models.py`:

- `NewsItem`: `id: str` (sha1 of source+title), `title: str`, `url: str`, `source: str` (feed name), `published: datetime | None` (UTC), `snippet: str` (RSS summary, HTML stripped, max 500 chars).
- `SocialItem`: `id: str`, `platform: str` ("reddit"|"hackernews"|"youtube"|"stocktwits"|"web"), `title: str`, `url: str`, `snippet: str`, `engagement: str`, `posted: datetime | None` (UTC, may be None).
- `Story`: `id: str` (stable slug), `title: str`, `status: str` (`"active"|"updated"|"resolved"`), `summary: str` (1–3 sentences), `first_seen: str` (ISO date), `last_seen: str` (ISO date), `urls: list[str]` (max 5).
- `Threat`: `title: str`, `severity: str` (`"high"|"medium"|"low"`), `summary: str`.
- `Sentiment`: `score: float` (-1.0..1.0), `label: str`, `rationale: str`.
- `AnalysisResult`: `headline: str`, `report_md: str`, `highlights: list[str]`, `threats: list[Threat]`, `sentiment: Sentiment`, `stories: list[Story]`.
- `State`: `last_run: str | None`, `seen_ids: list[str]` (capped at 3000, FIFO).
- `Config` (in `src/config.py`): `company`, `topic_terms`, `rss_sources` (list of dicts: name, url, always_relevant), `lookback_hours` (48), `social_days` (30), `max_new_items` (40), `model`, `site` (title/description/base_url), `last30days_repo`, `last30days_ref`, `report_language`.

Persistent state files (committed to repo by the workflow, so the next run diffs against them):
- `data/state.json` — `State` as JSON.
- `data/stories.json` — `list[Story]` as JSON.

LLM contract — `analyze.py` asks OpenRouter for **strict JSON** (extracted from optional ```json fences, schema validation + one retry with error feedback):
```json
{"headline": "...", "report_md": "## markdown...", "highlights": ["..."],
 "sentiment": {"score": -0.2, "label": "negative", "rationale": "..."},
 "threats": [{"title": "...", "severity": "high", "summary": "..."}],
 "stories": [{"id": "slug", "title": "...", "status": "active|updated|resolved",
              "summary": "...", "first_seen": "2026-09-05", "last_seen": "2026-09-05",
              "urls": ["..."]}]}
```
`report_md` uses only these H2 sections: `## New Today`, `## Ongoing Stories`, `## Social & Sentiment`, `## Threats & Risks`.

## [Files]

New files (repo root = `c:\Users\alusm\OneDrive\Dokumenty\Tata\projekty\int-monitor`):

- `.github/workflows/daily.yml` — GitHub Actions workflow: `schedule: cron "30 6 * * *"` (06:30 UTC = 08:30 CEST) + `workflow_dispatch`; steps: checkout (fetch-depth 0) → setup-python 3.12 → `pip install -r requirements.txt` → `python -m src.run --push` with env `OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}` (plus optional `SCRAPECREATORS_API_KEY`). `permissions: contents: write`. `--push` commits `reports/` + `data/` to `main` and pushes `site/` to `gh-pages` (git worktree based).
- `.gitignore` — `.last30days/`, `site/`, `__pycache__/`, `.env`, `*.pyc`.
- `requirements.txt` — `feedparser`, `jinja2`, `markdown`, `pyyaml` (pinned majors).
- `config.yml` — company/topic terms, RSS feeds, model ID, site metadata, last30days repo/ref.
- `README.md` — project description, local run instructions, GitHub setup (create repo, add `OPENROUTER_API_KEY` secret, enable Pages on `gh-pages`), how to change model/schedule.
- `src/models.py` — dataclasses + JSON (de)serialization helpers.
- `src/config.py` — `load_config(path) -> Config`.
- `src/collect_news.py` — RSS collection, relevance filter, dedupe.
- `src/collect_social.py` — last30days engine acquisition (local path → `.last30days/` clone) + headless run + tolerant JSON parsing.
- `src/analyze.py` — OpenRouter call, prompt construction, strict-JSON parsing with retry/fallback.
- `src/site.py` — Jinja2 rendering + markdown → HTML.
- `src/templates/` — `base.html`, `index.html`, `report.html` + `static/style.css`.
- `src/run.py` — pipeline CLI orchestrator (`python -m src.run [--date YYYY-MM-DD] [--dry-run] [--skip-social] [--push] [--mock]`) + git publish helpers.
- `data/state.json`, `data/stories.json` — seeded empty, committed.
- `reports/` — one `YYYY-MM-DD.md` + `YYYY-MM-DD.meta.json` per day, committed.
- `tests/fixtures/` — sample `news_items.json`, `social_raw.json` for `--mock` mode.
- `TODOs.txt` — kept as-is.

Runtime-only (not in repo): `.last30days/` (cloned engine), `site/` (build output, pushed to `gh-pages` only).

## [Functions]

- `src/config.py` — `load_config(path: str = "config.yml") -> Config`: read YAML, apply defaults, validate required keys.
- `src/collect_news.py`:
  - `fetch_feed(name, url, always_relevant) -> list[NewsItem]` (feedparser; per-feed failures warn, never abort).
  - `strip_html(text) -> str`; `is_relevant(item, terms)` — case-insensitive term match in title/snippet.
  - `collect_new_items(config, state) -> tuple[list[NewsItem], State]` — fetch all feeds, filter, drop ids in `state.seen_ids`, drop items older than `lookback_hours` (first run: 7 days), sort desc, cap `max_new_items`, update `state.seen_ids`.
- `src/collect_social.py`:
  - `ensure_engine(config) -> Path` — use `.last30days/scripts/last30days.py` if present; else local skill dir (env `LAST30DAYS_SKILL_DIR` or `~/.cline/skills/last30days`); else `git clone --depth 1 -b <ref> <repo> .last30days`.
  - `run_engine(topic, days, save_dir, engine_path) -> dict` — subprocess with `--emit json --json-profile raw --days N --no-browser-cookies --save-dir --quick`, timeout 480 s; on failure return `{}` with warning.
  - `load_engine_output(save_dir, stdout) -> dict` — prefer `<save_dir>/last-report.json`, fallback: JSON object extracted from stdout.
  - `extract_social_items(raw, terms) -> list[SocialItem]` — tolerant recursive walk for dicts with url+text/title keys; platform from source/platform/subreddit keys; term filter; cap 30.

- `src/analyze.py`:
  - `build_messages(config, new_items, social_items, stories, prev_headline) -> list[dict]` — analyst system prompt; rules: only use supplied items, every claim linked, strict JSON schema, English.
  - `call_openrouter(model, messages, api_key) -> str` — stdlib `urllib.request` POST to `https://openrouter.ai/api/v1/chat/completions`, 120 s timeout, temperature 0.2.
  - `parse_analysis(text) -> AnalysisResult` — strip fences, `json.loads`, validate/coerce; raise `AnalysisError`.
  - `analyze(config, new_items, social_items, stories) -> AnalysisResult` — call → parse → 1 retry with error feedback → `build_fallback_report(...)` (templated raw-items report, neutral sentiment, stories unchanged).
  - `prune_stories(stories) -> list[Story]` — drop resolved stories with `last_seen` older than 14 days; cap at 20.
- `src/site.py` — `build_site(reports_dir, out_dir, config) -> None`: read all `*.meta.json` sidecars (newest first), render `index.html` (latest report + highlights + sentiment + threats + archive) and per-day `reports/YYYY-MM-DD.html`; markdown via `markdown` lib (extensions: tables, fenced_code); copy `style.css`.
- `src/run.py` — `main(argv)`; `git_commit_state(date)` (commits `reports/` + `data/` to current branch); `publish_site_ghpages(site_dir)` (git worktree of `gh-pages`, copy `site/`, commit, push); both no-op-safe when nothing changed or not a git repo.

## [Classes]

No behavioral classes — function/dataclass based (dataclasses in [Types]).

## [Dependencies]

- Python 3.12 (local + `actions/setup-python`); stdlib for HTTP/subprocess/JSON/hashlib.
- `feedparser`, `jinja2`, `markdown`, `pyyaml` via `requirements.txt`.
- OpenRouter REST API (`/api/v1/chat/completions`) — GitHub secret `OPENROUTER_API_KEY`.
- `last30days` engine (MIT) cloned at runtime to `.last30days/` — pure stdlib, keyless sources.

## [Testing]

Validation by execution (no test framework — pipeline project):
1. **Offline:** `python -m src.run --mock --dry-run` — fixtures instead of live collection; verifies analysis → report files → `site/index.html`.
2. **Live local:** `python -m src.run --dry-run` (env `OPENROUTER_API_KEY` present) — real feeds, real engine, real LLM; inspect `reports/<today>.md` quality.
3. **Dedupe/continuity:** run twice; second run must find ~0 new items; stories carried into next prompt.
4. **CI:** push → `workflow_dispatch` → verify Actions log + published Pages URL.

## [Implementation Order]

1. `git init` + `.gitignore` / `requirements.txt` / `config.yml` / `src/models.py` / `src/config.py` → imports load.
2. `src/collect_news.py` + live feed fetch test.
3. `src/collect_social.py` + headless engine validation locally.
4. `src/analyze.py` + real OpenRouter call.
5. `src/templates/` + `src/site.py` + `src/run.py` → `--mock --dry-run` builds site; live `--dry-run` produces full report.
6. Second live run → dedupe + story continuity verified.
7. `.github/workflows/daily.yml` + `README.md`.
8. Create public GitHub repo via browser, add remote, add `OPENROUTER_API_KEY` secret, push `main`.
9. Enable GitHub Pages → `gh-pages` → verify published URL.

## Assumptions & risks

- Google News RSS links are `news.google.com` redirects — used as-is; dedupe by title hash, not URL.
- Keyless last30days sources may occasionally rate-limit; wrapper degrades gracefully (warning, empty social section).
- Run must stay under ~10 min (engine `--quick` + 480 s subprocess timeout).
- Adding `SCRAPECREATORS_API_KEY` as a repo secret later activates X coverage without code changes.
- Deviation from original sketch: `config.yml` parsing adds `pyyaml` (stdlib has no YAML); state files are JSON.


