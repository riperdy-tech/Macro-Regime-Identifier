"""WO_MA1: the health watch reads the job record, the anchor, the exports, the store and the regime copies."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import yaml

from macro_engine import health_watch as hw
from macro_engine.health_watch import run_health_watch

NOW = datetime(2026, 11, 13, 0, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[1]


def _w(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _job(hours_old: float, exit_code=0, nonfatal=None) -> dict:
    stamp = (NOW - timedelta(hours=hours_old)).isoformat()
    return {"last_success": stamp, "last_exit_code": exit_code, "non_fatal_failures": nonfatal or []}


def _good_outputs(outputs: Path) -> None:
    _w(outputs / "job_status.json", {"jobs": {"macro_daily": _job(10), "events_daily": _job(10)}})
    _w(
        outputs / "cost_of_capital_anchor.json",
        {"asof": "2026-11-12", "degraded": False, "provenance": {"degradation_reasons": []}},
    )
    _w(outputs / "current_regime.json", _regime("goldilocks", {"goldilocks": 0.4, "recession": 0.1}))
    _w(outputs / "risk_state.json", {"degraded": False, "reasons": []})


def _regime(top: str, probs: dict) -> dict:
    return {"reported_regime": top, "regime_probabilities": probs}


def _run(tmp_path: Path, **kw) -> dict:
    return run_health_watch(tmp_path / "outputs", out_dir=tmp_path / "out", now=NOW, **kw)


def _codes(summary: dict) -> list[tuple[str, str]]:
    return [(f["code"], f["subject"]) for f in summary["findings"]]


def _has(summary: dict, code: str, subject: str | None = None) -> bool:
    return any(f["code"] == code and (subject is None or f["subject"] == subject) for f in summary["findings"])


def _by(summary: dict, code: str) -> dict:
    return next(f for f in summary["findings"] if f["code"] == code)


# --------------------------------------------------------------------------- jobs


def test_a_healthy_set_of_files_gives_status_ok_and_strict_json(tmp_path: Path):
    _good_outputs(tmp_path / "outputs")
    other = tmp_path / "site.json"
    _w(other, _regime("goldilocks", {"goldilocks": 0.45, "recession": 0.05}))
    summary = _run(tmp_path, compare_regime=other)
    assert summary["status"] == "ok" and summary["findings"] == []
    assert summary["counts"] == {"critical": 0, "warning": 0, "total": 0}
    json.loads((tmp_path / "out" / "health_watch.json").read_text(encoding="utf-8"), parse_constant=_reject)


def _reject(token):
    raise AssertionError(f"non-strict JSON token {token}")


def test_job_age_boundary_25_hours_is_fine_and_27_hours_is_not_run(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    _w(outputs / "job_status.json", {"jobs": {"macro_daily": _job(25), "events_daily": _job(27)}})
    summary = _run(tmp_path)
    assert _has(summary, "job_not_run", "events_daily")
    assert not _has(summary, "job_not_run", "macro_daily")
    assert _by(summary, "job_not_run")["severity"] == "critical"


def test_a_job_with_no_record_or_no_success_is_not_run(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    _w(
        outputs / "job_status.json",
        {"jobs": {"macro_daily": {"last_success": None, "last_exit_code": None}}},
    )
    summary = _run(tmp_path)
    assert _has(summary, "job_not_run", "macro_daily")
    assert _has(summary, "job_not_run", "events_daily")
    # None is an absence, not a failure: no job_failed for a None exit code
    assert not _has(summary, "job_failed")


def test_exit_code_0_1_and_none(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    _w(
        outputs / "job_status.json",
        {"jobs": {"macro_daily": _job(1, exit_code=0), "events_daily": _job(1, exit_code=1)}},
    )
    summary = _run(tmp_path)
    assert _has(summary, "job_failed", "events_daily") and not _has(summary, "job_failed", "macro_daily")
    _w(outputs / "job_status.json", {"jobs": {"macro_daily": _job(1, exit_code=None), "events_daily": _job(1)}})
    assert not _has(_run(tmp_path), "job_failed")


def test_non_fatal_failures_empty_and_non_empty(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    _w(
        outputs / "job_status.json",
        {"jobs": {"macro_daily": _job(1, nonfatal=["sector price panel"]), "events_daily": _job(1, nonfatal=[])}},
    )
    summary = _run(tmp_path)
    assert _has(summary, "job_nonfatal", "macro_daily") and not _has(summary, "job_nonfatal", "events_daily")
    finding = _by(summary, "job_nonfatal")
    assert finding["severity"] == "warning" and "sector price panel" in finding["detail"]
    assert summary["status"] == "warning"


def test_a_missing_or_unreadable_job_file_is_a_finding_not_a_pass(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    (outputs / "job_status.json").write_text("{not json", encoding="utf-8")
    summary = _run(tmp_path)
    assert _has(summary, "source_unreadable", "job_status.json") and summary["status"] == "critical"
    (outputs / "job_status.json").unlink()
    assert _has(_run(tmp_path), "source_unreadable", "job_status.json")


# --------------------------------------------------------------------------- anchor


def test_anchor_age_and_degraded(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    base = {"degraded": False, "provenance": {"degradation_reasons": []}}
    _w(outputs / "cost_of_capital_anchor.json", {**base, "asof": "2026-11-12"})  # yesterday: fine
    assert not _has(_run(tmp_path), "anchor_stale_or_degraded")
    _w(outputs / "cost_of_capital_anchor.json", {**base, "asof": "2026-11-11"})  # two days
    assert _has(_run(tmp_path), "anchor_stale_or_degraded")
    _w(outputs / "cost_of_capital_anchor.json", {**base, "asof": "2026-11-13"})  # today: age 0 is a value
    assert not _has(_run(tmp_path), "anchor_stale_or_degraded")
    _w(
        outputs / "cost_of_capital_anchor.json",
        {"asof": "2026-11-13", "degraded": True, "provenance": {"degradation_reasons": ["loadings withheld"]}},
    )
    finding = _by(_run(tmp_path), "anchor_stale_or_degraded")
    assert finding["severity"] == "critical" and "loadings withheld" in finding["detail"]
    _w(outputs / "cost_of_capital_anchor.json", {"asof": "2026-11-13"})  # no degraded flag is not "not degraded"
    assert _has(_run(tmp_path), "anchor_stale_or_degraded")


# --------------------------------------------------------------------------- exports


def _export(folder: Path, hours_old: float, names=hw.EXPORTED_FILES) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = folder / name
        path.write_text("{}", encoding="utf-8")
        stamp = (NOW - timedelta(hours=hours_old)).timestamp()
        os.utime(path, (stamp, stamp))


def test_export_age_boundary_and_missing(tmp_path: Path):
    _good_outputs(tmp_path / "outputs")
    folder = tmp_path / "export"
    _export(folder, 25)
    assert not _has(_run(tmp_path, export_dir=folder), "export_stale")
    _export(folder, 27)
    summary = _run(tmp_path, export_dir=folder)
    assert sum(1 for f in summary["findings"] if f["code"] == "export_stale") == len(hw.EXPORTED_FILES)
    (folder / "current_regime.json").unlink()
    detail = next(f for f in _run(tmp_path, export_dir=folder)["findings"] if f["subject"] == "current_regime.json")
    assert "missing" in detail["detail"]


# --------------------------------------------------------------------------- inputs (the store)


def _store(path: Path, rows: list[tuple], asof: list[tuple], with_fallback_column: bool = True) -> None:
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE dimension_feature_contributions "
        "(dimension_id VARCHAR, feature_id VARCHAR, date DATE, valid BOOLEAN, reason VARCHAR)"
    )
    extra = ", fallback_months INTEGER" if with_fallback_column else ""
    con.execute(
        "CREATE TABLE asof_feature_values "
        f"(evaluation_date DATE, feature_id VARCHAR, source_observation_date DATE, lag_days INTEGER{extra})"
    )
    con.execute("CREATE TABLE feature_health (feature_id VARCHAR, series_id VARCHAR)")
    for row in rows:
        con.execute("INSERT INTO dimension_feature_contributions VALUES (?, ?, ?, ?, ?)", row)
        con.execute("INSERT INTO feature_health VALUES (?, ?)", (row[1], "S_" + row[1]))
    for row in asof:
        marks = "?, ?, ?, ?" + (", ?" if with_fallback_column else "")
        con.execute(f"INSERT INTO asof_feature_values VALUES ({marks})", (tuple(row) + (None,) * (5 - len(row))) if with_fallback_column else row[:4])
    con.close()


def _config(path: Path) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "dimensions": [
                    {"dimension_id": "growth", "required_for_regime": True},
                    {"dimension_id": "dollar", "required_for_regime": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


DAY = date(2026, 11, 1)


def test_invalid_input_is_critical_for_a_required_dimension_and_a_warning_for_a_shadow(tmp_path: Path):
    _good_outputs(tmp_path / "outputs")
    db = tmp_path / "s.duckdb"
    _store(
        db,
        [
            ("growth", "ip", DAY, False, "stale_asof_value"),
            ("dollar", "usd", DAY, False, "stale_asof_value"),
            ("growth", "off", DAY, False, "disabled_feature"),
            ("growth", "fine", DAY, True, "ok"),
        ],
        [],
    )
    summary = _run(tmp_path, db_path=db, dimension_config=_config(tmp_path / "c.yaml"))
    invalid = {f["subject"]: f["severity"] for f in summary["findings"] if f["code"] == "input_invalid"}
    assert invalid == {"growth/ip (S_ip)": "critical", "dollar/usd (S_usd)": "warning"}  # disabled is not a failure


def test_carried_forward_boundary_60_days_and_zero_is_a_value(tmp_path: Path):
    _good_outputs(tmp_path / "outputs")
    db = tmp_path / "s.duckdb"
    _store(
        db,
        [
            ("growth", "a", DAY, True, "carried_forward"),
            ("growth", "b", DAY, True, "carried_forward"),
            ("growth", "c", DAY, True, "carried_forward"),
        ],
        [(DAY, "a", date(2026, 9, 2), 60), (DAY, "b", date(2026, 8, 31), 61), (DAY, "c", DAY, 0)],
    )
    summary = _run(tmp_path, db_path=db, dimension_config=_config(tmp_path / "c.yaml"))
    subjects = [f["subject"] for f in summary["findings"] if f["code"] == "input_carried_forward"]
    assert subjects == ["growth/b (S_b)"]


def test_fallback_months_boundary_and_unknown_months(tmp_path: Path):
    _good_outputs(tmp_path / "outputs")
    db = tmp_path / "s.duckdb"
    rows = [("growth", n, DAY, True, "extended_by_fallback") for n in ("two", "three", "four")]
    _store(db, rows, [(DAY, "two", DAY, 0, 2), (DAY, "three", DAY, 0, 3), (DAY, "four", DAY, 0, 4)])
    summary = _run(tmp_path, db_path=db, dimension_config=_config(tmp_path / "c.yaml"))
    subjects = sorted(f["subject"] for f in summary["findings"] if f["code"] == "input_on_fallback")
    assert subjects == ["growth/four (S_four)", "growth/three (S_three)"]
    # a store without the months column: annotated as unknown, never silent
    db2 = tmp_path / "old.duckdb"
    _store(db2, rows[:1], [(DAY, "two", DAY, 0)], with_fallback_column=False)
    summary = _run(tmp_path, db_path=db2, dimension_config=_config(tmp_path / "c.yaml"))
    assert "months not recorded" in _by(summary, "input_on_fallback")["detail"]


def test_a_missing_store_is_source_unreadable(tmp_path: Path):
    _good_outputs(tmp_path / "outputs")
    summary = _run(tmp_path, db_path=tmp_path / "absent.duckdb")
    assert _has(summary, "source_unreadable", "store")
    assert not (tmp_path / "absent.duckdb").exists()  # read-only: never created


# --------------------------------------------------------------------------- regime comparison and risk state


def test_regime_disagreement_top_probability_and_absent_file(tmp_path: Path):
    _good_outputs(tmp_path / "outputs")
    site = tmp_path / "site.json"
    assert _by(_run(tmp_path), "regime_compare_unavailable")["severity"] == "warning"
    assert _by(_run(tmp_path, compare_regime=site), "regime_compare_unavailable")  # path given, file absent
    _w(site, _regime("recession", {"goldilocks": 0.4, "recession": 0.1}))
    assert _by(_run(tmp_path, compare_regime=site), "regime_disagrees")["severity"] == "critical"
    _w(site, _regime("goldilocks", {"goldilocks": 0.51, "recession": 0.1}))  # 0.11 apart
    assert _has(_run(tmp_path, compare_regime=site), "regime_disagrees")
    _w(site, _regime("goldilocks", {"goldilocks": 0.49, "recession": 0.1}))  # 0.09 apart
    assert not _has(_run(tmp_path, compare_regime=site), "regime_disagrees")
    # the published regime_status.json carries the top probability only
    _w(site, {"dominant_regime": "goldilocks", "regime_probability": 0.40})
    assert not _has(_run(tmp_path, compare_regime=site), "regime_disagrees")
    _w(site, {"dominant_regime": "goldilocks", "regime_probability": 0.80})
    assert _has(_run(tmp_path, compare_regime=site), "regime_disagrees")
    site.write_text("{broken", encoding="utf-8")
    assert _has(_run(tmp_path, compare_regime=site), "source_unreadable", "second regime file")


def test_risk_state_absent_degraded_and_unreadable(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    _w(outputs / "risk_state.json", {"degraded": True, "reasons": ["spy_stale: last SPY close 2026-11-01"]})
    finding = _by(_run(tmp_path), "risk_state_degraded")
    assert finding["severity"] == "warning" and "spy_stale" in finding["detail"]
    (outputs / "risk_state.json").unlink()
    assert "absent" in _by(_run(tmp_path), "risk_state_degraded")["detail"]
    (outputs / "risk_state.json").write_text("[1]", encoding="utf-8")
    assert _has(_run(tmp_path), "source_unreadable", "risk_state.json")


# --------------------------------------------------------------------------- changed


def test_changed_is_true_on_a_new_finding_and_false_on_a_repeat(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    _w(outputs / "job_status.json", {"jobs": {"macro_daily": _job(1, nonfatal=["x"]), "events_daily": _job(1)}})
    assert _run(tmp_path)["changed"] is True  # first run, no state: new finding
    assert _run(tmp_path)["changed"] is False  # same set again
    _w(outputs / "job_status.json", {"jobs": {"macro_daily": _job(1), "events_daily": _job(1)}})
    assert _run(tmp_path)["changed"] is True  # the finding cleared
    assert _run(tmp_path)["changed"] is False
    state = json.loads((tmp_path / "out" / "health_watch_state.json").read_text(encoding="utf-8"))
    assert state["pairs"] == [["regime_compare_unavailable", "current_regime"]]


def test_the_watch_writes_only_into_out_dir(tmp_path: Path):
    outputs = tmp_path / "outputs"
    _good_outputs(outputs)
    before = sorted(p.name for p in outputs.iterdir())
    _run(tmp_path)
    assert sorted(p.name for p in outputs.iterdir()) == before


# --------------------------------------------------------------------------- the workflow file


def test_pc_watch_workflow_triggers_permissions_and_no_input_interpolation():
    path = REPO / ".github" / "workflows" / "pc-watch.yml"
    text = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    triggers = doc.get("on", doc.get(True))
    assert set(triggers) == {"workflow_dispatch", "schedule"}
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"status", "changed", "message"}
    assert triggers["schedule"] == [{"cron": "40 23 * * *"}]
    assert doc["permissions"] == {"issues": "write", "actions": "read"}
    for job in doc["jobs"].values():
        assert job["timeout-minutes"] <= 10
        for step in job["steps"]:
            assert "${{ inputs." not in step.get("run", "")
            assert "${{ github.event.inputs" not in step.get("run", "")
    assert "actions/checkout" not in text
