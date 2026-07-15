from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DATA_DIR = REPO_ROOT / "dashboard" / "public" / "data"


def _dashboard_data_entries() -> set[str]:
    if not DASHBOARD_DATA_DIR.exists():
        return set()
    return {entry.name for entry in DASHBOARD_DATA_DIR.iterdir()}


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
