from __future__ import annotations

import os

import duckdb
import pandas as pd
import pytest

from macro_engine.ingest.fred import FredClient, FredError
from macro_engine.ingest.registry import load_ingestion_sources, select_sources
from macro_engine.ingest.service import run_fred_ingestion
from macro_engine.storage.duckdb_store import DuckDBStore


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.payloads.pop(0)


class FakeFredClient:
    def __init__(self, stale_series: set[str] | None = None, fail_series: set[str] | None = None):
        self.stale_series = stale_series or set()
        self.fail_series = fail_series or set()

    def get_series_metadata(self, series_id: str):
        if series_id in self.fail_series:
            raise FredError(f"invalid series {series_id}")
        return {
            "series_id": series_id,
            "title": f"{series_id} title",
            "frequency": "M",
            "units": "Index",
            "seasonal_adjustment": "SA",
            "last_updated": "2026-05-01 12:00:00-05",
            "notes": f"{series_id} notes",
        }

    def get_series_observations(self, series_id: str, observation_start=None, observation_end=None):
        if series_id in self.fail_series:
            raise FredError(f"invalid series {series_id}")
        end = "2020-02-01" if series_id in self.stale_series else "2026-05-01"
        dates = pd.date_range("2026-01-01", end, freq="MS")
        if series_id in self.stale_series:
            dates = pd.date_range("2019-10-01", end, freq="MS")
        return pd.DataFrame(
            {
                "series_id": series_id,
                "date": dates.date,
                "value": [float(index + 1) for index in range(len(dates))],
                "realtime_start": [pd.Timestamp("2026-05-12").date()] * len(dates),
                "realtime_end": [pd.Timestamp("9999-12-31").date()] * len(dates),
            }
        )


def test_load_production_controlled_source_set():
    sources = load_ingestion_sources("config/phase_b_sources.yaml")
    selected = select_sources(sources)

    assert len(selected) == 12
    assert {source.series_id for source in selected} == {
        "INDPRO",
        "PAYEMS",
        "UNRATE",
        "CPIAUCSL",
        "PCEPI",
        "FEDFUNDS",
        "DGS10",
        "BAA10Y",
        "NFCI",
        "T10Y2Y",
        "ICSA",
        "BAMLH0A0HYM2",
    }


def test_missing_api_key_has_clear_error():
    with pytest.raises(FredError, match="FRED_API_KEY is required"):
        FredClient(api_key="")


def test_fred_client_parses_mocked_observation_response():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "observations": [
                        {
                            "realtime_start": "2026-05-01",
                            "realtime_end": "9999-12-31",
                            "date": "2026-01-01",
                            "value": "1.5",
                        },
                        {
                            "realtime_start": "2026-05-01",
                            "realtime_end": "9999-12-31",
                            "date": "2026-02-01",
                            "value": ".",
                        },
                    ]
                }
            )
        ]
    )
    client = FredClient(api_key="test", session=session)

    frame = client.get_series_observations("FEDFUNDS")

    assert frame["series_id"].tolist() == ["FEDFUNDS", "FEDFUNDS"]
    assert frame["value"].iloc[0] == 1.5
    assert pd.isna(frame["value"].iloc[1])


def test_fred_client_reports_mocked_api_error():
    session = FakeSession([FakeResponse({"error_message": "Bad Request"})])
    client = FredClient(api_key="test", session=session)

    with pytest.raises(FredError, match="Bad Request"):
        client.get_series_metadata("BADID")


def test_ingestion_creates_canonical_tables_and_parquet(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    parquet_dir = tmp_path / "fred"

    summary = run_fred_ingestion(
        config_path="config/phase_b_sources.yaml",
        requested_series=["FEDFUNDS"],
        end="2026-05-12",
        db_path=db_path,
        parquet_dir=parquet_dir,
        client=FakeFredClient(),
    )

    assert summary.series_requested == 1
    assert summary.series_succeeded == 1
    assert summary.series_failed == 0
    for table in ["ingestion_runs", "series_metadata", "raw_observations", "source_health"]:
        assert (parquet_dir / f"{table}.parquet").exists()

    store = DuckDBStore(db_path)
    assert set(store.read_table("series_metadata")["series_id"]) == {"FEDFUNDS"}
    assert not store.read_raw_observations("FEDFUNDS").empty


def test_ingestion_is_idempotent(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    parquet_dir = tmp_path / "fred"
    kwargs = {
        "config_path": "config/phase_b_sources.yaml",
        "requested_series": ["FEDFUNDS"],
        "end": "2026-05-12",
        "db_path": db_path,
        "parquet_dir": parquet_dir,
        "client": FakeFredClient(),
    }

    run_fred_ingestion(**kwargs)
    run_fred_ingestion(**kwargs)

    store = DuckDBStore(db_path)
    raw = store.read_raw_observations("FEDFUNDS")
    assert len(raw) == raw[["series_id", "date", "realtime_start", "realtime_end"]].drop_duplicates().shape[0]


def test_invalid_series_is_recorded_as_failure(tmp_path):
    summary = run_fred_ingestion(
        config_path="config/phase_b_sources.yaml",
        requested_series=["BADID"],
        end="2026-05-12",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
        client=FakeFredClient(fail_series={"BADID"}),
    )

    assert summary.series_failed == 1
    assert summary.series_succeeded == 0


def test_health_detects_stale_and_disabled_sources(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    run_fred_ingestion(
        config_path="config/phase_b_sources.yaml",
        end="2026-05-12",
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        client=FakeFredClient(stale_series={"INDPRO"}),
    )

    health = DuckDBStore(db_path).read_table("source_health").set_index("series_id")

    assert bool(health.loc["INDPRO", "stale_flag"]) is True
    assert bool(health.loc["INDPRO", "usable"]) is False
    assert health.loc["INDPRO", "reason"] == "unusable_stale"
    assert bool(health.loc["USSLIND", "usable"]) is False
    assert health.loc["USSLIND", "reason"] == "discontinued_or_stale"


def test_duckdb_can_query_stored_parquet(tmp_path):
    parquet_dir = tmp_path / "fred"
    run_fred_ingestion(
        config_path="config/phase_b_sources.yaml",
        requested_series=["FEDFUNDS"],
        end="2026-05-12",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=parquet_dir,
        client=FakeFredClient(),
    )

    count = duckdb.sql(
        f"SELECT count(*) FROM read_parquet('{(parquet_dir / 'raw_observations.parquet').as_posix()}')"
    ).fetchone()[0]

    assert count > 0


@pytest.mark.skipif(not os.getenv("FRED_API_KEY"), reason="FRED_API_KEY not set")
def test_optional_live_fred_ingestion_smoke(tmp_path):
    summary = run_fred_ingestion(
        config_path="config/phase_b_sources.yaml",
        requested_series=["FEDFUNDS"],
        start="2026-01-01",
        end="2026-02-01",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
    )

    assert summary.series_succeeded == 1
