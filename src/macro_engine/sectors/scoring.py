from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from macro_engine.sectors.config import SectorConfig, SectorDefinition


@dataclass(frozen=True)
class SectorBuildResult:
    sector_scores: pd.DataFrame
    components: pd.DataFrame
    sector_health: pd.DataFrame


def build_sector_scores(
    *,
    regime_scores: pd.DataFrame,
    regime_health: pd.DataFrame,
    dimension_scores: pd.DataFrame,
    timeline: pd.DataFrame,
    config: SectorConfig,
) -> SectorBuildResult:
    macro_dates = _macro_date_frame(regime_health, timeline)
    regime_frame = regime_scores.copy()
    dimension_frame = dimension_scores.copy()
    if not regime_frame.empty:
        regime_frame["date"] = pd.to_datetime(regime_frame["date"], errors="coerce")
    if not dimension_frame.empty:
        dimension_frame["date"] = pd.to_datetime(dimension_frame["date"], errors="coerce")

    component_rows: list[dict] = []
    score_rows: list[dict] = []
    health_rows: list[dict] = []

    active_sectors = [sector for sector in config.sectors if sector.enabled]
    for macro_row in macro_dates.to_dict(orient="records"):
        date = pd.Timestamp(macro_row["date"])
        date_regimes = regime_frame[
            (regime_frame["date"] == date)
            & (regime_frame["valid"])
            & (regime_frame["probability"].notna())
        ]
        date_dimensions = dimension_frame[
            (dimension_frame["date"] == date)
            & (dimension_frame["valid"])
            & (dimension_frame["score"].notna())
        ]
        date_component_rows: list[dict] = []
        date_score_rows: list[dict] = []
        for sector in active_sectors:
            sector_components = _sector_components(
                sector=sector,
                date=date,
                regime_scores=date_regimes,
                dimension_scores=date_dimensions,
                config=config,
            )
            date_component_rows.extend(sector_components)
            score_row = _sector_score_row(
                sector=sector,
                date=date,
                macro_row=macro_row,
                components=sector_components,
                config=config,
            )
            date_score_rows.append(score_row)
            health_rows.append(_sector_health_row(score_row, sector_components))
        _rank_sector_rows(date_score_rows)
        component_rows.extend(date_component_rows)
        score_rows.extend(date_score_rows)

    return SectorBuildResult(
        sector_scores=pd.DataFrame(score_rows, columns=_score_columns()),
        components=pd.DataFrame(component_rows, columns=_component_columns()),
        sector_health=pd.DataFrame(health_rows, columns=_health_columns()),
    )


def _macro_date_frame(regime_health: pd.DataFrame, timeline: pd.DataFrame) -> pd.DataFrame:
    if not timeline.empty:
        frame = timeline.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        valid = frame[frame["valid"]].copy()
        if not valid.empty:
            return pd.DataFrame(
                [
                    {
                        "date": row["date"],
                        "macro_reported_regime": row.get("reported_regime")
                        or row.get("dominant_regime"),
                        "macro_raw_dominant_regime": row.get("raw_dominant_regime")
                        or row.get("dominant_regime"),
                        "macro_confidence": row.get("raw_confidence")
                        if pd.notna(row.get("raw_confidence"))
                        else row.get("confidence"),
                    }
                    for row in valid.sort_values("date").to_dict(orient="records")
                ]
            )
    if regime_health.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "macro_reported_regime",
                "macro_raw_dominant_regime",
                "macro_confidence",
            ]
        )
    frame = regime_health.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    valid = frame[frame["valid"]].copy()
    return pd.DataFrame(
        [
            {
                "date": row["date"],
                "macro_reported_regime": row.get("dominant_regime"),
                "macro_raw_dominant_regime": row.get("dominant_regime"),
                "macro_confidence": row.get("confidence"),
            }
            for row in valid.sort_values("date").to_dict(orient="records")
        ]
    )


def _sector_components(
    *,
    sector: SectorDefinition,
    date: pd.Timestamp,
    regime_scores: pd.DataFrame,
    dimension_scores: pd.DataFrame,
    config: SectorConfig,
) -> list[dict]:
    rows: list[dict] = []
    for regime_id, priors in config.regime_priors.items():
        value_row = regime_scores[regime_scores["regime_id"] == regime_id]
        prior = priors.get(sector.sector_id, 0.0)
        if value_row.empty:
            rows.append(
                _component_row(
                    sector.sector_id,
                    date,
                    "regime_prior",
                    regime_id,
                    None,
                    prior,
                    0.0,
                    False,
                    "missing_regime_probability",
                )
            )
            continue
        probability = float(value_row.iloc[0]["probability"])
        rows.append(
            _component_row(
                sector.sector_id,
                date,
                "regime_prior",
                regime_id,
                probability,
                prior,
                probability * prior,
                True,
                "ok",
            )
        )

    for dimension_id, exposure in config.exposures[sector.sector_id].items():
        value_row = dimension_scores[dimension_scores["dimension_id"] == dimension_id]
        if value_row.empty:
            rows.append(
                _component_row(
                    sector.sector_id,
                    date,
                    "dimension_exposure",
                    dimension_id,
                    None,
                    exposure,
                    0.0,
                    False,
                    "missing_dimension_score",
                )
            )
            continue
        score = float(value_row.iloc[0]["score"])
        rows.append(
            _component_row(
                sector.sector_id,
                date,
                "dimension_exposure",
                dimension_id,
                score,
                exposure,
                score * exposure,
                True,
                "ok",
            )
        )
    return rows


def _sector_score_row(
    *,
    sector: SectorDefinition,
    date: pd.Timestamp,
    macro_row: dict,
    components: list[dict],
    config: SectorConfig,
) -> dict:
    valid_components = [component for component in components if component["valid"]]
    missing_components = [
        f"{component['component_type']}:{component['component_id']}"
        for component in components
        if not component["valid"]
    ]
    valid = not missing_components
    reason = "ok" if valid else "missing_macro_components"
    regime_prior_score = sum(
        component["contribution"]
        for component in valid_components
        if component["component_type"] == "regime_prior"
    )
    dimension_exposure_score = sum(
        component["contribution"]
        for component in valid_components
        if component["component_type"] == "dimension_exposure"
    )
    raw_score = regime_prior_score + dimension_exposure_score if valid else None
    macro_confidence = _optional_float(macro_row.get("macro_confidence"))
    multiplier = _confidence_multiplier(macro_confidence, config)
    adjusted = None if raw_score is None else raw_score * multiplier
    return {
        "sector_id": sector.sector_id,
        "date": date.date(),
        "raw_sector_score": raw_score,
        "confidence_adjusted_score": adjusted,
        "rank": None,
        "macro_reported_regime": macro_row.get("macro_reported_regime"),
        "macro_raw_dominant_regime": macro_row.get("macro_raw_dominant_regime"),
        "macro_confidence": macro_confidence,
        "valid": valid,
        "reason": reason,
    }


def _rank_sector_rows(rows: list[dict]) -> None:
    valid_rows = [row for row in rows if row["valid"] and row["confidence_adjusted_score"] is not None]
    ranked = sorted(
        valid_rows,
        key=lambda row: (-float(row["confidence_adjusted_score"]), row["sector_id"]),
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank


def _sector_health_row(score_row: dict, components: list[dict]) -> dict:
    missing = [
        f"{component['component_type']}:{component['component_id']}"
        for component in components
        if not component["valid"]
    ]
    warning_count = 1 if score_row["macro_confidence"] is not None and score_row["macro_confidence"] < 0.10 else 0
    return {
        "sector_id": score_row["sector_id"],
        "date": score_row["date"],
        "valid": score_row["valid"],
        "component_count": len(components),
        "missing_components": missing,
        "warning_count": warning_count,
        "reason": score_row["reason"] if score_row["valid"] else "missing_macro_components",
    }


def _confidence_multiplier(confidence: float | None, config: SectorConfig) -> float:
    if confidence is None:
        clamped = 0.0
    else:
        clamped = max(0.0, min(float(confidence), 1.0))
    span = config.scoring.max_multiplier - config.scoring.min_multiplier
    return config.scoring.min_multiplier + (span * clamped)


def _optional_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _component_row(
    sector_id: str,
    date: pd.Timestamp,
    component_type: str,
    component_id: str,
    input_value: float | None,
    weight_or_exposure: float,
    contribution: float,
    valid: bool,
    reason: str,
) -> dict:
    return {
        "sector_id": sector_id,
        "date": date.date(),
        "component_type": component_type,
        "component_id": component_id,
        "input_value": input_value,
        "weight_or_exposure": float(weight_or_exposure),
        "contribution": float(contribution),
        "valid": valid,
        "reason": reason,
    }


def _score_columns() -> list[str]:
    return [
        "sector_id",
        "date",
        "raw_sector_score",
        "confidence_adjusted_score",
        "rank",
        "macro_reported_regime",
        "macro_raw_dominant_regime",
        "macro_confidence",
        "valid",
        "reason",
    ]


def _component_columns() -> list[str]:
    return [
        "sector_id",
        "date",
        "component_type",
        "component_id",
        "input_value",
        "weight_or_exposure",
        "contribution",
        "valid",
        "reason",
    ]


def _health_columns() -> list[str]:
    return [
        "sector_id",
        "date",
        "valid",
        "component_count",
        "missing_components",
        "warning_count",
        "reason",
    ]
