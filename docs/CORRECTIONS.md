# Corrections to committed narrative

Git history on this branch is not rewritten. When a commit's message or a code comment
states something that later turns out to be wrong, the correction is recorded here instead,
dated and pointing at what was actually true.

## 2026-09-22 — `014467c` overstated the point-in-time freeze's cause

Commit `014467c` ("S0.7b vintage step becomes required; a fetch date can never become
realtime_start") said:

> This is the mechanism that let the empirical publication index (the MIN stored
> realtime_start per series/date) read as published on whatever day a human happened to run
> the backfill.

That diagnosis was investigated further and withdrawn (see
`../../stocks-workspace/docs/review_2026-09-22/reports/MRI_S0_APPROVAL.md` §0.1, §2.2, and the
correction applied in `src/macro_engine/ingest/fred.py` and
`tests/test_pit_no_lookahead.py` in this same review).

`raw_observation_vintages.realtime_start` is an **as-of index by design**: it records the
as-of date at which a vintage was requested, not a publication date FRED assigns. Routine
ingest (`get_series_observations`) never writes that table at all — the only writer is the
ALFRED vintage path (`ingest/service.py`).

The point-in-time freeze between 2026-05-31 and 2026-09-18 was caused by **absence, not
mislabelling**: no as-of date in June, July or August was ever queried, because the venv's
editable install pointed at a path the 2026-09-22 reorganisation had removed, so the chain
could not run at all. With no vintage rows in that window, point-in-time resolution for those
evaluation dates found nothing newer than the last successful as-of (2026-05-01) and was
correctly rejected as `stale_asof_value`.

The commit's code change itself (`fred.py`'s guard against a response that omits
`realtime_start`) is correct and unaffected by this correction — it defends against a
hypothetical malformed response, not against the mechanism that actually caused the freeze.
