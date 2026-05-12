from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from macro_engine.config.schemas import (
    DimensionConfig,
    LoadedConfig,
    ModelConfig,
    RegimeConfig,
    SourceConfig,
)


def _read_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def load_model_config(path: str | Path = "config/model.yaml") -> ModelConfig:
    return ModelConfig.model_validate(_read_yaml(path)["model"])


def load_dimensions_config(
    path: str | Path = "config/dimensions.yaml",
) -> dict[str, DimensionConfig]:
    data = _read_yaml(path)
    return {
        name: DimensionConfig.model_validate(value)
        for name, value in data.get("dimensions", {}).items()
    }


def load_sources_config(path: str | Path = "config/sources.yaml") -> list[SourceConfig]:
    data = _read_yaml(path)
    return [SourceConfig.model_validate(item) for item in data.get("sources", [])]


def load_regimes_config(path: str | Path = "config/regimes.yaml") -> dict[str, RegimeConfig]:
    data = _read_yaml(path)
    return {
        name: RegimeConfig.model_validate(value)
        for name, value in data.get("regimes", {}).items()
    }


def load_all_configs(config_dir: str | Path = "config") -> LoadedConfig:
    config_path = Path(config_dir)
    loaded = LoadedConfig(
        model=load_model_config(config_path / "model.yaml"),
        dimensions=load_dimensions_config(config_path / "dimensions.yaml"),
        sources=load_sources_config(config_path / "sources.yaml"),
        regimes=load_regimes_config(config_path / "regimes.yaml"),
    )
    validate_loaded_config(loaded)
    return loaded


def validate_loaded_config(config: LoadedConfig) -> None:
    dimension_names = set(config.dimensions)

    feature_ids = [source.feature_id for source in config.sources]
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError("source feature_id values must be unique")

    for source in config.sources:
        if source.dimension not in dimension_names:
            raise ValueError(f"source {source.feature_id} references unknown dimension")
        if source.weight == 0 and config.dimensions[source.dimension].required_for_regime:
            if source.enabled and source.required:
                raise ValueError(f"required core source {source.feature_id} cannot have zero weight")

    for regime_name, regime in config.regimes.items():
        for dimension in regime.scoring:
            if dimension not in dimension_names:
                raise ValueError(f"regime {regime_name} references unknown dimension {dimension}")
            if not config.dimensions[dimension].required_for_regime:
                raise ValueError(
                    f"regime {regime_name} cannot score context dimension {dimension} in Phase A"
                )

    for dimension_name, dimension in config.dimensions.items():
        if not dimension.required_for_regime:
            continue
        required_weight = sum(
            source.weight
            for source in config.sources
            if source.dimension == dimension_name and source.required and source.enabled
        )
        if required_weight < dimension.min_required_weight:
            raise ValueError(
                f"core dimension {dimension_name} has required weight {required_weight:.2f}, "
                f"below minimum {dimension.min_required_weight:.2f}"
            )
