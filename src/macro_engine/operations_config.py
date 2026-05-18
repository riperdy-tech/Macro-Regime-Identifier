from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator
import yaml


class DailyMacroConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/phase_b_sources.yaml"
    mode: str = "live"


class DailySectorConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/phase_b_sources.yaml"
    sector_config_path: str = "config/sectors.yaml"
    exposure_config_path: str = "config/sector_exposures.yaml"
    prior_config_path: str = "config/sector_regime_priors.yaml"


class DailyNewsConfig(BaseModel):
    enabled: bool = True
    source_profile: str = "synthetic_sample"
    news_sources_config: str = "config/news_sources.yaml"
    news_ai_config: str = "config/news_ai.yaml"
    news_themes_config: str = "config/news_themes.yaml"
    news_scoring_config: str = "config/news_scoring.yaml"
    allow_live_ai: bool = False
    mock_mode_default: bool = True


class DailyCombinedConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/sector_news_integration.yaml"


class DailyMonitoringConfig(BaseModel):
    enabled: bool = True
    config_path: str = "config/news_monitoring.yaml"
    source_profile: str = "synthetic_sample"


class DailyOutputsConfig(BaseModel):
    archive_enabled: bool = True
    archive_root: str = "outputs/archive"
    include_json: bool = True
    include_markdown: bool = True
    include_run_summary: bool = True


class DailySafetyConfig(BaseModel):
    fail_on_guardrail_violation: bool = True
    fail_on_missing_api_key_if_live_ai_enabled: bool = True
    fail_on_macro_pipeline_failure: bool = True
    allow_success_with_warnings: bool = True


class DailyPipelineConfig(BaseModel):
    macro: DailyMacroConfig = Field(default_factory=DailyMacroConfig)
    sector: DailySectorConfig = Field(default_factory=DailySectorConfig)
    news: DailyNewsConfig = Field(default_factory=DailyNewsConfig)
    combined: DailyCombinedConfig = Field(default_factory=DailyCombinedConfig)
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
    max_retry_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    max_repair_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    max_failure_rate: float = Field(default=0.10, ge=0.0, le=1.0)


class NewsAccumulationConfig(BaseModel):
    enabled: bool = True
    source_profile: str = "synthetic_sample"
    min_items_per_run: int = Field(default=1, ge=0)
    target_items_per_day: int = Field(default=25, ge=0)
    max_items_per_day: int = Field(default=300, ge=1)
    min_source_count: int = Field(default=1, ge=0)
    min_source_groups: int = Field(default=1, ge=0)
    dedupe_across_runs: bool = True
    retain_raw_items: bool = True
    retain_classifications: bool = True
    output_history_report: bool = True
    output_dir: str = "outputs"
    quality_status_thresholds: AccumulationQualityThresholds = Field(
        default_factory=AccumulationQualityThresholds
    )

    @model_validator(mode="after")
    def validate_item_limits(self):
        if self.target_items_per_day > self.max_items_per_day:
            raise ValueError("target_items_per_day cannot exceed max_items_per_day")
        return self


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
