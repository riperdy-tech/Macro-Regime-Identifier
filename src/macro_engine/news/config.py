from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
import yaml

from macro_engine.news.schema import (
    ALLOWED_IMPACT_DIRECTIONS,
    ALLOWED_THEME_DIRECTIONS,
    ALLOWED_TIME_HORIZONS,
)


class NewsSourceDefinition(BaseModel):
    source_id: str
    provider: Literal["local_csv", "local_json", "manual_text"]
    enabled: bool = True
    path: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_provider_fields(self):
        if self.provider in {"local_csv", "local_json"} and not self.path:
            raise ValueError(f"{self.provider} source {self.source_id} requires path")
        if self.provider == "manual_text" and not self.items:
            raise ValueError(f"manual_text source {self.source_id} requires items")
        return self


class NewsSourcesConfig(BaseModel):
    news_sources: list[NewsSourceDefinition]


class NewsThemeDefinition(BaseModel):
    theme_id: str
    label: str
    enabled: bool = True


class NewsThemesConfig(BaseModel):
    macro_themes: list[NewsThemeDefinition]
    sector_ids: list[str]
    sector_impact_directions: list[str] = Field(
        default_factory=lambda: sorted(ALLOWED_IMPACT_DIRECTIONS)
    )
    theme_directions: list[str] = Field(default_factory=lambda: sorted(ALLOWED_THEME_DIRECTIONS))
    time_horizons: list[str] = Field(default_factory=lambda: sorted(ALLOWED_TIME_HORIZONS))

    @property
    def active_theme_ids(self) -> set[str]:
        return {theme.theme_id for theme in self.macro_themes if theme.enabled}

    @model_validator(mode="after")
    def validate_taxonomy(self):
        theme_ids = [theme.theme_id for theme in self.macro_themes]
        duplicates = {theme_id for theme_id in theme_ids if theme_ids.count(theme_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate news theme ids: {sorted(duplicates)}")
        if not self.active_theme_ids:
            raise ValueError("at least one active news theme is required")
        if not self.sector_ids:
            raise ValueError("at least one sector_id is required")
        unknown_impacts = set(self.sector_impact_directions) - ALLOWED_IMPACT_DIRECTIONS
        if unknown_impacts:
            raise ValueError(f"unknown sector impact directions: {sorted(unknown_impacts)}")
        unknown_theme_directions = set(self.theme_directions) - ALLOWED_THEME_DIRECTIONS
        if unknown_theme_directions:
            raise ValueError(f"unknown theme directions: {sorted(unknown_theme_directions)}")
        unknown_horizons = set(self.time_horizons) - ALLOWED_TIME_HORIZONS
        if unknown_horizons:
            raise ValueError(f"unknown time horizons: {sorted(unknown_horizons)}")
        return self


class NewsAIConfig(BaseModel):
    provider: Literal["deepseek", "mock"] = "deepseek"
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1600, gt=0)
    api_key_env: str = "DEEPSEEK_API_KEY"
    enable_live_ai: bool = False
    mock_mode: bool = True
    request_timeout_seconds: int = Field(default=60, gt=0)
    output_dir: str = "outputs"


def load_news_sources_config(path: str | Path = "config/news_sources.yaml") -> NewsSourcesConfig:
    return NewsSourcesConfig.model_validate(_load_yaml(path))


def load_news_themes_config(path: str | Path = "config/news_themes.yaml") -> NewsThemesConfig:
    return NewsThemesConfig.model_validate(_load_yaml(path))


def load_news_ai_config(path: str | Path = "config/news_ai.yaml") -> NewsAIConfig:
    data = _load_yaml(path)
    payload = data.get("ai", data)
    return NewsAIConfig.model_validate(payload)


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
