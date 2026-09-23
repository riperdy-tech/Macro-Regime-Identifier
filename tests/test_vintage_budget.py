from __future__ import annotations

from pathlib import Path

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


def test_pipeline_warning_and_status_on_deferred_vintages(tmp_path):
    """B3: When vintage ingestion has deferred pairs, the pipeline records
    vintage_partial:deferred={n}:frontier={frontier} warning and finishes
    with success_with_warnings status."""
    from macro_engine.ingest.schemas import VintageIngestionSummary
    from macro_engine.pipeline_runner import run_pipeline

    def _mock_ingest(*args, **kwargs):
        class _IngestSumm:
            series_requested = 1
            series_succeeded = 1
            stale_series = []
        return _IngestSumm()

    def _deferred_vrunner(*args, **kwargs):
        return VintageIngestionSummary(
            run_id="run1",
            series_requested=1,
            as_of_dates=["2026-09-01"],
            vintage_rows=10,
            vintage_series=1,
            empty_vintage_count=0,
            failed_count=0,
            storage_path=str(tmp_path / "alfred"),
            deferred_count=15,
            deferred_oldest_asof="2014-02-01",
            deferred_newest_asof="2020-01-01",
            budget_exhausted=True,
            requests_made=5,
        )

    output_dir = tmp_path / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg_text = Path("config/phase_b_sources.yaml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace("output_dir: outputs", f"output_dir: {output_dir.as_posix()}")
    cfg_path = tmp_path / "sources_redirected.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    db_path = tmp_path / "macro.duckdb"
    summary = run_pipeline(
        config_path=cfg_path,
        db_path=db_path,
        parquet_dir=tmp_path / "fred",
        mode="mock",
        ingest_runner=_mock_ingest,
        vintage_runner=_deferred_vrunner,
    )

    assert summary.status == "success_with_warnings"
    assert summary.vintage_deferred_pairs == 15
    run = DuckDBStore(db_path).read_table("pipeline_runs").iloc[-1]
    assert run["status"] == "success_with_warnings"


def test_asof_resolver_pit_vintage_pending_behavior():
    """B4 / Resolver:
    - A missing answered as-of at E (>= boundary) gives pit_vintage_pending.
    - A present-date answer 1 day before E is usable (<= 7 days lag).
    - Fully answered gives output identical to answered_asofs=None.
    - Pre-boundary dates are unaffected."""
    from macro_engine.evaluation.calendar import build_asof_feature_values
    from macro_engine.evaluation.config import EvaluationCalendarConfig
    from macro_engine.features.config import FeatureDefinition
    from macro_engine.ingest.schemas import IngestionSource

    feature_def = FeatureDefinition.model_validate(
        {
            "feature_id": "s1_feat",
            "series_id": "S1",
            "transform": "level",
            "normalization": "none",
            "direction": "higher_is_test_positive",
            "enabled": True,
            "min_observations": 1,
        }
    )
    source = IngestionSource.model_validate(
        {
            "series_id": "S1",
            "name": "S1",
            "provider": "FRED",
            "dimension": "growth",
            "frequency": "monthly",
            "required": True,
            "enabled": True,
            "stale_after_days": 45,
            "unusable_after_days": 120,
        }
    )
    features_df = pd.DataFrame(
        {
            "feature_id": ["s1_feat", "s1_feat"],
            "date": [pd.Timestamp("2013-01-01"), pd.Timestamp("2024-05-01")],
            "transformed_value": [1.5, 2.5],
            "normalized_value": [1.5, 2.5],
            "valid": [True, True],
            "reason": ["ok", "ok"],
        }
    )
    cal_df = pd.DataFrame(
        {
            "evaluation_date": [pd.Timestamp("2013-01-01"), pd.Timestamp("2024-06-01")],
            "frequency": ["monthly", "monthly"],
            "valid": [True, True],
            "reason": ["ok", "ok"],
        }
    )
    pub_idx = pd.DataFrame(
        {
            "series_id": ["S1"],
            "date": [pd.Timestamp("2024-05-01")],
            "first_known_date": [pd.Timestamp("2024-05-15")],
        }
    )
    cal_cfg = EvaluationCalendarConfig()

    # Case 1: missing answered as-of for E=2024-06-01 (>= boundary 2014-02-01)
    asof_missing = build_asof_feature_values(
        features=features_df,
        feature_definitions=[feature_def],
        sources=[source],
        calendar=cal_df,
        config=cal_cfg,
        scoring_mode="point_in_time",
        point_in_time_start="2014-02-01",
        answered_asofs={"S1": []},
        publication_index=pub_idx,
    )
    row_pre = asof_missing[asof_missing["evaluation_date"] == pd.Timestamp("2013-01-01")].iloc[0]
    row_post = asof_missing[asof_missing["evaluation_date"] == pd.Timestamp("2024-06-01")].iloc[0]

    # Pre-boundary is unaffected by answered_asofs
    assert bool(row_pre["valid"]) is True
    assert row_pre["reason"] == "ok"

    # Post-boundary missing evidence gives pit_vintage_pending
    assert bool(row_post["valid"]) is False
    assert row_post["reason"] == "pit_vintage_pending"

    # Case 2: present-date answer 1 day before E (2024-05-31 is <= 7 days before 2024-06-01)
    asof_1day = build_asof_feature_values(
        features=features_df,
        feature_definitions=[feature_def],
        sources=[source],
        calendar=cal_df,
        config=cal_cfg,
        scoring_mode="point_in_time",
        point_in_time_start="2014-02-01",
        answered_asofs={"S1": [pd.Timestamp("2024-05-31")]},
        publication_index=pub_idx,
    )
    row_post_1day = asof_1day[asof_1day["evaluation_date"] == pd.Timestamp("2024-06-01")].iloc[0]
    assert bool(row_post_1day["valid"]) is True
    assert row_post_1day["reason"] == "ok"

    # Case 3: fully answered gives output identical to answered_asofs=None
    asof_none = build_asof_feature_values(
        features=features_df,
        feature_definitions=[feature_def],
        sources=[source],
        calendar=cal_df,
        config=cal_cfg,
        scoring_mode="point_in_time",
        point_in_time_start="2014-02-01",
        answered_asofs=None,
        publication_index=pub_idx,
    )
    asof_answered = build_asof_feature_values(
        features=features_df,
        feature_definitions=[feature_def],
        sources=[source],
        calendar=cal_df,
        config=cal_cfg,
        scoring_mode="point_in_time",
        point_in_time_start="2014-02-01",
        answered_asofs={"S1": [pd.Timestamp("2024-06-01")]},
        publication_index=pub_idx,
    )
    pd.testing.assert_frame_equal(asof_none, asof_answered)


def test_config_github_defaults_and_step_timeout_rejected():
    """B5 / Config (Test 9.3 #8):
    - Each config/daily_pipeline_github*.yaml loads with an effective
      overall_run_timeout_minutes <= 20 and vintage_budget_minutes <= 6.
    - step_timeout_minutes is rejected."""
    from macro_engine.operations_config import (
        DailySafetyConfig,
        load_daily_pipeline_config,
    )

    for path in [
        "config/daily_pipeline_github.yaml",
        "config/daily_pipeline_github_live.yaml",
    ]:
        cfg = load_daily_pipeline_config(path)
        assert cfg.safety.overall_run_timeout_minutes <= 20.0
        assert cfg.macro.vintage_budget_minutes <= 6.0

    with pytest.raises(ValueError, match="step_timeout_minutes has been removed"):
        DailySafetyConfig.model_validate({"step_timeout_minutes": 20})


def test_run_macro_propagates_vintage_partial_warning():
    """B5 / _run_macro (Test 9.3 #9):
    _run_macro propagates vintage_partial:* warnings into daily warnings."""
    from macro_engine.daily import _run_macro
    from macro_engine.operations_config import DailyPipelineConfig
    from macro_engine.pipeline_runner import PipelineSummary

    class _MockRunner:
        def __call__(self, *args, **kwargs):
            return PipelineSummary(
                run_id="run1",
                status="success_with_warnings",
                failed_step=None,
                warning_count=1,
                config_path="config.yaml",
                mode="mock",
                output_dir="outputs",
                vintage_deferred_pairs=5,
                warnings=("vintage_partial:deferred=5:frontier=2020-01-01", "unrelated_warning"),
            )

    cfg = DailyPipelineConfig()
    warnings: list[str] = []
    _run_macro(
        cfg,
        db_path="data/macro_engine.duckdb",
        services={"run_pipeline": _MockRunner()},
        warnings=warnings,
    )
    assert "vintage_partial:deferred=5:frontier=2020-01-01" in warnings
    assert "unrelated_warning" not in warnings


def test_daily_deadline_stops_classification_and_skips_later_steps(tmp_path, monkeypatch):
    """B5 / Deadline (Test 9.3 #7):
    With a fake clock, classification stops at deadline - reserve;
    the warning is present; later steps are skipped_deadline;
    the status is success_with_warnings."""
    from macro_engine.daily import run_daily_diagnostic
    from macro_engine.pipeline_runner import PipelineSummary

    output_dir = tmp_path / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg_text = Path("config/daily_pipeline.yaml").read_text(encoding="utf-8")
    cfg_text = cfg_text.replace("archive_root: outputs/archive", f"archive_root: {(tmp_path / 'archive').as_posix()}")
    cfg_text = cfg_text.replace("overall_run_timeout_minutes: 60", "overall_run_timeout_minutes: 20")
    cfg_path = tmp_path / "daily_pipeline_test.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    db_path = tmp_path / "macro.duckdb"

    # Simulated clock: starts at 0.0
    current_time = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: current_time[0])

    def _mock_pipeline(**kwargs):
        # Macro finishes when clock is still early
        current_time[0] = 100.0
        return PipelineSummary(
            run_id="pipe1",
            status="success",
            failed_step=None,
            warning_count=0,
            config_path="",
            mode="mock",
            output_dir=str(output_dir),
        )

    def _mock_ingest(**kwargs):
        # Ingestion finishes right before classification; advance clock past classification deadline (1020s)
        current_time[0] = 1050.0
        return pd.DataFrame()

    def _mock_classify(**kwargs):
        deadline = kwargs.get("deadline_monotonic")
        assert deadline is not None
        # Assert clock is past classification deadline (1020s) but before run deadline (1200s)
        assert current_time[0] >= deadline
        assert current_time[0] < 1200.0
        # Classification stops at deadline and advances clock past run deadline (1200s)
        current_time[0] = 1250.0
        return {
            "classifications": pd.DataFrame(),
            "theme_scores": pd.DataFrame(),
            "sector_impacts": pd.DataFrame(),
            "selected_count": 10,
            "completed_count": 2,
            "deadline_hit": True,
        }

    mock_services = {
        "run_pipeline": _mock_pipeline,
        "build_sector_scores": lambda **_: None,
        "run_sector_validation": lambda **_: None,
        "write_sector_report": lambda **_: (output_dir / "s.json", output_dir / "s.md"),
        "ingest_news": _mock_ingest,
        "classify_news": _mock_classify,
        "write_news_report": lambda **_: (output_dir / "nr.json", output_dir / "nr.md"),
        "build_news_scores": lambda **_: None,
        "write_news_score_report": lambda **_: (output_dir / "ns.json", output_dir / "ns.md"),
        "run_combined": lambda **_: None,
        "run_anchors": lambda **_: None,
        "run_monitoring": lambda **_: None,
    }

    result = run_daily_diagnostic(
        config_path=cfg_path,
        db_path=db_path,
        services=mock_services,
        output_dir=output_dir,
    )

    assert result.status == "success_with_warnings"
    assert any("news_classification_deadline:2/10" in w for w in result.warnings)
    assert any("deadline_reached:skipped=news_report" in w for w in result.warnings)
    assert any("deadline_reached:skipped=news_scoring" in w for w in result.warnings)



