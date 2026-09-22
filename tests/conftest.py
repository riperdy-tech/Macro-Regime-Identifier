from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DATA_DIR = REPO_ROOT / "dashboard" / "public" / "data"
OUTPUTS_DIR = REPO_ROOT / "outputs"


def _dashboard_data_entries() -> set[str]:
    if not DASHBOARD_DATA_DIR.exists():
        return set()
    return {entry.name for entry in DASHBOARD_DATA_DIR.iterdir()}


def _outputs_snapshot() -> dict[str, float]:
    if not OUTPUTS_DIR.exists():
        return {}
    return {
        str(path.relative_to(OUTPUTS_DIR)): path.stat().st_mtime
        for path in OUTPUTS_DIR.rglob("*")
        if path.is_file()
    }


@pytest.fixture(scope="session", autouse=True)
def guard_dashboard_data_dir():
    """Fail the session if any test leaks files into the repo dashboard data dir.

    dashboard/public/data must stay pristine (only .gitkeep) so the local
    dashboard falls back to its bundled sample fixtures. Tests that exercise
    export_dashboard_data or replay_news_history must redirect
    dashboard_data_dir to a tmp_path location.
    """
    before = _dashboard_data_entries()
    yield
    leaked = _dashboard_data_entries() - before
    assert not leaked, (
        "tests leaked files into dashboard/public/data "
        f"(breaks dashboard sample-data fallback): {sorted(leaked)}"
    )


@pytest.fixture(scope="session", autouse=True)
def guard_repo_outputs_dir():
    """Fail the session if any test writes into or modifies the repo's outputs/ dir.

    outputs/ holds generated artifacts that other processes read straight off disk
    (build_regime_status reads daily_diagnostic_summary.json; export-dashboard-data
    copies regime_timeline.json and macro_features_timeline.json). A test run that
    writes here corrupts those artifacts until the next real pipeline run overwrites
    them -- this is what MRI_S0_APPROVAL.md F1 found and requires stopped. Every
    writer that defaults to "outputs" must be pointed at tmp_path in tests.
    """
    before = _outputs_snapshot()
    yield
    after = _outputs_snapshot()
    new_files = sorted(set(after) - set(before))
    changed_files = sorted(
        path for path in set(after) & set(before) if after[path] != before[path]
    )
    assert not new_files and not changed_files, (
        "tests wrote into the repo outputs/ directory (must redirect to tmp_path): "
        f"new={new_files} changed={changed_files}"
    )
