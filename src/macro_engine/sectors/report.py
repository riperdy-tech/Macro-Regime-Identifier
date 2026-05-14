from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from macro_engine.reports.config import load_report_config
from macro_engine.sectors.config import SectorConfig, load_sector_config
from macro_engine.storage.duckdb_store import DuckDBStore

SECTOR_DISCLAIMER = (
    "This sector ranking is an experimental macro diagnostic. It is not investment "
    "advice and does not provide trading, allocation, portfolio sizing, or security "
    "selection guidance. Proxy tickers are reporting references only."
)


def write_current_sector_report(
    *,
    config_path: str | Path = "config/phase_b_sources.yaml",
    sector_config_path: str | Path = "config/sectors.yaml",
    exposure_config_path: str | Path = "config/sector_exposures.yaml",
    prior_config_path: str | Path = "config/sector_regime_priors.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> tuple[Path, Path]:
    report_config = load_report_config(config_path)
    sector_config = load_sector_config(
        macro_config_path=config_path,
        sector_config_path=sector_config_path,
        exposure_config_path=exposure_config_path,
        prior_config_path=prior_config_path,
    )
    store = DuckDBStore(db_path)
    payload = build_current_sector_report(
        sector_scores=store.read_table("sector_scores"),
        components=store.read_table("sector_score_components"),
        health=store.read_table("sector_health"),
        config=sector_config,
        max_contributors=report_config.max_contributors,
    )
    markdown = current_sector_report_markdown(payload)
    output_dir = Path(report_config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "current_sector_ranking.json"
    markdown_path = output_dir / "current_sector_ranking.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def build_current_sector_report(
    *,
    sector_scores: pd.DataFrame,
    components: pd.DataFrame,
    health: pd.DataFrame,
    config: SectorConfig,
    max_contributors: int = 5,
) -> dict[str, Any]:
    if sector_scores.empty:
        return {
            "valid": False,
            "reason": "no_sector_scores",
            "disclaimer": SECTOR_DISCLAIMER,
        }
    scores = sector_scores.copy()
    scores["date"] = pd.to_datetime(scores["date"], errors="coerce")
    valid_scores = scores[scores["valid"]].sort_values(["date", "rank"])
    if valid_scores.empty:
        return {
            "valid": False,
            "reason": "no_valid_sector_scores",
            "disclaimer": SECTOR_DISCLAIMER,
        }
    latest_date = valid_scores["date"].max()
    latest_scores = valid_scores[valid_scores["date"] == latest_date].sort_values("rank")
    latest_components = components.copy()
    if not latest_components.empty:
        latest_components["date"] = pd.to_datetime(latest_components["date"], errors="coerce")
        latest_components = latest_components[latest_components["date"] == latest_date]
    latest_health = health.copy()
    if not latest_health.empty:
        latest_health["date"] = pd.to_datetime(latest_health["date"], errors="coerce")
        latest_health = latest_health[latest_health["date"] == latest_date]
    sector_lookup = {sector.sector_id: sector for sector in config.sectors}
    ranking = [
        _sector_rank_record(row, sector_lookup, latest_components, max_contributors)
        for row in latest_scores.to_dict(orient="records")
    ]
    top_supported = [
        item for item in ranking if item["confidence_adjusted_score"] is not None
        and item["confidence_adjusted_score"] > 0
    ][:max_contributors]
    top_pressured = sorted(
        [
            item for item in ranking if item["confidence_adjusted_score"] is not None
            and item["confidence_adjusted_score"] < 0
        ],
        key=lambda item: (item["confidence_adjusted_score"], item["sector_id"]),
    )[:max_contributors]
    macro_confidence = _to_float(latest_scores.iloc[0]["macro_confidence"])
    warnings = []
    if macro_confidence is None or macro_confidence < 0.05:
        warnings.append(
            "Macro confidence is low; sector ranking should be treated as a weak diagnostic signal."
        )
    invalid_health = latest_health[~latest_health["valid"]] if not latest_health.empty else pd.DataFrame()
    warnings.extend(
        f"Sector {row['sector_id']} invalid: {row['reason']}"
        for row in invalid_health.to_dict(orient="records")
    )
    latest = latest_scores.iloc[0]
    return _json_safe(
        {
            "valid": True,
            "date": str(latest_date.date()),
            "reported_macro_regime": latest["macro_reported_regime"],
            "raw_macro_leader": latest["macro_raw_dominant_regime"],
            "macro_confidence": macro_confidence,
            "sector_ranking": ranking,
            "top_macro_supported_sectors": top_supported,
            "top_macro_pressured_sectors": top_pressured,
            "warnings": warnings,
            "disclaimer": SECTOR_DISCLAIMER,
        }
    )


def current_sector_report_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("valid"):
        return f"# Current Sector Macro Ranking\n\nNo valid sector ranking.\n\n{payload['disclaimer']}\n"
    ranking = "\n".join(
        "- {rank}. {label} ({sector_id}): adjusted {adjusted:.3f}, raw {raw:.3f}".format(
            rank=item["rank"],
            label=item["label"],
            sector_id=item["sector_id"],
            adjusted=item["confidence_adjusted_score"],
            raw=item["raw_sector_score"],
        )
        for item in payload["sector_ranking"]
    )
    supported = "\n".join(
        f"- {item['label']}: positive macro tailwind score {item['confidence_adjusted_score']:.3f}"
        for item in payload["top_macro_supported_sectors"]
    ) or "- None"
    pressured = "\n".join(
        f"- {item['label']}: negative macro sensitivity score {item['confidence_adjusted_score']:.3f}"
        for item in payload["top_macro_pressured_sectors"]
    ) or "- None"
    warnings = "\n".join(f"- {warning}" for warning in payload["warnings"]) or "- None"
    explanations = "\n\n".join(
        _sector_explanation_markdown(item) for item in payload["top_macro_supported_sectors"]
    )
    return f"""# Current Sector Macro Ranking

Date: {payload["date"]}
Reported macro regime: {payload["reported_macro_regime"]}
Raw macro leader: {payload["raw_macro_leader"]}
Macro confidence: {payload["macro_confidence"]:.3f}

## Sector Ranking

{ranking}

## Top Macro-Supported Sectors

{supported}

## Top Macro-Pressured Sectors

{pressured}

## Top Sector Explanations

{explanations}

## Warnings

{warnings}

{payload["disclaimer"]}
"""


def _sector_rank_record(
    row: dict,
    sector_lookup: dict[str, Any],
    components: pd.DataFrame,
    max_contributors: int,
) -> dict[str, Any]:
    sector_id = row["sector_id"]
    sector = sector_lookup[sector_id]
    sector_components = components[components["sector_id"] == sector_id].copy()
    valid_components = sector_components[sector_components["valid"]].copy()
    supporting = valid_components[valid_components["contribution"] > 0].sort_values(
        "contribution",
        ascending=False,
    )
    opposing = valid_components[valid_components["contribution"] < 0].sort_values(
        "contribution"
    )
    return {
        "sector_id": sector_id,
        "label": sector.label,
        "proxy_ticker": sector.proxy_ticker,
        "rank": int(row["rank"]),
        "raw_sector_score": _to_float(row["raw_sector_score"]),
        "confidence_adjusted_score": _to_float(row["confidence_adjusted_score"]),
        "macro_reported_regime": row["macro_reported_regime"],
        "macro_raw_dominant_regime": row["macro_raw_dominant_regime"],
        "macro_confidence": _to_float(row["macro_confidence"]),
        "top_supporting_components": _component_records(supporting.head(max_contributors)),
        "top_opposing_components": _component_records(opposing.head(max_contributors)),
    }


def _component_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "component_type": row["component_type"],
            "component_id": row["component_id"],
            "input_value": _to_float(row["input_value"]),
            "weight_or_exposure": _to_float(row["weight_or_exposure"]),
            "contribution": _to_float(row["contribution"]),
            "reason": row["reason"],
        }
        for row in frame.to_dict(orient="records")
    ]


def _sector_explanation_markdown(item: dict[str, Any]) -> str:
    supporting = "\n".join(
        "- {component_id} ({component_type}) contributed {contribution:.3f}".format(**component)
        for component in item["top_supporting_components"]
    ) or "- None"
    opposing = "\n".join(
        "- {component_id} ({component_type}) contributed {contribution:.3f}".format(**component)
        for component in item["top_opposing_components"]
    ) or "- None"
    return f"""### {item["label"]}

This sector has a macro diagnostic score of {item["confidence_adjusted_score"]:.3f}.

Supporting components:
{supporting}

Opposing components:
{opposing}
"""


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _to_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
