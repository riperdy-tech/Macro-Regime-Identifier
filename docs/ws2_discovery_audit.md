# WS-2 Discovery Audit — Macro Regime Indicator

**Date:** 2026-05-24  
**Auditor:** WS-2 discovery agent  
**Scope:** Read-only audit of `Macro Regime Indicator/` repo for WS-2 (history backfill + theme-layer extension) task specification.

---

## 1. Repo Intent (≤10 lines)

Per `README.md` (lines 1–10) and `pyproject.toml` (line 3), this repo is a **local-first U.S. macro regime diagnostic engine** (`macro-regime-engine` v1.0rc1). It ingests FRED macro series, scores 5 regimes (goldilocks, reflation, stagflation, recession, tightening), maps them to 11 GICS sectors, and overlays an AI-assisted news/theme classification layer. It is explicitly **not** investment advice, a trading system, or a performance-validated forecasting model. The README positions the news/theme layer as "experimental" and the accumulated real-news history as `insufficient_history`.

---

## 2. Module Map

| Module path | Role | Entry points | Stable? (tests/docs evidence) |
|---|---|---|---|
| `src/macro_engine/cli.py` | CLI entry point (Typer app) | `python -m macro_engine.cli <command>` | Stable. 62K lines, tested via `CliRunner` in multiple test files. |
| `src/macro_engine/pipeline.py` | Macro pipeline orchestration | Called by CLI `run-pipeline` | Stable. Covered by `test_phase_h_pipeline.py`. |
| `src/macro_engine/ingest/fred.py` | FRED API ingestion | Called by pipeline `ingest` step | Stable. `FredClient` class, tested in `test_phase_b_ingestion.py`. |
| `src/macro_engine/features/` | Feature building (transforms, normalization) | Called by pipeline `build-features` | Stable. `test_phase_c_features.py` covers transforms. |
| `src/macro_engine/dimensions/` | Dimension scoring | Called by pipeline `build-dimensions` | Stable. `test_phase_d_dimensions.py`. |
| `src/macro_engine/regimes/` | Regime scoring + softmax | Called by pipeline `build-regimes` | Stable. `test_phase_e_regimes.py`, `test_regime_rules.py`. |
| `src/macro_engine/sectors/` | GICS sector macro mapper | `build-sector-scores` CLI | Experimental. `test_phase_v02_sectors.py` (18K lines). Calibration result described as "weak/mixed" in README. |
| `src/macro_engine/news/` | News ingestion, AI classification, scoring, monitoring, source coverage | `ingest-news`, `classify-news`, `build-news-scores`, `run-news-monitoring`, etc. | Experimental. `test_phase_v03_m1_news.py` (16K), `test_phase_v03_m2_m3_news_scores.py` (13K), `test_phase_v04_m4_news_monitoring.py` (11K). |
| `src/macro_engine/accumulation.py` | News accumulation tracker + readiness labels | `run-news-accumulation` | Stable plumbing. `readiness_label()` function at line 139. |
| `src/macro_engine/daily.py` | Daily operating workflow | `run-daily-diagnostic` CLI | Stable. `test_phase_v05_m1_m2_operations.py` (13K). |
| `src/macro_engine/replay.py` | Historical news operating replay | `replay-news-history` CLI | Stable. `test_phase_v09_m3_replay.py` (6.5K). |
| `src/macro_engine/storage/duckdb_store.py` | DuckDB persistence layer | Used by all modules | Stable. 67K lines, load-bearing for all data. |
| `src/macro_engine/dashboard_export.py` | Dashboard JSON export | `export-dashboard-data` CLI | Stable. |
| `src/macro_engine/config/` | Config loading (Pydantic models) | Used by all modules | Stable. `test_config_loader.py`. |

**Key distinction:** The macro-regime engine (v0.1 core) is stable/release-candidate. The sector layer (v0.2) is experimental. The news/theme layer (v0.3–v0.6) is experimental and explicitly labeled `insufficient_history`.

---

## 3. Theme/News Layer — Current State

### 3.1 Where `technology_ai` source group is defined

The `technology_ai` source group is defined as a **required** constant in:

- **`src/macro_engine/news/config.py`, line 27** — `REQUIRED_NEWS_SOURCE_GROUPS` set includes `"technology_ai"`.
- **`config/news_source_watchlist.yaml`, line 12** — `required_source_groups` list includes `"technology_ai"`.
- **`config/news_source_watchlist.yaml`, lines 128–137** — A watchlist entry `daily_technology_ai_csv` is configured with `source_group: technology_ai`, pointing at `data/news_pilot/news_items_last_30_days.csv`.

### 3.2 Full list of source groups (12 total)

From `src/macro_engine/news/config.py`, lines 17–29:

1. `macro_general`
2. `inflation_rates`
3. `labor`
4. `energy_commodities`
5. `credit_financial_conditions`
6. `real_estate`
7. `consumer`
8. `manufacturing_industrials`
9. `geopolitical`
10. `technology_ai`
11. `healthcare`
12. `defensive_sectors`

### 3.3 Data sources actually wired up vs mock/placeholder

**Wired up (enabled in config):**
- `synthetic_sample_news` — local CSV at `data/examples/sample_news_items.csv` (6 synthetic items). Enabled by default.
- `daily_macro_general_csv` through `daily_defensive_sectors_csv` — 12 watchlist entries all pointing at `data/news_pilot/news_items_last_30_days.csv`. Enabled in watchlist config.

**Disabled / placeholder:**
- `pilot_local_csv` → `data/news_pilot/news_items.csv` (disabled)
- `pilot_expanded_local_csv` → `data/news_pilot/news_items_expanded.csv` (disabled)
- `pilot_balanced_local_csv` → `data/news_pilot/news_items_balanced.csv` (disabled)
- `last_30_days_local_csv` → `data/news_pilot/news_items_last_30_days.csv` (disabled in `news_sources.yaml`)
- `rss_watchlist_example` → `https://example.invalid/rss.xml` (disabled, placeholder URL)
- `manual_text_example` → synthetic manual item (disabled)

**Reality:** All real-news sources are disabled in `config/news_sources.yaml`. The only enabled source is the 6-item synthetic sample. The watchlist config enables the 12 daily sources, but the source coverage report (`outputs/news_source_coverage_report.json`) shows only 2 stored items, all in `macro_general`, with 11 of 12 groups having **no stored data**.

### 3.4 Confidence values currently emitted

Sample from `outputs/news_score_report.json`:

| Item | Confidence | Severity |
|---|---|---|
| High-severity items (energy/geopolitical) | 0.80 | 0.60 |
| Low-confidence items (mixed topics) | 0.35 | 0.20 |
| Top theme `monetary_tightening` (avg) | 0.70 | 0.50 |
| Top theme `commodity_pressure` (avg) | 0.80 | 0.60 |
| Sector tailwind `energy` (avg) | 0.75 | 0.60 |

Macro regime confidence from `outputs/current_regime.json`: **~0.033** (raw/reported confidence for "reflation" regime). Historical diagnostic average confidence: **~0.085** (`outputs/historical_diagnostic.json`).

### 3.5 `insufficient_history` markers

**Origin:** `src/macro_engine/accumulation.py`, function `readiness_label()` at **line 139**:

```python
def readiness_label(*, run_dates: int, classified_items: int, source_count: int = 0) -> str:
    if run_dates < 5 or classified_items < 100:
        return "insufficient_history"
    if run_dates < 20:
        return "early_history"
    if run_dates < 60 or source_count < 3:
        return "monitor_ready"
    return "validation_candidate"
```

**Threshold gates:**
- `insufficient_history`: fewer than **5 run dates** OR fewer than **100 classified items**
- `early_history`: 5–20 run dates
- `monitor_ready`: 20+ run dates with reasonable source coverage
- `validation_candidate`: 60+ run dates with stable source coverage

**Current status** (from `outputs/news_accumulation_report.json`):
- `readiness_label`: `"insufficient_history"`
- Classified items: 270 (exceeds 100 threshold)
- But run dates: the accumulation report shows items from 2018-09-23 to 2026-05-21, but the `_run_date_count()` function counts distinct `classified_at` dates, not `published_at` dates. The report shows only ~10 combined diagnostic dates (2026-05-12 to 2026-05-21), suggesting fewer than 5 distinct classification run dates.

The label is also referenced in:
- `README.md` (search for "insufficient_history" — appears multiple times)
- `docs/model_limitations.md` (section "Insufficient History")
- `docs/reviews/phase_v06_m3_accumulated_history_validation_readiness.md`
- `docs/PROJECT_HANDOFF.md` (§1, §14)

---

## 4. History/Backfill Picture

### 4.1 What time range of data does the repo currently have?

**Macro (FRED) data:** The DuckDB at `data/macro_engine.duckdb` (143 MB) contains FRED observations. The historical diagnostic (`outputs/historical_diagnostic.json`) shows a date range of **1990-01-01 to 2031-08-01** (revised data, including projected/future dates). The `raw_observations.parquet` file (1.6 MB) contains the stored FRED series.

**News data:** The accumulated news items span **2018-09-23 to 2026-05-21** (`outputs/news_accumulation_report.json`). However, this is a sparse set — most dates have 1 item, with density increasing only in April–May 2026. The 270 items come from the synthetic sample + the `news_items_last_30_days.csv` file.

**Sector data:** Sector scores exist in `data/raw/fred/sector_scores.parquet` (92 KB) and `sector_score_components.parquet` (334 KB).

### 4.2 Minimum history needed to clear `insufficient_history`

Per `src/macro_engine/accumulation.py` line 139:
- **5 distinct run dates** (classified_at dates) **AND** 100 classified items.

Currently: 270 classified items ✓, but fewer than 5 run dates ✗.

### 4.3 What's the gap?

The gap is **operating run dates**, not raw item count. The repo has 270 classified items but they were classified in too few batch runs. To reach `early_history` (5–20 run dates), the operator needs to run the classification pipeline across at least 5 separate calendar dates. To reach `monitor_ready` (20+ run dates), they need 20+ separate dates.

### 4.4 Sources the repo already knows how to ingest

**FRED macro sources (can backfill by running existing fetchers further back):**
All 12 enabled FRED series in `config/phase_b_sources.yaml`:
- INDPRO, PAYEMS, UNRATE, CPIAUCSL, PCEPI, FEDFUNDS, DGS10, BAA10Y, NFCI, T10Y2Y, ICSA, BAMLH0A0HYM2

The `FredClient.get_series_observations()` method (`src/macro_engine/ingest/fred.py`, line 33) accepts `observation_start` and `observation_end` parameters — so backfill = running the existing fetcher with an earlier start date.

**News sources (would require new fetch code for RSS backfill):**
- `local_csv` / `local_json` providers: These read from local files. Backfill = providing historical CSV/JSON files.
- `rss` provider: Currently points to `example.invalid`. Real RSS backfill would require real feed URLs and a historical RSS archive service (most RSS feeds only return recent items).
- `manual_text` provider: Manual entry only.

**Key insight:** The repo has no historical news fetch capability. All news data must be provided as local files. RSS feeds only return recent items. There is no news API integration (no NewsAPI, GDELT, etc.).

---

## 5. Self-Reports Already in the Repo

The orchestration brief mentions "§11/§12 self-reports." These correspond to sections in `docs/PROJECT_HANDOFF.md`:

**§11 — Standard Operating Workflows** (`docs/PROJECT_HANDOFF.md`, lines ~200–260):
Documents the macro, sector, news, daily, accumulation, and replay workflows with exact CLI commands. No maturity/confidence claims — purely operational documentation.

**§12 — Dashboard Operation** (`docs/PROJECT_HANDOFF.md`, lines ~262–290):
Documents the dashboard architecture, pages, and rules. States: "It is read-only. It must not contain API keys. It must not call AI services. It must not calculate backend scores."

**Maturity/confidence claims found elsewhere:**

1. **`docs/PROJECT_HANDOFF.md` §1** (line 20): `"insufficient_history"` — the operating-readiness label.
2. **`docs/PROJECT_HANDOFF.md` §14** (line ~310): `"macro confidence: approximately 7.18%"` — the latest regime confidence.
3. **`docs/model_limitations.md`** (entire document): Extensive self-assessment. Key quote: *"The project still requires repeated balanced real-news collection before credible validation or calibration work can begin. Current readiness labels such as `insufficient_history` should be treated literally."*
4. **`docs/reviews/phase_v06_m3_accumulated_history_validation_readiness.md`**: Verdict states *"accumulated history: insufficient_history / validation readiness: not ready"*.
5. **`docs/release_checklist_v1_0.md`**: Positions the release as `v1.0-rc1` diagnostic software, not validated.
6. **`README.md`** (line ~30): *"insufficient accumulated real-news history for predictive validation"*.

**No §11/§12 self-report files exist as separate documents.** The brief's reference likely means sections 11 and 12 of the PROJECT_HANDOFF.md document.

---

## 6. Surface Area for WS-2 Work

### (a) History Backfill Candidates

1. **Backfill FRED macro series from 1980→present** — The existing `FredClient.get_series_observations()` in `src/macro_engine/ingest/fred.py` accepts `observation_start`. A script or CLI wrapper could run `ingest` with `observation_start=1980-01-01` for all 12 enabled series. The pipeline already handles data from 1990-01-01 (per `config/phase_b_sources.yaml` `historical_diagnostic.start_date`). Extending to 1980 would give 10 more years of normalization history.

2. **Backfill news from local CSV archives** — The repo already has `data/news_pilot/news_items_expanded.csv` (120 items) and `data/news_pilot/news_items_balanced.csv` (more items). These are disabled in `config/news_sources.yaml`. Task: enable them under a new profile, run `ingest-news` + `classify-news` with that profile, and accumulate the results.

3. **Create a historical news replay run across 2025–2026** — The `replay-news-history` CLI command (`src/macro_engine/replay.py`) already supports replay over date ranges. Task: run replay with `--start-date 2025-01-01 --end-date 2026-05-21` using the existing `news_items_last_30_days.csv` (or a broader file) to generate multiple run dates and clear the `insufficient_history` threshold.

4. **Build a daily news fetch-and-classify automation** — The existing `scripts/run_daily_diagnostic.ps1` / `.sh` scripts run the daily pipeline. Task: extend these to run daily (via cron/Task Scheduler) with a real-news source profile, accumulating run dates toward `early_history`.

### (b) Theme-Layer Extension Candidates

1. **Extend `source_groups` to add an `ai_compute` theme grouping** — Currently `technology_ai` is one of 12 source groups. Task: add a new `ai_compute` group to `REQUIRED_NEWS_SOURCE_GROUPS` in `src/macro_engine/news/config.py` (line 17), add it to `config/news_source_watchlist.yaml`, and create a watchlist entry pointing at a new local CSV with AI-compute-related news.

2. **Add a `secular_theme` taxonomy alongside `macro_themes`** — The current `config/news_themes.yaml` defines 18 macro themes (inflation, growth, labor, etc.) but no secular/long-duration themes. Task: add a `secular_themes` list to `config/news_themes.yaml` (e.g., `ai_adoption`, `demographic_shift`, `energy_transition`, `deglobalization`), extend the Pydantic schema in `src/macro_engine/news/config.py` (`NewsThemesConfig`), and add secular-theme classification fields to the AI prompt in `src/macro_engine/news/classify.py`.

3. **Wire up real RSS feeds for the `technology_ai` source group** — The current RSS entry (`rss_watchlist_example`) points to `example.invalid`. Task: replace with real RSS feeds (e.g., tech news feeds), enable RSS ingestion, and add source_group mapping rules in `config/news_sources.yaml` so ingested items are tagged as `technology_ai`.

4. **Add a `theme_aggregation` mode that scores secular themes over multi-month windows** — The current `news_scoring.yaml` aggregates at daily/weekly frequencies with a 7-day half-life. Task: add a `monthly` or `quarterly` aggregation frequency with a longer half-life (e.g., 30 days) to capture secular theme drift, and extend `src/macro_engine/news/scoring.py` to compute secular theme scores.

5. **Create a `secular_theme_tracker` report** — Similar to the existing `news_score_report` but focused on long-duration themes. Task: add a new CLI command `build-secular-theme-scores` and report writer, reusing the scoring engine but with secular-theme-specific config.

---

## 7. Risks / Contradictions Found

### 7.1 Brief staleness

The orchestration brief (filename style suggests several weeks old) describes the repo as having a `technology_ai` source group — this **does exist** and is correctly described. However:

- The brief implies WS-2 is blocked on "repo access not granted yet." The repo is now present and fully accessible.
- The brief mentions "§11/§12 self-reports" as if they are separate documents. They are actually sections within `docs/PROJECT_HANDOFF.md`. No standalone §11/§12 files exist.
- The brief may have been written before v1.1/v1.2 release checklists existed. The repo now has checklists through v1.2.

### 7.2 Contradictions between brief and repo reality

| Brief claim | Repo reality |
|---|---|
| "technology_ai source group" exists | ✅ Confirmed — defined in `src/macro_engine/news/config.py` line 27 and `config/news_source_watchlist.yaml` |
| News/theme layer is "closest to paradigm" | ✅ Accurate — it's the most experimental layer, explicitly labeled `insufficient_history` |
| Implied that backfill is straightforward | ⚠️ Partially true for FRED (existing fetcher), but **not** for news — no historical news API integration exists. News backfill requires providing local CSV files. |
| §11/§12 self-reports exist | ⚠️ They are sections in `PROJECT_HANDOFF.md`, not standalone files. The brief may have expected separate documents. |

### 7.3 Key risks for WS-2 planning

1. **No news API integration** — The repo has no way to fetch historical news. All news data must be provided as local files. Any "backfill" of news requires either (a) providing pre-collected CSV files, or (b) building a new news API fetcher (e.g., NewsAPI, GDELT, Bing News).

2. **RSS is effectively dead for backfill** — The RSS provider exists but points to `example.invalid`. Real RSS feeds only return recent items (typically 10–50 most recent). Historical RSS is not available without a third-party archive service.

3. **The `insufficient_history` gate is about run dates, not item count** — The repo has 270 classified items but is still `insufficient_history` because they were classified in too few batch runs. Simply adding more items won't clear the gate; the operator needs to run the pipeline across separate calendar dates.

4. **DuckDB state is local-only** — The 143 MB `data/macro_engine.duckdb` is gitignored. Any WS-2 work that depends on existing database state must either (a) preserve the DuckDB file across environments, or (b) regenerate it by re-running pipelines.

5. **Sector calibration is "weak/mixed"** — Per README and `docs/model_limitations.md`, the sector mapper's validation result is not strong. WS-2 theme-layer extension that depends on sector scores should account for this.

6. **The repo has no concept of "secular themes"** — The current theme taxonomy (`config/news_themes.yaml`) has 18 macro themes, all cyclical/short-to-medium-term. Adding secular themes (AI adoption, energy transition, etc.) requires extending the schema, config, AI prompts, scoring logic, and reports — a multi-file change.

---

*End of audit. No files outside `docs/ws2_discovery_audit.md` were created or modified.*
