# WS2-T1 — FRED 1980 Backfill Plan

**Date:** 2026-05-24  
**Status:** Plan — operator must review before executing `scripts/backfill_fred_1980.py`

---

## 1. What Changes

### Which DuckDB tables get new rows

Only **`raw_observations`** gets new rows. The backfill script calls `run_fred_ingestion()` with `observation_start="1980-01-01"`, which fetches FRED observations and writes them via `DuckDBStore.upsert_raw_observations()`.

The `source_health` and `ingestion_runs` tables are also updated as a side effect of `run_fred_ingestion()` (see `service.py:64–68` and `service.py:76–87`).

### Approximate row counts per series

From `config/phase_b_sources.yaml`, the 12 enabled series and their frequencies:

| Series ID | Frequency | Est. rows 1980–1990 (10 yr) | Notes |
|-----------|-----------|-----------------------------|-------|
| INDPRO | monthly | ~120 | 1 per month |
| PAYEMS | monthly | ~120 | 1 per month |
| UNRATE | monthly | ~120 | 1 per month |
| CPIAUCSL | monthly | ~120 | 1 per month |
| PCEPI | monthly | ~120 | 1 per month |
| FEDFUNDS | monthly | ~120 | 1 per month |
| DGS10 | daily | ~2,530 | ~253 trading days/yr × 10 |
| BAA10Y | daily | ~2,530 | ~253 trading days/yr × 10 |
| NFCI | weekly | ~520 | ~52 weeks/yr × 10 |
| T10Y2Y | daily | ~2,530 | ~253 trading days/yr × 10 |
| ICSA | weekly | ~520 | ~52 weeks/yr × 10 |
| BAMLH0A0HYM2 | daily | ~2,530 | ~253 trading days/yr × 10 |

**Total estimated new rows: ~11,940** (monthly: ~720, weekly: ~1,040, daily: ~10,120)

> **Note:** USSLIND is disabled (`enabled: false` in config), so it is NOT backfilled.

---

## 2. What Does NOT Change

- **No schema migration.** The `raw_observations` table schema is unchanged. The `PRIMARY KEY(series_id, date, realtime_start, realtime_end)` already exists (`duckdb_store.py:150–155`).
- **No config edit required.** `phase_b_sources.yaml` is untouched. The `historical_diagnostic.start_date` remains `1990-01-01` — the backfill adds earlier data but does not change the diagnostic start date. The operator can later adjust `start_date` if desired, but that is out of scope for this task.
- **No impact on classification/scoring code.** Feature building, dimension scoring, regime scoring all operate on whatever data is in `raw_observations`. Adding older data simply gives the rolling normalizers (e.g., `rolling_z_5y`, `rolling_z_10y`) more history to compute from. No code changes needed.
- **No new pip dependencies.** The script uses only `FredClient`, `run_fred_ingestion`, `DuckDBStore`, and stdlib — all already in `pyproject.toml`.

---

## 3. Idempotency

**The ingest path IS idempotent.** `DuckDBStore.upsert_raw_observations()` (`duckdb_store.py:157–170`) uses a **DELETE + INSERT** pattern:

```python
DELETE FROM raw_observations
USING raw_frame
WHERE raw_observations.series_id = raw_frame.series_id
  AND raw_observations.date = raw_frame.date
  AND raw_observations.realtime_start = raw_frame.realtime_start
  AND raw_observations.realtime_end = raw_frame.realtime_end
```

This matches on the composite primary key, deletes existing rows, then inserts the new ones. Running the script twice produces the same final state — no duplicates.

**However**, the `ingestion_runs` table gets a new row each run (different `run_id`). This is harmless metadata.

**The script itself is also idempotent** — it always fetches the full 1980–present range and upserts. No incremental logic needed.

---

## 4. API Quota

### FRED rate limits

The FRED API does **not** publish a hard rate limit for registered users. However, best practice is to stay under ~120 requests/minute. The `FredClient` (`fred.py:33–55`) does **not** implement any delay or backoff — it fires requests as fast as the session allows.

### Cost of this backfill

- 12 series × 1 API call each for metadata = 12 requests
- 12 series × 1 API call each for observations = 12 requests
- **Total: 24 requests** (one round-trip per series)

At ~200–500 ms per request, the entire backfill should complete in **under 30 seconds** on a typical connection. This is negligible.

### Risk

If the operator's API key has no rate-limit buffer, the script could hit a temporary 429. The script does not implement retry logic. If a 429 occurs, `FredError` is raised and the script exits non-zero. The operator can retry with `--series` to backfill one series at a time.

---

## 5. Storage Delta

The DuckDB is currently ~143 MB (per audit §4.1). The backfill adds ~11,940 rows to `raw_observations`.

- Each row is ~100–150 bytes (text fields + doubles + dates).
- Estimated raw data: ~11,940 × 150 B ≈ **1.8 MB**
- DuckDB compresses well; actual on-disk increase likely **< 1 MB**.
- The Parquet export (`data/raw/fred/`) will also grow by a similar amount.

**Conclusion:** Storage impact is negligible (< 1% of current DB size).

---

## 6. Rollback

If the backfilled data is bad, the operator can undo with a single SQL DELETE:

```sql
DELETE FROM raw_observations WHERE date < '1990-01-01';
```

This can be run via the DuckDB CLI or a one-liner:

```bash
python -c "
from macro_engine.storage.duckdb_store import DuckDBStore
store = DuckDBStore('data/macro_engine.duckdb')
with store._connect() as con:
    con.execute(\"DELETE FROM raw_observations WHERE date < '1990-01-01'\")
    print(f'Deleted {con.fetchall()} rows')
"
```

Alternatively, the operator can restore a DuckDB backup if one was taken before running. The plan recommends taking a backup:

```bash
cp data/macro_engine.duckdb data/macro_engine.duckdb.bak
```

**The script itself does NOT delete rows, drop tables, or modify schema.** It only inserts/upserts.

---

## 7. Step-by-Step Run Procedure

### Prerequisites

1. `FRED_API_KEY` environment variable set (or in `.env` file).
2. DuckDB at `data/macro_engine.duckdb` exists (has been initialized by a prior pipeline run).
3. Working directory is `Macro Regime Indicator/`.

### Steps

```bash
# 0. (Optional) Backup the current DuckDB
cp data/macro_engine.duckdb data/macro_engine.duckdb.bak

# 1. Dry-run — see what would be fetched (no network, no writes)
python scripts/backfill_fred_1980.py

# 2. (Optional) Dry-run a single series
python scripts/backfill_fred_1980.py --series UNRATE

# 3. Apply — actually fetch and write
python scripts/backfill_fred_1980.py --apply

# 4. (Optional) Apply a single series first (cautious workflow)
python scripts/backfill_fred_1980.py --apply --series UNRATE

# 5. Verify — check row counts
python -c "
from macro_engine.storage.duckdb_store import DuckDBStore
store = DuckDBStore('data/macro_engine.duckdb')
obs = store.read_raw_observations()
print('Total raw_observations:', len(obs))
print('Pre-1990 rows:', len(obs[obs['date'] < '1990-01-01']))
print('Series breakdown:')
for sid in sorted(obs['series_id'].unique()):
    sub = obs[obs['series_id'] == sid]
    pre90 = sub[sub['date'] < '1990-01-01']
    print(f'  {sid}: {len(sub)} total, {len(pre90)} pre-1990')
"
```

### Pass/Fail Criteria

| Check | Expected | How to verify |
|-------|----------|---------------|
| Dry-run prints series list | 12 enabled series printed | `python scripts/backfill_fred_1980.py` |
| Dry-run prints estimated row counts | ~120 for monthly, ~2,530 for daily | See output |
| `--apply` exits 0 | Exit code 0 | `echo $?` |
| Pre-1990 rows exist | > 0 for each enabled series | Verification query above |
| UNRATE pre-1990 rows | ~120 (monthly, 1980-01 to 1990-01) | Verification query |
| DGS10 pre-1990 rows | ~2,530 (daily trading days) | Verification query |
| No duplicate rows | Row count unchanged on re-run | Run `--apply` twice, verify same count |
| `--series UNRATE` only affects UNRATE | Only UNRATE gets new pre-1990 rows | Verification query |

---

## 8. Risks from Audit §7

### Applicable risks

1. **§7.3(4) — DuckDB state is local-only** (HIGH). The backfill only affects the local `data/macro_engine.duckdb` file. It does **not** propagate to CI, staging, or other environments. The operator must manually copy the DuckDB or re-run the script in each environment.

2. **§7.3(1) — No news API integration** (NOT APPLICABLE). This is a FRED macro backfill, not a news backfill.

3. **§7.3(3) — `insufficient_history` gate** (INDIRECTLY APPLICABLE). The FRED backfill does not directly affect the news `insufficient_history` label. However, adding 10 years of normalization history may improve the quality of rolling z-scores, which could indirectly improve regime confidence. The `insufficient_history` label is a news-layer concern and is not addressed by this task.

4. **§7.3(5) — Sector calibration is "weak/mixed"** (NOT APPLICABLE). This backfill does not touch sector code.

5. **§7.3(6) — No concept of "secular themes"** (NOT APPLICABLE). This is a macro data backfill, not a theme-layer change.

### Contradictions check

The audit doc claims at §6(a) that the `FredClient.get_series_observations()` accepts `observation_start`. **Confirmed** — `fred.py:31–55` shows the parameter is accepted and passed to the API. The audit doc is accurate on this point.

The audit doc claims the current pipeline floor is 1990. **Confirmed** — `phase_b_sources.yaml` has `historical_diagnostic.start_date: "1990-01-01"`.

No contradictions found between audit doc and repo reality for this candidate.

---

## Appendix: File Citations

| Claim | File | Line(s) |
|-------|------|---------|
| `FredClient.get_series_observations()` accepts `observation_start` | `src/macro_engine/ingest/fred.py` | 31–55 |
| `run_fred_ingestion()` passes `start` to `observation_start` | `src/macro_engine/ingest/service.py` | 52 |
| `upsert_raw_observations()` is idempotent (DELETE+INSERT) | `src/macro_engine/storage/duckdb_store.py` | 157–170 |
| `raw_observations` table schema (PK = series_id, date, realtime_start, realtime_end) | `src/macro_engine/storage/duckdb_store.py` | 150–155 |
| `historical_diagnostic.start_date` is `1990-01-01` | `config/phase_b_sources.yaml` | `historical_diagnostic.start_date` |
| 12 enabled FRED series with frequencies | `config/phase_b_sources.yaml` | sources list |
| CLI `ingest` command accepts `--start` | `src/macro_engine/cli.py` | 105–120 |
| DuckDB is ~143 MB, local-only, gitignored | `docs/ws2_discovery_audit.md` | §4.1, §7.3(4) |
| No rate-limit/backoff in FredClient | `src/macro_engine/ingest/fred.py` | 33–55 (no sleep/retry) |
| Existing script conventions (shebang, cd to repo root) | `scripts/run_daily_diagnostic.sh` | 1–10 |
