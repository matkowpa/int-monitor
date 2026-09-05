# Intrum AB Daily Monitor (int-monitor)

**Live site: https://matkowpa.github.io/int-monitor/**

Fully automated daily intelligence on **Intrum AB**: news, market coverage, social-media
screening, threat analysis and sentiment — published every morning to a static website
served by **GitHub Pages**.

## How it works

```
GitHub Actions (daily cron 06:30 UTC)
        |
        v
1. COLLECT   Google News RSS (EN + SV + GlobeNewswire press releases)
2. SCREEN    last30days engine (headless): Reddit, HN, YouTube, web
             + keyless Reddit/HN fallbacks when the engine comes up empty
3. ANALYZE   OpenRouter LLM -> daily report (new items, ongoing stories,
             sentiment score, threats) as strict JSON
4. PUBLISH   reports/*.md + data/state committed to main,
             static site pushed to gh-pages -> GitHub Pages
```

- **"New since last publish"**: every run dedupes against `data/state.json`
  (seen item ids), so the report focuses on what appeared since the last run.
- **Weekly context**: every report contains a **"Last Week Highlights"** section —
  an LLM synthesis of the previous 7 days plus a last-month overview (sentiment
  trend, reports count, top ongoing stories), built from the site's own report
  history.
- **Story continuity**: `data/stories.json` tracks ongoing stories between runs;
  the LLM is asked to update statuses (active / updated / resolved).
- **Never-breaks design**: a failed feed, empty social screening or LLM outage
  still publishes a (fallback) report for the day.

## Repository layout

| Path | Purpose |
| --- | --- |
| `config.yml` | All tuning: feeds, terms, model, subreddits, site metadata |
| `src/` | Pipeline code (collect_news, collect_social, analyze, site, run) |
| `reports/` | Generated daily reports (`YYYY-MM-DD.md` + `.meta.json`) |
| `data/` | Persisted state (seen ids, tracked stories) |
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

The last30days engine is acquired automatically: local Cline skill copy if present,
otherwise cloned from GitHub into `.last30days/`.

## GitHub setup

1. ~~Create a **public** repository~~ — done: https://github.com/matkowpa/int-monitor
2. ~~Push `main`~~ — done.
3. Repo **Settings -> Secrets and variables -> Actions**:
   add `OPENROUTER_API_KEY` (required). Optionally add `SCRAPECREATORS_API_KEY`
   to unlock X/Twitter coverage in the social screening.
4. Trigger the workflow once: **Actions -> daily-monitor -> Run workflow**.
   The first run creates the `gh-pages` branch.
5. Repo **Settings -> Pages**: Source = *Deploy from a branch*, Branch = `gh-pages`, `/ (root)`.
6. ~~Set the published URL in `config.yml`~~ — done (`site.base_url` =
   `https://matkowpa.github.io/int-monitor/`).

## Configuration notes

- **Model**: `llm.model` in `config.yml` (default `google/gemini-2.5-flash`,
  ≈ $0.01–0.05 per daily run). Any OpenRouter chat model id works.
- **Schedule**: `cron` in `.github/workflows/daily.yml`.
- **Sources**: add/remove RSS entries under `rss_sources`; feeds that fail only
  produce a warning.
- **Social screening**: the last30days engine runs keyless (Reddit, HN, YouTube,
  StockTwits, web) and its keyless backends are sometimes IP-blocked — a direct
  Reddit/HN fallback fills the gap. Adding a `SCRAPECREATORS_API_KEY` secret
  activates X/Twitter and richer sources without code changes.
- **Engine version**: `last30days.ref` in `config.yml` — pin a tag/commit for
  reproducible CI runs.

## Limitations

- Summaries are machine-generated; always verify against the linked sources.
- Without API keys, social coverage is limited to what keyless Reddit/HN
  endpoints return — quiet results are normal for a company with little
  social chatter.
- Google News links are redirects; dedupe is by title, not final URL.
