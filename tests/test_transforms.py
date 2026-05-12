import pandas as pd
import pytest

from macro_engine.config.loader import load_all_configs
from macro_engine.normalize.transforms import apply_transform, periods_for_frequency


def test_periods_for_frequency_supports_annual():
    assert periods_for_frequency("annual", 12) == 1
    assert periods_for_frequency("monthly", 3) == 3
    assert periods_for_frequency("daily", 3) == 63


def test_yoy_pct_change_monthly():
    config = load_all_configs("config")
    source = next(item for item in config.sources if item.feature_id == "headline_cpi_yoy_z")
    values = pd.Series([100 + i for i in range(13)], dtype=float)

    result = apply_transform(values, source)

    assert result.iloc[12] == pytest.approx(12.0)


def test_diff_3m_inverse_source_transform():
    config = load_all_configs("config")
    source = next(item for item in config.sources if item.feature_id == "unemployment_3m_change_z")
    values = pd.Series([4.0, 4.1, 4.2, 4.5], dtype=float)

    result = apply_transform(values, source)

    assert round(result.iloc[3], 4) == 0.5
