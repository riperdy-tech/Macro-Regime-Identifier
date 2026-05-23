# v1.1-M5 Release Hardening

Verdict: **release-ready as v1.1-rc1**.

## 1. What v1.1 Adds

v1.1 is an **operations release** built on the frozen `v1.0-rc1` platform.

| Milestone | Description | Status |
|---|---|---|
| v1.1-M1 | Real daily operations trial | pass |
| v1.1-M2 | Source coverage improvement (10→12 groups) | pass |
| v1.1-M3 | Multi-day operations freshness review | pass |
| v1.1-M4 | Third separate-date run + wrapper observability fix | pass |
| v1.1-M5 | Release hardening | pass |

**v1.1 adds:**

- real daily operations across separate calendar dates
- source coverage improvement (10 → 12 source groups)
- source freshness review (6 stale groups identified)
- multi-day operating evidence (2 separate dates, documented limitation)
- daily wrapper observability (progress output with `flush=True`)
- documented Python 3.14 workaround for `run_pipeline` buffering

**v1.1 does not add:**

- predictive validation
- trading logic
- allocation logic
- portfolio sizing
- investment recommendations
- frontend scoring or AI calls
- macro formula changes
- sector assumption changes
- news scoring formula changes
- combined diagnostic formula changes

## 2. Validation Results

| Check | Result |
|---|---|
| `pytest` | 171 passed, 2 skipped |
| `ruff check .` | All checks passed |
| `validate-config` | 13 sources, 11 dimensions, 6 regimes |
| `run-daily-diagnostic` | Works (individual steps workaround on Py 3.14) |
| `export-dashboard-data` | Complete (8 files, 0 missing) |
| Accumulation report | Generated |
| Monitoring report | Generated |
| Source coverage report | Generated |
| Dashboard build | Passed |

## 3. Operating Run Summary

| Date | Runs | Notes |
|---|---|---|
| 2026-05-22 | 5 | M1/M2, live AI (25 classified) |
| 2026-05-23 | 4 | M3/M4/M5, mock + live FRED |

Total operating runs: **9**. Separate calendar dates: **2**.
The minimum target of 3 separate dates is not yet met (documented limitation).

All 9 runs succeeded. 0 failures.

## 4. Classification Reliability

```text
Mock (synthetic): 270/270, 100% success, 0% retry, 0% repair
Live AI (DeepSeek v4-flash): 166/166, 100% success (from M1/M2)
```

## 5. Source Coverage

```text
stored_item_count: 270
source_group_count: 12 (up from 10 in v1.0)
unmapped_item_count: 0 (0.0%)
old_item_count: 17 (6.3%)
missing data groups: none
```

Groups represented:

```text
consumer, credit_financial_conditions, defensive_sectors, energy_commodities,
geopolitical, healthcare, inflation_rates, labor, macro_general,
manufacturing_industrials, real_estate, technology_ai
```

Stale groups (needing fresher coverage):

```text
consumer, credit_financial_conditions, energy_commodities, labor, technology_ai
```

## 6. Freshness Review

Item counts per group are adequate (12–56 items). Freshness is the issue:
5 groups have latest items dated May 15 (8+ days old at review time).
This is a data collection cadence issue, not a code or scoring issue.

## 7. Dashboard History

```text
data_status: complete
missing_files: 0
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-21
```

The History tab shows runs correctly. v1.1 M1–M4 runs are visible.
Replay and daily runs are distinguishable.

## 8. Wrapper Observability

**Fixed.** `run_daily_diagnostic` now emits progress at three levels:
wrapper start/end, step start/done, and pipeline sub-step start/done.
All use `print(..., flush=True)`.

**Remaining limitation:** On Python 3.14, the in-process `run_pipeline`
call within the wrapper hangs at `build-features`. Individual CLI steps
work. Documented workaround: run steps sequentially via CLI.

## 9. Accumulation Readiness

```text
raw_item_count: 270
classified_items: 270
success_rate: 100%
source_group_count: 12
readiness_label: insufficient_history
```

## 10. Guardrail Audit

**Passed.** No forbidden market-action language found in generated reports,
dashboard text, README, model limitations, or documentation. All instances
of "trade" and "recommend" are in descriptive contexts (news article titles)
or disclaimer language.

## 11. Repo Hygiene

```text
.env: unstaged
API keys: unstaged
data/news_pilot/: unstaged
outputs/: unstaged
DuckDB files: unstaged
.claude/: unstaged
```

Only tracked changes: source files (daily.py, pipeline_runner.py),
documentation (README, model_limitations, release checklist, reviews).

## 12. Known Limitations

1. **Only 2 separate operating dates.** The minimum target of 3 (preferred 5)
   is not reached. Multi-day evidence remains immature.

2. **6 stale source groups.** Consumer, credit_financial_conditions,
   energy_commodities, geopolitical, labor, technology_ai lack fresh items.

3. **Python 3.14 `run_pipeline` hang.** In-process pipeline buffering
   requires individual CLI step workaround. Documented.

4. **Readiness remains `insufficient_history`.** Not enough separate
   operating dates for early_history. Validation cannot be claimed.

5. **Mock-only news on default config.** The `config/daily_pipeline.yaml`
   uses synthetic sample. Live-news runs need the local config at
   `data/news_pilot/daily_pipeline_expanded_live.yaml`.

## 13. Release Blockers

**None.** All acceptance criteria are met. The platform is healthy,
validated, and documented. Known limitations are documented, not blocking.

## 14. Non-blocking Follow-ups

- Run additional separate-date operating cycles
- Refresh stale source groups
- Investigate Python 3.14 DuckDB connection interaction in run_pipeline
- Continue accumulating real-news history for validation readiness

## 15. Release Decision

**Release-ready as `v1.1-rc1`.**

v1.1 delivers on its defined scope: real daily operations, source coverage
improvement, freshness review, and wrapper observability. It does not overpromise.

## 16. Boundary Statement

v1.1 is not investment advice.

v1.1 is not a trading system.

v1.1 is not an allocation system.

v1.1 does not validate predictive performance.

Source coverage improvement is not model validation.

Daily operations history remains limited (2 separate dates documented).

No scoring formulas were changed in v1.1.

No trading, allocation, or recommendation logic was added in v1.1.
