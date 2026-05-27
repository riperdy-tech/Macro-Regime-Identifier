# Macro Regime Indicator — Handoff to Codex

> Author: Claude Opus 4.7 (transferring scope).
> Date: 2026-05-26.
> Target: Codex picks up MGI work from here. Stock Screener stays with Claude.
> Both repos exist side-by-side under `C:\Users\riper\Downloads\Stock Screener\`.

---

## 0. TL;DR

**MGI status:** alive, infrastructure mostly in place, news layer immature.

- Macro engine (FRED-driven cyclical regime + GICS sector diagnostic): **working, calibration weak/mixed per its own self-reports**.
- News/theme layer: **`insufficient_history`** (3-20% confidence per its own §11/§12 reports). Only synthetic-mock news source is enabled. All real-news sources disabled.
- Secular-theme taxonomy: **just added (WS2-T6)**. 9 themes parallel to existing 18 macro themes. Empty of actual scoring weight — wiring exists, propagation through scoring not done.

**Stock Screener interface:** MGI's role per brief is "weak overlay, never load-bearing on paradigm scoring until MGI matures." Stock Screener reads FRED data directly via its own `fetch_macro_state.py`; it does NOT read MGI's processed regime/news output yet. WS-3 (wiring MGI into Stock Screener) is **blocked on MGI maturity gate** and not yet started.

**Codex's first job:** read this doc front-to-back, then pick the highest-leverage item from §4 Roadmap.

---

## 1. Repo & Context Map

### Two parallel repos, two GitHub remotes

| Repo | Local path | GitHub | Purpose |
|---|---|---|---|
| **MGI** (this repo) | `C:\Users\riper\Downloads\Stock Screener\Macro Regime Indicator\` | `https://github.com/riperdy-tech/Macro-Regime-Identifier` | Macro regime diagnostic + news/theme tracker. Deploys to GH Pages. |
| **Stock Screener** | `C:\Users\riper\Downloads\Stock Screener\Stock Screener\` | `https://github.com/riperdy-tech/stock-screener` | Reverse-engine stock screener + Paradigm dimension. Deploys to Vercel (`stock-screen-rt.vercel.app`). |

Workspace root (`C:\Users\riper\Downloads\Stock Screener\`) is NOT a git repo. It's a shared parent dir with shared `.env`, shared task-queue tooling (`scripts/launch_workers.py`, `scripts/deepseek_worker.py`), and a `tasks/` queue. Both repos can be worked simultaneously from this parent.

### The brief

Single source of truth: **`tasks/briefs/paradigm_orchestration_brief.md`** (workspace root, lives in Stock Screener repo's tasks dir). Read this verbatim. Key passages relevant to MGI:

- §1: "The Macro Regime Indicator is a macro-regime + cyclical-sector diagnostic. It is NOT a paradigm engine. Macro regime = the cyclical weather (months); paradigm = the secular climate (years). Its 11 GICS sectors cannot distinguish NVIDIA from a generic IT company."
- §1: "WS-2's job is therefore TWO things: (a) backfill history so the existing macro/sector diagnostic becomes statistically usable, and (b) extend (add, never remove) the theme layer from '12 sector-news groups' into a real secular-theme tracker that WS-1 can consume."
- §1: "Until WS-2 reaches its own `monitor_ready`/`validation_candidate` threshold, its outputs are weak overlays, never keynote/load-bearing inputs."
- §3 standing constraints apply to MGI too: additive only on existing systems, no fabricated data, deterministic, no network in scoring code, STOP-and-report on contradiction.

### The MGI audit (what's actually there)

**`docs/ws2_discovery_audit.md`** — read this. Comprehensive read-only audit of MGI as of 2026-05-25. 7 sections + tables. Authoritative on current architecture, module roles, news layer maturity, history coverage, self-reports, surface area for follow-up work, and known risks. **Anything in this handoff that contradicts the audit, trust the audit.**

---

## 2. Tech Stack & Setup

### Runtime
- Python ≥ 3.11 (per `pyproject.toml`)
- Dependencies: pandas, numpy, requests, pydantic, duckdb, yaml, dotenv, openai, pytest

### Setup checklist (from clean clone)
```bash
cd "Macro Regime Indicator"
python -m venv .venv
.venv\Scripts\activate           # Windows
# OR: source .venv/bin/activate  # POSIX
pip install -e ".[dev]"          # or pip install -e . then pytest separately
```

### Required env vars (in `.env`)
- **`FRED_API_KEY`** — for FRED data ingestion (free key from https://fred.stlouisfed.org/docs/api/api_key.html). **Already populated** in local `.env`.
- **`DEEPSEEK_API_KEY`** — for news classification. Currently populated.
- Other vars per `.env.example`.

### Critical config files
- `pyproject.toml` — package config, deps, CLI entry points
- `config/phase_b_sources.yaml` — FRED series + ingestion start dates (currently `1990-01-01`; T1 backfilled to 1980 in DuckDB but config still says 1990)
- `config/news_sources.yaml` — RSS/CSV sources (mostly disabled, see §6)
- `config/news_themes.yaml` — 18 macro themes + 9 secular themes (T6 just added secular block)
- `config/news_source_watchlist.yaml` — which source groups are required (now includes `ai_compute` after T5)

### Data files (gitignored where appropriate)
- `data/macro_engine.duckdb` — main DB, ~150 MB after T1 backfill. **Local only**, never committed.
- `data/raw/fred/` — Parquet exports from FRED ingestion. Gitignored.
- `data/news_pilot/news_items_expanded.csv` — committed, 77 KB.
- `data/news_pilot/news_items_balanced.csv` — committed.
- `outputs/` — run outputs (JSON/MD). Mostly gitignored.

### Tests
```bash
pytest                                  # full suite
pytest tests/test_ws2_t6_secular_themes.py  # specific
```

---

## 3. Progress Done (with commits + receipts)

### Pre-handoff work by Claude

#### WS2-T0 — Discovery audit (commit included in 5e5e619)
- File: `docs/ws2_discovery_audit.md`
- 19 KB, 7 sections, full module map, theme-layer maturity, history picture, surface-area candidates.
- **Codex: read this before anything else.**

#### WS2-T1 — FRED 1980 backfill (commit 5e5e619)
- File: `scripts/backfill_fred_1980.py` (built earlier, **executed today** with `--apply`).
- Plan doc: `docs/ws2_t1_fred_1980_backfill_plan.md`.
- Result: 12/12 series succeeded. Backfilled to FRED-available start dates per series (INDPRO 1919, PAYEMS 1939, CPIAUCSL 1947, UNRATE 1948, FEDFUNDS 1954, PCEPI 1959, DGS10 1962, ICSA 1967, NFCI 1971, T10Y2Y 1976, BAA10Y 1986, BAMLH0A0HYM2 1996).
- **~116k pre-1990 rows in `raw_observations` table.**
- Script is **idempotent** (DELETE+INSERT on composite primary key); safe to re-run.
- Two Windows-cp949 bugs patched today: en-dash in dry-run header (line 99), `getattr` fallback for `summary.errors` (pydantic schema drift in `IngestionRunSummary`).

#### WS2-T5 — Add `ai_compute` source group (commit 5e5e619)
- `config/news_source_watchlist.yaml`: added `ai_compute` entry pointing at `data/news_pilot/news_items_expanded.csv` (stub for plumbing).
- `src/macro_engine/news/config.py`: `ai_compute` added to `REQUIRED_NEWS_SOURCE_GROUPS` allowlist.
- **Does not start ingesting AI-compute news yet** — just lets the config validate. Real RSS feeds needed (WS2-T7).

#### WS2-T6 — Secular themes taxonomy (commit 5e5e619)
- `config/news_themes.yaml`: new `secular_themes` block with all 9 IDs mirroring Stock Screener paradigm_config.json **exactly** (interface contract — do not change IDs).
- `src/macro_engine/news/schema.py`: `secular_theme: str | None = None` optional field on `NewsClassificationPayload` and `NewsClassificationRecord`. Defaults None, backward-compatible.
- `src/macro_engine/news/config.py`: `secular_themes` optional dict + `secular_theme_ids` property on `NewsThemesConfig`.
- `src/macro_engine/news/classify.py`: extended system prompt to request `secular_theme` field. `MockNewsClassifier` returns null. Validation rejects unknown secular theme IDs.
- `tests/test_ws2_t6_secular_themes.py`: 6 tests passing (YAML load, backward compat, prompt generation, payload/record acceptance, unknown ID rejection).
- 18 macro themes block **untouched**.
- **Scoring weights NOT extended** — `config/news_scoring.yaml` does not yet score secular themes (that's WS2-T8/T9).

### Pre-handoff work by user/operator (predates this session)
- Macro engine core (FRED ingestion, regime classification, GICS sector diagnostic) operational.
- News layer scaffolding (12 source groups including `technology_ai`, mock classifier, scoring pipeline).
- DuckDB store at `src/macro_engine/storage/duckdb_store.py`.
- CLI entry points in `pyproject.toml` (run `python -m macro_engine.cli --help` to discover).

### Commit reference
- `5bc5f0d` — last commit before Claude's WS-2 session (operator's prior work)
- `5e5e619` — Claude's WS-2 commit (T1 + T5 + T6 bundled)
- `5d59ea6` - Codex WS-2 commit (T7 RSS config, T8/T9 secular scoring/tracker, daily hooks)
- `4844371` - Codex WS-2 commit (T3 accumulation-grade replay persistence)
- `cbaecf2` - Codex WS-2 commit (T4/T2 automation summary hardening)
- `54e03bf` - Codex WS-2 commit (T7 real `ai_compute_rss` feed verification)
- `44b7ae5` - Codex WS-2 commit (T2 live-RSS mock smoke receipt)
- `b425558` - Codex WS-2 commit (dashboard publishes secular + automation outputs)
- `c871740` - Codex WS-2 commit (MGI-only diagnostic `regime_status.json` publisher)
- `8e52fd1` - Codex WS-2 commit (dashboard refresh after automation summary)
- `7b082b0` - Codex WS-2 commit (`ai_compute` added to news monitoring groups)
- `a758556` - Codex WS-2 commit (source coverage report wired into daily automation)
- Branch: `master`
- Untracked items operator may want to handle: `outputs/` (gitignored), `.claude/` (skip).

---

## 4. Roadmap — Remaining Work to Full Completion

Numbering continues from WS-2 audit doc §6. Sequenced by leverage + dependency.

### 4.1 Backfill candidates (audit §6a)

#### WS2-T2 — Enable local news CSV archives + accumulate runs
- **Status:** PARTIAL / LIVE-RSS MOCK SMOKE VERIFIED. Plumbing exists and a news-only daily diagnostic smoke using `--source-profile ai_compute_rss` succeeded on 2026-05-27 with real RSS ingest, mock bounded classification, 15/15 classified items, accumulation, accumulation report, and secular tracker output. It remains insufficient history with one run date, as expected.
- **Why it matters:** `insufficient_history` is currently gated on **run dates** (5 distinct dates OR 100 classified items per accumulator), NOT item count. Per audit: `src/macro_engine/accumulation.py:139`. Even if you have 1000 items today, one run = one date = still insufficient.
- **What to do:**
  1. Keep `ai_compute_rss` disabled by default in config; select it explicitly with `--source-profile ai_compute_rss`.
  2. Run news-only daily diagnostic with mock classification for at least 5 distinct dates, or use T3 replay for operating readiness.
  3. Only enable live DeepSeek classification after mock RSS runs are stable.
  4. After 5+ dates or 100+ classified items, `insufficient_history` should clear and confidence rises.
- **Cost:** DeepSeek classification calls (the mock classifier currently runs; the real `DeepSeekClassifier` calls API). Estimate ~$0.0003/item × items/run × runs.
- **Watch out for:** mock vs real classifier toggle. Check `MockNewsClassifier` vs `DeepSeekClassifier` selection logic in `classify.py`.

#### WS2-T3 — Historical news replay
- **Status:** DONE IN CODE / REPLAY-CORPUS VERIFIED. `replay-news-history` now supports `--persist-replay-db`, letting replay-day classifications persist into the central `--db-path` so accumulation counts replay dates.
- **Receipt:** Mock same-day replay over `data/news_pilot/news_items_last_30_days.csv` from 2026-04-22 to 2026-05-21 produced 30 replay days, 133 persisted unique classifications, 27 classified replay dates, and `run-news-accumulation` reported `monitor_ready` on `data/ws2_t3_replay_persist.duckdb`.
- **Why it matters:** Replay over a date range generates multiple run dates from one large CSV file. Much cheaper way to clear `insufficient_history` than waiting 5 actual calendar days.
- **What to do:**
  1. Use `--same-day-only --persist-replay-db` for accumulation-grade replay; this avoids duplicate prior-day reclassification overwriting central classifications by `news_id`.
  2. Run accumulation against the same `--db-path` after replay.
- **Watch out for:** Replay is still an operating replay, not validation. Real signal still needs T2 daily accumulation with fresh inputs.

#### WS2-T4 — Daily automation
- **Status:** PARTIAL / MOCK-SAFE. GitHub workflow and local daily scripts run daily diagnostic, accumulation, accumulation report, source coverage report, secular theme tracker, diagnostic `regime_status.json`, dashboard export, and automation summary. Automation summary now includes secular-theme tracker state when present, and dashboard export is refreshed after automation summary is written.
- **What to do:**
  1. Keep scheduled/default mode mock-safe until live AI and live RSS are explicitly selected.
  2. Persist small JSON/MD artifacts only, not DuckDB database or raw CSV.
  3. Watch first scheduled runs for source coverage and dashboard artifact completeness.
- **Cost:** depends on classifier (mock = free, DeepSeek = pennies).

### 4.2 Theme-layer extension (audit §6b)

#### WS2-T7 — Wire real RSS feeds for `ai_compute`
- **Status:** DONE IN CODE / DISABLED BY DEFAULT. Disabled-by-default `ai_compute_rss` sources added for NVIDIA Blog, NVIDIA Developer Blog, and Google Cloud Blog. `validate-news-input --profile ai_compute_rss` passes without fetching RSS during validation. Live fetch smoke returned 15 items, all mapped to `ai_compute` (`nvidia_blog`: 3, `nvidia_developer_blog`: 11, `google_cloud_blog`: 1).
- **Monitoring:** `ai_compute` is now included in news monitoring source groups and source coverage daily reporting.
- **Why it matters:** Without real news flow, `ai_compute` source group has no input. Mock-only.
- **What to do:**
  1. Keep disabled by default until operator approves live ingestion.
  2. If enabling, use `--source-profile ai_compute_rss` and bounded mock classification first.
  3. Re-run RSS smoke if any feed starts returning HTML/non-XML.
- **Watch out for:**
  - RSS feeds only give recent items (~10-50 latest). Historical not available without paid archive.
  - Some feeds need user-agent string or referer.
  - Rate limits unclear; cache responses.

#### WS2-T8 — Multi-month aggregation for secular themes
- **Status:** DONE IN CODE. `config/news_scoring.yaml` now has `secular_scoring` defaults for monthly/quarterly scoring with 30-day half-life and 180-day max age. Secular scoring reads stored `news_classifications.secular_theme` separately from macro theme scoring.
- **Why it matters:** Current `news_scoring.yaml` aggregates daily/weekly with a 7-day half-life. Secular themes need months-to-quarters horizons to capture drift.
- **What to do:**
  1. Add `monthly` and/or `quarterly` aggregation frequency to `config/news_scoring.yaml`.
  2. Extend `src/macro_engine/news/scoring.py` to compute secular-theme scores at the longer horizons.
  3. Separate output file: `outputs/secular_theme_scores.json` (keyed by `secular_theme_id`).
- **Watch out for:** the scoring code currently lumps macro and secular themes together in some paths. Separate them so macro themes keep their 7-day half-life and only secular themes get the longer horizon.

#### WS2-T9 — Secular theme tracker report
- **Status:** DONE IN CODE. New CLI: `python -m macro_engine.cli build-secular-theme-scores`.
- **What to do:**
  1. New CLI command: `python -m macro_engine.cli build-secular-theme-scores`.
  2. New report writer in `src/macro_engine/news/report.py` (or wherever existing news reports live).
  3. Output: `outputs/secular_theme_tracker_YYYYMMDD.md` (Markdown for humans) + `outputs/secular_theme_scores.json` (machine).
  4. Each theme gets: current score, 30-day trend, top 5 contributing news items, confidence, and ratio of mock vs real-source contributions.

### 4.3 WS-3 — Wire MGI into Stock Screener (CROSS-REPO)

- **Status:** NOT STARTED. **Blocked** on MGI reaching `monitor_ready` per brief discipline.
- **MGI-side note:** `outputs/regime_status.json` now exists as a diagnostic-only publish artifact. Stock Screener is still not wired to consume it.
- **What it is:** Stock Screener's `score_paradigm.py` currently reads raw FRED data (its own `fetch_macro_state.py`). It does NOT read MGI's processed regime or theme scores. WS-3 connects them.
- **Two patterns (operator chose option C "flags only" for the initial macro overlay):**
  - **A. Hard multiplier:** Stock Screener pulls MGI's `dominant_regime` (Goldilocks/Reflation/Stagflation/Recession) and applies a per-regime multiplier to `pdm_signal`. Requires MGI signal to be trustworthy.
  - **B. Band downgrade:** MGI regime → demote paradigm band one tier in stressed regimes.
  - **C. Flags-only (current):** Stock Screener reads its own FRED snapshot. MGI not involved. Already shipped (WS1-T9).
- **What to build for WS-3:**
  1. MGI publishes nightly: `outputs/regime_status.json` with `{dominant_regime, regime_probability, secular_theme_scores: {...}, monitor_ready: bool, computed_at}`. **MGI side done; output remains diagnostic-only until ready.**
  2. Commit it to MGI's repo on every nightly run.
  3. Stock Screener fetches it via raw GitHub URL: `https://raw.githubusercontent.com/riperdy-tech/Macro-Regime-Identifier/master/outputs/regime_status.json`.
  4. Stock Screener's `score_paradigm.py` reads + applies (multiplier OR band downgrade OR flags).
- **DO NOT START WS-3 until:**
  - MGI's news layer has cleared `insufficient_history` (5+ run dates accumulated).
  - MGI's regime classifier is producing stable, non-mock signals.
  - Operator approves the chosen integration pattern (A/B/C).

---

## 5. Interface Contracts — DO NOT MODIFY

These are stable contracts between MGI and Stock Screener. Breaking them silently breaks WS-3 plans and any future cross-repo automation.

### 5.1 The 9 secular theme IDs (HARD contract)

In `config/news_themes.yaml` → `secular_themes:`:
```
ai_compute
physical_ai
glp1_metabolic
cloud_software
energy_transition
cybersecurity
quantum_computing
space_economy
nuclear_renaissance
```

These IDs **must match exactly** the keys in Stock Screener's `Stock Screener/scripts/paradigm_config.json` → `themes[].id`. WS-3 joins by these IDs. Renaming any one of them on either side breaks the join.

**To add a new secular theme:** add it on Stock Screener side FIRST (operator-curated seeds + keywords are the source of truth). Then mirror the ID + label here.

**To remove:** discuss with operator. Removing has cross-repo implications.

### 5.2 The 12 FRED series (consumed by both repos)

Both repos read the same FRED series for macro signal:
- DGS10, T10Y2Y, BAA10Y, NFCI, BAMLH0A0HYM2 (already in Stock Screener's `fetch_macro_state.py`)
- Plus INDPRO, PAYEMS, UNRATE, CPIAUCSL, PCEPI, FEDFUNDS, ICSA (MGI internal)

Stock Screener fetches DIRECTLY from FRED via its own script. **It does NOT read from MGI's DuckDB.** Independence by design — paradigm scoring must not break if MGI is unavailable.

Don't restructure or rename these series in MGI's config in a way that would break MGI's own consumption. Stock Screener doesn't care, but MGI's pipelines do.

### 5.3 Macro themes (18) — DO NOT TOUCH

`config/news_themes.yaml` → `macro_themes:` block. 18 themes for cyclical regime (inflation, growth, labor, etc.). Operator-tuned over time. **Do not rename, remove, or reorder.** Only ADD new macro themes if absolutely necessary (and document why).

### 5.4 Brief constraints (from §3 orchestration brief)

These apply to all WS-2 work:
- **Additive only.** Never remove or rename existing fields/scores.
- **No fabricated data.** If a source has no data, degrade gracefully (null + confidence penalty). Never invent.
- **No network calls in scoring.** Ingestion + classification + scoring are separate phases. Scoring reads from disk/DB only.
- **Determinism.** Same input → same output. Snapshots are dated; results reproducible.
- **STOP-and-report on contradiction.** If repo reality differs from this doc, halt and report — do not improvise.
- **"Failsafe"/"proven safe" language is banned.** Validation is forward-logging, never claimed as proof.

### 5.5 The reverse engine (Stock Screener, NOT MGI)

Stock Screener's reverse engine (`Stock Screener/scripts/score_reverse.py`) is FROZEN per the orchestration brief. MGI does not touch it. WS-3 may consume its outputs but never modify.

This is irrelevant to MGI internally but Codex should know: if you ever find yourself looking at Stock Screener code, **do not modify** `score_reverse.py`, `reverse_config.json`, or anything `rev_*`.

---

## 6. Current State of Major Subsystems

### 6.1 FRED ingestion
- **Working.** Backfilled to as early as FRED has per-series data (1919 for INDPRO, etc.).
- DuckDB store at `data/macro_engine.duckdb` (gitignored, ~150 MB).
- Re-running ingestion is idempotent (DELETE+INSERT on composite key).
- Daily refresh wired via `scripts/run_daily_diagnostic.ps1` / `.sh` (may need GH Action wrapper).

### 6.2 Sector regime classifier (11 GICS sectors)
- Per audit: "weak/mixed validation result."
- Operator-tuned. Don't recalibrate without operator approval.
- **Not a paradigm engine.** Sectors can't distinguish NVIDIA from generic IT (per brief §1). The secular-theme layer is the right place for paradigm-aware signals.

### 6.3 News layer
- **`insufficient_history`** per audit. Confidence currently 3-20%.
- Only the synthetic-sample news source is enabled. All real-news sources disabled (RSS placeholder = `example.invalid`).
- 12 source groups including `technology_ai` (existing) and now `ai_compute` (T5 added).
- DeepSeek-based classifier in `classify.py`; mock classifier for tests.
- Classification per news item produces: `macro_theme`, `secular_theme` (new T6 field), `sentiment`, `confidence`.

### 6.4 Scoring
- Daily/weekly aggregation with 7-day half-life.
- Macro themes scored. **Secular themes NOT scored yet** (WS2-T8).
- Output: `outputs/news_score_report.{json,md}` (when run).

### 6.5 Self-reports
- `docs/PROJECT_HANDOFF.md` contains §11 and §12 maturity reports (operator's own writeups).
- `docs/model_limitations.md` — operator's honest accounting of known weaknesses.
- `docs/reviews/phase_v06_m3_*` — phase reviews.

---

## 7. Known Footguns

### 7.1 Windows cp949 console
- Default Windows terminal encoding in operator's environment is **cp949** (Korean). Non-ASCII characters in `print()` calls crash with `UnicodeEncodeError`.
- **Bitten twice this session:** em-dash in `score_paradigm.py` (Stock Screener side), en-dash in `backfill_fred_1980.py` (MGI side).
- **Fix:** strip non-ASCII from any `print()` output. Use ASCII `-` instead of `—` or `–`. Docstrings and comments are fine — only printed strings matter.
- **Defensive idiom:** at top of any script that prints stock/company names or data with unknown content:
  ```python
  import sys
  try:
      sys.stdout.reconfigure(encoding="utf-8", errors="replace")
  except Exception:
      pass
  ```

### 7.2 Pydantic schema drift
- `IngestionRunSummary` (in `src/macro_engine/ingest/service.py` or nearby) has had its fields renamed at least once. `backfill_fred_1980.py` originally referenced `summary.errors`; that attribute no longer exists.
- **Defensive idiom:** use `getattr(obj, 'attr', default)` when reading optional pydantic fields from this codebase.

### 7.3 DuckDB local-only
- `data/macro_engine.duckdb` is gitignored. Each environment must rebuild from scratch (re-run ingestion + backfill) OR receive a copy out-of-band.
- GH Actions deployment can't rely on a committed DB.
- WS-3 publishing strategy must NOT depend on DuckDB being available to Stock Screener.

### 7.4 RSS provider stub
- `example.invalid` is a real placeholder in the config. Calls fail by design. T7 must replace.

### 7.5 Replay vs daily-run semantics
- Replay processes a historical CSV with synthetic dates — useful for plumbing.
- Daily-run processes today's news for today's date — accumulates real history.
- **`insufficient_history` is cleared by either**, but replay may not reflect real theme rotation. Real-history is the honest path; replay is the shortcut.

### 7.6 Mock vs real classifier
- `MockNewsClassifier` returns deterministic stub responses. Used in tests + when no `DEEPSEEK_API_KEY` set.
- `DeepSeekClassifier` calls real API.
- Selection logic in `classify.py` — verify which is active before running large batches.

### 7.7 `secular_themes` is optional
- T6 made it `Optional` in pydantic schemas. Existing classified data without `secular_theme` field still validates. Don't break this — operator may have months of pre-T6 classifications stored.

### 7.8 No automated MGI-to-Stock-Screener pipeline yet
- WS-3 is deferred. If Codex adds any output file expecting Stock Screener to consume it, document the URL clearly so the Claude side can wire later.

---

## 8. Operator Preferences (Important)

From observed working style this session:
- **Terse over verbose.** Operator uses caveman-mode communication. Drop articles, hedging, pleasantries.
- **Push to GitHub when work is done.** Don't accumulate commits indefinitely; push after each cohesive change set. Operator wants Vercel/GH-Pages deploys to track work in real-time.
- **Verify before claiming done.** Run tests. Run scripts. Show actual output. Don't claim success without receipts.
- **No fabricated data, ever.** Brief is strict on this. LLM outputs must include confidence guards and `STOP-and-report` paths.
- **Discipline over coverage.** Operator chose strict precision (composite ≥2.0, anti-Nikola) over broad recall multiple times. When in doubt: pick precision.
- **Brief is sacred.** When tempted to deviate, re-read `paradigm_orchestration_brief.md` §0 and §3.
- **`/caveman` plugin active.** Operator's Claude sessions run in caveman mode. Match the brevity.
- **Forward validation, not backtest.** Operator dislikes any artifact claiming to "prove" the strategy. Logging signals now for later observation is the only honest path.

---

## 9. Cross-Repo Coordination

### When to coordinate with Claude (Stock Screener side)

| Trigger | What to do |
|---|---|
| Add/rename/remove a `secular_themes` ID | Ping operator first; Claude must update Stock Screener's `paradigm_config.json` in lockstep |
| Change FRED series schema or DB shape | Stock Screener doesn't read MGI's DB, but operator should know |
| Publish a new `outputs/*.json` for Stock Screener to consume | Document URL + schema in this handoff doc; operator wires Stock Screener side |
| Modify the orchestration brief itself | NEVER. Brief is the source of truth; treat as read-only |

### What Stock Screener publishes (Codex can read for context)
- `Stock Screener/scripts/paradigm_config.json` — secular theme IDs + descriptions. Read-only reference.
- `Stock Screener/docs/paradigm/*.md` — design docs, illustrative validation, decisions log. Read-only.
- Live deploy: `https://stock-screen-rt.vercel.app/`

### What MGI publishes (current)
- `outputs/*.json` and `*.md` — gitignored by default. Not consumed by anything external yet.
- Repo deploys to GH Pages (operator's domain).

---

## 10. First-Day Pickup Guide for Codex

### Hour 1: Read
1. This doc (you're doing it).
2. `docs/ws2_discovery_audit.md` — full audit.
3. `tasks/briefs/paradigm_orchestration_brief.md` (workspace root, in Stock Screener repo's tasks dir) — operator's thesis.
4. `docs/PROJECT_HANDOFF.md` — operator's own §11/§12 self-reports.
5. `docs/model_limitations.md` — honest weaknesses.

### Hour 2: Inspect
1. `python -m macro_engine.cli --help` → discover CLI surface.
2. `pytest -q` → confirm test suite passes.
3. `python scripts/backfill_fred_1980.py` → dry-run, confirm DuckDB has post-T1 row counts (~116k pre-1990 rows).
4. Run a `news-classify` against the synthetic sample, observe end-to-end flow.

### Hour 3-N: Pick the next task
Recommended order (by leverage):

1. **WS2-T3 (historical news replay)** — fastest path to clearing `insufficient_history`. Free, deterministic, uses existing CLI.
2. **WS2-T8 (multi-month aggregation)** — unblocks secular theme scoring. Code change in `scoring.py`; tests required.
3. **WS2-T9 (secular theme tracker report)** — produces the artifact WS-3 will eventually consume. Self-contained, no cross-repo dep.
4. **WS2-T7 (real RSS feeds for `ai_compute`)** — only after T3/T9 prove the pipeline works on synthetic data. Adds external dependency risk.
5. **WS2-T4 (daily automation via GH Actions)** — last; depends on the pipeline being stable.
6. **WS2-T2 (enable + accumulate daily)** — overlaps with T4; can run in parallel once automation exists.
7. **WS-3 (cross-repo wiring)** — only when MGI reaches `monitor_ready`. Ping operator first.

### Each task workflow
1. Read this doc's §4 entry for that task.
2. Read the relevant audit doc section.
3. Make changes (additive only).
4. Add tests.
5. Run `pytest -q` — all green.
6. Run end-to-end flow if applicable.
7. Commit + push to MGI's GitHub remote.
8. Update this handoff doc's §3 with a "Done" entry + commit hash.

---

## 11. Quick Reference

### Common commands
```bash
# Tests
pytest -q
pytest tests/test_ws2_t6_secular_themes.py -v

# CLI discovery
python -m macro_engine.cli --help

# FRED ingestion (current + historical via T1 script)
python scripts/backfill_fred_1980.py            # dry-run
python scripts/backfill_fred_1980.py --apply    # writes to DuckDB

# News pipeline (per audit; verify exact commands)
python -m macro_engine.cli ingest-news
python -m macro_engine.cli classify-news
python -m macro_engine.cli score-news
python -m macro_engine.cli replay-news-history --start-date 2025-01-01 --end-date 2026-05-21

# Daily pipeline (existing entry point)
.\scripts\run_daily_diagnostic.ps1     # Windows
./scripts/run_daily_diagnostic.sh      # POSIX
```

### Git
```bash
cd "Macro Regime Indicator"
git status
git log --oneline -10
git push        # to riperdy-tech/Macro-Regime-Identifier
```

### File locations cheat sheet
| What | Where |
|---|---|
| FRED ingestion logic | `src/macro_engine/ingest/fred.py` + `service.py` |
| DuckDB store | `src/macro_engine/storage/duckdb_store.py` |
| News config | `config/news_*.yaml` + `src/macro_engine/news/config.py` |
| News classifier | `src/macro_engine/news/classify.py` |
| News scoring | `src/macro_engine/news/scoring.py` |
| Accumulation gate | `src/macro_engine/accumulation.py:139` (`insufficient_history` rule) |
| Replay command | `src/macro_engine/replay.py` |
| Schema (Pydantic) | `src/macro_engine/news/schema.py` + `src/macro_engine/news/config.py` |
| Tests | `tests/` |
| Daily diagnostic entry | `scripts/run_daily_diagnostic.{ps1,sh}` |

---

## 12. Open Questions Codex Should Get Operator to Answer Before Starting WS-3

Reserved for when MGI is mature enough that WS-3 is on the table. Don't ask these now.

- Integration pattern: A (hard multiplier), B (band downgrade), C (flags only, current)? Brief warns against load-bearing macro overlay until matured.
- Publish channel: commit `outputs/regime_status.json` to MGI repo (read via raw GitHub URL from Stock Screener), or via Supabase, or via a small API endpoint?
- Refresh cadence: nightly? Pre-market open? Real-time webhook on regime change?
- Failure mode: if MGI's published output is stale or unavailable, what does Stock Screener do? Brief implies "fail open" (paradigm continues with flags only, no MGI input).
- Regime taxonomy alignment: MGI uses {Goldilocks, Reflation, Stagflation, Recession}. Stock Screener doesn't have a regime field yet. Add one to `paradigm_config.json`? Use existing band system? Add a new `pdm_macro_overlay_band` field? (Note: brief locks the 12-field `pdm_*` shape — adding a 13th needs operator approval.)

---

## 13. Things I Didn't Get To (Honest Inventory)

- **News scoring weights for secular themes (T8).** Schema is there (T6); the math isn't.
- **Confidence calibration on classifier.** Operator's own `model_limitations.md` flags this; not addressed in this session.
- **RSS feed selection for `ai_compute`.** Stub only.
- **Daily automation GH Action.** Not built.
- **Backfill_fred_1980.py polish.** I patched two crashes; the script could use a proper retry-on-429 and a `--from-date` flag for arbitrary backfill windows.
- **Sector regime calibration audit.** Operator's own notes say "weak/mixed"; not investigated.
- **Cross-checking secular theme IDs across both repos in CI.** Easy to drift silently. Could add a test that fetches Stock Screener's `paradigm_config.json` (or a snapshot of it) and asserts ID parity.
- **Forward-logging hook for MGI.** Stock Screener side has T7 (`paradigm_signal_log.jsonl`). MGI doesn't. Symmetric forward-validation infrastructure would be valuable.
- **`outputs/` cleanup policy.** Gitignored runs accumulate locally. No rotation/archival.

---

## 14. What I'd Do First If I Were Picking This Up

Codex, if you read nothing else:

1. Confirm `pytest -q` passes from a clean clone.
2. Read `docs/ws2_discovery_audit.md` fully.
3. Run `python scripts/backfill_fred_1980.py` (dry-run) — verify it doesn't crash and shows expected 12-series count.
4. Pick **WS2-T3 (historical news replay)** as your first task. It's the highest-leverage move: clears `insufficient_history` cheaply and validates that the existing T1+T5+T6 plumbing actually works end-to-end. If T3 succeeds, you'll have a real signal to point at for the next 2-3 tasks.
5. After T3 lands, **WS2-T9 (secular theme tracker report)** is the next-best move because it produces the artifact that WS-3 will eventually consume.
6. Push to MGI's GitHub after every task. Update this doc's §3 with each landing.
7. When WS-3 starts becoming a real conversation, ping the operator first. The brief's discipline on load-bearing macro input is strict.

Good luck. The architecture is mostly sound; the gaps are mostly in news data freshness + historical accumulation. Both are tractable.

— Claude Opus 4.7, 2026-05-26
