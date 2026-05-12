from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from macro_engine.features.config import FeatureDefinition, load_feature_config

Polarity = Literal["positive", "negative"]


class DimensionFeatureConfig(BaseModel):
    feature_id: str
    weight: float = Field(gt=0)
    polarity: Polarity


class DimensionDefinition(BaseModel):
    dimension_id: str
    enabled: bool = True
    required_for_regime: bool = True
    min_valid_features: int = Field(ge=1)
    min_coverage_ratio: float = Field(ge=0, le=1)
    features: list[DimensionFeatureConfig]

    @model_validator(mode="after")
    def has_features(self) -> DimensionDefinition:
        if not self.features:
            raise ValueError(f"dimension {self.dimension_id} must define at least one feature")
        feature_ids = [feature.feature_id for feature in self.features]
        duplicates = {feature_id for feature_id in feature_ids if feature_ids.count(feature_id) > 1}
        if duplicates:
            raise ValueError(
                f"dimension {self.dimension_id} has duplicate feature_id values: {sorted(duplicates)}"
            )
        return self


class DimensionConfig(BaseModel):
    features: list[FeatureDefinition]
    dimensions: list[DimensionDefinition]


def load_dimension_config(path: str | Path) -> DimensionConfig:
    feature_config = load_feature_config(path)
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    dimensions = [
        DimensionDefinition.model_validate(item) for item in data.get("dimensions", [])
    ]
    _validate_dimensions(dimensions, feature_config.features)
    return DimensionConfig(features=feature_config.features, dimensions=dimensions)


def _validate_dimensions(
    dimensions: list[DimensionDefinition],
    features: list[FeatureDefinition],
) -> None:
    dimension_ids = [dimension.dimension_id for dimension in dimensions]
    duplicates = {
        dimension_id for dimension_id in dimension_ids if dimension_ids.count(dimension_id) > 1
    }
    if duplicates:
        raise ValueError(f"duplicate dimension_id values: {sorted(duplicates)}")

    feature_ids = {feature.feature_id for feature in features}
    for dimension in dimensions:
        for feature in dimension.features:
            if feature.feature_id not in feature_ids:
                raise ValueError(
                    f"dimension {dimension.dimension_id} references unknown feature_id "
                    f"{feature.feature_id}"
                )
