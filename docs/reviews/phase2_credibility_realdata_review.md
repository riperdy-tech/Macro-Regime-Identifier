# Phase 2 Credibility: Real-Data Review

Date: 2026-07-14
Data: full revised FRED history for all 14 production series, fetched via the
key-less `fredgraph.csv` endpoint into a clean single-vintage local DuckDB.
Window: evaluation months 1990-01 through 2026-07 (439 valid months).

All numbers below are revised-data diagnostics, not point-in-time results and
not evidence of predictive value.

## Finding 1: Duplicate raw-observation vintages corrupted live scores

FRED stamps `realtime_start` with the fetch date. The raw-observation upsert
keyed its delete on the full realtime tuple, so a DuckDB persisted across
daily CI runs accumulated one duplicate copy of the full history per run.
Rolling-window z-scores and positional transforms (diff, pct_change) then ran
over duplicated rows.

Measured impact on the same July 2026 evaluation month:

| source | reported confidence |
| --- | --- |
| live dashboard (duplicate-inflated cached DB) | 0.136 |
| clean single-vintage rebuild | 0.009 |

Fix shipped in this phase: upsert now keeps one row per `(series_id, date)`
(latest fetch wins) and the feature builder drops duplicate dates
defensively, so the polluted CI cache self-heals on the next run.

Follow-up: the confidence-calibration ledger accumulated entries while macro
confidence was inflated; resetting it should be considered once this deploys.

## Finding 2: Publication-lag alignment changes 12% of reported history

With per-source `publication_lag_days` applied, evaluation dates can no
longer see observations before their approximate release date.

| comparison (both timelines valid, 439 months) | months |
| --- | --- |
| raw dominant regime differs | 67 |
| reported regime differs | 52 |

Differences cluster around turning points (2002, 2007-2008, 2015, 2017,
2019, 2021), which is exactly where look-ahead flattered the old timeline.

## Finding 3: NBER benchmark baseline

Production formulas, publication lags applied, clean data:

| metric | with lags | without lags |
| --- | --- | --- |
| AUROC (recession probability vs NBER months) | 0.873 | 0.906 |
| threshold 0.25: NBER months flagged | 95.0% | 90.0% |
| threshold 0.25: expansion months flagged | 28.8% | 27.8% |
| raw dominant label = recession in NBER months | 55.0% | 67.5% |
| raw dominant label = recession in expansion months | 23.6% | 23.3% |
| detection lead, 1990 / 2001 / 2008 / 2020 | -6 / -3 / -4 / -4 | -6 / -5 / -4 / -6 |

Read: monthly recession probability separates NBER recessions well, but the
five-way dominant label is noisy (it marks recession in roughly a quarter of
expansion months), and usable thresholds still flag a lot of expansion
months. This is the honest starting point the lag-free timeline overstated.

## Experiment A: Transition-filter hysteresis

`config/experiments/phase2_hysteresis.yaml`, run against the clean lag-aware
database. Production formulas throughout; only the reported-transition filter
varies. 439 months, 32 raw regime switches (mean spell ~13.7 months).

| variant | reported switches |
| --- | --- |
| no filter | 32 |
| production gate (confidence >= 0.08) | 32 |
| gate 0.10 | 30 |
| gate 0.08 + two-month confirmation below confidence 0.15 | 27 |
| gate 0.08 + unconditional two-month confirmation | 23 |

Notable: the current production gate filters zero switches on clean data —
its apparent effect to date came from the duplicate-inflated confidence
scale. A two-month confirmation rule is the effective whipsaw control
(32 -> 23 switches, mean spell ~19 months) and needs no threshold tuning.

## Experiment B: Inflation normalization window

Identical pipeline with only `headline_cpi_yoy_z` and `pce_price_yoy_z`
moved from `rolling_z_5y` to `rolling_z_10y` (less recentering during long
inflation episodes). Reported regime changes in 47 of 439 months.

| metric | 5y window (production) | 10y window |
| --- | --- | --- |
| AUROC | 0.873 | 0.888 |
| threshold 0.30: NBER months flagged | 82.5% | 97.5% |
| threshold 0.30: expansion months flagged | 24.3% | 25.6% |
| detection lead, 1990 / 2001 / 2008 / 2020 | -6 / -3 / -4 / -4 | -6 / -9 / -4 / -5 |

The 10y window improves recession-month coverage materially at roughly
unchanged false-positive share.

## Candidate promotion decisions (not applied)

Per experiment policy, production config is unchanged. Candidates for a
promotion review, in order of evidence strength:

1. Two-month confirmation in the transition filter (unconditional variant).
2. `rolling_z_10y` for the two inflation features.
3. Regime-taxonomy simplification (five overlapping regimes produce the noisy
   dominant label above); needs a design decision, not just a config change.
4. Confidence-calibration ledger reset after the dedupe fix deploys.

## Caveats

- Revised data with fixed approximate publication lags, not ALFRED vintages.
- NBER dates are themselves published with long real-time delays.
- Benchmark agreement is a sanity check of the configured formulas, not
  validation of predictive usefulness.
