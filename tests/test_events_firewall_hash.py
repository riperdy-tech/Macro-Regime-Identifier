"""Canonicalisation and check logic of scripts/events_firewall_hash.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import events_firewall_hash as fw  # noqa: E402


def test_run_metadata_is_ignored_but_values_are_not():
    a = {"built_at": "t1", "source_run_id": "r1", "x": {"fetched_at": "a", "v": 1.0}, "asof": "2026-10-16"}
    b = {"built_at": "t2", "source_run_id": "r2", "x": {"fetched_at": "b", "v": 1.0}, "asof": "2026-10-16"}
    c = {"built_at": "t1", "x": {"v": 1.5}, "asof": "2026-10-16"}
    assert fw._digest(fw.canonicalise(a)) == fw._digest(fw.canonicalise(b))
    assert fw._digest(fw.canonicalise(a)) != fw._digest(fw.canonicalise(c))


def test_zero_is_a_value_not_an_absence():
    assert fw.numeric_leaves({"a": 0.0, "b": 0}) == {"/a": 0.0, "/b": 0}
    assert fw._digest(fw.numeric_leaves({"a": 0.0})) != fw._digest(fw.numeric_leaves({}))


def test_numeric_view_ignores_additive_text_but_catches_number_change():
    base = {"shocks": [{"level": 1.5, "name": "oil"}], "built_at": "x"}
    added = {"shocks": [{"level": 1.5, "name": "oil", "narrative": "new text"}], "built_at": "y",
             "new_text_field": "hello"}
    moved = {"shocks": [{"level": 1.6, "name": "oil"}], "built_at": "x"}
    assert fw.numeric_leaves(base) == fw.numeric_leaves(added)
    assert fw.numeric_leaves(base) != fw.numeric_leaves(moved)


def test_bools_are_not_numbers():
    assert fw.numeric_leaves({"flag": True, "n": 2}) == {"/n": 2}


def _build_inputs(tmp_path: Path, value: float = 4.1):
    out = tmp_path / "outputs"
    out.mkdir()
    for name in fw.JSON_ARTIFACTS + fw.NUMERIC_ONLY_ARTIFACTS:
        (out / name).write_text(json.dumps({"built_at": "t", "k": 1.0}), encoding="utf-8")
    db = tmp_path / "s.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE raw_observations (series_id TEXT, date DATE, value DOUBLE, realtime_start DATE,"
        " realtime_end DATE, fetched_at TIMESTAMP)"
    )
    for sid in fw.RAW_SERIES:
        con.execute(
            "INSERT INTO raw_observations VALUES (?, '2026-10-15', ?, '2026-10-15', '9999-12-31', now())",
            [sid, value],
        )
    con.close()
    return out, db


def test_write_then_check_roundtrip_and_detects_change(tmp_path):
    out, db = _build_inputs(tmp_path)
    baseline = tmp_path / "base.json"
    assert fw.main(["--write", str(baseline), "--outputs-dir", str(out), "--db-path", str(db)]) == 0
    args = ["--check", str(baseline), "--outputs-dir", str(out), "--db-path", str(db)]
    assert fw.main(args) == 0

    # metadata-only rebuild: still OK
    (out / "current_regime.json").write_text(json.dumps({"built_at": "later", "k": 1.0}), encoding="utf-8")
    assert fw.main(args) == 0

    # a number moves: caught
    (out / "current_regime.json").write_text(json.dumps({"built_at": "later", "k": 2.0}), encoding="utf-8")
    assert fw.main(args) == 1


def test_raw_observation_change_is_caught_and_zero_value_hashes(tmp_path):
    out, db = _build_inputs(tmp_path, value=0.0)
    baseline = tmp_path / "base.json"
    fw.main(["--write", str(baseline), "--outputs-dir", str(out), "--db-path", str(db)])
    con = duckdb.connect(str(db))
    con.execute("UPDATE raw_observations SET value = 0.01 WHERE series_id = 'DFII10'")
    con.close()
    args = ["--check", str(baseline), "--outputs-dir", str(out), "--db-path", str(db)]
    assert fw.main(args) == 1


def test_missing_input_fails(tmp_path):
    out, db = _build_inputs(tmp_path)
    (out / "shock_register.json").unlink()
    assert fw.main(["--write", str(tmp_path / "b.json"), "--outputs-dir", str(out), "--db-path", str(db)]) == 1
