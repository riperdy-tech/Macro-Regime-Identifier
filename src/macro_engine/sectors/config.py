from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
import yaml

from macro_engine.regimes.config import load_regime_config


class SectorDefinition(BaseModel):
    sector_id: str
    label: str
    proxy_ticker: str | None = None
    enabled: bool = True
    # S1.5 (P0_0 §1.3.2): set only on the six sub-industries carved from a parent GICS
    # sector (`config/sector_exposures.yaml` comments name the same parents). None for the
    # 11 GICS-level sectors themselves. Used to rank the two cross-sections separately
    # instead of pooling parents and sub-industries into one 17-row ranking.
    parent_sector_id: str | None = None


class SectorScoringConfig(BaseModel):
    # S1.2 (P0_0 §2.5): min_multiplier/max_multiplier are deleted, not renamed. The clean
    # S0 store measured the confidence*peakedness product at mean 0.197 / median 0.152, with
    # the 2026-09-01 value 0.0226 -- so the 0.40 floor alone was setting the live sector
    # quota table's multiplier on 73%+ of days. `extra="forbid"` makes a config that still
    # sets either key fail loudly instead of the key being silently ignored.
    model_config = ConfigDict(extra="forbid")


class SectorConfig(BaseModel):
    sectors: list[SectorDefinition]
    exposures: dict[str, dict[str, float]]
    regime_priors: dict[str, dict[str, float]]
    scoring: SectorScoringConfig = SectorScoringConfig()


def load_sector_config(
    *,
    macro_config_path: str | Path = "config/phase_b_sources.yaml",
    sector_config_path: str | Path = "config/sectors.yaml",
    exposure_config_path: str | Path = "config/sector_exposures.yaml",
    prior_config_path: str | Path = "config/sector_regime_priors.yaml",
) -> SectorConfig:
    macro_config = load_regime_config(macro_config_path)
    sector_data = _load_yaml(sector_config_path)
    exposure_data = _load_yaml(exposure_config_path)
    prior_data = _load_yaml(prior_config_path)

    sectors = [
        SectorDefinition.model_validate(item)
        for item in sector_data.get("sectors", [])
    ]
    exposures = _coerce_nested_float_map(
        exposure_data.get("sector_exposures", {}),
        "sector_exposures",
    )
    priors = _coerce_nested_float_map(
        prior_data.get("sector_regime_priors", {}),
        "sector_regime_priors",
    )
    scoring = SectorScoringConfig.model_validate(
        sector_data.get("sector_scoring", {})
        | exposure_data.get("sector_scoring", {})
        | prior_data.get("sector_scoring", {})
    )
    config = SectorConfig(
        sectors=sectors,
        exposures=exposures,
        regime_priors=priors,
        scoring=scoring,
    )
    _validate_sector_config(
        config,
        known_dimensions={dimension.dimension_id for dimension in macro_config.dimensions},
        known_regimes={regime.regime_id for regime in macro_config.regimes},
    )
    return config


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _coerce_nested_float_map(data: Any, label: str) -> dict[str, dict[str, float]]:
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping")
    coerced: dict[str, dict[str, float]] = {}
    for outer_key, inner in data.items():
        if not isinstance(inner, dict):
            raise ValueError(f"{label}.{outer_key} must be a mapping")
        coerced[str(outer_key)] = {}
        for inner_key, value in inner.items():
            if not isinstance(value, int | float):
                raise ValueError(f"{label}.{outer_key}.{inner_key} must be numeric")
            coerced[str(outer_key)][str(inner_key)] = float(value)
    return coerced


def _validate_sector_config(
    config: SectorConfig,
    *,
    known_dimensions: set[str],
    known_regimes: set[str],
) -> None:
    if not config.sectors:
        raise ValueError("at least one sector is required")
    sector_ids = [sector.sector_id for sector in config.sectors]
    duplicates = {sector_id for sector_id in sector_ids if sector_ids.count(sector_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate sector_id values: {sorted(duplicates)}")
    active_sector_ids = {sector.sector_id for sector in config.sectors if sector.enabled}
    if not active_sector_ids:
        raise ValueError("at least one active sector is required")

    for sector in config.sectors:
        if sector.parent_sector_id is None:
            continue
        if sector.parent_sector_id == sector.sector_id:
            raise ValueError(f"sector {sector.sector_id} cannot be its own parent_sector_id")
        if sector.parent_sector_id not in sector_ids:
            raise ValueError(
                f"sector {sector.sector_id} has unknown parent_sector_id "
                f"{sector.parent_sector_id!r}"
            )

    exposure_sector_ids = set(config.exposures)
    missing_exposures = sorted(active_sector_ids - exposure_sector_ids)
    if missing_exposures:
        raise ValueError(f"missing sector exposure rows: {missing_exposures}")
    unknown_exposure_sectors = sorted(exposure_sector_ids - set(sector_ids))
    if unknown_exposure_sectors:
        raise ValueError(f"unknown sectors in exposures: {unknown_exposure_sectors}")

    for sector_id, exposures in config.exposures.items():
        unknown_dimensions = sorted(set(exposures) - known_dimensions)
        if unknown_dimensions:
            raise ValueError(
                f"sector {sector_id} references unknown dimension_id values: "
                f"{unknown_dimensions}"
            )

    prior_regimes = set(config.regime_priors)
    unknown_regimes = sorted(prior_regimes - known_regimes)
    if unknown_regimes:
        raise ValueError(f"unknown regime_id values in sector priors: {unknown_regimes}")
    missing_regimes = sorted(known_regimes - prior_regimes)
    if missing_regimes:
        raise ValueError(f"missing sector priors for regimes: {missing_regimes}")

    for regime_id, priors in config.regime_priors.items():
        unknown_prior_sectors = sorted(set(priors) - set(sector_ids))
        if unknown_prior_sectors:
            raise ValueError(
                f"regime {regime_id} references unknown sector_id values: "
                f"{unknown_prior_sectors}"
            )
