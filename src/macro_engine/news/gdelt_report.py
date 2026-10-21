"""E0.2b measurement: rolling GDELT success rate over scheduled pulls (MRI_EVENTS_PLAN v2 section 9).

A scheduled pull is one `news_source_runs` row for a GDELT source (the run writes a row for every
selected source, including those it skipped as `circuit_open`). A pull succeeded when GDELT answered:
`ok` (articles) or `empty` (answered, none). `rate_limited`, `circuit_open` and `error` are failures.
The >= 90% criterion is only *evaluable* once the window holds `window_days` days of runs.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

TARGET_RATE = 0.90
DEFAULT_WINDOW_DAYS = 14
SUCCESS_STATUSES = ("ok", "empty")


def rolling_gdelt_success_rate(
    source_runs: pd.DataFrame,
    *,
    as_of: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
    target: float = TARGET_RATE,
    since: date | None = None,
) -> dict[str, Any]:
    start = pd.Timestamp(as_of) - pd.Timedelta(days=window_days - 1)
    if since is not None:
        start = max(start, pd.Timestamp(since))
    out: dict[str, Any] = {
        "as_of": str(as_of),
        "window_days": window_days,
        "window_start": str(start.date()),
        "since": None if since is None else str(since),
        "target": target,
        "per_day": [],
    }
    if source_runs.empty:
        frame = pd.DataFrame(columns=["day", "status"])
    else:
        runs = source_runs[source_runs["provider"].eq("gdelt")]
        day = pd.to_datetime(runs["run_at"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
        frame = pd.DataFrame({"day": day.values, "status": runs["status"].values}).dropna(subset=["day"])
    frame = frame[(frame["day"] >= start) & (frame["day"] <= pd.Timestamp(as_of))]
    frame = frame.assign(success=frame["status"].isin(SUCCESS_STATUSES))
    for d, g in frame.groupby("day"):
        out["per_day"].append(
            {"day": str(pd.Timestamp(d).date()), "scheduled": int(len(g)), "succeeded": int(g["success"].sum())}
        )
    scheduled, succeeded = int(len(frame)), int(frame["success"].sum())
    observed_days = len(out["per_day"])
    out.update(
        scheduled=scheduled,
        succeeded=succeeded,
        success_rate=None if scheduled == 0 else round(succeeded / scheduled, 4),
        status_counts={k: int(v) for k, v in frame["status"].value_counts().items()},
        observed_days=observed_days,
        criterion_evaluable=observed_days >= window_days,
        meets_target=None if scheduled == 0 or observed_days < window_days else succeeded / scheduled >= target,
    )
    return out
