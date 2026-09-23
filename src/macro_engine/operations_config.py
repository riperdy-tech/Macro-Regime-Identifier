from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator
import yaml


class DailyMacroConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/phase_b_sources.yaml"
    mode: str = "live"
    vintage_budget_minutes: float = 6.0


class DailySectorConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/phase_b_sources.yaml"
    sector_config_path: str = "config/sectors.yaml"
    exposure_config_path: str = "config/sector_exposures.yaml"
    prior_config_path: str = "config/sector_regime_priors.yaml"
    # C1 (MRI_S1_APPROVAL.md S6): sector validation now runs inside the daily sector step,
    # before the ranking report is written, so `current_sector_ranking.json`'s `validation`
    # block always describes the scores it was just built from -- not the previous run's.
    validation_config_path: str = "config/sector_validation.yaml"


class DailyNewsConfig(BaseModel):
    enabled: bool = True
    source_profile: str = "synthetic_sample"
    news_sources_config: str = "config/news_sources.yaml"
    news_ai_config: str = "config/news_ai.yaml"
    news_themes_config: str = "config/news_themes.yaml"
    news_scoring_config: str = "config/news_scoring.yaml"
    # N1.7: was hard-coded "config/news_selection.yaml" in daily.py.
    news_selection_config: str = "config/news_selection.yaml"
    allow_live_ai: bool = False
    mock_mode_default: bool = True
    # N1.4: durable news history (export/hydrate). None disables both steps --
    # existing configs that do not set this key are unaffected.
    history_dir: str | None = None


class DailyLiveAISafetyConfig(BaseModel):
    # The single spend cap for live classification (N1.7 removed
    # news_selection.daily_cap, which never bound anything tighter than this).
    # ~60/day x 30 ~= $1.2/month at flash rates for the mock/local 25-cap
    # config; the live 60-cap config is ~half that per the config comment
    # (unverified against an actual DeepSeek bill -- OA-3).
    max_items_per_run: int = Field(default=25, ge=1)
    classify_only_unclassified: bool = True
    continue_on_individual_failure: bool = True
    stop_on_failure_rate_above: float = Field(default=0.20, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def reject_dead_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("batch_size", "stop_on_timeout_count_above"):
                if key in data:
                    raise ValueError(
                        f"live_ai_safety.{key} has been removed (N1.7: unread by any code "
                        "path). No replacement is needed."
                    )
        return data


class DailyCombinedConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/sector_news_integration.yaml"


class DailyAdvisoryBlockConfig(BaseModel):
    enabled: bool = True
    # Non-fatal by default: the advisory block is an additive artifact and must never
    # be able to fail the daily diagnostic.
    required: bool = False
    config_path: str = "config/sector_news_integration.yaml"


class DailyAnchorsConfig(BaseModel):
    """Capital-market anchors (v0.2) — additive artifacts, never a pipeline blocker."""

    enabled: bool = True
    required: bool = False
    config_path: str = "config/anchors.yaml"
    macro_config_path: str = "config/phase_b_sources.yaml"
    sector_config_path: str = "config/sectors.yaml"
    advisory_block: DailyAdvisoryBlockConfig = Field(default_factory=DailyAdvisoryBlockConfig)


class DailyMonitoringConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/news_monitoring.yaml"
    source_profile: str = "synthetic_sample"


class DailyOutputsConfig(BaseModel):
    archive_enabled: bool = True
    archive_root: str = "outputs/archive"

    @model_validator(mode="before")
    @classmethod
    def reject_dead_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("include_json", "include_markdown", "include_run_summary"):
                if key in data:
                    raise ValueError(
                        f"outputs.{key} has been removed (N1.7: unread by any code path; "
                        "both files are always written). No replacement is needed."
                    )
        return data


class DailySafetyConfig(BaseModel):
    # Code default 20 minutes (down from 60): only the two GitHub configs rely on the default.
    # config/daily_pipeline.yaml and _replay_news_only.yaml set their timeout explicitly.
    overall_run_timeout_minutes: float = Field(default=20.0, ge=1.0)
    post_classification_reserve_minutes: float = Field(default=3.0, ge=0.0)
    fail_on_guardrail_violation: bool = True
    allow_success_with_warnings: bool = True

    @model_validator(mode="before")
    @classmethod
    def reject_dead_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and "step_timeout_minutes" in data:
            raise ValueError(
                "step_timeout_minutes has been removed (synchronous steps cannot be "
                "interrupted in-process). Use overall_run_timeout_minutes for run deadline "
                "and macro.vintage_budget_minutes / post_classification_reserve_minutes instead."
            )
        if isinstance(data, dict) and "fail_on_missing_api_key_if_live_ai_enabled" in data:
            raise ValueError(
                "safety.fail_on_missing_api_key_if_live_ai_enabled has been removed (N1.7: "
                "unread by any code path). No replacement is needed."
            )
        if isinstance(data, dict) and "fail_on_macro_pipeline_failure" in data:
            raise ValueError(
                "safety.fail_on_macro_pipeline_failure has been removed (N1.7: the macro step "
                "is hard-coded fail=True in daily.py; this flag was never read)."
            )
        return data


class DailyPipelineConfig(BaseModel):
    macro: DailyMacroConfig = Field(default_factory=DailyMacroConfig)
    sector: DailySectorConfig = Field(default_factory=DailySectorConfig)
    news: DailyNewsConfig = Field(default_factory=DailyNewsConfig)
    live_ai_safety: DailyLiveAISafetyConfig = Field(default_factory=DailyLiveAISafetyConfig)
    combined: DailyCombinedConfig = Field(default_factory=DailyCombinedConfig)
    anchors: DailyAnchorsConfig = Field(default_factory=DailyAnchorsConfig)
    monitoring: DailyMonitoringConfig = Field(default_factory=DailyMonitoringConfig)
    outputs: DailyOutputsConfig = Field(default_factory=DailyOutputsConfig)
    safety: DailySafetyConfig = Field(default_factory=DailySafetyConfig)

    @model_validator(mode="after")
    def validate_enabled_sections(self):
        if not any(
            [
                self.macro.enabled,
                self.sector.enabled,
                self.news.enabled,
                self.combined.enabled,
                self.monitoring.enabled,
            ]
        ):
            raise ValueError("at least one daily pipeline section must be enabled")
        return self


class AccumulationQualityThresholds(BaseModel):
    min_success_rate: float = Field(default=0.90, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def reject_dead_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("max_retry_rate", "max_repair_rate", "max_failure_rate"):
                if key in data:
                    raise ValueError(
                        f"news_accumulation.quality_status_thresholds.{key} has been removed "
                        "(N1.7: unread by any code path; news/config.py's "
                        "NewsMonitoringQualityThresholds is the equivalent that is actually "
                        "read). No replacement is needed."
                    )
        return data


class NewsAccumulationConfig(BaseModel):
    # N1.5/N1.7: compared against this run's new items (news_source_runs),
    # not the whole store -- min_items_per_run could never fire before.
    min_items_per_run: int = Field(default=1, ge=0)
    min_source_count: int = Field(default=1, ge=0)
    output_dir: str = "outputs"
    quality_status_thresholds: AccumulationQualityThresholds = Field(
        default_factory=AccumulationQualityThresholds
    )

    @model_validator(mode="before")
    @classmethod
    def reject_dead_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in (
                "enabled",
                "source_profile",
                "target_items_per_day",
                "max_items_per_day",
                "min_source_groups",
                "dedupe_across_runs",
                "retain_raw_items",
                "retain_classifications",
                "output_history_report",
            ):
                if key in data:
                    raise ValueError(
                        f"news_accumulation.{key} has been removed (N1.7: unread by any code "
                        "path). No replacement is needed."
                    )
        return data


def load_daily_pipeline_config(path: str | Path = "config/daily_pipeline.yaml") -> DailyPipelineConfig:
    data = _load_yaml(path)
    payload = data.get("daily_pipeline", data)
    return DailyPipelineConfig.model_validate(payload)


def load_news_accumulation_config(
    path: str | Path = "config/news_accumulation.yaml",
) -> NewsAccumulationConfig:
    data = _load_yaml(path)
    payload = data.get("news_accumulation", data)
    return NewsAccumulationConfig.model_validate(payload)


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
