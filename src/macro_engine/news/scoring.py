from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import math
import uuid

import pandas as pd

from macro_engine.news.config import NewsScoringConfig, load_news_scoring_config
from macro_engine.news.selection import assign_event_ids
from macro_engine.storage.duckdb_store import DuckDBStore


@dataclass(frozen=True)
class NewsScoreBuildResult:
    daily_theme_scores: pd.DataFrame
    daily_sector_scores: pd.DataFrame
    weekly_theme_scores: pd.DataFrame
    weekly_sector_scores: pd.DataFrame
    components: pd.DataFrame
    runs: pd.DataFrame


def build_stored_news_scores(
    *,
    config_path: str | Path = "config/news_scoring.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> NewsScoreBuildResult:
    config = load_news_scoring_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    started_at = datetime.now(UTC)
    try:
        result = build_news_scores(
            news_items=store.read_table("news_items"),
            classifications=store.read_table("news_classifications"),
            theme_scores=store.read_table("news_theme_scores"),
            sector_impacts=store.read_table("news_sector_impacts"),
            config=config,
            started_at=started_at,
        )
        store.replace_news_score_outputs(
            result.daily_theme_scores,
            result.daily_sector_scores,
            result.weekly_theme_scores,
            result.weekly_sector_scores,
            result.components,
            result.runs,
        )
        return result
    except Exception as exc:
        completed_at = datetime.now(UTC)
        runs = pd.DataFrame(
            [
                {
                    "run_id": f"news_score_{uuid.uuid4().hex}",
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "status": "error",
                    "frequency": ",".join(config.aggregation_frequency),
                    "item_count": 0,
                    "scored_item_count": 0,
                    "error_message": str(exc),
                }
            ]
        )
        store.replace_news_score_outputs(
            _daily_theme_frame(),
            _daily_sector_frame(),
            _weekly_theme_frame(),
            _weekly_sector_frame(),
            _component_frame(),
            runs,
        )
        raise


def build_news_scores(
    *,
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    theme_scores: pd.DataFrame,
    sector_impacts: pd.DataFrame,
    config: NewsScoringConfig,
    started_at: datetime | None = None,
) -> NewsScoreBuildResult:
    started_at = started_at or datetime.now(UTC)
    completed_at = datetime.now(UTC)
    if news_items.empty or classifications.empty:
        runs = _run_frame(
            started_at=started_at,
            completed_at=completed_at,
            config=config,
            item_count=len(news_items),
            scored_item_count=0,
            status="success",
            error_message="no_news_classifications",
        )
        return NewsScoreBuildResult(
            _daily_theme_frame(),
            _daily_sector_frame(),
            _weekly_theme_frame(),
            _weekly_sector_frame(),
            _component_frame(),
            runs,
        )

    items = news_items.copy()
    items["published_at"] = pd.to_datetime(items["published_at"], errors="coerce", utc=True)
    classes = classifications.copy()
    classes = classes[classes["classification_status"] == "success"].copy()
    classes["severity"] = pd.to_numeric(classes["severity"], errors="coerce").fillna(0.0)
    classes["confidence"] = pd.to_numeric(classes["confidence"], errors="coerce").fillna(0.0)
    if classes.empty:
        runs = _run_frame(
            started_at=started_at,
            completed_at=completed_at,
            config=config,
            item_count=len(news_items),
            scored_item_count=0,
            status="success",
            error_message="no_successful_classifications",
        )
        return NewsScoreBuildResult(
            _daily_theme_frame(),
            _daily_sector_frame(),
            _weekly_theme_frame(),
            _weekly_sector_frame(),
            _component_frame(),
            runs,
        )

    daily_components = _daily_components(
        items=items,
        classifications=classes,
        theme_scores=theme_scores,
        sector_impacts=sector_impacts,
        config=config,
    )
    # Lexical event clustering over the article corpus so one near-duplicate
    # narrative (carried by many articles/sources) cannot dominate a sector.
    event_map = assign_event_ids(
        items, similarity_threshold=config.event_dedupe_similarity_threshold
    )
    daily_theme = _aggregate_daily_theme(daily_components, config, completed_at)
    daily_sector = _aggregate_daily_sector(daily_components, config, completed_at, event_map)
    weekly_theme = _aggregate_weekly_theme(daily_theme, completed_at)
    weekly_sector = _aggregate_weekly_sector(daily_sector, completed_at)
    components = daily_components.drop(columns=["source"], errors="ignore")
    runs = _run_frame(
        started_at=started_at,
        completed_at=completed_at,
        config=config,
        item_count=len(news_items),
        scored_item_count=int(components["news_id"].nunique()) if not components.empty else 0,
        status="success",
        error_message=None,
    )
    return NewsScoreBuildResult(
        daily_theme,
        daily_sector,
        weekly_theme,
        weekly_sector,
        components[_component_columns()] if not components.empty else _component_frame(),
        runs,
    )


def freshness_weight(age_days: int, config: NewsScoringConfig) -> float:
    if not config.freshness_decay.enabled:
        return 1.0
    if age_days < 0:
        return 0.0
    if age_days > config.freshness_decay.max_age_days:
        return 0.0
    return float(0.5 ** (age_days / config.freshness_decay.half_life_days))


def _daily_components(
    *,
    items: pd.DataFrame,
    classifications: pd.DataFrame,
    theme_scores: pd.DataFrame,
    sector_impacts: pd.DataFrame,
    config: NewsScoringConfig,
) -> pd.DataFrame:
    dates = _score_dates(items, config)
    theme_base = _theme_base(items, classifications, theme_scores)
    sector_base = _sector_base(items, classifications, sector_impacts)
    rows: list[dict] = []
    for score_date in dates:
        for row in theme_base.to_dict(orient="records"):
            rows.extend(_component_for_theme_row(row, score_date, config))
        for row in sector_base.to_dict(orient="records"):
            rows.extend(_component_for_sector_row(row, score_date, config))
    if not rows:
        return _component_frame(with_source=True)
    frame = pd.DataFrame(rows)
    return frame[_component_columns() + ["source"]]


def _theme_base(
    items: pd.DataFrame,
    classifications: pd.DataFrame,
    theme_scores: pd.DataFrame,
) -> pd.DataFrame:
    if theme_scores.empty:
        return pd.DataFrame()
    frame = theme_scores.merge(
        items[["news_id", "source", "published_at"]],
        on="news_id",
        how="left",
    )
    frame["severity"] = pd.to_numeric(frame["severity"], errors="coerce").fillna(0.0)
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0.0)
    return frame.merge(
        classifications[["news_id"]].drop_duplicates(),
        on="news_id",
        how="inner",
    )


def _sector_base(
    items: pd.DataFrame,
    classifications: pd.DataFrame,
    sector_impacts: pd.DataFrame,
) -> pd.DataFrame:
    if sector_impacts.empty:
        return pd.DataFrame()
    frame = sector_impacts.merge(
        items[["news_id", "source", "published_at"]],
        on="news_id",
        how="left",
    )
    frame = frame.merge(
        classifications[["news_id", "severity"]].drop_duplicates(),
        on="news_id",
        how="inner",
    )
    frame["severity"] = pd.to_numeric(frame["severity"], errors="coerce").fillna(0.0)
    frame["confidence"] = pd.to_numeric(frame["confidence"], errors="coerce").fillna(0.0)
    frame["impact_score"] = pd.to_numeric(frame["impact_score"], errors="coerce").fillna(0.0)
    return frame


def _component_for_theme_row(
    row: dict,
    score_date: pd.Timestamp,
    config: NewsScoringConfig,
) -> list[dict]:
    if not _row_is_eligible(row, score_date, config):
        return []
    sign = _direction_sign(row.get("direction"))
    severity = float(row["severity"])
    confidence = float(row["confidence"])
    source_weight = _source_weight(row.get("source"), config)
    fresh = freshness_weight(_age_days(row["published_at"], score_date), config)
    raw = sign * (severity if config.severity_weighting else 1.0)
    adjusted = raw
    if config.confidence_weighting:
        adjusted *= confidence
    adjusted *= source_weight * fresh
    adjusted = _clip(adjusted, config.max_single_item_contribution)
    return [
        {
            "score_date": score_date.date(),
            "frequency": "daily",
            "news_id": row["news_id"],
            "theme_id": row["theme_id"],
            "sector_id": None,
            "component_type": "theme",
            "direction": row.get("direction"),
            "raw_component": raw,
            "adjusted_component": adjusted,
            "severity": severity,
            "confidence": confidence,
            "source_weight": source_weight,
            "freshness_weight": fresh,
            "rationale": "",
            "source": row.get("source"),
        }
    ]


def _component_for_sector_row(
    row: dict,
    score_date: pd.Timestamp,
    config: NewsScoringConfig,
) -> list[dict]:
    if not _row_is_eligible(row, score_date, config):
        return []
    confidence = float(row["confidence"])
    severity = float(row["severity"])
    source_weight = _source_weight(row.get("source"), config)
    fresh = freshness_weight(_age_days(row["published_at"], score_date), config)
    raw = float(row["impact_score"])
    if not config.severity_weighting:
        raw = _direction_sign(row.get("impact_direction")) or raw
    adjusted = raw
    if config.confidence_weighting:
        adjusted *= confidence
    adjusted *= source_weight * fresh
    adjusted = _clip(adjusted, config.max_single_item_contribution)
    return [
        {
            "score_date": score_date.date(),
            "frequency": "daily",
            "news_id": row["news_id"],
            "theme_id": None,
            "sector_id": row["sector_id"],
            "component_type": "sector",
            "direction": row.get("impact_direction"),
            "raw_component": raw,
            "adjusted_component": adjusted,
            "severity": severity,
            "confidence": confidence,
            "source_weight": source_weight,
            "freshness_weight": fresh,
            "rationale": row.get("rationale") or "",
            "source": row.get("source"),
        }
    ]


def _row_is_eligible(row: dict, score_date: pd.Timestamp, config: NewsScoringConfig) -> bool:
    published_at = pd.Timestamp(row.get("published_at"))
    if pd.isna(published_at):
        return False
    age = _age_days(published_at, score_date)
    if age < 0 or freshness_weight(age, config) <= 0.0:
        return False
    return float(row.get("confidence", 0.0)) >= config.min_confidence and float(
        row.get("severity", 0.0)
    ) >= config.min_severity


def _aggregate_daily_theme(
    components: pd.DataFrame,
    config: NewsScoringConfig,
    created_at: datetime,
) -> pd.DataFrame:
    if components.empty:
        return _daily_theme_frame()
    frame = components[components["component_type"] == "theme"].copy()
    if frame.empty:
        return _daily_theme_frame()
    capped = _source_capped(frame, "theme_id", config)
    rows = []
    for (score_date, theme_id), group in capped.groupby(["score_date", "theme_id"]):
        original = frame[(frame["score_date"] == score_date) & (frame["theme_id"] == theme_id)]
        rows.append(
            {
                "score_date": score_date,
                "theme_id": theme_id,
                "raw_score": float(original["raw_component"].sum()),
                "adjusted_score": float(group["capped_component"].sum()),
                "item_count": int(original["news_id"].nunique()),
                "avg_confidence": float(original["confidence"].mean()),
                "avg_severity": float(original["severity"].mean()),
                "top_news_ids": _top_news_ids(original),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows, columns=_daily_theme_columns())


def _aggregate_daily_sector(
    components: pd.DataFrame,
    config: NewsScoringConfig,
    created_at: datetime,
    event_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    if components.empty:
        return _daily_sector_frame()
    frame = components[components["component_type"] == "sector"].copy()
    if frame.empty:
        return _daily_sector_frame()
    capped = _sector_capped(frame, config, event_map or {})
    rows = []
    for (score_date, sector_id), group in capped.groupby(["score_date", "sector_id"]):
        original = frame[(frame["score_date"] == score_date) & (frame["sector_id"] == sector_id)]
        rows.append(
            {
                "score_date": score_date,
                "sector_id": sector_id,
                "raw_news_score": float(original["raw_component"].sum()),
                "adjusted_news_score": float(group["capped_component"].sum()),
                "positive_item_count": int(
                    (original["adjusted_component"] > config.neutral_score_threshold).sum()
                ),
                "negative_item_count": int(
                    (original["adjusted_component"] < -config.neutral_score_threshold).sum()
                ),
                "neutral_item_count": int(
                    (
                        original["adjusted_component"].abs()
                        <= config.neutral_score_threshold
                    ).sum()
                ),
                "avg_confidence": float(original["confidence"].mean()),
                "avg_severity": float(original["severity"].mean()),
                "top_news_ids": _top_news_ids(original),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows, columns=_daily_sector_columns())


def _aggregate_weekly_theme(daily: pd.DataFrame, created_at: datetime) -> pd.DataFrame:
    if daily.empty:
        return _weekly_theme_frame()
    frame = daily.copy()
    frame["week_start_date"] = pd.to_datetime(frame["score_date"]).dt.to_period("W-SUN").dt.start_time.dt.date
    rows = []
    for (week_start, theme_id), group in frame.groupby(["week_start_date", "theme_id"]):
        rows.append(
            {
                "week_start_date": week_start,
                "theme_id": theme_id,
                "raw_score": float(group["raw_score"].mean()),
                "adjusted_score": float(group["adjusted_score"].mean()),
                "item_count": int(group["item_count"].sum()),
                "avg_confidence": float(group["avg_confidence"].mean()),
                "avg_severity": float(group["avg_severity"].mean()),
                "top_news_ids": _merge_top_news_ids(group),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows, columns=_weekly_theme_columns())


def _aggregate_weekly_sector(daily: pd.DataFrame, created_at: datetime) -> pd.DataFrame:
    if daily.empty:
        return _weekly_sector_frame()
    frame = daily.copy()
    frame["week_start_date"] = pd.to_datetime(frame["score_date"]).dt.to_period("W-SUN").dt.start_time.dt.date
    rows = []
    for (week_start, sector_id), group in frame.groupby(["week_start_date", "sector_id"]):
        rows.append(
            {
                "week_start_date": week_start,
                "sector_id": sector_id,
                "raw_news_score": float(group["raw_news_score"].mean()),
                "adjusted_news_score": float(group["adjusted_news_score"].mean()),
                "positive_item_count": int(group["positive_item_count"].sum()),
                "negative_item_count": int(group["negative_item_count"].sum()),
                "neutral_item_count": int(group["neutral_item_count"].sum()),
                "avg_confidence": float(group["avg_confidence"].mean()),
                "avg_severity": float(group["avg_severity"].mean()),
                "top_news_ids": _merge_top_news_ids(group),
                "created_at": created_at,
            }
        )
    return pd.DataFrame(rows, columns=_weekly_sector_columns())


def _sector_capped(
    frame: pd.DataFrame,
    config: NewsScoringConfig,
    event_map: dict[str, str],
) -> pd.DataFrame:
    """Two-level cap for sector contributions: within each (date, sector,
    event) cap per-source sums at max_single_source_daily_contribution, then
    cap the event total at max_single_event_daily_contribution. Returns one
    capped_component row per (date, sector, event); the caller sums per sector.

    Event clustering stops one near-duplicate narrative - even spread across
    many outlets - from dominating; the inner source cap still stops a single
    outlet from dominating that event."""
    work = frame.copy()
    work["event_id"] = work["news_id"].map(event_map).fillna(work["news_id"])
    rows = []
    for (score_date, sector_id, event_id), group in work.groupby(
        ["score_date", "sector_id", "event_id"], dropna=False
    ):
        per_source = group.groupby("source", dropna=False)["adjusted_component"].sum()
        per_source = per_source.map(
            lambda v: _clip(v, config.max_single_source_daily_contribution)
        )
        event_total = _clip(
            float(per_source.sum()), config.max_single_event_daily_contribution
        )
        rows.append(
            {
                "score_date": score_date,
                "sector_id": sector_id,
                "event_id": event_id,
                "capped_component": event_total,
            }
        )
    return pd.DataFrame(rows)


def _source_capped(frame: pd.DataFrame, key_column: str, config: NewsScoringConfig) -> pd.DataFrame:
    rows = []
    for keys, group in frame.groupby(["score_date", key_column, "source"], dropna=False):
        total = float(group["adjusted_component"].sum())
        capped = _clip(total, config.max_single_source_daily_contribution)
        rows.append(
            {
                "score_date": keys[0],
                key_column: keys[1],
                "source": keys[2],
                "capped_component": capped,
            }
        )
    return pd.DataFrame(rows)


def _score_dates(items: pd.DataFrame, config: NewsScoringConfig) -> pd.DatetimeIndex:
    published = items["published_at"].dropna()
    if published.empty:
        return pd.DatetimeIndex([])
    start = pd.Timestamp(config.output_start_date, tz="UTC") if config.output_start_date else published.min().normalize()
    end = pd.Timestamp(config.output_end_date, tz="UTC") if config.output_end_date else published.max().normalize()
    if start > end:
        return pd.DatetimeIndex([])
    return pd.date_range(start=start, end=end, freq="D", tz="UTC")


def _age_days(published_at, score_date: pd.Timestamp) -> int:
    published = pd.Timestamp(published_at)
    if published.tzinfo is None:
        published = published.tz_localize("UTC")
    if score_date.tzinfo is None:
        score_date = score_date.tz_localize("UTC")
    return int((score_date.normalize() - published.normalize()).days)


def _direction_sign(direction: str | None) -> float:
    mapping = {
        "positive": 1.0,
        "tailwind": 1.0,
        "negative": -1.0,
        "headwind": -1.0,
        "mixed": 0.0,
        "neutral": 0.0,
        "unclear": 0.0,
    }
    return mapping.get(str(direction or "").lower(), 0.0)


def _source_weight(source: str | None, config: NewsScoringConfig) -> float:
    if source is None:
        return config.source_weights.default
    return config.source_weights.sources.get(str(source), config.source_weights.default)


def _clip(value: float, cap: float) -> float:
    if math.isnan(value):
        return 0.0
    return max(-cap, min(cap, value))


def _top_news_ids(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    ranked = frame.assign(abs_component=frame["adjusted_component"].abs()).sort_values(
        ["abs_component", "news_id"],
        ascending=[False, True],
    )
    return ranked["news_id"].drop_duplicates().head(5).tolist()


def _merge_top_news_ids(frame: pd.DataFrame) -> list[str]:
    seen: list[str] = []
    for ids in frame["top_news_ids"].tolist():
        for news_id in ids or []:
            if news_id not in seen:
                seen.append(news_id)
    return seen[:5]


def _run_frame(
    *,
    started_at: datetime,
    completed_at: datetime,
    config: NewsScoringConfig,
    item_count: int,
    scored_item_count: int,
    status: str,
    error_message: str | None,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": f"news_score_{uuid.uuid4().hex}",
                "started_at": started_at,
                "completed_at": completed_at,
                "status": status,
                "frequency": ",".join(config.aggregation_frequency),
                "item_count": int(item_count),
                "scored_item_count": int(scored_item_count),
                "error_message": error_message,
            }
        ]
    )


def _daily_theme_columns() -> list[str]:
    return [
        "score_date",
        "theme_id",
        "raw_score",
        "adjusted_score",
        "item_count",
        "avg_confidence",
        "avg_severity",
        "top_news_ids",
        "created_at",
    ]


def _daily_sector_columns() -> list[str]:
    return [
        "score_date",
        "sector_id",
        "raw_news_score",
        "adjusted_news_score",
        "positive_item_count",
        "negative_item_count",
        "neutral_item_count",
        "avg_confidence",
        "avg_severity",
        "top_news_ids",
        "created_at",
    ]


def _weekly_theme_columns() -> list[str]:
    return [
        "week_start_date",
        "theme_id",
        "raw_score",
        "adjusted_score",
        "item_count",
        "avg_confidence",
        "avg_severity",
        "top_news_ids",
        "created_at",
    ]


def _weekly_sector_columns() -> list[str]:
    return [
        "week_start_date",
        "sector_id",
        "raw_news_score",
        "adjusted_news_score",
        "positive_item_count",
        "negative_item_count",
        "neutral_item_count",
        "avg_confidence",
        "avg_severity",
        "top_news_ids",
        "created_at",
    ]


def _component_columns() -> list[str]:
    return [
        "score_date",
        "frequency",
        "news_id",
        "theme_id",
        "sector_id",
        "component_type",
        "direction",
        "raw_component",
        "adjusted_component",
        "severity",
        "confidence",
        "source_weight",
        "freshness_weight",
        "rationale",
    ]


def _daily_theme_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_daily_theme_columns())


def _daily_sector_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_daily_sector_columns())


def _weekly_theme_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_weekly_theme_columns())


def _weekly_sector_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=_weekly_sector_columns())


def _component_frame(*, with_source: bool = False) -> pd.DataFrame:
    columns = _component_columns()
    if with_source:
        columns = columns + ["source"]
    return pd.DataFrame(columns=columns)
