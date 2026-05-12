from __future__ import annotations

import pandas as pd

from macro_engine.config.schemas import SourceConfig
from macro_engine.normalize.normalization import (
    bounded_tanh_score,
    clip_z_score,
    expanding_z_score,
    percentile_score,
    rolling_z_score,
)


def periods_for_frequency(frequency: str, months: int) -> int:
    if frequency == "daily":
        return max(1, months * 21)
    if frequency == "weekly":
        return max(1, round(months * 4.33))
    if frequency == "monthly":
        return months
    if frequency == "quarterly":
        return max(1, round(months / 3))
    if frequency == "annual":
        return max(1, round(months / 12))
    raise ValueError(f"unsupported frequency {frequency}")


def observations_for_years(frequency: str, years: int) -> int:
    if frequency == "daily":
        return years * 252
    if frequency == "weekly":
        return years * 52
    if frequency == "monthly":
        return years * 12
    if frequency == "quarterly":
        return years * 4
    if frequency == "annual":
        return years
    raise ValueError(f"unsupported frequency {frequency}")


def apply_transform(values: pd.Series, source: SourceConfig) -> pd.Series:
    transform = source.transform
    if transform == "level":
        return values
    if transform == "diff_1m":
        return values.diff(periods_for_frequency(source.frequency, 1))
    if transform == "diff_3m":
        return values.diff(periods_for_frequency(source.frequency, 3))
    if transform == "diff_6m":
        return values.diff(periods_for_frequency(source.frequency, 6))
    if transform == "change_12m":
        return values.diff(periods_for_frequency(source.frequency, 12))
    if transform == "yoy_pct_change":
        return values.pct_change(periods_for_frequency(source.frequency, 12)) * 100
    if transform == "mom_pct_change":
        return values.pct_change(periods_for_frequency(source.frequency, 1)) * 100
    if transform == "qoq_annualized_pct_change":
        quarterly_change = values.pct_change(periods_for_frequency(source.frequency, 3))
        return ((1 + quarterly_change) ** 4 - 1) * 100
    if transform == "pct_change_1m":
        return values.pct_change(periods_for_frequency(source.frequency, 1)) * 100
    if transform == "pct_change_3m":
        return values.pct_change(periods_for_frequency(source.frequency, 3)) * 100
    if transform in {"spread", "real_rate"}:
        return values
    raise ValueError(f"unsupported transform {transform}")


def apply_normalization(
    transformed: pd.Series,
    source: SourceConfig,
    minimum_observations: int,
    zscore_clip: float,
    divisor: float,
) -> tuple[pd.Series, pd.Series]:
    normalization = source.normalization
    if normalization == "none":
        z_score = transformed
    elif normalization == "expanding_z":
        z_score = expanding_z_score(transformed, minimum_observations)
    elif normalization == "percentile_10y":
        z_score = percentile_score(
            transformed,
            observations_for_years(source.frequency, 10),
            minimum_observations,
        )
    else:
        years = int(normalization.removeprefix("rolling_z_").removesuffix("y"))
        z_score = rolling_z_score(
            transformed,
            observations_for_years(source.frequency, years),
            minimum_observations,
        )
    clipped = clip_z_score(z_score, zscore_clip)
    bounded = bounded_tanh_score(clipped, divisor)
    return z_score, bounded
