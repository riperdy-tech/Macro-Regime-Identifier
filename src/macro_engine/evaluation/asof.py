"""As-of resolution for evaluation dates.

Two rules live here, and they answer DIFFERENT questions:

`latest_observation_on_or_before_date` (the existing calendar rule)
    "which observation period does the evaluation date refer to?" It picks the newest
    observation whose period is on or before the evaluation date, then drops anything
    that could not yet have been released using a fixed per-series
    ``publication_lag_days`` approximation. The approximation is the weak link: real
    release lags vary by series and by episode.

`point_in_time_observation` (ALFRED vintages)
    "what was actually PUBLISHED on the evaluation date?" It answers the question with
    evidence instead of an approximation, using the empirical first-publication date
    recovered from stored vintages.

Both are exposed so callers select one explicitly. Nothing here silently upgrades the
calendar rule to the vintage rule: switching the factual basis of a historical
diagnostic must be a configured, visible decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import pandas as pd

AsOfReason = Literal[
    "ok",
    "stale_asof_value",
    "not_yet_published",
    "no_prior_valid_feature",
    "pit_vintage_missing",
]

PUBLICATION_INDEX_COLUMNS = ["series_id", "date", "first_known_date"]


def normalize_asof(value: pd.Timestamp | str | None) -> pd.Timestamp:
    """Coerce any as-of value to a tz-naive, day-normalised Timestamp.

    Every stored date in this engine is a naive calendar DATE. An as-of value that
    arrives tz-aware (easy to produce — `datetime.now(UTC)`) cannot be compared with
    those columns at all, and pandas raises rather than coercing. Normalising once, at
    the boundary, keeps the whole as-of surface on one representation; a tz-aware
    value reaching a comparison is then impossible rather than merely unlikely.
    """
    if value is None:
        return pd.Timestamp(datetime.now(UTC)).tz_localize(None).normalize()
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp.normalize()


def build_publication_index(vintages: pd.DataFrame) -> pd.DataFrame:
    """First date on which each (series_id, observation date) became publicly known.

    A vintage fetch queried at as-of D stamps realtime_start = D, so the minimum
    realtime_start observed for an (series, date) pair IS the first as-of date at
    which that observation was visible — the empirical publication date, measured
    rather than assumed. Resolution is therefore the granularity of the backfilled
    as-of dates, which is exactly the granularity the evaluation calendar asks about.
    """
    if vintages.empty:
        return pd.DataFrame(columns=PUBLICATION_INDEX_COLUMNS)
    frame = vintages.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["realtime_start"] = pd.to_datetime(frame["realtime_start"], errors="coerce")
    frame = frame.dropna(subset=["series_id", "date", "realtime_start"])
    if frame.empty:
        return pd.DataFrame(columns=PUBLICATION_INDEX_COLUMNS)
    index = (
        frame.groupby(["series_id", "date"], as_index=False)["realtime_start"]
        .min()
        .rename(columns={"realtime_start": "first_known_date"})
    )
    return index.sort_values(["series_id", "date"]).reset_index(drop=True)


def latest_observation_on_or_before_date(
    observations: pd.DataFrame,
    as_of: pd.Timestamp | str,
    *,
    publication_lag_days: int = 0,
) -> pd.Series | None:
    """The calendar rule: newest observation period on/before `as_of`, minus the lag.

    Returns None when nothing qualifies. None is a meaningful answer — "there is no
    published value for this date" — and callers must report it rather than
    substituting a default.
    """
    if observations.empty:
        return None
    cutoff = normalize_asof(as_of) - pd.Timedelta(days=int(publication_lag_days))
    frame = observations.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if "value" in frame.columns:
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame[frame["value"].notna()]
    frame = frame[frame["date"].notna() & (frame["date"] <= cutoff)]
    if frame.empty:
        return None
    return frame.sort_values("date").iloc[-1]


def _single_series(frame: pd.DataFrame, series_id: str | None, caller: str) -> pd.DataFrame:
    """Narrow a vintage frame to the series being asked about, or refuse to answer.

    WHY THIS EXISTS. The resolver was originally handed the WHOLE `raw_observation_vintages`
    table by the anchor builders and never filtered it, while every calendar-mode caller filtered
    its own series first. With ~20 series sharing one monthly as-of calendar, "the newest
    observation visible in the newest vintage" then returned whichever series happened to sort
    last -- which is how point-in-time mode published **294.43 as the 10-year nominal Treasury
    yield** (and, derived from it, an ERP of -294.33). The number was wrong by two orders of
    magnitude and nothing flagged it, because the value was perfectly well-formed.

    A caller that does not say which series it means cannot be answered correctly, so this raises
    rather than guesses. Silence here is indistinguishable from a real measurement downstream.
    """
    if series_id is not None:
        return frame[frame["series_id"].astype(str) == str(series_id)]
    if "series_id" in frame.columns and frame["series_id"].astype(str).nunique(dropna=True) > 1:
        raise ValueError(
            f"{caller} was handed {frame['series_id'].nunique()} series and no series_id; the "
            "answer would be whichever series sorts last, published as if it were the one asked "
            "for. Pass series_id=<the series you mean>."
        )
    return frame


def point_in_time_series(
    vintages: pd.DataFrame,
    as_of: pd.Timestamp | str,
    *,
    series_id: str | None = None,
) -> pd.DataFrame:
    """The series AS KNOWN on `as_of`: one row per observation period, from the newest
    vintage that had been published by then.

    Resolution is "the latest vintage with realtime_start <= as_of", NOT "rows whose window
    brackets as_of". The bracket test was wrong in practice: ALFRED clips realtime_end to the
    query's realtime_end, so a vintage fetched for a single day comes back with
    `realtime_start == realtime_end == that day` and the bracket only ever matched that exact
    date. Every other evaluation date silently resolved to nothing.

    This rule also gives the right answer for UNClipped vintages, where a row's realtime_end is
    when it was superseded and many vintages of the same period coexist: per observation period
    the newest vintage at or before `as_of` wins, which is the value a reader at that date saw.

    No look-ahead by construction: rows first published after `as_of` are excluded.

    `series_id` selects the series. Omit it only when `vintages` already holds exactly one
    series; a multi-series frame without it raises (see `_single_series`).
    """
    if vintages.empty:
        return vintages.copy()
    moment = normalize_asof(as_of)
    frame = _single_series(vintages.copy(), series_id, "point_in_time_series")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["realtime_start"] = pd.to_datetime(frame["realtime_start"], errors="coerce")
    frame = frame.dropna(subset=["date", "realtime_start"])
    known = frame[frame["realtime_start"] <= moment]
    if known.empty:
        return known
    known = known.sort_values(["date", "realtime_start"])
    return known.groupby("date", as_index=False).tail(1).sort_values("date").reset_index(drop=True)


def point_in_time_observation(
    vintages: pd.DataFrame,
    as_of: pd.Timestamp | str,
    *,
    series_id: str | None = None,
) -> pd.Series | None:
    """Newest observation period visible in the vintage current on `as_of`.

    There is no publication-lag subtraction here and there must not be: the vintage
    already encodes what had been released. Subtraction would double-count the lag.
    """
    visible = point_in_time_series(vintages, as_of, series_id=series_id)
    if visible.empty:
        return None
    return visible.iloc[-1]


def resolve_asof_observation(
    *,
    mode: Literal["calendar_asof", "point_in_time", "same_date"],
    observations: pd.DataFrame,
    as_of: pd.Timestamp | str,
    vintages: pd.DataFrame | None = None,
    publication_lag_days: int = 0,
    series_id: str | None = None,
) -> tuple[pd.Series | None, AsOfReason]:
    """Dispatch to the configured as-of rule, returning (row, reason).

    `point_in_time` with no stored vintages returns (None, "pit_vintage_missing").
    That is deliberate: an opted-in point-in-time read that cannot be evidenced must
    fail loudly, never silently fall back to the approximation it was chosen over.
    """
    if mode == "point_in_time":
        if vintages is None or vintages.empty:
            return None, "pit_vintage_missing"
        row = point_in_time_observation(vintages, as_of, series_id=series_id)
        if row is None:
            return None, "not_yet_published"
        return row, "ok"
    if mode == "same_date":
        cutoff = normalize_asof(as_of)
        frame = observations.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        exact = frame[frame["date"] == cutoff]
        if exact.empty:
            return None, "no_prior_valid_feature"
        return exact.sort_values("date").iloc[-1], "ok"
    row = latest_observation_on_or_before_date(
        observations, as_of, publication_lag_days=publication_lag_days
    )
    if row is None:
        return None, "not_yet_published"
    return row, "ok"
