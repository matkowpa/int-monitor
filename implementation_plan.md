# Implementation Plan — Przebudowa int-monitor: dzienne archiwum briefów last30days dla Intrum

## [Overview]

Zamiana strony z „raportów analityka LLM" na **dzień po dniu archiwum wyników odpalenia skilla `last30days` dla Intrum**: codziennie silnik last30days zbiera dane (Reddit, HN, YouTube, StockTwits, Polymarket, web — keyless; opcjonalnie X przy kluczu SCRAPECREATORS), a LLM przez OpenRouter pisze syntezę **„What I learned"** dokładnie w formacie skilla (badge, transformed prose, source coverage, footer verbatim). Newsy RSS są **integrowane bezpośrednio w treść briefa** jako jedna całość. Zakres: modyfikacja istniejącego repo — usuwamy stary koncept (stories/sentiment/threats/„Last Week Highlights"/JSON-contract), zostaje: RSS → silnik → synteza → strona.

Kluczowe decyzje:
- Silnik emituje `--emit compact` = evidence pack z **badge w linii 1** + blokami „evidence for synthesis" + pass-through footer (statystyki źródeł) — dokładnie to, co agent czyta przy interaktywnym użyciu skilla. To wejście do LLM.
- Badge wycinamy z evidence i **doklejamy programowo** do finalnego briefa — gwarancja zgodności z kontraktem skilla niezależnie od LLM.
- Headless test (2026-09-05) wykazał problem jakości przy `--quick` bez flag (klastery off-topic) — plan zawiera tuning flag (`--search` z jawną listą źródeł keyless).
- Stare raporty (`reports/2026-09-05.*`) usuwamy — nie pasują do nowego konceptu (decyzja użytkownika).

## [Types]

Zmiany w `src/models.py`:
- **USUWANE:** `Story`, `Threat`, `Sentiment`, `AnalysisResult`, `SocialItem`, `parse_iso`.
- **ZOSTAJĄ:** `NewsItem`, `State`, helpery (`utcnow`, `iso`, `make_id`).
- Kontrakt briefa = zwykły markdown: linia 1 = badge `🌐 last30days v{VERSION} · synced {YYYY-MM-DD}`, dalej `What I learned:` (proza, bez wymyślonych tytułów/sekcji — LAW 2/4), source coverage, footer statystyk verbatim (LAW 5), zakaz trailing `Sources:` (LAW 1). RSS newsy wplecione w narrację z linkami.
- `data/stories.json` — **usuwany**. `data/state.json` — zostaje (dedupe RSS). `reports/*.meta.json` — minimalizowane do `{"date", "generated_at", "news_count", "evidence_chars"}`.

## [Files]

- **Nowy:** `src/synthesize.py` — prompt wg kontraktu skilla + OpenRouter (markdown, nie JSON) + fallback.
- **Modyfikowane:** `src/collect_social.py` (`run_evidence_pack` z `--emit compact` + `--search`; usunięcie `run_engine`, `load_engine_output`, `extract_social_items`, `social_fallback_search`, `_engagement_str`), `src/run.py` (nowy przepływ + mirror-publish usuwa stare strony z gh-pages), `src/site.py` + `src/templates/index.html`/`report.html`/`base.html`, `config.yml` (`social.search`, `evidence_max_chars`, `llm.max_tokens: 8000`), `src/config.py` (`social_search`, `evidence_max_chars`), `README.md`.
- **Usuwane:** `src/analyze.py`, `data/stories.json`, `reports/2026-09-05.*`, `tests/fixtures/social_raw.json` (zastąpiony `tests/fixtures/evidence_pack.md`).
- **Bez zmian:** `.github/workflows/daily.yml` (poza komentarzem kroku), `.gitignore`, `requirements.txt`, `src/collect_news.py`, `src/templates/404.html`, `static/style.css`.

## [Functions]

- **Nowe** `src/synthesize.py`:
  - `split_badge(evidence: str) -> tuple[str, str]` — wyodrębnia linię badge z evidence.
  - `build_messages(config, evidence, news_items) -> list[dict]` — system prompt: kontrakt skilla (`What I learned:` proza; transformuj, nie kopiuj; zakaz wymyślonych nagłówków i `Sources:`; footer verbatim; newsy RSS wplecione z linkami; EN; claim = link).
  - `call_openrouter(model, messages, api_key, temperature, max_tokens) -> str` — przeniesiona z analyze.py.
  - `synthesize(config, evidence, news_items) -> str` — badge + znormalizowana odpowiedź LLM (1 retry); fallback: badge + notka + surowy evidence + lista RSS.
- **Modyfikowane:**
  - `src/collect_social.py::run_evidence_pack(topic, days, save_dir, engine_path, search, subreddits) -> str` — `--emit compact`, `--days`, `--no-browser-cookies`, `--quick`, `--save-dir`, `--search`; timeout 480 s; błędy → `""` + warning.
  - `src/run.py::main(argv)` — 1) RSS `collect_new_items`, 2) `run_evidence_pack` + trunc do `evidence_max_chars`, 3) `synthesize` → `reports/{date}.md` + minimalne meta, 4) `state.json`, 5) `build_site`, 6) `--push`. `publish_site_ghpages` dostaje mirror-semantics (usuwa z gh-pages pliki nieobecne w nowym buildzie — sprząta stare strony konceptu). Flagi CLI bez zmian.
- **Usuwane:** `run_engine`, `load_engine_output`, `extract_social_items`, `social_fallback_search`, `analyze`, `build_fallback_report`, `parse_analysis`, `prune_stories`, stare `build_messages`, `_week_context`, `_load_stories`, `_prev_headline`; `_mock_inputs` zwraca teraz `(news, evidence_str)`.

## [Classes]

Brak klas behawioralnych (dataclassy w [Types]). Żadnych nowych klas.

## [Dependencies]

Bez zmian: Python 3.12, `feedparser`, `jinja2`, `markdown`, `pyyaml`, OpenRouter REST (`OPENROUTER_API_KEY`), silnik last30days (klon do `.last30days/` w CI / `~/.cline/skills/last30days` lokalnie). Koszt: 1 wywołanie LLM/dzień, kontekst ~35k znaków. Model: użytkownik lokalnie ustawił `z-ai/glm-5.3` (zmiana zachowana, niescommitowana decyzja użytkownika).

## [Testing]

1. **Offline:** `python -m src.run --mock --dry-run` — fixture `evidence_pack.md` + `news_items.json` → badge w linii 1, `What I learned:`, brak wymyślonych nagłówków, footer, site się buduje.
2. **Live lokalnie:** `python -m src.run --dry-run` — realny silnik z nowymi flagami + realna synteza OpenRouter → ocena briefa.
3. **Dedupe RSS:** drugi bieg — newsy się nie duplikują (state.json).
4. **Fallbacki:** brak `OPENROUTER_API_KEY` → brief z surowym evidence; martwy silnik → brief tylko z RSS.
5. **CI:** push → `workflow_dispatch` → weryfikacja Pages.

## [Implementation Order]

1. `implementation_plan.md` (ten dokument).
2. `src/models.py` — usunięcie starych typów → verify: import.
3. `src/synthesize.py` (nowy) + usunięcie `src/analyze.py` → verify: import.
4. `src/collect_social.py` → `run_evidence_pack` + `split_badge`... (split_badge w synthesize.py).
5. `config.yml` + `src/config.py`.
6. `src/run.py` — przepięcie pipeline'u.
7. `src/site.py` + szablony; usunięcie starych artefaktów.
8. `tests/fixtures/evidence_pack.md` + `README.md`.
9. `--mock --dry-run` → verify offline.
10. Live `--dry-run` → verify jakości briefa.
11. Commit + push; `--push` publikuje dzisiejszy brief + nowy site na gh-pages (mirror czyści stare strony).

## Assumptions & ryzyka

- Jakość keyless źródeł dla B2B jak Intrum bywa skromna — niektóre dni będą krótkie; „quiet day brief" to uczciwy wynik (skill nigdy nie fabrykuje tematów). `SCRAPECREATORS_API_KEY` odblokuje X bez zmian w kodzie.
- Odpowiedź LLM to markdown (bez JSON-parsowania) — prostsze i odporniejsze; ryzyko improwizacji łagodzi sztywny system prompt + programowy badge + footer w wejściu.
- Silnik na ref `main` — pinning do rozważenia po stabilizacji.
- Lokalna, niescommitowana zmiana użytkownika: `llm.model: z-ai/glm-5.3` — zachowana.

