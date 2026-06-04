"""Sector exposure guardrails: anomaly flags only, never score overrides.

The LLM sector mapping is the primary engine. This module is a lightweight
sanity check: a hand-set matrix of well-established macro-theme -> sector sign
priors. When the LLM emits a high-confidence sector impact that CONTRADICTS the
matrix, an anomaly is flagged for review. Nothing here changes a score.

Used for: guardrails, anomaly detection, explanation checking. NOT for scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field
import yaml

# theme.direction -> sign; impact_direction -> sign; expected -> sign
_THEME_SIGN = {"positive": 1.0, "negative": -1.0}
_IMPACT_SIGN = {"tailwind": 1.0, "headwind": -1.0}
_EXPECTED_SIGN = {"positive": 1.0, "negative": -1.0}

_ANOMALY_COLUMNS = [
    "news_id",
    "theme_id",
    "sector_id",
    "theme_direction",
    "impact_direction",
    "expected_direction",
    "confidence",
    "reason",
]


class GuardrailExpectation(BaseModel):
    theme_id: str
    sector_id: str
    expected: Literal["positive", "negative"]


class SectorGuardrailConfig(BaseModel):
    min_confidence_to_flag: float = Field(default=0.6, ge=0.0, le=1.0)
    expectations: list[GuardrailExpectation] = Field(default_factory=list)

    def expectation_map(self) -> dict[tuple[str, str], float]:
        return {(e.theme_id, e.sector_id): _EXPECTED_SIGN[e.expected] for e in self.expectations}


@dataclass(frozen=True)
class SectorGuardrailResult:
    anomalies: pd.DataFrame
    checked_pairs: int
    json_path: Path | None = None
    markdown_path: Path | None = None


def load_sector_guardrail_config(
    path: str | Path = "config/sector_exposure_guardrails.yaml",
) -> SectorGuardrailConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    payload = data.get("sector_exposure_guardrails", data)
    return SectorGuardrailConfig.model_validate(payload)


# ---- pure check ------------------------------------------------------------


def check_sector_guardrails(
    theme_scores: pd.DataFrame,
    sector_impacts: pd.DataFrame,
    config: SectorGuardrailConfig,
) -> pd.DataFrame:
    """Flag LLM sector impacts that contradict the expectation matrix.

    For each article, for each (active theme, sector impact) pair whose
    (theme_id, sector_id) is in the matrix: the predicted sign is
    expected_sign * theme_direction_sign. If the observed impact sign is
    directional and opposite to the prediction, and confidence is high enough,
    it is flagged. Returns a DataFrame of anomalies (empty if none)."""
    expectations = config.expectation_map()
    if not expectations or theme_scores is None or theme_scores.empty:
        return pd.DataFrame(columns=_ANOMALY_COLUMNS)
    if sector_impacts is None or sector_impacts.empty:
        return pd.DataFrame(columns=_ANOMALY_COLUMNS)

    themes_by_news: dict[str, list[dict]] = {}
    for row in theme_scores.to_dict(orient="records"):
        themes_by_news.setdefault(str(row.get("news_id")), []).append(row)

    rows: list[dict] = []
    for impact in sector_impacts.to_dict(orient="records"):
        news_id = str(impact.get("news_id"))
        sector_id = str(impact.get("sector_id"))
        observed = _IMPACT_SIGN.get(str(impact.get("impact_direction") or "").lower(), 0.0)
        confidence = _to_float(impact.get("confidence"))
        if observed == 0.0 or confidence < config.min_confidence_to_flag:
            continue
        for theme in themes_by_news.get(news_id, []):
            theme_id = str(theme.get("theme_id"))
            key = (theme_id, sector_id)
            if key not in expectations:
                continue
            theme_sign = _THEME_SIGN.get(str(theme.get("direction") or "").lower(), 0.0)
            if theme_sign == 0.0:
                continue
            predicted = expectations[key] * theme_sign
            if observed != predicted:
                rows.append(
                    {
                        "news_id": news_id,
                        "theme_id": theme_id,
                        "sector_id": sector_id,
                        "theme_direction": theme.get("direction"),
                        "impact_direction": impact.get("impact_direction"),
                        "expected_direction": "tailwind" if predicted > 0 else "headwind",
                        "confidence": confidence,
                        "reason": "llm_sector_sign_contradicts_guardrail_matrix",
                    }
                )
    return pd.DataFrame(rows, columns=_ANOMALY_COLUMNS)


def _to_float(value) -> float:
    try:
        if value is None or pd.isna(value):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ---- service ---------------------------------------------------------------


def run_sector_guardrail_check(
    *,
    config_path: str | Path = "config/sector_exposure_guardrails.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
    output_dir: str | Path = "outputs",
) -> SectorGuardrailResult:
    from macro_engine.storage.duckdb_store import DuckDBStore

    config = load_sector_guardrail_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    theme_scores = store.read_table("news_theme_scores")
    sector_impacts = store.read_table("news_sector_impacts")
    anomalies = check_sector_guardrails(theme_scores, sector_impacts, config)

    checked = 0
    if not theme_scores.empty and not sector_impacts.empty:
        checked = int(len(sector_impacts))

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = _write_reports(out_dir, anomalies, checked)
    return SectorGuardrailResult(
        anomalies=anomalies,
        checked_pairs=checked,
        json_path=json_path,
        markdown_path=markdown_path,
    )


def _write_reports(out_dir: Path, anomalies: pd.DataFrame, checked: int) -> tuple[Path, Path]:
    import json

    payload = {
        "checked_sector_impacts": checked,
        "anomaly_count": int(len(anomalies)),
        "anomalies": anomalies.to_dict(orient="records"),
        "notes": (
            "Anomaly flags only - no score override applied. Flags mark LLM "
            "sector calls that contradict established macro-theme priors and "
            "warrant explanation review. Diagnostic, not trading guidance."
        ),
    }
    json_path = out_dir / "sector_exposure_guardrails.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = [
        "# Sector Exposure Guardrails",
        "",
        f"Checked sector impacts: {checked}  |  Anomalies: {len(anomalies)}",
        "",
        "Anomaly flags only - no score override. Flags mark LLM sector calls "
        "that contradict established macro-theme priors.",
        "",
    ]
    if anomalies.empty:
        lines.append("No guardrail anomalies.")
    else:
        lines.append("| News | Theme | Sector | Theme Dir | LLM Impact | Expected | Conf |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in anomalies.to_dict(orient="records"):
            lines.append(
                "| {n} | {t} | {s} | {td} | {imp} | {exp} | {c:.2f} |".format(
                    n=row["news_id"], t=row["theme_id"], s=row["sector_id"],
                    td=row["theme_direction"], imp=row["impact_direction"],
                    exp=row["expected_direction"], c=float(row["confidence"]),
                )
            )
    markdown_path = out_dir / "sector_exposure_guardrails.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path
