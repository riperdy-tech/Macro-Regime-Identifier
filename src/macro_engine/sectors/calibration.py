from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field
import yaml

from macro_engine.sectors.config import SectorConfig, load_sector_config
from macro_engine.sectors.scoring import SectorBuildResult, build_sector_scores
from macro_engine.sectors.validation import (
    SectorValidationConfig,
    load_sector_validation_config,
    run_sector_validation,
)
from macro_engine.storage.duckdb_store import DuckDBStore


class SectorCalibrationExperiment(BaseModel):
    name: str = "v02_m1_sector_calibration"
    macro_config: str = "config/phase_b_sources.yaml"
    sector_config: str = "config/sectors.yaml"
    exposure_config: str = "config/sector_exposures.yaml"
    prior_config: str = "config/sector_regime_priors.yaml"
    validation_config: str = "config/sector_validation.yaml"
    output_dir: str = "outputs/experiments/v02_m1_sector_calibration"
    primary_horizon: str = "3m"
    secondary_horizon: str = "1m"


class SectorCalibrationVariant(BaseModel):
    variant_id: str
    description: str
    regime_prior_weight: float = 1.0
    dimension_exposure_weight: float = 1.0
    exposure_multipliers: dict[str, dict[str, float]] = Field(default_factory=dict)
    exposure_overrides: dict[str, dict[str, float]] = Field(default_factory=dict)
    prior_multipliers: dict[str, dict[str, float]] = Field(default_factory=dict)
    prior_overrides: dict[str, dict[str, float]] = Field(default_factory=dict)


class SectorCalibrationConfig(BaseModel):
    experiment: SectorCalibrationExperiment = SectorCalibrationExperiment()
    variants: list[SectorCalibrationVariant]


@dataclass(frozen=True)
class SectorCalibrationResult:
    output_dir: Path
    variant_results: list[dict[str, Any]]
    comparison: pd.DataFrame
    markdown_path: Path


def load_sector_calibration_config(
    path: str | Path = "config/experiments/sector_calibration_v02_m1.yaml",
) -> SectorCalibrationConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    config = SectorCalibrationConfig.model_validate(data)
    if not config.variants:
        raise ValueError("at least one sector calibration variant is required")
    variant_ids = [variant.variant_id for variant in config.variants]
    duplicates = {variant_id for variant_id in variant_ids if variant_ids.count(variant_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate sector calibration variant_id values: {sorted(duplicates)}")
    return config


def run_sector_calibration_experiments(
    *,
    experiment_config_path: str | Path = "config/experiments/sector_calibration_v02_m1.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> SectorCalibrationResult:
    calibration_config = load_sector_calibration_config(experiment_config_path)
    experiment = calibration_config.experiment
    base_config = load_sector_config(
        macro_config_path=experiment.macro_config,
        sector_config_path=experiment.sector_config,
        exposure_config_path=experiment.exposure_config,
        prior_config_path=experiment.prior_config,
    )
    validation_config = load_sector_validation_config(experiment.validation_config)
    store = DuckDBStore(db_path)
    store.initialize()
    prices = store.read_sector_proxy_prices()
    macro_inputs = {
        "regime_scores": store.read_table("regime_scores"),
        "regime_health": store.read_table("regime_health"),
        "dimension_scores": store.read_table("dimension_scores"),
        "timeline": store.read_table("historical_regime_timeline"),
    }
    output_dir = Path(experiment.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    variant_results: list[dict[str, Any]] = []
    for variant in calibration_config.variants:
        variant_config = _variant_sector_config(base_config, variant)
        build_result = build_sector_scores(config=variant_config, **macro_inputs)
        validation_result = run_sector_validation(
            sector_scores=build_result.sector_scores,
            prices=prices,
            config=validation_config,
        )
        result = _variant_result(
            variant=variant,
            build_result=build_result,
            validation_config=validation_config,
            validation_summary=validation_result.summary,
            validation_returns=validation_result.returns,
        )
        variant_results.append(result)
        _write_json(output_dir / f"{variant.variant_id}.json", result)

    comparison = _comparison_frame(
        variant_results,
        primary_horizon=experiment.primary_horizon,
        secondary_horizon=experiment.secondary_horizon,
    )
    _write_json(output_dir / "comparison.json", comparison.to_dict(orient="records"))
    markdown = _comparison_markdown(calibration_config, variant_results, comparison)
    markdown_path = output_dir / "comparison.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    return SectorCalibrationResult(
        output_dir=output_dir,
        variant_results=variant_results,
        comparison=comparison,
        markdown_path=markdown_path,
    )


def _variant_sector_config(
    base_config: SectorConfig,
    variant: SectorCalibrationVariant,
) -> SectorConfig:
    exposures = deepcopy(base_config.exposures)
    priors = deepcopy(base_config.regime_priors)

    for sector_id, dimension_map in exposures.items():
        for dimension_id, value in dimension_map.items():
            exposures[sector_id][dimension_id] = value * variant.dimension_exposure_weight
    for regime_id, sector_map in priors.items():
        for sector_id, value in sector_map.items():
            priors[regime_id][sector_id] = value * variant.regime_prior_weight

    _apply_nested_multipliers(exposures, variant.exposure_multipliers, "exposure_multipliers")
    _apply_nested_overrides(exposures, variant.exposure_overrides, "exposure_overrides")
    _apply_nested_multipliers(priors, variant.prior_multipliers, "prior_multipliers")
    _apply_nested_overrides(priors, variant.prior_overrides, "prior_overrides")

    return SectorConfig(
        sectors=base_config.sectors,
        exposures=exposures,
        regime_priors=priors,
        scoring=base_config.scoring,
    )


def _apply_nested_multipliers(
    target: dict[str, dict[str, float]],
    multipliers: dict[str, dict[str, float]],
    label: str,
) -> None:
    for outer_key, inner in multipliers.items():
        if outer_key not in target:
            raise ValueError(f"{label} references unknown key {outer_key}")
        for inner_key, multiplier in inner.items():
            if inner_key not in target[outer_key]:
                raise ValueError(f"{label}.{outer_key} references unknown key {inner_key}")
            target[outer_key][inner_key] *= multiplier


def _apply_nested_overrides(
    target: dict[str, dict[str, float]],
    overrides: dict[str, dict[str, float]],
    label: str,
) -> None:
    for outer_key, inner in overrides.items():
        if outer_key not in target:
            raise ValueError(f"{label} references unknown key {outer_key}")
        for inner_key, value in inner.items():
            if inner_key not in target[outer_key]:
                raise ValueError(f"{label}.{outer_key} references unknown key {inner_key}")
            target[outer_key][inner_key] = value


def _variant_result(
    *,
    variant: SectorCalibrationVariant,
    build_result: SectorBuildResult,
    validation_config: SectorValidationConfig,
    validation_summary: pd.DataFrame,
    validation_returns: pd.DataFrame,
) -> dict[str, Any]:
    scores = build_result.sector_scores.copy()
    components = build_result.components.copy()
    valid_scores = scores[scores["valid"]].copy()
    valid_returns = validation_returns[validation_returns["valid"]].copy()
    return _json_safe(
        {
            "variant_id": variant.variant_id,
            "description": variant.description,
            "settings": variant.model_dump(),
            "validation": {
                "summary": validation_summary.to_dict(orient="records"),
                "valid_observation_count": int(len(valid_returns)),
                "date_range": _date_range(valid_returns, "score_date"),
                "benchmark_ticker": validation_config.benchmark_ticker,
            },
            "sector_ranking_stability": _ranking_stability(valid_scores),
            "score_dispersion": _score_dispersion(valid_scores),
            "component_dominance": _component_dominance(components),
            "top_sector_distribution": _rank_distribution(valid_scores, rank=1),
            "bottom_sector_distribution": _rank_distribution(
                valid_scores,
                rank=int(valid_scores["rank"].max()) if not valid_scores.empty else 0,
            ),
            "sector_level_validation": _sector_level_validation(valid_returns),
        }
    )


def _comparison_frame(
    variant_results: list[dict[str, Any]],
    *,
    primary_horizon: str,
    secondary_horizon: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for result in variant_results:
        summary_by_horizon = {
            row["horizon"]: row for row in result["validation"]["summary"]
        }
        primary = summary_by_horizon.get(primary_horizon, {})
        secondary = summary_by_horizon.get(secondary_horizon, {})
        rows.append(
            {
                "variant_id": result["variant_id"],
                "description": result["description"],
                "primary_horizon": primary_horizon,
                "primary_rank_ic": primary.get("rank_ic_spearman"),
                "primary_top_minus_bottom_spread": primary.get("top_minus_bottom_spread"),
                "primary_hit_rate_top_positive": primary.get("hit_rate_top_positive"),
                "secondary_horizon": secondary_horizon,
                "secondary_rank_ic": secondary.get("rank_ic_spearman"),
                "secondary_top_minus_bottom_spread": secondary.get("top_minus_bottom_spread"),
                "secondary_hit_rate_top_positive": secondary.get("hit_rate_top_positive"),
                "observation_count": result["validation"]["valid_observation_count"],
                "average_score_dispersion": result["score_dispersion"]["average_cross_section_std"],
                "average_component_dominance": result["component_dominance"]["average_max_component_share"],
                "always_top_sectors": result["top_sector_distribution"]["always_ranked_sectors"],
                "always_bottom_sectors": result["bottom_sector_distribution"]["always_ranked_sectors"],
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values(
            ["primary_rank_ic", "primary_top_minus_bottom_spread", "secondary_rank_ic"],
            ascending=[False, False, False],
            na_position="last",
        ).reset_index(drop=True)
    return frame


def _ranking_stability(scores: pd.DataFrame) -> dict[str, Any]:
    if scores.empty:
        return {"date_count": 0, "average_monthly_rank_change": None}
    frame = scores.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    pivot = frame.pivot_table(index="date", columns="sector_id", values="rank", aggfunc="last")
    changes = pivot.sort_index().diff().abs()
    return {
        "date_count": int(len(pivot)),
        "average_monthly_rank_change": None
        if changes.empty
        else float(changes.stack().mean()),
    }


def _score_dispersion(scores: pd.DataFrame) -> dict[str, Any]:
    if scores.empty:
        return {"average_cross_section_std": None, "max_abs_adjusted_score": None}
    grouped = scores.groupby("date")["confidence_adjusted_score"]
    return {
        "average_cross_section_std": float(grouped.std().mean()),
        "max_abs_adjusted_score": float(scores["confidence_adjusted_score"].abs().max()),
    }


def _component_dominance(components: pd.DataFrame) -> dict[str, Any]:
    valid = components[components["valid"]].copy()
    if valid.empty:
        return {
            "average_max_component_share": None,
            "max_component_share": None,
            "largest_average_abs_components": [],
        }
    valid["abs_contribution"] = valid["contribution"].abs()
    totals = valid.groupby(["sector_id", "date"])["abs_contribution"].transform("sum")
    valid["component_share"] = valid["abs_contribution"] / totals.where(totals != 0)
    largest = (
        valid.groupby(["component_type", "component_id"])["abs_contribution"]
        .mean()
        .reset_index()
        .sort_values("abs_contribution", ascending=False)
        .head(8)
    )
    return {
        "average_max_component_share": float(
            valid.groupby(["sector_id", "date"])["component_share"].max().mean()
        ),
        "max_component_share": float(valid["component_share"].max()),
        "largest_average_abs_components": largest.to_dict(orient="records"),
    }


def _rank_distribution(scores: pd.DataFrame, *, rank: int) -> dict[str, Any]:
    if scores.empty or rank <= 0:
        return {"distribution": {}, "always_ranked_sectors": []}
    date_count = int(scores["date"].nunique())
    ranked = scores[scores["rank"] == rank]
    counts = ranked["sector_id"].value_counts().sort_index()
    distribution = {sector: int(count) for sector, count in counts.items()}
    always = sorted([sector for sector, count in distribution.items() if count == date_count])
    return {
        "distribution": distribution,
        "always_ranked_sectors": always,
    }


def _sector_level_validation(returns: pd.DataFrame) -> list[dict[str, Any]]:
    if returns.empty:
        return []
    valid = returns[returns["valid"]].copy()
    rows: list[dict[str, Any]] = []
    for sector_id, group in valid.groupby("sector_id"):
        row = {
            "sector_id": sector_id,
            "rows": int(len(group)),
            "average_score": float(group["sector_score"].mean()),
            "average_adjusted_score": float(group["confidence_adjusted_score"].mean()),
        }
        for horizon in [1, 3]:
            column = f"relative_forward_{horizon}m_return"
            if column in group:
                horizon_group = group[group[column].notna()]
                row[f"average_relative_{horizon}m_return"] = None if horizon_group.empty else float(horizon_group[column].mean())
                row[f"hit_rate_{horizon}m"] = None if horizon_group.empty else float((horizon_group[column] > 0).mean())
        rows.append(row)
    return sorted(rows, key=lambda item: item["average_adjusted_score"], reverse=True)


def _date_range(frame: pd.DataFrame, column: str) -> dict[str, str | None]:
    if frame.empty or column not in frame:
        return {"start": None, "end": None}
    dates = pd.to_datetime(frame[column], errors="coerce").dropna()
    if dates.empty:
        return {"start": None, "end": None}
    return {"start": str(dates.min().date()), "end": str(dates.max().date())}


def _comparison_markdown(
    config: SectorCalibrationConfig,
    variant_results: list[dict[str, Any]],
    comparison: pd.DataFrame,
) -> str:
    if comparison.empty:
        table = "No comparison rows generated."
    else:
        table = _markdown_table(
            comparison[
            [
                "variant_id",
                "primary_rank_ic",
                "primary_top_minus_bottom_spread",
                "secondary_rank_ic",
                "secondary_top_minus_bottom_spread",
                "average_component_dominance",
            ]
            ]
        )
    best = None if comparison.empty else comparison.iloc[0]["variant_id"]
    return f"""# v0.2-M1 Sector Calibration Comparison

Mode: diagnostic validation, not a trading backtest.

Primary horizon: {config.experiment.primary_horizon}
Secondary horizon: {config.experiment.secondary_horizon}
Best-ranked variant by configured metrics: {best or "n/a"}

{table}

This comparison uses sector ETF proxies only as validation references. It is not investment advice, trading guidance, allocation guidance, portfolio sizing guidance, or a security recommendation.
"""


def _markdown_table(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for row in frame.to_dict(orient="records"):
        values = [_format_markdown_value(row.get(column)) for column in columns]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator, *rows])


def _format_markdown_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2, sort_keys=True), encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except ValueError:
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
