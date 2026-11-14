"""WO_MX2 build 2 (RR_MX1 I2): the daily record's warnings say what is wrong today.

The macro step used to carry the same 25 invalid dimension months from 2003 to 2014 every day (a `.tail(25)` of the
whole stored history), so the recent invalid months fell off the list; monthly series were "stale" for the whole of
the gap between their period and the next release; and a feature switched off in config was a warning every day.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from macro_engine.daily import run_daily_diagnostic
from macro_engine.ingest.health import build_source_health
from macro_engine.ingest.registry import load_ingestion_sources
from macro_engine.pipeline_runner import (
    _collect_invalid_dimension_warnings,
    _collect_invalid_feature_warnings,
    _collect_invalid_regime_warnings,
    run_pipeline,
)
from macro_engine.storage.duckdb_store import DuckDBStore

from test_phase_h_pipeline import _mock_ingest, _mock_vintages, _redirected_config
from test_phase_v05_m1_m2_operations import _daily_config, _daily_services

AS_OF = pd.Timestamp("2026-11-13")


def _scores(rows: list[tuple[str, str, bool, str]], id_column: str = "dimension_id") -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d), id_column: i, "valid": v, "reason": r} for d, i, v, r in rows]
    )


def _history_with_recent_gaps() -> pd.DataFrame:
    old = [
        (f"{y}-{m:02d}-01", "real_rates", False, "below_min_valid_features")
        for y, m in [(2003, 9), (2003, 10), (2004, 1), (2005, 8)]
    ]
    old.append(("2014-02-01", "dollar", False, "below_min_valid_features"))
    recent = [
        ("2025-12-01", "housing_activity", False, "below_min_valid_features"),
        ("2026-01-01", "housing_activity", False, "below_min_valid_features"),
        ("2026-02-01", "housing_activity", False, "below_min_valid_features"),
        ("2025-12-01", "inflation_pressure", False, "below_min_valid_features"),
        ("2026-01-01", "inflation_pressure", False, "below_min_valid_features"),
    ]
    valid = [("2026-03-01", "housing_activity", True, "ok"), ("2020-05-01", "growth", True, "ok")]
    return _scores(old + recent + valid)


def test_dimension_warnings_report_the_last_twelve_months_newest_first_with_the_month():
    warnings: list[str] = []
    older = _collect_invalid_dimension_warnings(_history_with_recent_gaps(), warnings, as_of=AS_OF)

    assert warnings == [
        "invalid_dimension:housing_activity:2026-02:below_min_valid_features",
        "invalid_dimension:housing_activity:2026-01:below_min_valid_features",
        "invalid_dimension:inflation_pressure:2026-01:below_min_valid_features",
        "invalid_dimension:housing_activity:2025-12:below_min_valid_features",
        "invalid_dimension:inflation_pressure:2025-12:below_min_valid_features",
    ]
    assert older == 5  # the 2003-2014 months are a fact about the start of the history, not warnings


def test_no_recent_invalid_month_is_dropped_however_many_there_are():
    rows = [
        (f"{y}-{m:02d}-01", dim, False, "below_min_valid_features")
        for (y, m) in [(2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7),
                       (2026, 8), (2026, 9), (2026, 10)]
        for dim in ("a", "b", "c")
    ]
    warnings: list[str] = []
    older = _collect_invalid_dimension_warnings(_scores(rows), warnings, as_of=AS_OF)
    assert len(warnings) == 33 and older == 0  # more than the old cap of 25
    assert warnings[0].startswith("invalid_dimension:a:2026-10")


def test_the_window_edge_is_twelve_months_before_the_evaluation_date():
    rows = [
        ("2025-11-01", "a", False, "r"),  # older than 12 months before 2026-11-13
        ("2025-12-01", "a", False, "r"),
    ]
    warnings: list[str] = []
    older = _collect_invalid_dimension_warnings(_scores(rows), warnings, as_of=AS_OF)
    assert warnings == ["invalid_dimension:a:2025-12:r"]
    assert older == 1


def test_valid_rows_and_an_empty_frame_give_nothing():
    warnings: list[str] = []
    assert _collect_invalid_dimension_warnings(pd.DataFrame(), warnings, as_of=AS_OF) == 0
    assert _collect_invalid_dimension_warnings(_scores([("2026-10-01", "a", True, "ok")]), warnings, as_of=AS_OF) == 0
    assert warnings == []


def test_regime_warnings_have_the_same_window_and_month():
    frame = _scores(
        [
            ("2004-03-01", "goldilocks", False, "below_min_valid_dimensions"),
            ("2026-06-01", "goldilocks", False, "below_min_valid_dimensions"),
        ],
        id_column="regime_id",
    )
    warnings: list[str] = []
    older = _collect_invalid_regime_warnings(frame, warnings, as_of=AS_OF)
    assert warnings == ["invalid_regime:goldilocks:2026-06:below_min_valid_dimensions"]
    assert older == 1


def test_a_feature_disabled_in_config_is_listed_not_warned():
    health = pd.DataFrame(
        [
            {"feature_id": "leading_index_level_z", "usable": False, "reason": "disabled_feature"},
            {"feature_id": "dollar_yoy_z", "usable": False, "reason": "missing_source_data"},
            {"feature_id": "fine", "usable": True, "reason": "ok"},
        ]
    )
    warnings: list[str] = []
    disabled = _collect_invalid_feature_warnings(health, warnings)
    assert disabled == ["leading_index_level_z"]
    assert warnings == ["invalid_feature:dollar_yoy_z:missing_source_data"]


def test_the_pipeline_summary_carries_the_facts_and_counts_only_warnings(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    summary = run_pipeline(
        config_path=_redirected_config(tmp_path),
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
        vintage_runner=_mock_vintages,
    )
    record = summary.to_dict()
    assert record["disabled_features"], "the shipped config switches at least one feature off"
    assert not any(w.startswith("invalid_feature:") and ":disabled_feature" in w for w in record["warnings"])
    assert record["warning_count"] == len(record["warnings"])

    store = DuckDBStore(db_path)
    stored_invalid = int((~store.read_table("dimension_scores")["valid"]).sum()) + int(
        (~store.read_table("regime_scores")["valid"]).sum()
    )
    listed = [w for w in record["warnings"] if w.startswith(("invalid_dimension:", "invalid_regime:"))]
    # every invalid stored row is either listed (recent) or counted once as history, none lost and none doubled
    assert len(listed) + record["history_invalid_months"] == stored_invalid


def test_the_daily_record_keeps_the_facts_out_of_warning_count(tmp_path: Path):
    services = _daily_services(tmp_path)
    services["run_pipeline"] = lambda **_: SimpleNamespace(
        status="success",
        warnings=(),
        disabled_features=("leading_index_level_z",),
        history_invalid_months=25,
    )
    result = run_daily_diagnostic(
        config_path=_daily_config(tmp_path),
        db_path=tmp_path / "macro.duckdb",
        run_date="2026-11-13",
        services=services,
        output_dir=tmp_path / "outputs",
    )
    summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    assert summary["warning_count"] == 0 and summary["warnings"] == []
    assert summary["disabled_features"] == ["leading_index_level_z"]
    assert summary["history_invalid_months"] == 25


def _raw(series_id: str, last_observation: str) -> pd.DataFrame:
    return pd.DataFrame({"series_id": [series_id], "date": [pd.Timestamp(last_observation)], "value": [1.0]})


def _stale(series_id: str, age_days: int) -> bool:
    sources = [s for s in load_ingestion_sources("config/phase_b_sources.yaml") if s.series_id == series_id]
    last = AS_OF - pd.Timedelta(days=age_days)
    health = build_source_health(sources, _raw(series_id, str(last.date())), as_of=str(AS_OF.date()))
    return bool(health.iloc[0]["stale_flag"])


def test_monthly_series_are_not_stale_while_the_next_release_is_not_due():
    # RR_MX1 measured the newest observation of each at 73 days on 2026-11-13, awaiting the mid-November release
    for series_id in ("INDPRO", "PCEPI", "M2SL", "HOUST"):
        assert not _stale(series_id, 73), series_id


def test_a_monthly_series_that_missed_a_whole_release_is_still_stale():
    # lag + one missed month (30) + 15 days of margin
    limits = {"INDPRO": 45, "PCEPI": 58, "M2SL": 55, "HOUST": 47}
    for series_id, lag in limits.items():
        assert _stale(series_id, lag + 46), series_id
        assert not _stale(series_id, lag + 44), series_id


def test_the_weekly_dollar_index_is_not_stale_at_its_weekly_gap_but_is_after_a_missed_week():
    assert not _stale("DTWEXBGS", 8)
    assert _stale("DTWEXBGS", 11)


def test_daily_series_keep_their_limits():
    assert _stale("DGS10", 6)
    assert not _stale("DGS10", 4)
