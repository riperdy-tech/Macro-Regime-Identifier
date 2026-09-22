"""Duplicate raw-observation vintages must not corrupt stored data or features.

FRED stamps realtime_start with the fetch date, so a database persisted
across daily ingestion runs used to accumulate one duplicate row per
observation per run, silently corrupting positional transforms and
rolling-window normalization.
"""

from __future__ import annotations

import pandas as pd

from macro_engine.features.config import FeatureDefinition
from macro_engine.features.feature_builder import build_features_from_raw
from macro_engine.ingest.schemas import IngestionSource
from macro_engine.storage.duckdb_store import DuckDBStore


def _source(series_id: str = "TEST") -> IngestionSource:
    return IngestionSource.model_validate(
        {
            "series_id": series_id,
            "name": series_id,
            "provider": "FRED",
            "dimension": "test",
            "frequency": "monthly",
            "required": True,
            "enabled": True,
            "stale_after_days": 45,
            "unusable_after_days": 120,
        }
    )


def _feature(series_id: str = "TEST") -> FeatureDefinition:
    return FeatureDefinition.model_validate(
        {
            "feature_id": "test_level",
            "series_id": series_id,
            "transform": "level",
            "normalization": "none",
            "direction": "higher_is_test_positive",
            "enabled": True,
            "min_observations": 1,
        }
    )


def _raw(realtime_start: str, fetched_at: str, value_offset: float = 0.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=6, freq="MS")
    return pd.DataFrame(
        {
            "series_id": "TEST",
            "date": dates,
            "value": [float(index + 1) + value_offset for index in range(len(dates))],
            "realtime_start": [pd.Timestamp(realtime_start).date()] * len(dates),
            "realtime_end": [pd.Timestamp(realtime_start).date()] * len(dates),
            "source": ["FRED"] * len(dates),
            "fetched_at": [pd.Timestamp(fetched_at)] * len(dates),
            "frequency": ["monthly"] * len(dates),
            "units": ["Index"] * len(dates),
        }
    )


def test_upsert_replaces_same_observation_across_fetch_days(tmp_path):
    store = DuckDBStore(tmp_path / "test.duckdb")
    store.initialize()
    store.upsert_raw_observations(_raw("2026-07-01", "2026-07-01"))
    store.upsert_raw_observations(_raw("2026-07-02", "2026-07-02", value_offset=0.5))

    stored = store.read_raw_observations("TEST")
    assert len(stored) == 6, "re-ingesting the same observations must not duplicate rows"
    assert (pd.to_numeric(stored["value"]) - pd.Series(range(1, 7)) == 0.5).all()


def test_feature_builder_ignores_duplicate_vintages():
    single = build_features_from_raw(
        _raw("2026-07-02", "2026-07-02", value_offset=0.5), [_source()], [_feature()]
    ).features
    duplicated_raw = pd.concat(
        [_raw("2026-07-01", "2026-07-01"), _raw("2026-07-02", "2026-07-02", value_offset=0.5)],
        ignore_index=True,
    )
    deduped = build_features_from_raw(duplicated_raw, [_source()], [_feature()]).features

    assert len(deduped) == len(single) == 6
    # Latest vintage wins.
    assert deduped["raw_value"].tolist() == single["raw_value"].tolist()


def test_upsert_features_leaves_exactly_one_row_per_key_after_two_writes(tmp_path):
    """The `features` table must not accumulate duplicates across repeated runs -- this is
    the defect that let up to 11 rows pile up per (feature_id, date), with no column
    recording which was current."""
    store = DuckDBStore(tmp_path / "test.duckdb")
    store.initialize()
    first = build_features_from_raw(
        _raw("2026-07-01", "2026-07-01"), [_source()], [_feature()]
    ).features
    second = build_features_from_raw(
        _raw("2026-07-02", "2026-07-02", value_offset=0.5), [_source()], [_feature()]
    ).features

    store.upsert_features(first)
    store.upsert_features(second)

    stored = store.read_features("test_level")
    keys = stored[["feature_id", "date"]].drop_duplicates()
    assert len(stored) == len(keys) == 6, "re-writing the same keys must not duplicate rows"
    assert (pd.to_numeric(stored.sort_values("date")["raw_value"]) - pd.Series(range(1, 7)) == 0.5).all()
