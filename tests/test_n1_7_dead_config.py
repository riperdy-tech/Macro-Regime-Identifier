"""N1.7: dead config removed or wired (spec §4).

Each shipped config still loads (the dead keys are gone from the yaml, not
just from the model), and re-supplying any removed key is rejected with a
message naming why -- the pattern S2 established for step_timeout_minutes."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from macro_engine.accumulation import _accumulation_run_frame, _latest_run_new_items
from macro_engine.news.config import (
    NewsMonitoringConfig,
    NewsMonitoringFreshnessRules,
    NewsMonitoringQualityThresholds,
    NewsMonitoringSourceGroup,
    NewsSelectionConfig,
    load_news_monitoring_config,
    load_news_selection_config,
)
from macro_engine.operations_config import (
    AccumulationQualityThresholds,
    DailyLiveAISafetyConfig,
    DailyOutputsConfig,
    DailySafetyConfig,
    NewsAccumulationConfig,
    load_daily_pipeline_config,
    load_news_accumulation_config,
)


@pytest.mark.parametrize(
    "path",
    [
        "config/daily_pipeline.yaml",
        "config/daily_pipeline_github.yaml",
        "config/daily_pipeline_github_live.yaml",
        "config/daily_pipeline_replay_news_only.yaml",
    ],
)
def test_daily_pipeline_configs_still_load(path):
    cfg = load_daily_pipeline_config(path)
    assert cfg.live_ai_safety.max_items_per_run >= 1


def test_news_accumulation_config_still_loads():
    cfg = load_news_accumulation_config("config/news_accumulation.yaml")
    assert cfg.min_items_per_run >= 0


def test_news_monitoring_config_still_loads():
    cfg = load_news_monitoring_config("config/news_monitoring.yaml")
    assert cfg.source_groups


def test_news_selection_config_still_loads():
    cfg = load_news_selection_config("config/news_selection.yaml")
    assert cfg.min_priority >= 0


@pytest.mark.parametrize("key", ["batch_size", "stop_on_timeout_count_above"])
def test_live_ai_safety_rejects_dead_keys(key):
    with pytest.raises(ValueError, match="has been removed"):
        DailyLiveAISafetyConfig.model_validate({key: 1})


@pytest.mark.parametrize("key", ["include_json", "include_markdown", "include_run_summary"])
def test_outputs_rejects_dead_keys(key):
    with pytest.raises(ValueError, match="has been removed"):
        DailyOutputsConfig.model_validate({key: True})


def test_safety_still_rejects_step_timeout_minutes():
    with pytest.raises(ValueError, match="step_timeout_minutes has been removed"):
        DailySafetyConfig.model_validate({"step_timeout_minutes": 20})


@pytest.mark.parametrize(
    "key", ["fail_on_missing_api_key_if_live_ai_enabled", "fail_on_macro_pipeline_failure"]
)
def test_safety_rejects_dead_keys(key):
    with pytest.raises(ValueError, match="has been removed"):
        DailySafetyConfig.model_validate({key: True})


@pytest.mark.parametrize(
    "key",
    [
        "enabled",
        "source_profile",
        "target_items_per_day",
        "max_items_per_day",
        "min_source_groups",
        "dedupe_across_runs",
        "retain_raw_items",
        "retain_classifications",
        "output_history_report",
    ],
)
def test_news_accumulation_rejects_dead_keys(key):
    with pytest.raises(ValueError, match="has been removed"):
        NewsAccumulationConfig.model_validate({key: True})


@pytest.mark.parametrize("key", ["max_retry_rate", "max_repair_rate", "max_failure_rate"])
def test_accumulation_quality_thresholds_rejects_dead_keys(key):
    with pytest.raises(ValueError, match="has been removed"):
        AccumulationQualityThresholds.model_validate({key: 0.1})


def test_news_monitoring_source_group_rejects_target_item_count():
    with pytest.raises(ValueError, match="has been removed"):
        NewsMonitoringSourceGroup.model_validate({"group_id": "x", "target_item_count": 5})


@pytest.mark.parametrize("key", ["warn_old_items", "warn_short_body"])
def test_news_monitoring_freshness_rules_rejects_dead_keys(key):
    with pytest.raises(ValueError, match="has been removed"):
        NewsMonitoringFreshnessRules.model_validate({key: True})


def test_news_monitoring_config_rejects_duplicate_handling():
    with pytest.raises(ValueError, match="has been removed"):
        NewsMonitoringConfig.model_validate(
            {
                "source_groups": [{"group_id": "macro_general"}],
                "duplicate_handling": {"content_hash_dedupe": True},
            }
        )


def test_news_monitoring_quality_thresholds_rejects_min_body_length():
    with pytest.raises(ValueError, match="has been removed"):
        NewsMonitoringQualityThresholds.model_validate({"min_body_length": 25})


def test_news_selection_rejects_daily_cap():
    with pytest.raises(ValueError, match="has been removed"):
        NewsSelectionConfig.model_validate({"daily_cap": 120})


def test_min_items_per_run_wired_to_this_runs_new_items_not_whole_store():
    """The old defect: raw_item_count == new_unique_items == the whole store,
    so min_items_per_run could never fire. Now it compares against
    new_items_this_run (from news_source_runs)."""
    config = NewsAccumulationConfig(min_items_per_run=5)
    # A big store (100 items) but THIS RUN only brought in 2 new ones.
    news_items = pd.DataFrame(
        [
            {
                "news_id": f"n{i}",
                "source": "reuters",
                "content_hash": f"h{i}",
                "published_at": datetime(2026, 6, 1, tzinfo=UTC),
            }
            for i in range(100)
        ]
    )
    frame = _accumulation_run_frame(
        config=config,
        news_items=news_items,
        classifications=pd.DataFrame(),
        run_date=datetime(2026, 6, 1, tzinfo=UTC).date(),
        created_at=datetime.now(UTC),
        new_items_this_run=2,
    )
    row = frame.iloc[0]
    assert row["raw_item_count"] == 100
    assert row["new_items_this_run"] == 2
    assert row["quality_status"] == "warning"
    assert "new items this run below configured minimum" in row["warning_json"]


def test_latest_run_new_items_is_none_without_telemetry():
    assert _latest_run_new_items(pd.DataFrame()) is None
