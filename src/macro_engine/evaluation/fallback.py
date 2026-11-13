"""A configured fallback for a monthly source that has stopped publishing (WO_MF3).

`ratio_splice_monthly_mean`: the source's level is carried forward from its last observation by the
growth of the fallback series' monthly mean over the same months, and the feature's own transform
and normalisation are applied to the extended series. Where the source has an observation it is
used; only the months after its last one are extended.

Measured on NNUSBIS against DTWEXBGS, 235 months 2007-01..2026-07 (revised data): the mean absolute
error of the spliced year-over-year change is 0.41 / 0.65 / 0.82 / 0.97 / 1.10 / 1.23 points for a
gap of 1..6 months (0.07 to 0.18 in z units), against 1.64 points (0.25 to 0.27 z) for the fallback's
own year-over-year change and 1.76 to 6.06 points for carrying the last reading forward.
"""

from __future__ import annotations

import pandas as pd

from macro_engine.features.config import FeatureDefinition
from macro_engine.features.feature_builder import (
    apply_feature_normalization,
    apply_feature_transform,
)

EXTENDED = "extended_by_fallback"
GAP_EXCEEDS_LIMIT = "fallback_gap_exceeds_limit"
UNAVAILABLE = "fallback_unavailable"


def last_complete_month(known_dates: pd.Series, evaluation_date: pd.Timestamp) -> pd.Period | None:
    """The newest month whose every observation is in hand on the evaluation date; never a partial one.

    `known_dates` are the observation dates already published. A month is complete when the
    evaluation date lies in a later month AND the newest known observation is on or after the
    month's last weekday. Otherwise the month before the newest observation's is the answer (an
    observation in a later month proves it ended). A holiday on a month's last weekday only delays
    that month until the next month's first observation is known; it never admits a partial month.
    """
    if known_dates.empty:
        return None
    newest = pd.Timestamp(known_dates.max())
    month = newest.to_period("M")
    last_weekday = pd.bdate_range(end=month.end_time.normalize(), periods=1)[0]
    if evaluation_date.to_period("M") > month and newest >= last_weekday:
        return month
    return month - 1


def ratio_splice_monthly_mean(
    *,
    primary: pd.DataFrame,
    fallback_known: pd.DataFrame,
    evaluation_date: pd.Timestamp,
    feature: FeatureDefinition,
    max_months: int,
    max_lag_days: int,
) -> tuple[dict | None, str]:
    """(spliced reading, reason). The reading is None when the fallback cannot extend the source.

    `primary` holds the source's observations (date, value) up to the last one usable on the
    evaluation date; `fallback_known` the fallback's observations already published on it.
    """
    primary = primary.dropna(subset=["value"]).sort_values("date")
    if primary.empty:
        return None, UNAVAILABLE
    last = pd.Timestamp(primary["date"].iloc[-1]).to_period("M")
    target = last_complete_month(fallback_known["date"], evaluation_date)
    if target is None or target <= last:
        return None, UNAVAILABLE
    target_date = target.to_timestamp()
    lag_days = int((evaluation_date.normalize() - target_date).days)
    if lag_days > max_lag_days:
        # the fallback itself is stale: its last complete month is too old to be this reading
        return None, UNAVAILABLE
    months = (target - last).n
    if months > max_months:
        return None, GAP_EXCEEDS_LIMIT

    observed = fallback_known.dropna(subset=["value"])
    means = observed.groupby(observed["date"].dt.to_period("M"))["value"].mean()
    span = pd.period_range(last, target, freq="M")
    if not span.isin(means.index).all() or not (means[span] > 0).all():
        return None, UNAVAILABLE
    last_level = float(primary["value"].iloc[-1])
    extension = pd.DataFrame(
        {
            "date": span[1:].to_timestamp(),
            "value": [last_level * means[month] / means[last] for month in span[1:]],
        }
    )
    extended = pd.concat([primary[["date", "value"]], extension], ignore_index=True)
    values = extended["value"].astype(float)
    transformed = apply_feature_transform(values, feature.transform, "monthly", dates=extended["date"])
    normalized, _ = apply_feature_normalization(
        transformed, extended["date"], feature.normalization, "monthly", feature.min_observations
    )
    if pd.isna(transformed.iloc[-1]) or pd.isna(normalized.iloc[-1]):
        return None, UNAVAILABLE
    return (
        {
            "source_observation_date": target_date,
            "transformed_value": float(transformed.iloc[-1]),
            "normalized_value": float(normalized.iloc[-1]),
            "lag_days": lag_days,
            "fallback_months": months,
        },
        EXTENDED,
    )
