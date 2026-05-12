from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from macro_engine.ingest.registry import load_ingestion_sources
from macro_engine.ingest.schemas import Frequency, IngestionSource

TransformName = Literal["level", "diff_3m", "diff_6m", "diff_12m", "yoy_pct_change", "spread"]
NormalizationName = Literal[
    "none",
    "rolling_z_3y",
    "rolling_z_5y",
    "rolling_z_10y",
    "expanding_z",
]


class FeatureDefinition(BaseModel):
    feature_id: str
    series_id: str
    transform: TransformName
    normalization: NormalizationName
    direction: str
    enabled: bool = True
    reason_disabled: str | None = None
    min_observations: int = Field(default=24, gt=0)

    @model_validator(mode="after")
    def disabled_has_reason(self) -> FeatureDefinition:
        if not self.enabled and not self.reason_disabled:
            raise ValueError(f"disabled feature {self.feature_id} must include reason_disabled")
        return self


class FeatureConfig(BaseModel):
    sources: list[IngestionSource]
    features: list[FeatureDefinition]


def load_feature_config(path: str | Path) -> FeatureConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    sources = load_ingestion_sources(path)
    features = [FeatureDefinition.model_validate(item) for item in data.get("features", [])]
    _validate_feature_config(sources, features)
    return FeatureConfig(sources=sources, features=features)


def source_frequency_by_id(sources: list[IngestionSource]) -> dict[str, Frequency]:
    return {source.series_id: source.frequency for source in sources}


def enabled_source_ids(sources: list[IngestionSource]) -> set[str]:
    return {source.series_id for source in sources if source.enabled}


def _validate_feature_config(
    sources: list[IngestionSource],
    features: list[FeatureDefinition],
) -> None:
    feature_ids = [feature.feature_id for feature in features]
    duplicates = {feature_id for feature_id in feature_ids if feature_ids.count(feature_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate feature_id values: {sorted(duplicates)}")

    source_ids = {source.series_id for source in sources}
    for feature in features:
        if feature.series_id not in source_ids:
            raise ValueError(
                f"feature {feature.feature_id} references unknown series {feature.series_id}"
            )
