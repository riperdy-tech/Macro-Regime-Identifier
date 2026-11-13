"""The dashboard's risk-dial and data-health exports (WO_MS1).

`risk_state.json` is an optional artifact: present, absent or degraded it is copied as written and never
touches `data_status`. `data_health.json` is built by the exporter from what the run already has; a fact
that is not there is null with a reason, never a guessed status.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import duckdb

from macro_engine.dashboard_export import (
    DASHBOARD_OUTPUT_FILES,
    DATA_HEALTH_FILE,
    OPTIONAL_OUTPUT_FILES,
    build_data_health,
    export_dashboard_data,
)
from macro_engine.risk_state import STALE_DAYS

TODAY = date(2026, 11, 13)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _strict(path: Path) -> dict:
    def reject(token: str):
        raise AssertionError(f"non-strict JSON token {token}")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject)


def _required(outputs: Path) -> None:
    for filename in DASHBOARD_OUTPUT_FILES:
        _write(outputs / filename, {"ok": True})


def _store(path: Path, contributions: list[tuple], asof: list[tuple], health: list[tuple]) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE dimension_feature_contributions "
        "(dimension_id VARCHAR, feature_id VARCHAR, date DATE, valid BOOLEAN, reason VARCHAR)"
    )
    con.execute(
        "CREATE TABLE asof_feature_values "
        "(evaluation_date DATE, feature_id VARCHAR, source_observation_date DATE, lag_days INTEGER)"
    )
    con.execute("CREATE TABLE feature_health (feature_id VARCHAR, series_id VARCHAR)")
    for row in contributions:
        con.execute("INSERT INTO dimension_feature_contributions VALUES (?, ?, ?, ?, ?)", row)
    for row in asof:
        con.execute("INSERT INTO asof_feature_values VALUES (?, ?, ?, ?)", row)
    for row in health:
        con.execute("INSERT INTO feature_health VALUES (?, ?)", row)
    con.close()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- risk_state.json


def test_risk_state_is_optional_not_required():
    assert "risk_state.json" in OPTIONAL_OUTPUT_FILES
    assert "risk_state.json" not in DASHBOARD_OUTPUT_FILES


def test_risk_state_absent_keeps_data_status_complete(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _required(outputs)
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "complete"
    assert "risk_state.json" in manifest["optional_missing_files"]
    assert not (data_dir / "risk_state.json").exists()


def test_risk_state_present_is_copied(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _required(outputs)
    _write(outputs / "risk_state.json", {"status": "shadow", "dial": {"state": "full"}, "degraded": False})
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert "risk_state.json" in manifest["optional_available_files"]
    assert _strict(data_dir / "risk_state.json")["dial"]["state"] == "full"


def test_degraded_risk_state_is_copied_as_written(tmp_path: Path):
    """A degraded file says so itself; the export must not repair, default or drop it."""
    outputs = tmp_path / "outputs"
    _required(outputs)
    degraded = {
        "status": "shadow",
        "dial": {"state": None, "rule": "price_200d", "since": None},
        "price_rule": None,
        "macro_reading": None,
        "vix_alarm": {"active": None},
        "degraded": True,
        "reasons": ["spy_missing: no SPY closes in the store", "regime_missing: no valid regime reading"],
    }
    _write(outputs / "risk_state.json", degraded)
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "complete"
    assert _strict(data_dir / "risk_state.json") == degraded


# --------------------------------------------------------------------------- data_health.json


def test_data_health_with_nothing_reported_is_all_null_with_reasons(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    data_dir = tmp_path / "dashboard"

    export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    health = _strict(data_dir / DATA_HEALTH_FILE)
    assert health["daily"] is None
    assert health["sector_prices"] is None
    assert health["reading_date"] is None
    assert health["input_groups"] is None
    assert health["carried_forward"] is None
    assert health["input_groups_reason"] == "store_not_read"


def test_data_health_does_not_change_data_status_or_the_manifest_lists(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _required(outputs)
    data_dir = tmp_path / "dashboard"

    manifest = export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir)

    assert manifest["data_status"] == "complete"
    assert DATA_HEALTH_FILE not in manifest["missing_files"]
    assert DATA_HEALTH_FILE not in manifest["optional_available_files"]
    assert (data_dir / DATA_HEALTH_FILE).exists()


def test_daily_warnings_are_grouped_in_first_seen_order(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _write(
        outputs / "daily_diagnostic_summary.json",
        {
            "run_date": "2026-11-12",
            "status": "success_with_warnings",
            "warning_count": 4,
            "warnings": ["stale_source:INDPRO", "invalid_dimension:dollar", "invalid_dimension:dollar", "x"],
            "errors": [],
            "non_fatal_failures": ["sector price panel"],
            "feature_freshness": {"gap_days": 0, "max_feature_date": "2026-11-11", "stale": False},
        },
    )

    daily = build_data_health(outputs, today=TODAY)["daily"]

    assert daily["run_date"] == "2026-11-12"
    assert daily["status"] == "success_with_warnings"
    assert daily["warning_count"] == 4
    assert daily["warnings"] == [
        {"warning": "stale_source:INDPRO", "count": 1},
        {"warning": "invalid_dimension:dollar", "count": 2},
        {"warning": "x", "count": 1},
    ]
    assert daily["errors"] == []
    assert daily["non_fatal_failures"] == ["sector price panel"]
    assert daily["feature_freshness"] == {"gap_days": 0, "max_feature_date": "2026-11-11", "stale": False}


def test_a_zero_warning_count_is_a_value_and_absent_fields_stay_absent(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _write(outputs / "daily_diagnostic_summary.json", {"warning_count": 0, "warnings": []})
    daily = build_data_health(outputs, today=TODAY)["daily"]
    assert daily["warning_count"] == 0
    assert daily["warnings"] == []
    # Not written by this run: null, never an empty list or a default status.
    assert daily["status"] is None
    assert daily["non_fatal_failures"] is None
    assert daily["errors"] is None
    assert daily["feature_freshness"] is None


def test_warning_count_falls_back_to_the_list_and_is_null_without_either(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _write(outputs / "daily_diagnostic_summary.json", {"warnings": ["a", "b"]})
    assert build_data_health(outputs, today=TODAY)["daily"]["warning_count"] == 2
    _write(outputs / "daily_diagnostic_summary.json", {"status": "success"})
    daily = build_data_health(outputs, today=TODAY)["daily"]
    assert daily["warning_count"] is None
    assert daily["warnings"] is None


def test_sector_price_panel_freshness_uses_the_risk_dial_stale_rule(tmp_path: Path):
    outputs = tmp_path / "outputs"
    panel = {"market_observed": True, "reasons": []}
    _write(outputs / "sector_validation.json", {"price_end_date": "2026-11-08", "price_panel": panel})
    fresh = build_data_health(outputs, today=TODAY)["sector_prices"]
    assert fresh == {
        "last_price_date": "2026-11-08",
        "age_days": 5,
        "fresh": True,
        "fresh_within_days": STALE_DAYS,
        "market_observed": True,
        "reasons": [],
    }

    _write(outputs / "sector_validation.json", {"price_end_date": "2026-11-07", "price_panel": panel})
    stale = build_data_health(outputs, today=TODAY)["sector_prices"]
    assert stale["age_days"] == 6
    assert stale["fresh"] is False


def test_sector_price_panel_without_a_date_reports_no_freshness(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _write(outputs / "sector_validation.json", {"valid": False, "reason": "no_validation_summary"})
    prices = build_data_health(outputs, today=TODAY)["sector_prices"]
    assert prices["last_price_date"] is None
    assert prices["age_days"] is None
    assert prices["fresh"] is None
    assert prices["market_observed"] is None
    assert prices["reasons"] is None


def test_input_groups_come_from_the_store_read_only(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    db = tmp_path / "store.duckdb"
    day = date(2026, 11, 1)
    _store(
        db,
        contributions=[
            # An older date must not leak into the latest reading.
            ("inflation_pressure", "cpi", date(2026, 10, 1), True, "ok"),
            ("inflation_pressure", "cpi", day, True, "carried_forward"),
            ("inflation_pressure", "pce", day, True, "ok"),
            ("yield_curve", "curve", day, True, "ok"),
            ("dollar", "usd", day, False, "stale_asof_value"),
            ("growth", "no_asof_row", day, True, "ok"),
        ],
        asof=[
            (date(2026, 10, 1), "cpi", date(2026, 8, 1), 61),
            (day, "cpi", date(2026, 8, 1), 92),
            (day, "pce", date(2026, 9, 1), 61),
            (day, "curve", day, 0),
            (day, "usd", date(2026, 7, 1), 123),
        ],
        health=[("cpi", "CPIAUCSL"), ("pce", "PCEPI"), ("curve", "T10Y2Y"), ("usd", "NNUSBIS")],
    )
    before = _sha(db)

    health = build_data_health(outputs, db_path=db, today=TODAY)

    assert _sha(db) == before
    assert health["reading_date"] == "2026-11-01"
    assert health["input_groups_reason"] is None
    groups = {group["group"]: group for group in health["input_groups"]}
    assert list(groups) == ["dollar", "growth", "inflation_pressure", "yield_curve"]

    inflation = groups["inflation_pressure"]
    assert inflation["oldest_input_date"] == "2026-08-01"
    assert inflation["oldest_input_age_days"] == 92
    assert inflation["input_count"] == 2
    assert inflation["carried_forward_count"] == 1
    assert inflation["not_used_count"] == 0
    assert inflation["inputs"][0] == {
        "feature_id": "cpi",
        "series_id": "CPIAUCSL",
        "observation_date": "2026-08-01",
        "age_days": 92,
        "used": True,
        "reason": "carried_forward",
    }

    # 0 days is an age, not an absence.
    assert groups["yield_curve"]["oldest_input_age_days"] == 0
    assert groups["yield_curve"]["oldest_input_date"] == "2026-11-01"

    assert groups["dollar"]["not_used_count"] == 1
    assert groups["dollar"]["inputs"][0]["used"] is False
    assert groups["dollar"]["inputs"][0]["reason"] == "stale_asof_value"

    # No as-of row: the date and the age stay absent.
    assert groups["growth"]["oldest_input_date"] is None
    assert groups["growth"]["oldest_input_age_days"] is None
    assert groups["growth"]["inputs"][0]["series_id"] is None

    assert health["carried_forward"] == [
        {
            "group": "inflation_pressure",
            "feature_id": "cpi",
            "series_id": "CPIAUCSL",
            "observation_date": "2026-08-01",
            "age_days": 92,
        }
    ]


def test_no_carried_release_is_an_empty_list_not_null(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    db = tmp_path / "store.duckdb"
    day = date(2026, 11, 1)
    _store(db, [("yield_curve", "curve", day, True, "ok")], [(day, "curve", day, 0)], [("curve", "T10Y2Y")])
    assert build_data_health(outputs, db_path=db, today=TODAY)["carried_forward"] == []


def test_a_missing_store_is_not_created_and_is_named(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    db = tmp_path / "absent.duckdb"

    health = build_data_health(outputs, db_path=db, today=TODAY)

    assert not db.exists()
    assert health["input_groups"] is None
    assert health["carried_forward"] is None
    assert health["input_groups_reason"] == "store_missing"


def test_a_store_without_the_tables_is_named_not_raised(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    db = tmp_path / "empty.duckdb"
    duckdb.connect(str(db)).close()

    health = build_data_health(outputs, db_path=db, today=TODAY)

    assert health["input_groups"] is None
    assert health["input_groups_reason"].startswith("store_unreadable:")


def test_a_store_with_no_reading_is_named(tmp_path: Path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    db = tmp_path / "store.duckdb"
    _store(db, [], [], [])

    health = build_data_health(outputs, db_path=db, today=TODAY)

    assert health["input_groups"] is None
    assert health["input_groups_reason"] == "no_reading_in_store"


def test_export_passes_the_store_through_and_writes_strict_json(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _required(outputs)
    db = tmp_path / "store.duckdb"
    day = date(2026, 11, 1)
    _store(db, [("yield_curve", "curve", day, True, "ok")], [(day, "curve", day, 0)], [("curve", "T10Y2Y")])
    data_dir = tmp_path / "dashboard"

    export_dashboard_data(outputs_dir=outputs, dashboard_data_dir=data_dir, db_path=db)

    health = _strict(data_dir / DATA_HEALTH_FILE)
    assert health["schema"] == "mri.dashboard_data_health.v1"
    assert health["input_groups"][0]["group"] == "yield_curve"
