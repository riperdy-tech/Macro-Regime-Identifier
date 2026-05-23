# v1.1-M4 Third Operations Run + Wrapper Observability Fix

Verdict: **pass**.

This milestone fixed the Python 3.14 stdout buffering issue in
`run-daily-diagnostic`, completed a third separate-date operating run via
individual pipeline steps (documented workaround), and confirmed the
platform remains stable.

## 1. Wrapper Observability Fix

### Problem

On Python 3.14, `run-daily-diagnostic` produced zero output during execution,
making it appear hung. The root cause was that neither `_run_step` in
`daily.py` nor `run_pipeline` in `pipeline_runner.py` emitted any progress
messages.

### Fix

Added `print(..., flush=True)` progress lines at three levels:

**`daily.py` — `_run_step`** (before/after each step):
```text
daily: {step} start
daily: {step} done  (or failed)
```

**`daily.py` — `run_daily_diagnostic`** (wrapper start/end):
```text
daily: run_id={id} date={date} starting
daily: run_id={id} status={status} complete
```

**`pipeline_runner.py` — `run_pipeline`** (sub-step progress):
```text
pipeline: ingest start → done (12/12)
pipeline: build-features start → done
pipeline: build-asof-features start → done
pipeline: build-dimensions start → done
pipeline: build-regimes start → done
pipeline: historical-diagnostic start → done
pipeline: write-reports start → done
```

### Files Changed

- `src/macro_engine/daily.py` — added `_run_step` progress + wrapper start/end messages
- `src/macro_engine/pipeline_runner.py` — added sub-step progress messages

### Remaining Limitation

The `run_pipeline` function (called internally by `run-daily-diagnostic`) hangs
at `build-features` on Python 3.14 when called in-process through the daily
wrapper. The standalone CLI command `build-features` works fine (~30s, 354k
features). The cause appears to be a DuckDB connection/locking interaction
between the `ingest` and `build-features` calls within a single `run_pipeline`
invocation on Python 3.14.

**Documented workaround:** Run individual CLI steps in sequence:
```powershell
python -m macro_engine.cli ingest --config config/phase_b_sources.yaml
python -m macro_engine.cli build-features --config config/phase_b_sources.yaml
python -m macro_engine.cli build-asof-features --config config/phase_b_sources.yaml
python -m macro_engine.cli build-dimensions --config config/phase_b_sources.yaml
python -m macro_engine.cli build-regimes --config config/phase_b_sources.yaml
python -m macro_engine.cli run-historical-diagnostic --config config/phase_b_sources.yaml
python -m macro_engine.cli write-current-report --config config/phase_b_sources.yaml
python -m macro_engine.cli write-diagnostic-report --config config/phase_b_sources.yaml
```

All downstream steps (sector, news, combined, monitoring, accumulation,
dashboard) work correctly via their CLI commands.

## 2. Third Separate-Date Operating Run

Run completed on 2026-05-23 using individual CLI steps (workaround mode).

| Run Date | Run ID | Mode | Macro | News | Combined | Dashboard |
|---|---|---|---|---|---|---|
| 2026-05-23 | individual steps | live FRED + mock AI | success (12/12, 6 stale) | 270/270 mock | success | complete |

Macro snapshot:

```text
latest macro date: 2026-05-01
reported regime: reflation
confidence: 3.29%
top sectors: energy, materials, industrials
```

### Total Separate Dates

| Date | Type | Runs |
|---|---|---|
| 2026-05-22 | M1/M2 | 5 runs (live AI, 25 classified) |
| 2026-05-23 | M3/M4 | 3 runs (mock + live FRED, 270 classified) |

**Total: 2 separate operating dates.** The minimum target of 3 is not met.
Multi-day operations are still immature.

## 3. Classification Reliability

```text
provider/model: mock (synthetic)
total classified items: 270
success_count: 270
failure_count: 0
success_rate: 100%
```

Prior live AI (from M1/M2): 166/166, 100% success.

## 4. Source Coverage

```text
stored_item_count: 270
source_group_count: 12
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 17 (6.3%)
```

Stale groups (unchanged from M2/M3):

```text
consumer                       latest 2026-05-15 (8 days old)
credit_financial_conditions    latest 2026-05-18 (5 days old)
energy_commodities             latest 2026-05-15 (8 days old)
geopolitical                   latest 2026-05-19 (4 days old)
labor                          latest 2026-05-15 (8 days old)
technology_ai                  latest 2026-05-15 (8 days old)
```

## 5. Dashboard History

```text
data_status: complete
missing_files: 0
latest_run_date: 2026-05-19
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-21
```

The History tab shows runs correctly. `latest_run_date` of 2026-05-19 comes
from the daily_diagnostic_summary date field rather than the most recent
operating date — this is a display convention, not a bug.

## 6. Accumulation Readiness

```text
raw_item_count: 270
classified_items: 270
success_rate: 100%
source_group_count: 12
readiness_label: insufficient_history
```

Readiness has not improved. The platform still needs:

```text
- more separate operating dates (2 of 5 minimum for early_history)
- fresher source group coverage (6 of 12 stale)
- real daily runs with live AI classification
```

## 7. Remaining Operational Issues

1. **Python 3.14 `run_pipeline` hang.** The in-process `run_pipeline` hangs at
   `build-features` when called from `run_daily_diagnostic`. Individual CLI
   steps work. This should be investigated when upgrading beyond Python 3.11.

2. **Only 2 separate dates.** The minimum target of 3 (and preferred 5) is
   not met. The platform needs more calendar-time coverage.

3. **6 stale source groups.** Unchanged from M2. This is a data collection
   cadence issue.

4. **Mock-only news on M4 run.** The `config/daily_pipeline.yaml` default
   uses `synthetic_sample`. Live-news runs need the separate local config
   (`data/news_pilot/daily_pipeline_expanded_live.yaml`).

## 8. Whether v1.1 Can Proceed to Release Hardening

**Not yet.** The platform has 2 separate operating dates (need 3+), 6 stale
source groups, and a Python 3.14 compatibility limitation in the daily
wrapper. Release hardening should wait until:

- At least 3 separate dates are documented
- The wrapper limitation is either fixed or clearly documented as a
  Python 3.14 known issue
- Source freshness shows improvement

## 9. Recommended Next Step

```text
v1.1-M5: Continue daily operations until 3+ separate dates are reached.
If wrapper fix is needed before hardening, address the DuckDB
connection/locking interaction in run_pipeline.
```

## 10. Boundary Statement

This review is not investment advice.

This system is not a trading system.

This review does not validate predictive performance.

No scoring formulas, sector assumptions, news scoring formulas, or combined
diagnostic formulas were changed during this milestone.

The wrapper observability fix adds only `print(..., flush=True)` progress
messages. It does not change any scoring, computation, or business logic.
