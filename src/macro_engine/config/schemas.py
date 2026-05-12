from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Frequency = Literal["daily", "weekly", "monthly", "quarterly", "annual"]
Transform = Literal[
    "level",
    "diff_1m",
    "diff_3m",
    "diff_6m",
    "change_12m",
    "yoy_pct_change",
    "mom_pct_change",
    "qoq_annualized_pct_change",
    "pct_change_1m",
    "pct_change_3m",
    "spread",
    "real_rate",
]
Normalization = Literal[
    "none",
    "rolling_z_3y",
    "rolling_z_5y",
    "rolling_z_10y",
    "expanding_z",
    "percentile_10y",
]
Direction = Literal["direct", "inverse"]
DimensionType = Literal["core", "context", "experimental"]
MissingPolicy = Literal["drop_feature", "neutralize", "fail_dimension"]
ScoringMode = Literal[
    "linear",
    "reward_positive",
    "reward_negative",
    "penalize_positive",
    "penalize_negative",
    "neutral_band",
]


class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    version: str
    geography: str
    base_frequency: Frequency
    historical_mode: str


class NormalizationConfig(BaseModel):
    default: Normalization
    minimum_observations: int = Field(gt=0)
    zscore_clip: float = Field(gt=0)
    bounded_transform: Literal["tanh"]
    bounded_transform_divisor: float = Field(gt=0)


class ClassificationConfig(BaseModel):
    method: str
    softmax_temperature: float = Field(gt=0)
    low_conviction_threshold: float = Field(ge=0, le=1)
    transition_zone_probability_gap: float = Field(ge=0, le=1)


class ConfidenceConfig(BaseModel):
    weights: dict[str, float]

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> ConfidenceConfig:
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"confidence weights must sum to 1.0, got {total}")
        return self


class FreshnessDefaults(BaseModel):
    stale_after_days: int = Field(gt=0)
    unusable_after_days: int = Field(gt=0)

    @model_validator(mode="after")
    def unusable_after_stale(self) -> FreshnessDefaults:
        if self.unusable_after_days < self.stale_after_days:
            raise ValueError("unusable_after_days must be >= stale_after_days")
        return self


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    version: str
    geography: str
    base_frequency: Frequency
    historical_mode: str
    normalization: NormalizationConfig
    classification: ClassificationConfig
    confidence: ConfidenceConfig
    freshness_defaults: dict[Frequency, FreshnessDefaults]


class DimensionConfig(BaseModel):
    label: str
    dimension_type: DimensionType
    required_for_regime: bool
    description: str
    positive_means: str
    negative_means: str
    min_required_weight: float = Field(ge=0, le=1)
    display_order: int = Field(ge=0)


class SourceConfig(BaseModel):
    feature_id: str
    series_id: str
    name: str
    provider: str
    dimension: str
    frequency: Frequency
    transform: Transform
    normalization: Normalization
    direction: Direction
    weight: float = Field(ge=0)
    source_tier: int = Field(ge=0, le=4)
    required: bool
    enabled: bool = True
    reason_disabled: str | None = None
    stale_after_days: int = Field(gt=0)
    unusable_after_days: int = Field(gt=0)
    max_staleness_days: int = Field(gt=0)
    allow_discontinued: bool = False
    min_history_years: int = Field(ge=0)
    missing_policy: MissingPolicy
    notes: str = ""

    @model_validator(mode="after")
    def disabled_sources_are_not_required(self) -> SourceConfig:
        if not self.enabled and self.required:
            raise ValueError(f"disabled source {self.feature_id} cannot be required")
        if self.unusable_after_days < self.stale_after_days:
            raise ValueError("unusable_after_days must be >= stale_after_days")
        return self


class RegimeDimensionRule(BaseModel):
    mode: ScoringMode
    weight: float = Field(ge=0)
    target: float | None = None


class DriverTemplates(BaseModel):
    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class RegimeConfig(BaseModel):
    label: str
    description: str
    scoring: dict[str, RegimeDimensionRule]
    driver_templates: DriverTemplates = Field(default_factory=DriverTemplates)


class LoadedConfig(BaseModel):
    model: ModelConfig
    dimensions: dict[str, DimensionConfig]
    sources: list[SourceConfig]
    regimes: dict[str, RegimeConfig]


class SourceHealthItem(BaseModel):
    feature_id: str
    series_id: str
    status: str
    last_observation: str | None
    used_in_score: bool
    freshness_score: float = Field(ge=0, le=1)
    reason: str | None = None


class SourceHealthReport(BaseModel):
    provider: str = "FRED"
    total_series: int
    available_series: int
    stale_series: int
    missing_series: int
    disabled_series: int
    required_series_missing: list[str]
    required_series_stale: list[str]
    items: list[SourceHealthItem]
