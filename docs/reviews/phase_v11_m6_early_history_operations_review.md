# v1.1-M6 Early-History Operations Review

Verdict: **interim — insufficient separate-date coverage**.

## 1. Objective

Continue operating the frozen v1.1-rc1 platform until 5+ separate calendar
dates are reached.

## 2. Separate Operating Dates

Distinct run dates in DB: **8** (includes historical replay).

Real daily operating dates (non-replay): **3**.

| Date | Type | Runs | Macro | News |
|---|---|---|---|---|
| 2026-05-22 | M1/M2 real | 5 | live FRED | live AI (25 classified) |
| 2026-05-23 | M3/M4/M5 real | 3 | live FRED | mock (270 classified) |
| 2026-05-24 | M6 real | 1 | live FRED (individual steps) | mock (from prior) |

Replay dates in DB (not real operating dates): April 22-23, May 18-21.

**3 real operating dates — short of the 5-date target.**

## 3. Day 3 Run Summary (May 24)

Run via individual CLI steps (Python 3.14 workaround):

```text
ingest:           12/12 series, 6 stale
build-features:   354,445 features, 340,320 valid
build-dimensions: 2,185 valid dimension rows
build-regimes:    2,185 valid regime rows
historical diag:  437 dates, 66 switches
sector scores:    4,807 valid rows
combined:         3,454 rows
accumulation:     readiness = insufficient_history
dashboard:        8/8 files, complete
```

## 4. Current Operating Snapshot

```text
latest macro date:   2026-05-01
reported regime:     reflation (3.3% confidence)
top sectors:         energy, materials, industrials
classified items:    270 (100% success)
source groups:       12/12 (0% unmapped)
stale groups:        6 (consumer, credit, energy, geopolitical, labor, tech_ai)
readiness label:     insufficient_history
```

## 5. Classification Reliability

```text
mock (synthetic):    270/270, 100% success
live AI (M1/M2):     166/166, 100% success
total classified:    436 items
```

## 6. Accumulation Readiness

```text
raw_item_count:      270
classified_items:    270
success_rate:        100%
source_group_count:  12
readiness_label:     insufficient_history
```

Readiness thresholds:

```text
insufficient_history  < 5 real dates    ← CURRENT (3 real dates)
early_history         5-20 real dates
monitor_ready         20+ real dates, good coverage
validation_candidate  60+ real dates, stable coverage
```

## 7. Remaining Operational Issues

1. **Only 3 real operating dates.** Minimum 5 needed for `early_history`.
2. **6 stale source groups.** Unchanged from M2. Collection cadence issue.
3. **Python 3.14 workaround still needed.** Individual CLI steps required;
   `run_pipeline` and chained commands hang at build-features.
4. **Replay dates inflate DB count.** 8 distinct dates in DB, but only 3
   are real daily operating dates. This distinction must be preserved.

## 8. Dashboard History

Dashboard export complete (8/8 files). History tab reflects run data.
Replay and daily runs are distinguishable in the DB.

## 9. Whether v1.2 Validation-Readiness Can Begin

**No.** The platform needs 2 more real operating dates to reach `early_history`
and at least 17 more to reach `monitor_ready`. The 6 stale source groups also
need fresh data before validation work begins. Starting validation before
operating evidence is sufficient would be premature.

## 10. Recommended Next Step

```text
v1.1-M7: Continue real daily operations until 5+ separate dates.
```

- Run the pipeline on at least 2 more separate calendar dates
- Refresh stale source groups through continued data collection
- Do not start v1.2 until `early_history` threshold is met
- Preserve v1.1-rc1 behavior — no scoring changes

## 11. Boundary Statement

This review is not investment advice.

This system is not a trading system.

This review does not validate predictive performance.

No scoring formulas were changed.

The 3 real operating dates are a documented limitation.
