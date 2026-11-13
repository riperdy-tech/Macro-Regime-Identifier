"""WO_MF3 part A: a monthly source that stops publishing is extended by a configured fallback series.

NNUSBIS (the BIS narrow dollar index, monthly) had no observation after 2026-07 and the dollar
dimension lost its only input. The mechanism is configured on the SOURCE, for any series:
`fallback_series`, `fallback_method: ratio_splice_monthly_mean`, `fallback_max_months`. Past the
normal age limit the source's level is carried from its last observation by the growth of the
fallback's monthly mean, month by month, up to the fallback's last COMPLETE month known on the
evaluation date; the feature's own transform and normalisation are then applied as usual. The
reading is valid, flagged `extended_by_fallback`, and carries the number of months extended.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd
import pytest

from macro_engine.dimensions.scoring import build_dimensions_from_features
from macro_engine.evaluation.calendar import asof_values_to_feature_frame, build_asof_feature_values
from macro_engine.features.config import FeatureDefinition, load_feature_config
from macro_engine.features.feature_builder import build_features_from_raw
from macro_engine.ingest.schemas import IngestionSource
from macro_engine.storage.duckdb_store import DuckDBStore

from test_phase_d_dimensions import _dimension
from test_phase_j_evaluation import _calendar_config

PRIMARY_LAST = "2026-07-01"

FEATURE = FeatureDefinition.model_validate(
    {
        "feature_id": "prim_yoy_z",
        "series_id": "PRIM",
        "transform": "yoy_log_change",
        "normalization": "rolling_z_5y",
        "direction": "higher_is_test_positive",
        "min_observations": 24,
    }
)


def _primary_source(**fallback) -> IngestionSource:
    return IngestionSource.model_validate(
        {
            "series_id": "PRIM",
            "name": "PRIM",
            "dimension": "test",
            "frequency": "monthly",
            "stale_after_days": 45,
            "unusable_after_days": 120,
            **fallback,
        }
    )


FALLBACK = {
    "fallback_series": "FALL",
    "fallback_method": "ratio_splice_monthly_mean",
    "fallback_max_months": 4,
}
FALLBACK_SOURCE = IngestionSource.model_validate(
    {
        "series_id": "FALL",
        "name": "FALL",
        "dimension": "test",
        "frequency": "daily",
        "stale_after_days": 5,
        "unusable_after_days": 15,
    }
)


def _primary_raw() -> pd.DataFrame:
    dates = pd.date_range("2019-01-01", PRIMARY_LAST, freq="MS")
    steps = np.arange(len(dates))
    return pd.DataFrame(
        {"series_id": "PRIM", "date": dates, "value": 100.0 + 5.0 * np.sin(steps / 7.0) + 0.1 * steps}
    )


def _fallback_raw(end: str) -> pd.DataFrame:
    dates = pd.bdate_range("2019-01-01", end)
    months = (dates.year - 2019) * 12 + dates.month
    values = 80.0 + 3.0 * np.cos(months / 5.0) + 0.05 * months + 0.02 * dates.day
    return pd.DataFrame({"series_id": "FALL", "date": dates, "value": values})


def _features(raw: pd.DataFrame) -> pd.DataFrame:
    return build_features_from_raw(raw, [_primary_source()], [FEATURE]).features


def _asof(evaluation_date: str, *, fallback_end: str, source: IngestionSource | None = None, **kwargs):
    primary = _primary_raw()
    calendar = pd.DataFrame(
        {
            "evaluation_date": pd.to_datetime([evaluation_date]),
            "frequency": ["monthly"],
            "valid": [True],
            "reason": ["ok"],
        }
    )
    result = build_asof_feature_values(
        features=_features(primary),
        feature_definitions=[FEATURE],
        sources=[source or _primary_source(**FALLBACK), FALLBACK_SOURCE],
        calendar=calendar,
        config=_calendar_config(),
        raw_observations=pd.concat([primary, _fallback_raw(fallback_end)], ignore_index=True),
        **kwargs,
    )
    return result.iloc[0]


def _expected(target_month: str, *, fallback_end: str) -> pd.Series:
    """The feature builder's own answer for a primary series extended BY HAND to `target_month`."""
    primary = _primary_raw()
    fallback = _fallback_raw(fallback_end)
    means = fallback.groupby(fallback["date"].dt.to_period("M"))["value"].mean()
    last = pd.Period(PRIMARY_LAST, "M")
    last_level = float(primary["value"].iloc[-1])
    months = pd.period_range(last + 1, pd.Period(target_month, "M"), freq="M")
    extension = pd.DataFrame(
        {
            "series_id": "PRIM",
            "date": months.to_timestamp(),
            "value": [last_level * means[month] / means[last] for month in months],
        }
    )
    frame = _features(pd.concat([primary, extension], ignore_index=True))
    return frame[frame["date"] == pd.Timestamp(target_month).date()].iloc[0]


def test_no_gap_is_identical_to_a_source_without_a_fallback():
    with_fallback = _asof("2026-09-01", fallback_end="2026-08-31")  # 62 days: inside the 75-day limit
    without = _asof("2026-09-01", fallback_end="2026-08-31", source=_primary_source())

    assert with_fallback["reason"] == "ok"
    assert with_fallback["source_observation_date"] == pd.Timestamp(PRIMARY_LAST)
    assert pd.isna(with_fallback["fallback_months"])
    pd.testing.assert_series_equal(with_fallback, without)


def test_a_gap_inside_the_limit_is_spliced_flagged_and_counts_its_months():
    row = _asof("2026-11-01", fallback_end="2026-10-30")  # Friday 30 October: October is complete
    expected = _expected("2026-10-01", fallback_end="2026-10-30")

    assert bool(row["valid"]) is True
    assert row["reason"] == "extended_by_fallback"
    assert row["fallback_months"] == 3
    assert row["source_observation_date"] == pd.Timestamp("2026-10-01")
    assert row["lag_days"] == 31
    assert row["transformed_value"] == pytest.approx(expected["transformed_value"], rel=1e-12)
    assert row["normalized_value"] == pytest.approx(expected["normalized_value"], rel=1e-12)


def test_a_partial_month_is_never_used():
    # the fallback is known to Friday 23 October only: October is not complete, September is
    row = _asof("2026-11-01", fallback_end="2026-10-23")
    september = _expected("2026-09-01", fallback_end="2026-10-23")
    october_partial = _expected("2026-10-01", fallback_end="2026-10-23")

    assert row["reason"] == "extended_by_fallback"
    assert row["fallback_months"] == 2
    assert row["source_observation_date"] == pd.Timestamp("2026-09-01")
    assert row["normalized_value"] == pytest.approx(september["normalized_value"], rel=1e-12)
    assert row["normalized_value"] != pytest.approx(october_partial["normalized_value"], rel=1e-6)


def test_the_month_of_the_evaluation_date_is_never_complete():
    # evaluated on the last weekday of October with that day already in hand: still September
    row = _asof("2026-10-30", fallback_end="2026-10-30")
    assert row["source_observation_date"] == pd.Timestamp("2026-09-01")
    assert row["fallback_months"] == 2


def test_a_gap_beyond_the_limit_is_invalid_with_its_own_reason():
    source = _primary_source(**{**FALLBACK, "fallback_max_months": 2})
    row = _asof("2026-11-01", fallback_end="2026-10-30", source=source)  # three months to extend

    assert bool(row["valid"]) is False
    assert row["reason"] == "fallback_gap_exceeds_limit"
    assert row["source_observation_date"] == pd.Timestamp(PRIMARY_LAST)
    assert row["lag_days"] == 123
    assert pd.isna(row["fallback_months"])


def test_a_stale_fallback_extends_nothing():
    # the fallback stops with the primary: no complete month after the primary's last observation
    late = _asof("2026-10-01", fallback_end="2026-07-31")  # 92 days: B1 still carries it forward
    assert bool(late["valid"]) is True and late["reason"] == "carried_forward"

    stale = _asof("2026-11-01", fallback_end="2026-07-31")  # 123 days: past unusable_after_days
    assert bool(stale["valid"]) is False
    assert stale["reason"] == "fallback_unavailable"
    assert stale["source_observation_date"] == pd.Timestamp(PRIMARY_LAST)

    # one complete month more, but itself older than the monthly age limit on the evaluation date
    old = _asof("2026-12-01", fallback_end="2026-08-31")
    assert bool(old["valid"]) is False and old["reason"] == "fallback_unavailable"


def test_point_in_time_uses_only_fallback_observations_published_by_the_evaluation_date():
    primary = _primary_raw()
    fallback = _fallback_raw("2026-10-30")
    # the primary's months are published two months later; the fallback's days the following Monday,
    # so on Sunday 1 November the week of 26-30 October is not yet public
    publication_index = pd.concat(
        [
            pd.DataFrame(
                {
                    "series_id": "PRIM",
                    "date": primary["date"],
                    "first_known_date": primary["date"] + pd.DateOffset(months=2),
                }
            ),
            pd.DataFrame(
                {
                    "series_id": "FALL",
                    "date": fallback["date"],
                    "first_known_date": fallback["date"] + pd.offsets.Week(weekday=0),
                }
            ),
        ],
        ignore_index=True,
    )
    row = _asof(
        "2026-11-01",
        fallback_end="2026-10-30",
        scoring_mode="point_in_time",
        publication_index=publication_index,
    )
    assert row["reason"] == "extended_by_fallback"
    assert row["source_observation_date"] == pd.Timestamp("2026-09-01")
    assert row["fallback_months"] == 2

    row = _asof(
        "2026-11-03",
        fallback_end="2026-10-30",
        scoring_mode="point_in_time",
        publication_index=publication_index,
    )
    assert row["source_observation_date"] == pd.Timestamp("2026-10-01")
    assert row["fallback_months"] == 3


def test_point_in_time_without_fallback_vintages_extends_nothing():
    primary = _primary_raw()
    publication_index = pd.DataFrame(
        {
            "series_id": "PRIM",
            "date": primary["date"],
            "first_known_date": primary["date"] + pd.DateOffset(months=2),
        }
    )
    row = _asof(
        "2026-11-01",
        fallback_end="2026-10-30",
        scoring_mode="point_in_time",
        publication_index=publication_index,
    )
    assert bool(row["valid"]) is False and row["reason"] == "fallback_unavailable"


def test_a_dimension_built_on_an_extended_feature_is_valid_and_says_so():
    row = _asof("2026-11-01", fallback_end="2026-10-30")
    frame = asof_values_to_feature_frame(pd.DataFrame([row]))
    other = frame.assign(feature_id="feature_a", reason="ok")
    frame = pd.concat([other, frame.assign(feature_id="feature_b")], ignore_index=True)
    result = build_dimensions_from_features(frame, [_dimension()])

    score = result.dimension_scores.iloc[0]
    assert bool(score["valid"]) is True
    assert score["reason"] == "extended_by_fallback"
    reasons = dict(zip(result.contributions["feature_id"], result.contributions["reason"]))
    assert reasons == {"feature_a": "ok", "feature_b": "extended_by_fallback"}


def test_the_months_extended_are_stored_also_in_a_store_created_before_the_column(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    with duckdb.connect(str(db_path)) as con:  # the table as stores created before this change have it
        con.execute(
            "CREATE TABLE asof_feature_values (evaluation_date DATE, feature_id TEXT, "
            "source_observation_date DATE, transformed_value DOUBLE, normalized_value DOUBLE, "
            "lag_days INTEGER, valid BOOLEAN, reason TEXT, PRIMARY KEY(evaluation_date, feature_id))"
        )
    store = DuckDBStore(db_path)
    store.initialize()
    rows = pd.DataFrame(
        [
            _asof("2026-11-01", fallback_end="2026-10-30"),
            _asof("2026-09-01", fallback_end="2026-08-31"),
        ]
    )
    calendar = pd.DataFrame(
        {"evaluation_date": rows["evaluation_date"], "frequency": "monthly", "valid": True, "reason": "ok"}
    )
    store.replace_evaluation_outputs(calendar, rows)

    stored = store.read_asof_feature_values().sort_values("evaluation_date")
    assert stored["reason"].tolist() == ["ok", "extended_by_fallback"]
    assert pd.isna(stored["fallback_months"].iloc[0])
    assert stored["fallback_months"].iloc[1] == 3


def test_a_fallback_is_configured_whole_or_not_at_all():
    with pytest.raises(ValueError, match="fallback"):
        _primary_source(fallback_series="FALL")
    with pytest.raises(ValueError, match="fallback"):
        _primary_source(fallback_method="ratio_splice_monthly_mean", fallback_max_months=3)


def test_a_fallback_must_name_a_configured_source(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n"
        "  - series_id: PRIM\n    name: PRIM\n    dimension: test\n    frequency: monthly\n"
        "    stale_after_days: 45\n    unusable_after_days: 120\n"
        "    fallback_series: NOWHERE\n    fallback_method: ratio_splice_monthly_mean\n"
        "    fallback_max_months: 3\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="NOWHERE"):
        load_feature_config(config)


def test_the_narrow_dollar_index_falls_back_on_the_broad_daily_index():
    sources = {source.series_id: source for source in load_feature_config("config/phase_b_sources.yaml").sources}
    narrow = sources["NNUSBIS"]
    assert narrow.fallback_series == "DTWEXBGS"
    assert narrow.fallback_method == "ratio_splice_monthly_mean"
    assert narrow.fallback_max_months == 6
    assert sources["DTWEXBGS"].enabled
    # no other source changes behaviour
    assert [s.series_id for s in sources.values() if s.fallback_series] == ["NNUSBIS"]
