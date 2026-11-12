"""B1: a late release must not silently remove a regime dimension.

inflation_pressure was invalid from 2025-11 to 2026-04 (PCE released 92 to 122 days late against the
75-day limit; the dimension needs both of its two features) and is predicted invalid on 2026-12-01
(an empty October 2025 CPI row makes October 2026's year-over-year change undefined).

Rule implemented: year-over-year (and the other lagged changes) are computed against the prior-year
MONTH by date, a missing base month is flagged `missing_base_month`; a late MONTHLY reading is carried
forward, flagged `carried_forward`, up to the source's own `unusable_after_days`.
"""

from __future__ import annotations

import pandas as pd
import pytest

from macro_engine.dimensions.scoring import build_dimensions_from_features
from macro_engine.evaluation.calendar import asof_values_to_feature_frame, build_asof_feature_values
from macro_engine.features.feature_builder import build_features_from_raw

from test_phase_c_features import _feature as _definition, _monthly_source
from test_phase_d_dimensions import _dimension
from test_phase_j_evaluation import _calendar_config, _feature, _source


def _monthly_features(feature_id: str = "monthly_feature", series_id: str = "MONTHLY") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "feature_id": feature_id,
                "series_id": series_id,
                "date": "2026-02-01",
                "raw_value": 3.0,
                "transformed_value": 3.0,
                "normalized_value": 3.0,
                "transform": "level",
                "normalization": "none",
                "window_start": "2026-02-01",
                "window_end": "2026-02-01",
                "valid": True,
                "reason": "ok",
            }
        ]
    )


def _asof(evaluation_dates: list[str], *, frequency: str = "monthly", features=None, series_id: str = "MONTHLY"):
    calendar = pd.DataFrame(
        {
            "evaluation_date": pd.to_datetime(evaluation_dates),
            "frequency": ["monthly"] * len(evaluation_dates),
            "valid": [True] * len(evaluation_dates),
            "reason": ["ok"] * len(evaluation_dates),
        }
    )
    frame = _monthly_features(series_id=series_id) if features is None else features
    result = build_asof_feature_values(
        features=frame,
        feature_definitions=[_feature("monthly_feature", series_id)],
        sources=[_source(series_id, frequency)],
        calendar=calendar,
        config=_calendar_config(),
    )
    return result.set_index("evaluation_date")


def test_a_monthly_reading_past_the_normal_limit_is_carried_forward_and_flagged():
    asof = _asof(["2026-05-01"])  # observation 2026-02-01: 89 days old, limit 75, unusable after 120
    row = asof.loc[pd.Timestamp("2026-05-01")]

    assert bool(row["valid"]) is True
    assert row["reason"] == "carried_forward"
    assert row["lag_days"] == 89
    assert row["normalized_value"] == 3.0


def test_a_monthly_reading_inside_the_normal_limit_is_not_flagged():
    row = _asof(["2026-04-01"]).loc[pd.Timestamp("2026-04-01")]  # 59 days
    assert bool(row["valid"]) is True and row["reason"] == "ok"


def test_a_monthly_reading_past_unusable_after_days_is_stale_as_before():
    row = _asof(["2026-06-15"]).loc[pd.Timestamp("2026-06-15")]  # 134 days
    assert bool(row["valid"]) is False
    assert row["reason"] == "stale_asof_value"


def test_only_monthly_readings_are_carried():
    weekly = _asof(["2026-05-01"], frequency="weekly").loc[pd.Timestamp("2026-05-01")]
    assert bool(weekly["valid"]) is False
    assert weekly["reason"] == "stale_asof_value"


def test_a_dimension_built_on_a_carried_feature_is_valid_and_says_so():
    features = pd.concat(
        [
            _monthly_features("feature_a", "A").assign(date="2026-05-01"),
            _monthly_features("feature_b", "B").assign(date="2026-05-01"),
        ],
        ignore_index=True,
    )
    features.loc[features["feature_id"] == "feature_b", "reason"] = "carried_forward"
    result = build_dimensions_from_features(features, [_dimension()])

    score = result.dimension_scores.iloc[0]
    assert bool(score["valid"]) is True
    assert score["reason"] == "carried_forward"
    reasons = dict(zip(result.contributions["feature_id"], result.contributions["reason"]))
    assert reasons == {"feature_a": "ok", "feature_b": "carried_forward"}


def test_the_carried_flag_survives_the_asof_to_feature_frame_step():
    asof = _asof(["2026-05-01"]).reset_index()
    frame = asof_values_to_feature_frame(asof)
    assert frame.iloc[0]["reason"] == "carried_forward"
    assert bool(frame.iloc[0]["valid"]) is True


# ── year-over-year by date; a missing base month is flagged ───────────────────


def _cpi_like(missing: str, how: str) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", "2025-12-01", freq="MS")
    raw = pd.DataFrame(
        {"series_id": "TEST", "date": dates, "value": [100.0 + index for index in range(len(dates))]}
    )
    if how == "absent":
        raw = raw[raw["date"] != pd.Timestamp(missing)]
    else:
        raw.loc[raw["date"] == pd.Timestamp(missing), "value"] = float("nan")
    return raw


def _yoy(raw: pd.DataFrame) -> pd.DataFrame:
    result = build_features_from_raw(
        raw,
        [_monthly_source()],
        [_definition(transform="yoy_pct_change", normalization="none", min_observations=1)],
    )
    return result.features.set_index("date")


@pytest.mark.parametrize("how", ["absent", "empty"])
def test_a_missing_base_month_is_flagged_and_the_other_months_use_their_own_base(how: str):
    frame = _yoy(_cpi_like("2024-10-01", how))

    flagged = frame.loc[pd.Timestamp("2025-10-01").date()]
    assert bool(flagged["valid"]) is False
    assert flagged["reason"] == "missing_base_month"
    # November 2025 is compared with November 2024, wherever the missing row sits in the file
    november = frame.loc[pd.Timestamp("2025-11-01").date()]
    assert november["transformed_value"] == pytest.approx((122.0 / 110.0 - 1.0) * 100.0)
    # December 2025 against December 2024
    december = frame.loc[pd.Timestamp("2025-12-01").date()]
    assert december["transformed_value"] == pytest.approx((123.0 / 111.0 - 1.0) * 100.0)


def test_the_first_year_of_history_is_still_insufficient_history_not_a_missing_base():
    frame = _yoy(_cpi_like("2024-10-01", "empty"))
    first = frame.loc[pd.Timestamp("2024-06-01").date()]
    assert first["reason"] == "insufficient_transform_history"
