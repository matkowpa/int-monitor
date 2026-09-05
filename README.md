# Intrum AB Daily Monitor (int-monitor)

**Live site: https://matkowpa.github.io/int-monitor/**

A day-by-day archive of **/last30days research briefs on Intrum AB**, published
every morning to a static website served by **GitHub Pages**. Every daily page
is the result of one automated run of the [last30days](https://github.com/mvanhorn/last30days-skill)
skill: the engine collects what people actually say across Reddit, Hacker News,
YouTube, StockTwits, Polymarket and the web, and an LLM (via OpenRouter) writes
the synthesis in the skill's own output format — a mandatory badge line, a
**"What I learned:"** prose synthesis, source-coverage blocks and the engine's
stats footer verbatim. New RSS news items (Google News EN/SV + press releases)
are woven directly into the same narrative.

## How it works

```
GitHub Actions (cron 08:30 + 15:30 UTC)
        |
        v
1. COLLECT     Google News RSS (EN + SV + GlobeNewswire press releases)
2. EVIDENCE    last30days engine (headless, --emit compact): Reddit (global
               full-text search), TikTok, Instagram, YouTube, HN, Polymarket,
               GitHub  ->  evidence pack
               (badge + evidence blocks + stats footer)
3. SYNTHESIZE  OpenRouter LLM -> "What I learned" brief in the /last30days
               skill output format, RSS news integrated with links
4. PUBLISH     reports/*.md + data/state committed to main,
               static site pushed to gh-pages -> GitHub Pages
               (day-by-day archive; gh-pages is mirrored to the new build)
```

- **Every run is kept**: the first run of a day is `reports/YYYY-MM-DD.md`;
  any later run the same day becomes `YYYY-MM-DD-HHMM.md` (UTC) — repeated
  queries never overwrite each other and each run gets its own archive page.
- **Badge & footer**: the engine's first line (badge) is passed through
  programmatically, so every brief is authentic to the skill's contract even
  if the LLM misbehaves.
- **Never-breaks design**: a failed feed, empty social screening or LLM outage
  still publishes a (fallback) brief for the day — raw evidence + news list.
- **RSS dedupe**: `data/state.json` stores seen item ids, so the synthesis
  focuses on what appeared since the previous run.

## Repository layout

| Path | Purpose |
| --- | --- |
| `config.yml` | All tuning: feeds, terms, sources, model, subreddits, site metadata |
| `engine-plan.json` | Fixed last30days query plan (deterministic subqueries: community, corporate, transactions) |
| `src/` | Pipeline code (collect_news, collect_social, synthesize, site, run) |
| `reports/` | Generated daily briefs (`YYYY-MM-DD.md` + `.meta.json`) |
| `data/` | Persisted state (seen RSS ids) |
| `site/` | Build output (pushed to `gh-pages` by CI, not committed to `main`) |
| `.last30days/`, `.last30days-run/` | Runtime only (cloned engine, debug output) |

## Run locally

```powershell
pip install -r requirements.txt

# offline end-to-end with fixtures (no source network needed; LLM still called)
python -m src.run --mock --dry-run

# real run (uses OPENROUTER_API_KEY from the environment; does not commit anything)
python -m src.run --dry-run

# full run incl. committing reports/data and publishing the site (CI only)
python -m src.run --push
```

The last30days engine is acquired automatically: local Cline skill copy if
present, otherwise cloned from GitHub into `.last30days/`.

## GitHub setup

1. Repo: https://github.com/matkowpa/int-monitor (public)
2. Repo **Settings -> Secrets and variables -> Actions**:
   add `OPENROUTER_API_KEY` (required), `SCRAPECREATORS_API_KEY` (Instagram/TikTok)
   and `BRAVE_API_KEY` (web/grounding). Locally the engine reads the last two
   from its own global config (`~/.config/last30days/.env`).
3. Repo **Settings -> Pages**: Source = *Deploy from a branch*, Branch = `gh-pages`, `/ (root)`.
4. The workflow runs twice daily (08:30 and 15:30 UTC); trigger
   **Actions -> daily-monitor -> Run workflow** for an immediate run.
   Repeated runs the same day never overwrite each other — each run gets
   its own archive page (`YYYY-MM-DD-HHMM`).

## Configuration notes

- **Model**: `llm.model` in `config.yml`. Any OpenRouter chat model id works.
- **Schedule**: `cron` in `.github/workflows/daily.yml`.
- **Sources**: add/remove RSS entries under `rss_sources`; engine sources under
  `social.search`. Do **not** set `social.subreddits` — it replaces Reddit's
  global full-text search (which finds Intrum posts) with listing-scans of a
  few subreddits (which find none).
- **Evidence size**: `social.evidence_max_chars` caps what is fed to the LLM.
- **Engine version**: `last30days.ref` in `config.yml` — pin a tag/commit for
  reproducible CI runs.

## Limitations

- The brief is machine-generated; always verify against the linked sources.
- Without API keys, social coverage is limited to Reddit's global search and
  whatever the keyless endpoints return — quiet results are normal for a
  company with little social chatter. Web/grounding needs a `BRAVE_API_KEY` or
  `SERPER_API_KEY` for the engine; Instagram/TikTok need `SCRAPECREATORS_API_KEY`.
  All three are configured as repo secrets, so CI runs get the full coverage;
  local runs degrade gracefully when a key is missing.
- Google News links are redirects; RSS dedupe is by title, not final URL.