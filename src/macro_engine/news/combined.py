from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from macro_engine.news.config import (
    SectorNewsIntegrationConfig,
    load_sector_news_integration_config,
)
from macro_engine.storage.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class CombinedSectorDiagnosticResult:
    diagnostics: pd.DataFrame
    components: pd.DataFrame


def build_stored_combined_sector_diagnostics(
    *,
    config_path: str | Path = "config/sector_news_integration.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> CombinedSectorDiagnosticResult:
    config = load_sector_news_integration_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    result = build_combined_sector_diagnostics(
        sector_scores=store.read_table("sector_scores"),
        daily_news_scores=store.read_table("news_daily_sector_scores"),
        weekly_news_scores=store.read_table("news_weekly_sector_scores"),
        config=config,
    )
    store.replace_combined_sector_outputs(result.diagnostics, result.components)
    return result


def build_combined_sector_diagnostics(
    *,
    sector_scores: pd.DataFrame,
    daily_news_scores: pd.DataFrame,
    weekly_news_scores: pd.DataFrame,
    config: SectorNewsIntegrationConfig,
) -> CombinedSectorDiagnosticResult:
    if sector_scores.empty:
        return CombinedSectorDiagnosticResult(_diagnostic_frame(), _component_frame())
    macro = sector_scores.copy()
    macro["date"] = pd.to_datetime(macro["date"], errors="coerce")
    macro = macro[macro["valid"]].copy()
    if macro.empty:
        return CombinedSectorDiagnosticResult(_diagnostic_frame(), _component_frame())

    news = _news_frame(daily_news_scores, weekly_news_scores, config)
    created_at = datetime.now(UTC)
    diagnostic_rows: list[dict] = []
    component_rows: list[dict] = []

    for news_date in _diagnostic_dates(macro, news):
        macro_date = macro[macro["date"] <= news_date]["date"].max()
        if pd.isna(macro_date):
            continue
        date_macro = macro[macro["date"] == macro_date].copy()
        date_news = _news_for_date(news, news_date, config)
        normalized_macro = _normalized_macro_scores(date_macro)
        date_rows: list[dict] = []
        for row in date_macro.sort_values("sector_id").to_dict(orient="records"):
            sector_id = row["sector_id"]
            macro_score = float(normalized_macro.get(sector_id, 0.0))
            news_row = date_news[date_news["sector_id"] == sector_id]
            news_score = 0.0
            news_item_count = 0
            news_confidence = 0.0
            if not news_row.empty:
                item = news_row.iloc[-1]
                news_score = _clip(
                    float(item["adjusted_news_score"]),
                    config.max_news_adjustment,
                )
                news_item_count = int(
                    item.get("positive_item_count", 0)
                    + item.get("negative_item_count", 0)
                    + item.get("neutral_item_count", 0)
                )
                news_confidence = _optional_float(item.get("avg_confidence")) or 0.0
            macro_weight, news_weight, penalty, news_reason = _effective_weights(
                config=config,
                news_item_count=news_item_count,
                news_confidence=news_confidence,
                has_news=not news_row.empty,
            )
            combined = (macro_weight * macro_score) + (news_weight * news_score) - penalty
            diagnostic_confidence = _diagnostic_confidence(
                macro_confidence=_optional_float(row.get("macro_confidence")),
                news_confidence=news_confidence,
                news_weight=news_weight,
            )
            date_rows.append(
                {
                    "diagnostic_date": news_date.date(),
                    "sector_id": sector_id,
                    "sector_macro_score": macro_score,
                    "sector_news_score": news_score,
                    "combined_score": combined,
                    "macro_component_weight": macro_weight,
                    "news_component_weight": news_weight,
                    "news_item_count": news_item_count,
                    "news_confidence": news_confidence,
                    "diagnostic_confidence": diagnostic_confidence,
                    "rank": None,
                    "created_at": created_at,
                }
            )
            component_rows.extend(
                [
                    _component(
                        news_date,
                        sector_id,
                        "normalized_sector_macro_score",
                        macro_score,
                        macro_weight,
                        "Cross-sectional normalized sector macro score.",
                    ),
                    _component(
                        news_date,
                        sector_id,
                        "bounded_sector_news_score",
                        news_score,
                        news_weight,
                        news_reason,
                    ),
                    _component(
                        news_date,
                        sector_id,
                        "news_uncertainty_penalty",
                        penalty,
                        1.0,
                        "Penalty applied when news confidence is low and news contributes.",
                    ),
                ]
            )
        _rank_rows(date_rows)
        diagnostic_rows.extend(date_rows)
    return CombinedSectorDiagnosticResult(
        pd.DataFrame(diagnostic_rows, columns=_diagnostic_columns()),
        pd.DataFrame(component_rows, columns=_component_columns()),
    )


def _news_frame(
    daily_news_scores: pd.DataFrame,
    weekly_news_scores: pd.DataFrame,
    config: SectorNewsIntegrationConfig,
) -> pd.DataFrame:
    if config.news_score_frequency == "weekly":
        frame = weekly_news_scores.copy()
        if frame.empty:
            return pd.DataFrame()
        frame["score_date"] = pd.to_datetime(frame["week_start_date"], errors="coerce")
        return frame
    frame = daily_news_scores.copy()
    if frame.empty:
        return pd.DataFrame()
    frame["score_date"] = pd.to_datetime(frame["score_date"], errors="coerce")
    return frame


def _diagnostic_dates(macro: pd.DataFrame, news: pd.DataFrame) -> pd.DatetimeIndex:
    if news.empty:
        latest_macro = macro["date"].max()
        return pd.DatetimeIndex([latest_macro]) if pd.notna(latest_macro) else pd.DatetimeIndex([])
    return pd.DatetimeIndex(sorted(news["score_date"].dropna().unique()))


def _news_for_date(
    news: pd.DataFrame,
    diagnostic_date: pd.Timestamp,
    config: SectorNewsIntegrationConfig,
) -> pd.DataFrame:
    if news.empty:
        return news
    candidates = news[news["score_date"] <= diagnostic_date].copy()
    if candidates.empty:
        return candidates
    candidates["age_days"] = (diagnostic_date - candidates["score_date"]).dt.days
    candidates = candidates[candidates["age_days"] <= config.news_score_decay_days]
    if config.require_recent_news and candidates.empty:
        return candidates
    latest_by_sector = candidates.sort_values(["sector_id", "score_date"]).groupby(
        "sector_id",
        as_index=False,
    ).tail(1)
    return latest_by_sector


def _normalized_macro_scores(date_macro: pd.DataFrame) -> dict[str, float]:
    values = pd.to_numeric(date_macro["confidence_adjusted_score"], errors="coerce")
    mean = float(values.mean())
    std = float(values.std(ddof=0))
    if std == 0.0 or pd.isna(std):
        return {row["sector_id"]: 0.0 for row in date_macro.to_dict(orient="records")}
    return {
        row["sector_id"]: (float(row["confidence_adjusted_score"]) - mean) / std
        for row in date_macro.to_dict(orient="records")
    }


def _effective_weights(
    *,
    config: SectorNewsIntegrationConfig,
    news_item_count: int,
    news_confidence: float,
    has_news: bool,
) -> tuple[float, float, float, str]:
    total = config.macro_sector_weight + config.news_sector_weight
    macro_weight = config.macro_sector_weight / total
    news_weight = config.news_sector_weight / total
    if not has_news:
        return 1.0, 0.0, 0.0, "no_recent_news; macro-only diagnostic."
    if news_item_count < config.min_news_item_count:
        return 1.0, 0.0, 0.0, "thin_news_coverage; macro-only diagnostic."
    penalty = config.news_confidence_penalty * max(0.0, 1.0 - news_confidence)
    return macro_weight, news_weight, penalty, "bounded news overlay applied."


def _diagnostic_confidence(
    *,
    macro_confidence: float | None,
    news_confidence: float,
    news_weight: float,
) -> float:
    macro_confidence = 0.0 if macro_confidence is None else max(0.0, min(macro_confidence, 1.0))
    if news_weight <= 0.0:
        return macro_confidence
    return max(0.0, min(1.0, (0.75 * macro_confidence) + (0.25 * news_confidence)))


def _rank_rows(rows: list[dict]) -> None:
    ranked = sorted(rows, key=lambda row: (-float(row["combined_score"]), row["sector_id"]))
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank


def _clip(value: float, cap: float) -> float:
    return max(-cap, min(cap, value))


def _optional_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _component(
    diagnostic_date: pd.Timestamp,
    sector_id: str,
    component_name: str,
    value: float,
    weight: float,
    rationale: str,
) -> dict:
    return {
        "diagnostic_date": diagnostic_date.date(),
        "sector_id": sector_id,
        "component_name": component_name,
        "component_value": value,
        "component_weight": weight,
        "rationale": rationale,
    }


def _diagnostic_columns() -> list[str]:
    return [
        "diagnostic_date",
        "sector_id",
        "sector_macro_score",
        "sector_news_score",
        "combined_score",
        "macro_component_weight",
        "news_component_weight",
        "news_item_count",
        "news_confidence",
        "diagnostic_confidence",
        "rank",
        "created_at",
    ]


def _component_columns() -> list[str]:
    return [
        "diagnostic_date",
        "sector_id",
        "component_name",
        "component_value",
        "component_weight",
        "rationale",
    ]


def _diagnostic_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_diagnostic_columns())


def _component_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_component_columns())
