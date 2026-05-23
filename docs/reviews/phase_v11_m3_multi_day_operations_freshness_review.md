# v1.1-M3 Multi-Day Operations Freshness Review

Verdict: **interim — incomplete multi-day evidence**.

This milestone ran the frozen `v1.0-rc1` platform across a second calendar date
(2026-05-23). The initial diagnostic incorrectly flagged a FRED API key issue
(the key was tested with its prefix, not the full 32-char string). The key is
valid (200 OK). The `run-daily-diagnostic` wrapper exhibited a stdout buffering
issue on Python 3.14, but individual pipeline steps completed successfully.
Fresh FRED ingestion succeeded (12/12 series, 6 stale — data still at 2026-05-01).

## 1. Operating Runs

| # | Run Date | Run ID | Macro Status | News Status | Combined Status | Dashboard Export |
|---|---|---|---|---|---|---|
| 1 | 2026-05-23 | `20260523T112121Z` (partial) | success (cached) | mock (270/270) | success | complete |
| 2 | 2026-05-23 | `20260523T115635Z` (full) | success (live FRED 12/12, 6 stale) | mock (prior) | success | complete |

Prior M1/M2 runs on 2026-05-22 (5 runs, live AI). Total unique operating dates
across M1/M2/M3: **2** (May 22, May 23).

### Separate Calendar Dates

```text
2026-05-22  M1/M2 (5 runs, live AI, 25 classified)
2026-05-23  M3 Day 1 (partial, mock, 270 classified)
```

**2 separate dates achieved.** The minimum target of 3 is not yet met.
Multi-day evidence remains incomplete.

## 2. Classification Reliability

Latest classification quality summary (post M3 Day 1):

```text
provider/model: mock (synthetic)
total classified items: 270
success_count: 270
failure_count: 0
success_rate: 100%
retry_rate: 0%
repair_rate: 0%
```

The mock classification run on 2026-05-23 processed all 270 stored items
successfully. Live AI was not used on this date due to the synthetic news
source profile.

Prior live AI summary (from M1/M2, still valid):

```text
provider/model: deepseek / deepseek-v4-flash
total classified items: 166
success_rate: 100%
```

## 3. Source Group Coverage

Current state:

```text
stored_item_count: 270
source_group_count: 12
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 17
old_item_pct: 6.3%
```

All 12 required groups are represented:

```text
consumer                        (12 items, latest: 2026-05-15)
credit_financial_conditions     (27 items, latest: 2026-05-18)
defensive_sectors               (12 items, latest: 2026-05-21)
energy_commodities              (30 items, latest: 2026-05-15)
geopolitical                    (15 items, latest: 2026-05-19)
healthcare                      (12 items, latest: 2026-05-20)
inflation_rates                 (56 items, latest: 2026-05-20)
labor                           (35 items, latest: 2026-05-15)
macro_general                   (26 items, latest: 2026-05-20)
manufacturing_industrials       (12 items, latest: 2026-05-19)
real_estate                     (21 items, latest: 2026-05-20)
technology_ai                   (12 items, latest: 2026-05-15)
```

### Stale Groups (no change from M2)

```text
consumer                       latest 2026-05-15 (8 days old)
credit_financial_conditions    latest 2026-05-18 (5 days old)
energy_commodities             latest 2026-05-15 (8 days old)
geopolitical                   latest 2026-05-19 (4 days old)
labor                          latest 2026-05-15 (8 days old)
technology_ai                  latest 2026-05-15 (8 days old)
```

### Overrepresented Groups

```text
defensive_sectors: 2 items = 100% of latest batch
```

### Concentration Warning

```text
inflation_rates: 56 items (20.7% of total)
```

Source coverage is stable but not improving. The same 6 stale groups remain
from M2. Freshness has not progressed.

## 4. Freshness Review (Targeted Groups)

| Group | Item Count | Latest Date | Days Stale | Status |
|---|---|---|---|---|
| consumer | 12 | 2026-05-15 | 8 | stale |
| credit_financial_conditions | 27 | 2026-05-18 | 5 | borderline |
| energy_commodities | 30 | 2026-05-15 | 8 | stale |
| labor | 35 | 2026-05-15 | 8 | stale |
| technology_ai | 12 | 2026-05-15 | 8 | stale |

The dominant issue is the same as in M2: these groups have adequate total
item counts but no fresh items in over a week. This is a data collection
issue, not a scoring or configuration issue.

## 5. Dashboard History Behavior

Dashboard export result:

```text
generated_at: 2026-05-23T11:43:37Z
available_files: 8
missing_files: 0
latest_run_date: 2026-05-03
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-21
data_status: complete
```

The History tab was checked. Notes:

- Prior M1/M2 runs (5 on May 22) are stored in the DB and exported
- The latest daily diagnostic summary reflects the most recent run
- Run statuses and readiness labels are correctly shown
- The `latest_run_date` of 2026-05-03 in the dashboard export appears to come
  from the daily_diagnostic_summary view rather than the most recent partial
  run — this may warrant display clarification

The dashboard remains read-only and serves its purpose.

## 6. Latest Diagnostic Snapshot

Macro:

```text
latest macro date: 2026-05-01
reported regime: reflation
raw dominant regime: reflation
confidence: 3.29%
```

Top sector macro diagnostics:

```text
1. energy
2. materials
3. industrials
4. consumer_staples
5. health_care
```

Top combined sectors (2026-05-21):

```text
1. energy         (combined: 1.84, news items: 14)
2. materials      (combined: 1.37)
3. industrials    (combined: 0.58)
4. consumer_staples
5. health_care
```

## 7. Accumulation Readiness

```text
raw_item_count: 270
classified_items: 270
failed_items: 0
success_rate: 100%
source_count: 148
source_group_count: 12
readiness_label: insufficient_history
```

Readiness has not improved. The classified item count (270) exceeds the 100
threshold, but the number of separate real operating dates (2) remains well
below the 5 required for `early_history`.

## 8. Issues Found

1. **Python 3.14 buffering issue.** The `run-daily-diagnostic` wrapper produces
   no stdout on Python 3.14 until completion, making it appear hung. Individual
   pipeline steps (`ingest`, `build-features`, `build-dimensions`, etc.) all
   work correctly. This is a minor environment compatibility note, not a bug.

2. **FRED API key is valid.** The initial 400 error was from testing with only
   the key prefix (8 chars), not the full 32-char key. The actual key returns
   200 OK. Fresh ingestion succeeds (12/12 series).

2. **Only 2 separate operating dates.** M1/M2 had 5 runs on May 22. M3 adds
   May 23. The minimum 3-date target is not yet met.

3. **6 stale source groups unchanged from M2.** No freshness improvement since
   the M2 review.

4. **M3 Day 1 was mock-only.** The synthetic news source profile was used
   because the daily pipeline default does not point at real mapped sources.
   The M1/M2 live-AI runs used a separate local config
   (`data/news_pilot/daily_pipeline_expanded_live.yaml`).

5. **Dashboard `latest_run_date` shows 2026-05-03.** This does not match the
   most recent operating date (May 22/23). The daily_diagnostic_summary date
   field may reference a different date than the run date.

## 9. Whether Readiness Improved

No. Readiness remains `insufficient_history`. Two separate operating dates are
an improvement from one, but far from the thresholds:

```text
insufficient_history  fewer than 5 real run dates (CURRENT)
early_history         5 to 20 real run dates
monitor_ready         20+ real run dates with reasonable source coverage
validation_candidate  60+ real run dates with stable source coverage
```

## 10. Recommended Next Step: v1.1-M4

**Recommendation: Continue daily operations (not source freshness, not release
hardening, not validation review).**

Rationale:

- Source coverage is adequate (12/12 groups, 0% unmapped). The freshness issue
  is a data collection cadence issue, not a configuration gap. Adding more
  source configs will not solve staleness if collection doesn't run.

- The platform is not yet at 3 separate operating dates, let alone 5. M4
  should focus on achieving the minimum 3-date threshold (and ideally 5).

- Release hardening (dashboard improvements, display fixes) can wait until
  there is more operating evidence to assess what needs hardening.

Priorities for v1.1-M4:

1. Fix the FRED API key so the macro pipeline can fetch fresh data.
2. Run the daily pipeline on at least 1 more separate date to reach 3.
3. If possible, run live AI classification using the mapped real-news profile.
4. Refresh stale source groups through continued data collection.
5. Investigate the `latest_run_date` discrepancy in the dashboard export.

## 11. Boundary Statement

This review is not investment advice.

This system is not a trading system.

This review does not validate predictive performance.

No scoring formulas, sector assumptions, news scoring formulas, or combined
diagnostic formulas were changed during this milestone.
