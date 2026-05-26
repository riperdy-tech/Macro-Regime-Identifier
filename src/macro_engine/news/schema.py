from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ALLOWED_IMPACT_DIRECTIONS = {"tailwind", "headwind", "mixed", "neutral", "unclear"}
ALLOWED_THEME_DIRECTIONS = {"positive", "negative", "mixed", "neutral", "unclear"}
ALLOWED_TIME_HORIZONS = {"immediate", "short_term", "medium_term", "long_term", "unclear"}
ALLOWED_ENTITY_TYPES = {
    "company",
    "country",
    "central_bank",
    "commodity",
    "sector",
    "region",
    "person",
    "other",
}


class NewsItem(BaseModel):
    news_id: str
    source: str
    source_url: str | None = None
    title: str
    body: str
    published_at: datetime | None = None
    ingested_at: datetime
    provider: str
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str


class MacroThemeSignal(BaseModel):
    theme_id: str
    direction: Literal["positive", "negative", "mixed", "neutral", "unclear"]
    severity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    time_horizon: Literal["immediate", "short_term", "medium_term", "long_term", "unclear"]
    rationale: str = ""


class SectorImpactSignal(BaseModel):
    sector_id: str
    impact_direction: Literal["tailwind", "headwind", "mixed", "neutral", "unclear"]
    impact_score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""


class NewsEntity(BaseModel):
    name: str
    entity_type: Literal[
        "company",
        "country",
        "central_bank",
        "commodity",
        "sector",
        "region",
        "person",
        "other",
    ]
    relevance: float = Field(ge=0.0, le=1.0)


class NewsClassificationPayload(BaseModel):
    summary: str
    macro_themes: list[MacroThemeSignal] = Field(default_factory=list)
    sector_impacts: list[SectorImpactSignal] = Field(default_factory=list)
    entities: list[NewsEntity] = Field(default_factory=list)
    secular_theme: str | None = None
    overall_severity: float = Field(ge=0.0, le=1.0)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    time_horizon: Literal["immediate", "short_term", "medium_term", "long_term", "unclear"]

    @field_validator("summary")
    @classmethod
    def summary_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary is required")
        return value.strip()


class NewsClassificationRecord(BaseModel):
    classification_id: str
    news_id: str
    classified_at: datetime
    ai_provider: str
    ai_model: str
    macro_themes: list[dict[str, Any]]
    sector_impacts: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    secular_theme: str | None = None
    time_horizon: str | None
    severity: float | None
    confidence: float | None
    summary: str | None
    raw_ai_response: dict[str, Any]
    classification_status: str
    error_message: str | None = None
