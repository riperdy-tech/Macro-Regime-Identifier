from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from macro_engine.ingest.schemas import IngestionSource


def build_source_health(
    sources: list[IngestionSource],
    raw_observations: pd.DataFrame,
    as_of: str | None = None,
) -> pd.DataFrame:
    checked_at = pd.Timestamp(datetime.now(timezone.utc))
    as_of_date = (
        pd.Timestamp(as_of).tz_localize(None).normalize()
        if as_of
        else checked_at.tz_localize(None).normalize()
    )
    rows: list[dict] = []
    raw = raw_observations.copy()
    if raw.empty:
        raw = pd.DataFrame(columns=["series_id", "date", "value"])
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")

    for source in sources:
        if not source.enabled:
            rows.append(
                {
                    "series_id": source.series_id,
                    "last_observation_date": pd.NaT,
                    "days_since_last_observation": None,
                    "expected_frequency": source.frequency,
                    "stale_flag": False,
                    "missing_count": 0,
                    "usable": False,
                    "reason": source.reason_disabled or "disabled",
                    "checked_at": checked_at,
                }
            )
            continue

        series = raw[(raw["series_id"] == source.series_id) & raw["value"].notna()]
        missing_count = int(raw[raw["series_id"] == source.series_id]["value"].isna().sum())
        if series.empty:
            rows.append(
                {
                    "series_id": source.series_id,
                    "last_observation_date": pd.NaT,
                    "days_since_last_observation": None,
                    "expected_frequency": source.frequency,
                    "stale_flag": True,
                    "missing_count": missing_count,
                    "usable": False,
                    "reason": "no_observations",
                    "checked_at": checked_at,
                }
            )
            continue

        last_date = series["date"].max().normalize()
        days_since = int((as_of_date - last_date).days)
        stale = days_since > source.stale_after_days
        unusable = days_since > source.unusable_after_days
        reason = "fresh"
        if unusable:
            reason = "unusable_stale"
        elif stale:
            reason = "stale"
        rows.append(
            {
                "series_id": source.series_id,
                "last_observation_date": last_date.date(),
                "days_since_last_observation": days_since,
                "expected_frequency": source.frequency,
                "stale_flag": stale,
                "missing_count": missing_count,
                "usable": not unusable,
                "reason": reason,
                "checked_at": checked_at,
            }
        )

    return pd.DataFrame(rows)
