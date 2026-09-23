from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd
import pytest

from macro_engine.ingest.fred import FredError
from macro_engine.ingest.schemas import IngestionRunSummary, VintageIngestionSummary
from macro_engine.pipeline_runner import run_pipeline
from macro_engine.storage.duckdb_store import DuckDBStore
from tests.test_phase_c_features import _raw_monthly


def _raw_sub_monthly(series_id: str, freq: str, frequency_label: str) -> pd.DataFrame:
    # Daily/weekly series must be mocked at their real frequency: with
    # publication lags applied, a month-start-dated "daily" observation would
    # look one month stale at every evaluation date.
    dates = pd.date_range("2018-01-01", "2031-08-01", freq=freq)
    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": dates,
            "value": [float(index % 251 + 1) for index in range(len(dates))],
            "realtime_start": [pd.Timestamp("2026-05-12").date()] * len(dates),
            "realtime_end": [pd.Timestamp("9999-12-31").date()] * len(dates),
            "source": ["FRED"] * len(dates),
            "fetched_at": [pd.Timestamp("2026-05-12")] * len(dates),
            "frequency": [frequency_label] * len(dates),
            "units": ["Index"] * len(dates),
        }
    )


def _raw_fedfunds_monthly(periods: int) -> pd.DataFrame:
    # S1.4 (operator-approved 2026-09-23): with ten_year_yield_level_z removed from
    # policy_stance, fed_funds_6m_change_z is no longer masked by a second valid feature.
    # _raw_monthly's plain +1-per-month ramp gives diff_6m a constant 6.0 forever, zero
    # variance, so rolling_z_10y normalization can never produce a valid z-score
    # (insufficient_normalization_history) -- a pre-existing fixture gap S1.4 exposes, not
    # a production regression. A period-12 sawtooth gives the 6-month change real variance.
    frame = _raw_monthly("FEDFUNDS", periods)
    frame["value"] = [float(index % 12 + 1) for index in range(periods)]
    return frame


def _mock_ingest(config_path, start, end, db_path, parquet_dir):
    store = DuckDBStore(db_path)
    store.initialize()
    raw = pd.concat(
        [
            _raw_monthly("INDPRO", 140),
            _raw_monthly("PAYEMS", 140),
            _raw_monthly("UNRATE", 140),
            _raw_monthly("CPIAUCSL", 140),
            _raw_monthly("PCEPI", 140),
            _raw_fedfunds_monthly(140),
            _raw_sub_monthly("DGS10", "B", "daily"),
            _raw_sub_monthly("BAA10Y", "B", "daily"),
            _raw_sub_monthly("NFCI", "W-FRI", "weekly"),
            _raw_sub_monthly("T10Y2Y", "B", "daily"),
        ],
        ignore_index=True,
    )
    store.upsert_raw_observations(raw)
    store.export_parquet(parquet_dir)
    return IngestionRunSummary(
        run_id="mock-run",
        series_requested=10,
        series_succeeded=10,
        series_failed=0,
        stale_series=[],
        storage_path=str(parquet_dir),
    )


def _failing_ingest(config_path, start, end, db_path, parquet_dir):
    raise RuntimeError("mock hard failure")


def _mock_vintages(*, config_path, db_path, parquet_dir, start, end):
    # The pipeline's vintages step makes real network calls by default (see
    # `run_vintage_backfill`), so every test that runs the pipeline to completion must inject
    # a stand-in here -- the same reason `ingest_runner` is always mocked in this file.
    return VintageIngestionSummary(
        run_id="mock-vintages",
        series_requested=0,
        as_of_dates=[],
        vintage_rows=0,
        vintage_series=0,
        empty_vintage_count=0,
        failed_count=0,
        storage_path=str(parquet_dir),
    )


def _redirected_config(tmp_path) -> Path:
    """The production config with `output_dir` pointed at tmp_path, on the CALENDAR basis.

    Any test that runs the pipeline to COMPLETION must use this. The pipeline writes reports to
    a cwd-relative `outputs/`, so a test that passes the real config publishes a synthetic world
    straight over the live artifacts. That is not hypothetical: two tests here ran the pipeline
    on mock data dated to 2031 and overwrote `outputs/current_regime.json` with
    `date: 2031-08-01`, which then read as a current regime to anything consuming `outputs/`.
    Redirecting the output directory is the difference between testing the pipeline and
    publishing from it.

    It also pins `scoring_mode: calendar_asof`, for the same class of reason. The shipped basis is
    a point-in-time hybrid, and mock data has no ALFRED vintages -- so under the real config every
    as-of feature correctly reports `pit_vintage_missing` and the run legitimately produces no
    valid regime date. That is the honest behaviour (an opted-in point-in-time read must never
    fall back to the approximation it was chosen over), but it makes this a test of the basis
    rather than of the pipeline's wiring. The point-in-time path has its own tests.
    """
    output_dir = tmp_path / "outputs"
    config_path = tmp_path / "pipeline_config.yaml"
    source_config = open("config/phase_b_sources.yaml", encoding="utf-8").read()
    source_config = re.sub(r"(?m)^scoring_mode:.*$", "scoring_mode: calendar_asof", source_config)
    source_config = source_config.replace("output_dir: outputs", f"output_dir: {output_dir.as_posix()}")
    assert "scoring_mode: calendar_asof" in source_config
    config_path.write_text(source_config, encoding="utf-8")
    return config_path


def test_run_pipeline_works_against_temp_mock_data(tmp_path):
    db_path = tmp_path / "macro.duckdb"
    output_dir = tmp_path / "outputs"
    config_path = _redirected_config(tmp_path)

    summary = run_pipeline(
        config_path=config_path,
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
        vintage_runner=_mock_vintages,
    )

    assert summary.status in {"success", "success_with_warnings"}
    assert summary.series_requested == 10
    assert summary.series_succeeded == 10
    assert summary.latest_valid_regime_date is not None
    assert summary.dominant_regime is not None
    assert len(summary.outputs or []) == 4
    assert (output_dir / "current_regime.json").exists()
    assert (output_dir / "historical_diagnostic.md").exists()
    pipeline_runs = DuckDBStore(db_path).read_table("pipeline_runs")
    assert pipeline_runs.iloc[-1]["status"] == summary.status


def test_run_pipeline_records_failed_step_on_hard_failure(tmp_path):
    db_path = tmp_path / "macro.duckdb"

    with pytest.raises(RuntimeError, match="mock hard failure"):
        run_pipeline(
            config_path=_redirected_config(tmp_path),
            db_path=db_path,
            parquet_dir=tmp_path / "fred",
            mode="mock",
            ingest_runner=_failing_ingest,
        )

    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] == "failed"
    assert run["failed_step"] == "ingest"


def test_run_pipeline_fails_the_run_when_the_vintage_step_fails(tmp_path):
    """The vintages step is required: `run-pipeline` had no vintage step at all, which let
    point-in-time coverage silently degrade to whenever a human last ran the backfill by
    hand. A failed refresh must stop the run, not quietly proceed on a stale archive."""

    def _failing_vintages(*, config_path, db_path, parquet_dir, start, end):
        raise RuntimeError("mock vintage failure")

    db_path = tmp_path / "macro.duckdb"

    with pytest.raises(RuntimeError, match="mock vintage failure"):
        run_pipeline(
            config_path=_redirected_config(tmp_path),
            db_path=db_path,
            parquet_dir=tmp_path / "fred",
            mode="mock",
            ingest_runner=_mock_ingest,
            vintage_runner=_failing_vintages,
        )

    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] == "failed"
    assert run["failed_step"] == "vintages"


def test_run_pipeline_fails_the_run_when_every_vintage_fetch_fails(tmp_path):
    """§0.3 item 1, demonstrated the way the S0 approval demonstrated the defect: a client
    whose `get_series_observations_vintage` always raises FredError produced
    `failed_count = 4, vintage_rows = 0` with no exception, so an ALFRED outage, a revoked
    key, or a rate-limit ban all produced a "successful" run on a stale archive. `failed_count
    > 0` with `vintage_rows == 0` must now stop the run instead."""
    from macro_engine.ingest.fred import FredError
    from macro_engine.ingest.service import run_fred_vintage_ingestion

    class _DeadClient:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            raise FredError("ALFRED down")

    def _dead_vintages(*, config_path, db_path, parquet_dir, start, end):
        return run_fred_vintage_ingestion(
            as_of_dates=["2020-01-01"],
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
            client=_DeadClient(),
        )

    db_path = tmp_path / "macro.duckdb"

    with pytest.raises(FredError, match="vintage backfill failed"):
        run_pipeline(
            config_path=_redirected_config(tmp_path),
            db_path=db_path,
            parquet_dir=tmp_path / "fred",
            mode="mock",
            ingest_runner=_mock_ingest,
            vintage_runner=_dead_vintages,
        )

    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] == "failed"
    assert run["failed_step"] == "vintages"


def test_run_fred_vintage_ingestion_skips_not_yet_published_dates(tmp_path):
    """S1-fix item 1 (NEW DEFECT). `vintage_asof_dates` always includes today, and ALFRED does
    not publish a same-day vintage until later. A client that raises `VintageNotYetPublished`
    for that date must be counted separately from a real failure: zero fetch failures, zero
    rows, and the date named in `not_yet_published_dates` -- not `failed_series`."""
    from macro_engine.ingest.fred import VintageNotYetPublished
    from macro_engine.ingest.service import run_fred_vintage_ingestion

    class _NotYetPublishedClient:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            raise VintageNotYetPublished(f"ALFRED has not published {series_id} as of {as_of}")

    summary = run_fred_vintage_ingestion(
        as_of_dates=["2026-09-23"],
        config_path="config/phase_b_sources.yaml",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "alfred",
        client=_NotYetPublishedClient(),
    )

    assert summary.failed_count == 0
    assert summary.failed_series == []
    assert summary.vintage_rows == 0
    assert summary.not_yet_published_count > 0
    assert summary.not_yet_published_dates == ["2026-09-23"]


def test_run_pipeline_succeeds_when_only_the_not_yet_published_date_is_pending(tmp_path):
    """The real-store case this defect broke: every historical as-of date is already cached,
    and the only pending fetch is today, which ALFRED has not published yet. Before this fix,
    `vintage_rows == 0` with `failed_count > 0` failed the run outright every morning. It must
    now succeed."""
    from macro_engine.ingest.fred import VintageNotYetPublished
    from macro_engine.ingest.service import run_fred_vintage_ingestion

    class _NotYetPublishedClient:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            raise VintageNotYetPublished(f"ALFRED has not published {series_id} as of {as_of}")

    def _same_day_vintages(*, config_path, db_path, parquet_dir, start, end):
        return run_fred_vintage_ingestion(
            as_of_dates=["2026-09-23"],
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
            client=_NotYetPublishedClient(),
        )

    db_path = tmp_path / "macro.duckdb"

    summary = run_pipeline(
        config_path=_redirected_config(tmp_path),
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
        vintage_runner=_same_day_vintages,
    )

    assert summary.status in {"success", "success_with_warnings"}
    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] != "failed"
    assert run["failed_step"] is None or pd.isna(run["failed_step"])


def test_run_pipeline_still_fails_on_a_genuine_failure_amid_a_not_yet_published_skip(tmp_path):
    """The skip classification must never mask a real failure: a genuine fetch failure on an
    already-published date still stops the run, even when today's not-yet-published date is
    fetched in the same batch."""
    from macro_engine.ingest.fred import FredError, VintageNotYetPublished
    from macro_engine.ingest.service import run_fred_vintage_ingestion

    class _MixedClient:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            if as_of == "2026-09-23":
                raise VintageNotYetPublished(f"ALFRED has not published {series_id} as of {as_of}")
            raise FredError("ALFRED down")

    def _mixed_vintages(*, config_path, db_path, parquet_dir, start, end):
        return run_fred_vintage_ingestion(
            as_of_dates=["2020-01-01", "2026-09-23"],
            config_path=config_path,
            db_path=db_path,
            parquet_dir=parquet_dir,
            client=_MixedClient(),
        )

    db_path = tmp_path / "macro.duckdb"

    with pytest.raises(FredError, match="vintage backfill failed"):
        run_pipeline(
            config_path=_redirected_config(tmp_path),
            db_path=db_path,
            parquet_dir=tmp_path / "fred",
            mode="mock",
            ingest_runner=_mock_ingest,
            vintage_runner=_mixed_vintages,
        )

    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] == "failed"
    assert run["failed_step"] == "vintages"


def test_run_pipeline_warns_and_continues_on_a_partial_vintage_failure(tmp_path):
    """§0.3 item 1's other half: a PARTIAL vintage failure (some series fetched, some
    failed) must not stop the run outright -- it downgrades to success_with_warnings and
    names which series failed, rather than either silently proceeding or aborting on data
    that mostly refreshed fine."""
    from macro_engine.pipeline_runner import _vintage_partial_warnings

    partial_summary = VintageIngestionSummary(
        run_id="mock-partial-vintages",
        series_requested=2,
        as_of_dates=["2020-01-01"],
        vintage_rows=3,
        vintage_series=1,
        empty_vintage_count=0,
        failed_count=1,
        storage_path=str(tmp_path / "fred"),
        failed_series=["BBB"],
    )
    assert _vintage_partial_warnings(partial_summary) == ["vintage_partial:BBB"]

    db_path = tmp_path / "macro.duckdb"
    summary = run_pipeline(
        config_path=_redirected_config(tmp_path),
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
        vintage_runner=lambda **_: partial_summary,
    )

    assert summary.status == "success_with_warnings"
    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] == "success_with_warnings"
    assert run["failed_step"] is None


def test_live_pipeline_requires_fred_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with pytest.raises(FredError, match="FRED_API_KEY is required"):
        run_pipeline(
            config_path=_redirected_config(tmp_path),
            db_path=tmp_path / "macro.duckdb",
            parquet_dir=tmp_path / "fred",
            mode="live",
            ingest_runner=_mock_ingest,
            load_env=False,
        )


def test_live_pipeline_can_be_invoked_when_key_is_present_with_mock_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    summary = run_pipeline(
        config_path=_redirected_config(tmp_path),
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
        mode="live",
        ingest_runner=_mock_ingest,
        vintage_runner=_mock_vintages,
    )

    assert summary.series_succeeded == 10


def test_pipeline_summary_is_deterministic_shape(tmp_path):
    summary = run_pipeline(
        config_path=_redirected_config(tmp_path),
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
        vintage_runner=_mock_vintages,
    ).to_dict()

    assert set(summary) == {
        "run_id",
        "status",
        "failed_step",
        "warning_count",
        "config_path",
        "mode",
        "output_dir",
        "series_requested",
        "series_succeeded",
        "stale_series",
        "latest_valid_regime_date",
        "dominant_regime",
        "confidence",
        "outputs",
        "vintage_deferred_pairs",
    }
    assert summary["mode"] == "mock"


@pytest.mark.skipif(not os.getenv("FRED_API_KEY"), reason="FRED_API_KEY not set")
def test_optional_live_pipeline_smoke(tmp_path):
    summary = run_pipeline(
        config_path="config/phase_b_sources.yaml",
        db_path=tmp_path / "macro.duckdb",
        parquet_dir=tmp_path / "fred",
        mode="live",
    )

    assert summary.series_succeeded is not None


def test_run_vintage_backfill_restricts_to_point_in_time_start(tmp_path, monkeypatch):
    """B1: run_vintage_backfill restricts requested dates to >= point_in_time_start
    when scoring_mode == 'point_in_time' and no start was explicitly passed."""
    from macro_engine.ingest.service import run_vintage_backfill

    monkeypatch.setattr("macro_engine.ingest.service._FRED_REQUESTS_PER_MINUTE", 0)

    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    cal_dates = pd.date_range("1990-01-01", "2026-05-01", freq="MS")
    store.replace_evaluation_outputs(
        pd.DataFrame(
            {
                "evaluation_date": cal_dates,
                "frequency": "monthly",
                "valid": True,
                "reason": None,
            }
        ),
        pd.DataFrame(
            columns=["feature_id", "as_of", "value", "valid", "reason"]
        ),
    )

    cfg_path = tmp_path / "sources.yaml"
    cfg_content = """
scoring_mode: point_in_time
point_in_time_start: "2014-02-01"
sources:
  - series_id: DGS10
    name: 10-Year Treasury
    provider: FRED
    dimension: rates
    frequency: daily
    required: true
    enabled: true
    stale_after_days: 10
    unusable_after_days: 20
"""
    cfg_path.write_text(cfg_content, encoding="utf-8")

    requested_calls = []

    class _RecordingClient:
        def get_series_observations_vintage(
            self, series_id, as_of, observation_start=None, observation_end=None
        ):
            requested_calls.append((series_id, as_of, observation_start))
            return pd.DataFrame(
                columns=["date", "value", "realtime_start", "realtime_end"]
            )

    client = _RecordingClient()
    run_vintage_backfill(
        config_path=cfg_path,
        db_path=db_path,
        parquet_dir=tmp_path / "alfred",
        client=client,
    )

    assert len(requested_calls) > 0
    min_as_of = min(call[1] for call in requested_calls)
    assert min_as_of == "2014-02-01"
    # Observation start formula is oldest requested as-of - 1 year (2013-02-01)
    obs_starts = {call[2] for call in requested_calls}
    assert obs_starts == {"2013-02-01"}
    # Expected dates >= 2014-02-01 are all present
    expected_dates = [d.strftime("%Y-%m-%d") for d in cal_dates if d >= pd.Timestamp("2014-02-01")]
    for d in expected_dates:
        assert ("DGS10", d, "2013-02-01") in requested_calls


