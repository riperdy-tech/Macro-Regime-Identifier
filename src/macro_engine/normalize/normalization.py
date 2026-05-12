from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_z_score(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    rolling = series.rolling(window=window, min_periods=min_periods)
    mean = rolling.mean()
    std = rolling.std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def expanding_z_score(series: pd.Series, min_periods: int) -> pd.Series:
    expanding = series.expanding(min_periods=min_periods)
    mean = expanding.mean()
    std = expanding.std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def percentile_score(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def _percentile(values: pd.Series) -> float:
        current = values.iloc[-1]
        return float((values <= current).mean() * 2 - 1)

    return series.rolling(window=window, min_periods=min_periods).apply(_percentile, raw=False)


def clip_z_score(series: pd.Series, clip_value: float = 3.0) -> pd.Series:
    return series.clip(lower=-clip_value, upper=clip_value)


def bounded_tanh_score(z_score: pd.Series, divisor: float = 2.0) -> pd.Series:
    return pd.Series(np.tanh(z_score / divisor), index=z_score.index)
