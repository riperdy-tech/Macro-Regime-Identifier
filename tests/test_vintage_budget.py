from __future__ import annotations

import pandas as pd
import pytest

from macro_engine.ingest.service import run_fred_vintage_ingestion
from macro_engine.storage.duckdb_store import DuckDBStore


def _sample_vintage_frame(series_id: str, as_of: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series_id": [series_id],
            "date": [as_of],
            "value": [100.0],
            "realtime_start": [as_of],
            "realtime_end": [as_of],
            "source": ["ALFRED"],
            "fetched_at": [pd.Timestamp.now(tz="UTC")],
            "frequency": ["monthly"],
            "units": [None],
        }
    )


def test_vintage_ingestion_newest_first_and_budgeted(tmp_path, monkeypatch):
    """B2: Dates are processed newest-first across all series.
    Pairs never started due to budget are marked deferred (not errors or failures)."""
    monkeypatch.setattr("macro_engine.ingest.service._FRED_REQUESTS_PER_MINUTE", 0)

    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(
        """
sources:
  - series_id: S1
    name: Series 1
    provider: FRED
    dimension: growth
    frequency: monthly
    required: true
    enabled: true
    stale_after_days: 10
    unusable_after_days: 20
  - series_id: S2
    name: Series 2
    provider: FRED
    dimension: inflation
    frequency: monthly
    required: true
    enabled: true
    stale_after_days: 10
    unusable_after_days: 20
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    requested_calls: list[tuple[str, str]] = []
    call_limit = 3

    class _BudgetedClient:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            requested_calls.append((as_of, series_id))
            if len(requested_calls) >= call_limit:
                # Advance simulated clock past deadline
                fake_time["now"] = 1000.0
            return _sample_vintage_frame(series_id, as_of)

    fake_time = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: fake_time["now"])

    dates = ["2024-01-01", "2024-02-01", "2024-03-01"]
    summary = run_fred_vintage_ingestion(
        as_of_dates=dates,
        config_path=cfg_path,
        db_path=db_path,
        parquet_dir=tmp_path / "alfred",
        client=_BudgetedClient(),
        time_budget_seconds=10.0,
    )

    assert summary.requests_made == 3
    # Newest date 2024-03-01 processed first for both S1 and S2
    assert requested_calls[0] == ("2024-03-01", "S1")
    assert requested_calls[1] == ("2024-03-01", "S2")
    assert requested_calls[2] == ("2024-02-01", "S1")

    # 3 deferred pairs out of 6 total
    assert summary.deferred_count == 3
    assert summary.deferred_oldest_asof == "2024-01-01"
    assert summary.deferred_newest_asof == "2024-02-01"
    assert summary.budget_exhausted is True
    assert summary.failed_count == 0


def test_vintage_ingestion_kill_safety_per_date_flush(tmp_path, monkeypatch):
    """B2 / Kill-safety: The client raises after K pairs.
    Every as-of date completed before the interrupted one is stored;
    the interrupted date is not half-written."""
    monkeypatch.setattr("macro_engine.ingest.service._FRED_REQUESTS_PER_MINUTE", 0)

    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(
        """
sources:
  - series_id: S1
    name: Series 1
    provider: FRED
    dimension: growth
    frequency: monthly
    required: true
    enabled: true
    stale_after_days: 10
    unusable_after_days: 20
  - series_id: S2
    name: Series 2
    provider: FRED
    dimension: inflation
    frequency: monthly
    required: true
    enabled: true
    stale_after_days: 10
    unusable_after_days: 20
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    calls = 0

    class _CrashingClient:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            nonlocal calls
            calls += 1
            if calls == 3:
                # Crash on the 3rd pair (which is in the second date: 2024-01-01)
                raise RuntimeError("simulated process kill")
            return _sample_vintage_frame(series_id, as_of)

    dates = ["2024-01-01", "2024-02-01"]
    with pytest.raises(RuntimeError, match="simulated process kill"):
        run_fred_vintage_ingestion(
            as_of_dates=dates,
            config_path=cfg_path,
            db_path=db_path,
            parquet_dir=tmp_path / "alfred",
            client=_CrashingClient(),
            max_workers=1,
        )

    # Date 2024-02-01 completed and must be fully stored
    keys = store.read_vintage_keys()
    assert keys is not None
    stored_dates = set(pd.to_datetime(keys["realtime_start"]).dt.strftime("%Y-%m-%d"))
    assert "2024-02-01" in stored_dates

    # Date 2024-01-01 was interrupted; it must NOT be in the store at all
    assert "2024-01-01" not in stored_dates


def test_vintage_ingestion_warm_store_skips_answered_pairs(tmp_path, monkeypatch):
    """B2 / Warm store: All calendar pairs answered: requests = present dates * series only."""
    monkeypatch.setattr("macro_engine.ingest.service._FRED_REQUESTS_PER_MINUTE", 0)

    cfg_path = tmp_path / "sources.yaml"
    cfg_path.write_text(
        """
sources:
  - series_id: S1
    name: Series 1
    provider: FRED
    dimension: growth
    frequency: monthly
    required: true
    enabled: true
    stale_after_days: 10
    unusable_after_days: 20
""",
        encoding="utf-8",
    )

    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()

    # Pre-populate 2024-01-01 as already answered in store
    store.upsert_raw_observation_vintages(_sample_vintage_frame("S1", "2024-01-01"))

    requested = []

    class _Client:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            requested.append((as_of, series_id))
            return _sample_vintage_frame(series_id, as_of)

    dates = ["2024-01-01", "2026-09-23"]
    summary = run_fred_vintage_ingestion(
        as_of_dates=dates,
        config_path=cfg_path,
        db_path=db_path,
        parquet_dir=tmp_path / "alfred",
        client=_Client(),
    )

    assert summary.skipped_pairs == 1
    assert summary.requests_made == 1
    assert requested == [("2026-09-23", "S1")]
