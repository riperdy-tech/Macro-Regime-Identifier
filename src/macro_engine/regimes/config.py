from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from macro_engine.dimensions.config import DimensionDefinition, load_dimension_config

RegimePolarity = Literal[
    "positive",
    "negative",
    "positive_only",
    "negative_only",
    "penalize_positive_only",
    "penalize_negative_only",
    "reward_near_zero",
]


class RegimeDimensionConfig(BaseModel):
    dimension_id: str
    weight: float = Field(gt=0)
    polarity: RegimePolarity


class RegimeDefinition(BaseModel):
    regime_id: str
    enabled: bool = True
    min_valid_dimensions: int = Field(ge=1)
    min_coverage_ratio: float = Field(ge=0, le=1)
    dimensions: list[RegimeDimensionConfig]

    @model_validator(mode="after")
    def has_unique_dimensions(self) -> RegimeDefinition:
        if not self.dimensions:
            raise ValueError(f"regime {self.regime_id} must define at least one dimension")
        dimension_ids = [dimension.dimension_id for dimension in self.dimensions]
        duplicates = {
            dimension_id for dimension_id in dimension_ids if dimension_ids.count(dimension_id) > 1
        }
        if duplicates:
            raise ValueError(
                f"regime {self.regime_id} has duplicate dimension_id values: {sorted(duplicates)}"
            )
        return self


class RegimeScoringConfig(BaseModel):
    probability_method: Literal["softmax"] = "softmax"
    softmax_temperature: float = Field(gt=0)


class RegimeConfig(BaseModel):
    dimensions: list[DimensionDefinition]
    regimes: list[RegimeDefinition]
    scoring: RegimeScoringConfig


def load_regime_config(path: str | Path) -> RegimeConfig:
    dimension_config = load_dimension_config(path)
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    regimes = [RegimeDefinition.model_validate(item) for item in data.get("regimes", [])]
    scoring = RegimeScoringConfig.model_validate(data.get("regime_scoring", {}))
    _validate_regimes(regimes, dimension_config.dimensions)
    return RegimeConfig(
        dimensions=dimension_config.dimensions,
        regimes=regimes,
        scoring=scoring,
    )


def _validate_regimes(
    regimes: list[RegimeDefinition],
    dimensions: list[DimensionDefinition],
) -> None:
    regime_ids = [regime.regime_id for regime in regimes]
    duplicates = {regime_id for regime_id in regime_ids if regime_ids.count(regime_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate regime_id values: {sorted(duplicates)}")

    dimension_ids = {dimension.dimension_id for dimension in dimensions}
    for regime in regimes:
        for dimension in regime.dimensions:
            if dimension.dimension_id not in dimension_ids:
                raise ValueError(
                    f"regime {regime.regime_id} references unknown dimension_id "
                    f"{dimension.dimension_id}"
                )
