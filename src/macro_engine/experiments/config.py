from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from macro_engine.regimes.config import RegimeDimensionConfig, RegimePolarity


class InteractionComponent(BaseModel):
    dimension_id: str
    polarity: RegimePolarity


class InteractionConfig(BaseModel):
    interaction_id: str
    weight: float = Field(gt=0)
    components: list[InteractionComponent]

    @model_validator(mode="after")
    def has_components(self) -> InteractionConfig:
        if len(self.components) < 2:
            raise ValueError(f"interaction {self.interaction_id} needs at least two components")
        return self


class RegimeExperimentOverride(BaseModel):
    dimensions: list[RegimeDimensionConfig] | None = None
    interactions: list[InteractionConfig] = Field(default_factory=list)


class ExperimentVariant(BaseModel):
    variant_id: str
    description: str
    softmax_temperature: float = Field(gt=0)
    regime_overrides: dict[str, RegimeExperimentOverride] = Field(default_factory=dict)


class ExperimentSettings(BaseModel):
    name: str = "phase_l"
    output_dir: str = "outputs/experiments/phase_l"
    base_config: str = "config/phase_b_sources.yaml"


class CalibrationExperimentConfig(BaseModel):
    experiment: ExperimentSettings
    variants: list[ExperimentVariant]

    @model_validator(mode="after")
    def unique_variants(self) -> CalibrationExperimentConfig:
        variant_ids = [variant.variant_id for variant in self.variants]
        duplicates = {
            variant_id for variant_id in variant_ids if variant_ids.count(variant_id) > 1
        }
        if duplicates:
            raise ValueError(f"duplicate variant_id values: {sorted(duplicates)}")
        return self


def load_calibration_experiment_config(
    path: str | Path = "config/experiments/phase_l.yaml",
) -> CalibrationExperimentConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return CalibrationExperimentConfig.model_validate(data)

