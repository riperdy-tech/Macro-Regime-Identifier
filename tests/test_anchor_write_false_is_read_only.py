"""A6: `build_anchors(write=False)` must not write to the store.

rs2-local's pin_coe.py calls it that way to take a number, never to change a file. Before this, the
store opened read-write and ran CREATE TABLE IF NOT EXISTS first, so a "read" took the exclusive lock
and could not be done against a read-only file.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pandas as pd
import pytest

from macro_engine.anchors import service as anchor_service
from macro_engine.storage.duckdb_store import DuckDBStore

from test_anchor_service import MINIMAL_ANCHOR_CONFIG, _seed_observations, _seed_timeline

AS_OF = pd.Timestamp("2026-04-30")


def _make_store(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "db.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_raw_observations(_seed_observations(AS_OF))
    store.replace_diagnostic_outputs(
        _seed_timeline(["2026-02-01", "2026-03-01", "2026-04-01"], "goldilocks"),
        pd.DataFrame(),
        pd.DataFrame(),
    )
    config_path = tmp_path / "anchors.yaml"
    config_path.write_text(
        MINIMAL_ANCHOR_CONFIG.format(output_dir=(tmp_path / "out").as_posix()), encoding="utf-8"
    )
    return db_path, config_path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(db_path: Path, config_path: Path, *, write: bool):
    return anchor_service.build_anchors(
        config_path=str(config_path),
        macro_config_path="config/phase_b_sources.yaml",
        sector_config_path="config/sectors.yaml",
        db_path=str(db_path),
        as_of="2026-04-30",
        write=write,
    )


def test_write_false_leaves_the_store_bytes_unchanged(tmp_path: Path):
    db_path, config_path = _make_store(tmp_path)
    before = _digest(db_path)
    _build(db_path, config_path, write=False)
    assert _digest(db_path) == before
    assert not Path(str(db_path) + ".wal").exists()
    assert not (tmp_path / "out").exists()


def test_write_false_works_against_a_read_only_file(tmp_path: Path):
    """A store that cannot be opened for writing is still readable by a write=False build."""
    db_path, config_path = _make_store(tmp_path)
    os.chmod(db_path, stat.S_IREAD)
    try:
        bundle = _build(db_path, config_path, write=False)
    finally:
        os.chmod(db_path, stat.S_IREAD | stat.S_IWRITE)
    assert bundle.long_run_growth.terminal_g_suggestion is not None


def test_write_false_does_not_create_a_missing_store(tmp_path: Path):
    config_path = tmp_path / "anchors.yaml"
    config_path.write_text(
        MINIMAL_ANCHOR_CONFIG.format(output_dir=(tmp_path / "out").as_posix()), encoding="utf-8"
    )
    missing = tmp_path / "nowhere" / "db.duckdb"
    with pytest.raises(Exception):
        _build(missing, config_path, write=False)
    assert not missing.exists()
    assert not missing.parent.exists()


def test_write_true_still_writes(tmp_path: Path):
    db_path, config_path = _make_store(tmp_path)
    before = _digest(db_path)
    _build(db_path, config_path, write=True)
    assert _digest(db_path) != before
    assert (tmp_path / "out" / "cost_of_capital_anchor.json").exists()
