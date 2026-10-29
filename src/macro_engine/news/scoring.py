from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import hashlib
import math
import sys
import uuid

import numpy as np
import pandas as pd

from macro_engine.news.config import NewsScoringConfig, load_news_scoring_config
from macro_engine.news.selection import assign_event_ids
from macro_engine.storage.duckdb_store import DuckDBStore

# Bump when a change to this module alters scored output for unchanged inputs, so
# the next stored build recomputes every row instead of only the affected weeks.
SCORING_STATE_VERSION = "1"


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
    full: bool = False,
) -> NewsScoreBuildResult:
    """Score the stored classifications and write the score tables.

    Incremental by default (N1 C1): the store keeps a fingerprint of every
    scorable item as last scored, and only the weeks an added, changed or
    removed item can reach are recomputed and replaced; older rows are left as
    they are. A missing state, a config change or a SCORING_STATE_VERSION bump
    (or ``full=True``) recomputes everything. The returned frames hold only the
    rows this run wrote; the run row counts over the whole merged store."""
    config = load_news_scoring_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    started_at = datetime.now(UTC)
    try:
        classifications, theme_scores, sector_impacts = store.read_effective_news_classifications()
        news_items = store.read_table("news_items")
        event_map = assign_event_ids(
            news_items, similarity_threshold=config.event_dedupe_similarity_threshold
        )
        item_state, meta = _scoring_state(
            news_items, classifications, theme_scores, sector_impacts, event_map, config
        )
        previous = None if full else store.read_news_score_state()
        only_dates = _recompute_dates(previous, item_state, meta, config)
        print(
            _scoring_mode_line(full, previous, only_dates, meta, len(item_state)),
            file=sys.stderr,
            flush=True,
        )
        result = build_news_scores(
            news_items=news_items,
            classifications=classifications,
            theme_scores=theme_scores,
            sector_impacts=sector_impacts,
            config=config,
            started_at=started_at,
            event_map=event_map,
            only_dates=only_dates,
        )
        # Drop the state before touching outputs: a run that dies part-way then
        # leaves no state behind, and the next run rebuilds in full.
        store.replace_news_score_state(None, None)
        if only_dates is None:
            store.replace_news_score_outputs(
                result.daily_theme_scores,
                result.daily_sector_scores,
                result.weekly_theme_scores,
                result.weekly_sector_scores,
                result.components,
                result.runs,
            )
            runs = result.runs
        else:
            runs = store.update_news_score_outputs(
                score_dates=only_dates,
                range_start=meta["range_start"],
                range_end=meta["range_end"],
                daily_theme_scores=result.daily_theme_scores,
                daily_sector_scores=result.daily_sector_scores,
                weekly_theme_scores=result.weekly_theme_scores,
                weekly_sector_scores=result.weekly_sector_scores,
                components=result.components,
                runs=result.runs,
            )
        store.replace_news_score_state(item_state, meta)
        return NewsScoreBuildResult(
            result.daily_theme_scores,
            result.daily_sector_scores,
            result.weekly_theme_scores,
            result.weekly_sector_scores,
            result.components,
            runs,
        )
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
        store.replace_news_score_state(None, None)
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
    event_map: dict[str, str] | None = None,
    only_dates: set[date] | None = None,
) -> NewsScoreBuildResult:
    """Score the given inputs. ``event_map`` is computed from ``news_items`` when
    not given. ``only_dates`` restricts the output to those score dates; weekly
    rows are complete only for weeks whose in-range dates are all included."""
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

    items, classes = _scoring_inputs(news_items, classifications)
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
        only_dates=only_dates,
    )
    # Lexical event clustering over the article corpus so one near-duplicate
    # narrative (carried by many articles/sources) cannot dominate a sector.
    if event_map is None:
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


def _scoring_inputs(
    news_items: pd.DataFrame, classifications: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    items = news_items.copy()
    items["published_at"] = pd.to_datetime(items["published_at"], errors="coerce", utc=True)
    classes = classifications.copy()
    classes = classes[classes["classification_status"] == "success"].copy()
    classes["severity"] = pd.to_numeric(classes["severity"], errors="coerce").fillna(0.0)
    classes["confidence"] = pd.to_numeric(classes["confidence"], errors="coerce").fillna(0.0)
    return items, classes


# Every base-row field that a component or an aggregate reads, per base kind.
_THEME_STATE_FIELDS = ("theme_id", "direction", "severity", "confidence", "source", "published_at")
_SECTOR_STATE_FIELDS = (
    "sector_id",
    "impact_direction",
    "impact_score",
    "confidence",
    "rationale",
    "severity",
    "source",
    "published_at",
)


def _scoring_state(
    news_items: pd.DataFrame,
    classifications: pd.DataFrame,
    theme_scores: pd.DataFrame,
    sector_impacts: pd.DataFrame,
    event_map: dict[str, str],
    config: NewsScoringConfig,
) -> tuple[pd.DataFrame, dict]:
    """One row per scorable news_id: a fingerprint of everything scoring reads
    for it (its theme and sector rows, source, publication time, event id) and
    its publication date. ``meta`` holds what applies to every row."""
    parts: dict[str, list[str]] = {}
    published: dict[str, object] = {}
    dates = pd.DatetimeIndex([])
    if not news_items.empty and not classifications.empty:
        items, classes = _scoring_inputs(news_items, classifications)
        bases = (
            ("theme", _theme_base(items, classes, theme_scores), _THEME_STATE_FIELDS),
            ("sector", _sector_base(items, classes, sector_impacts), _SECTOR_STATE_FIELDS),
        )
        for kind, base, fields in bases:
            for row in base.to_dict(orient="records"):
                news_id = str(row["news_id"])
                values = tuple(row.get(field) for field in fields)
                parts.setdefault(news_id, []).append(
                    repr((kind, values, event_map.get(news_id, news_id)))
                )
                published[news_id] = row.get("published_at")
        dates = _score_dates(items, config)
    state = pd.DataFrame(
        [
            {
                "news_id": news_id,
                "fingerprint": hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest(),
                "published_date": _published_date(published[news_id]),
            }
            for news_id, rows in sorted(parts.items())
        ],
        columns=["news_id", "fingerprint", "published_date"],
    )
    meta = {
        "state_version": SCORING_STATE_VERSION,
        "config_json": config.model_dump_json(),
        "range_start": dates[0].date() if len(dates) else None,
        "range_end": dates[-1].date() if len(dates) else None,
    }
    return state, meta


def _published_date(published_at) -> date | None:
    if published_at is None or pd.isna(published_at):
        return None
    stamp = pd.Timestamp(published_at)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC").date()


def _recompute_dates(
    previous: tuple[pd.DataFrame, dict] | None,
    state: pd.DataFrame,
    meta: dict,
    config: NewsScoringConfig,
) -> set[date] | None:
    """Score dates to recompute, or None for a full rebuild.

    An item published on day P reaches score dates P..P+max_age_days (to the
    end of the range when freshness decay is off). The days an added, changed
    or removed item reaches, and the days entering or leaving the date range,
    are widened to whole weeks, because a weekly row averages its days."""
    if previous is None:
        return None
    previous_state, previous_meta = previous
    if (
        previous_meta.get("state_version") != meta["state_version"]
        or previous_meta.get("config_json") != meta["config_json"]
        or previous_meta.get("range_start") is None
        or meta["range_start"] is None
    ):
        return None
    start, end = meta["range_start"], meta["range_end"]
    old_start, old_end = previous_meta["range_start"], previous_meta["range_end"]
    last = max(end, old_end)
    merged = previous_state.merge(state, on="news_id", how="outer", suffixes=("_old", "_new"))
    changed = merged[merged["fingerprint_old"].fillna("") != merged["fingerprint_new"].fillna("")]
    touched: set[date] = set()
    for published in pd.concat([changed["published_date_old"], changed["published_date_new"]]):
        if published is None or pd.isna(published):
            continue
        first = pd.Timestamp(published).date()
        stop = last
        if config.freshness_decay.enabled:
            stop = min(last, first + timedelta(days=config.freshness_decay.max_age_days))
        touched.update(_day_range(first, stop))
    touched.update(set(_day_range(start, end)) ^ set(_day_range(old_start, old_end)))
    weeks = {_week_start(day) for day in touched}
    return {day for day in _day_range(start, end) if _week_start(day) in weeks}


def _scoring_mode_line(
    full: bool,
    previous: tuple[pd.DataFrame, dict] | None,
    only_dates: set[date] | None,
    meta: dict,
    item_count: int,
) -> str:
    """One log line saying which path this build takes, so a run log shows it."""
    if only_dates is not None:
        range_days = len(_day_range(meta["range_start"], meta["range_end"]))
        mode = f"mode=incremental dates={len(only_dates)}/{range_days}"
    elif full:
        mode = "mode=full reason=forced"
    elif previous is None:
        mode = "mode=full reason=no_state"
    else:
        mode = "mode=full reason=state_mismatch"
    return f"news-scoring: {mode} items={item_count}"


def _day_range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


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
    only_dates: set[date] | None = None,
) -> pd.DataFrame:
    dates = _score_dates(items, config)
    if only_dates is not None:
        dates = dates[[score_date.date() in only_dates for score_date in dates]]
    theme_rows = _rows_by_day(_theme_base(items, classifications, theme_scores))
    sector_rows = _rows_by_day(_sector_base(items, classifications, sector_impacts))
    rows: list[dict] = []
    for score_date in dates:
        for row in _rows_in_reach(theme_rows, score_date, config):
            rows.extend(_component_for_theme_row(row, score_date, config))
        for row in _rows_in_reach(sector_rows, score_date, config):
            rows.extend(_component_for_sector_row(row, score_date, config))
    if not rows:
        return _component_frame(with_source=True)
    frame = pd.DataFrame(rows)
    return frame[_component_columns() + ["source"]]


def _rows_by_day(base: pd.DataFrame) -> tuple[list[dict], np.ndarray]:
    """Base rows as records, beside each row's publication day number (NaN when
    unknown), so a score date can pick out the rows it can reach."""
    if base.empty:
        return [], np.array([], dtype=float)
    published = pd.to_datetime(base["published_at"], errors="coerce", utc=True)
    days = (published.dt.normalize() - pd.Timestamp(0, tz="UTC")).dt.days.to_numpy(dtype=float)
    return base.to_dict(orient="records"), days


def _rows_in_reach(
    rows: tuple[list[dict], np.ndarray],
    score_date: pd.Timestamp,
    config: NewsScoringConfig,
) -> list[dict]:
    """Rows that can be eligible on score_date, in base order: published on or
    before it and, with decay on, at most max_age_days earlier. Eligibility is
    still decided per row; this only skips rows that cannot pass it."""
    records, days = rows
    day = (score_date.normalize() - pd.Timestamp(0, tz="UTC")).days
    mask = days <= day
    if config.freshness_decay.enabled:
        mask &= days >= day - config.freshness_decay.max_age_days
    return [records[position] for position in np.flatnonzero(mask)]


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
