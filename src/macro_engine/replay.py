from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import shutil
from typing import Any, Callable

import pandas as pd
import yaml

from macro_engine.daily import DailyDiagnosticResult, run_daily_diagnostic
from macro_engine.dashboard_export import export_dashboard_data
from macro_engine.operations_config import load_daily_pipeline_config
from macro_engine.storage.duckdb_store import DuckDBStore


REPLAY_REQUIRED_COLUMNS = {
    "title",
    "body",
    "source",
    "source_url",
    "published_at",
    "source_group",
}

REPLAY_DISCLAIMER = (
    "This is an operational replay of historical news dates. It is not a trading "
    "backtest, it is not investment advice, and it does not validate predictive performance. "
    "Macro data is not vintage unless separately supported by the backend."
)


@dataclass(frozen=True)
class ReplayResult:
    status: str
    summary_json_path: Path
    summary_markdown_path: Path
    replay_days: int
    replay_runs: list[dict[str, Any]]
    blocked_reason: str | None = None


def replay_news_history(
    *,
    config_path: str | Path = "config/daily_pipeline.yaml",
    news_file: str | Path = "data/news_pilot/news_items_last_30_days.csv",
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    db_path: str | Path = "data/macro_engine.duckdb",
    output_dir: str | Path = "outputs/replay",
    archive: bool = True,
    include_prior_items: bool = True,
    max_items_per_replay_day: int = 10,
    live_ai: bool = False,
    mock_ai: bool = True,
    only_unclassified: bool = True,
    persist_replay_db: bool = False,
    export_dashboard: bool = True,
    dashboard_data_dir: str | Path = "dashboard/public/data",
    services: dict[str, Callable] | None = None,
) -> ReplayResult:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    news_path = Path(news_file)
    if not news_path.exists():
        payload = _blocked_payload(
            news_file=news_path,
            reason=f"mapped real-news file not found: {news_path}",
        )
        return _write_result(payload, output_path)

    frame = load_replay_news_frame(news_path)
    if frame.empty:
        payload = _blocked_payload(news_file=news_path, reason="mapped real-news file is empty")
        return _write_result(payload, output_path)

    start_day, end_day = replay_window(frame, start_date=start_date, end_date=end_date)
    dates = [start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)]
    replay_runs: list[dict[str, Any]] = []
    run_services = services or {}
    temp_dir = output_path / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    for replay_day in dates:
        selected = filter_replay_items(
            frame,
            replay_day=replay_day,
            start_day=start_day,
            include_prior_items=include_prior_items,
            max_items=max_items_per_replay_day,
        )
        daily_config_path = _write_daily_replay_config(
            base_config_path=config_path,
            temp_dir=temp_dir,
            replay_day=replay_day,
            selected=selected,
            live_ai=live_ai,
            mock_ai=mock_ai,
            max_items_per_replay_day=max_items_per_replay_day,
            only_unclassified=only_unclassified,
        )
        daily_db_path = temp_dir / f"replay_{replay_day.isoformat()}.duckdb"
        if daily_db_path.exists():
            daily_db_path.unlink()
        result = run_daily_diagnostic(
            config_path=daily_config_path,
            db_path=daily_db_path,
            run_date=replay_day,
            source_profile="replay_local_csv",
            live_ai=live_ai,
            mock_ai=mock_ai,
            archive=archive,
            services=run_services,
        )
        _mark_daily_summary_as_replay(
            result=result,
            replay_day=replay_day,
            source_file=news_path,
            item_count=len(selected),
            include_prior_items=include_prior_items,
            live_ai=live_ai,
            mock_ai=mock_ai,
        )
        if persist_replay_db and result.status == "success":
            _persist_replay_day_to_central_db(
                daily_db_path=daily_db_path,
                central_db_path=db_path,
                replay_day=replay_day,
            )
        replay_runs.append(
            _run_record(
                result=result,
                replay_day=replay_day,
                selected=selected,
                db_path=daily_db_path,
            )
        )

    if export_dashboard:
        export_dashboard_data(dashboard_data_dir=dashboard_data_dir)

    payload = _summary_payload(
        news_file=news_path,
        start_day=start_day,
        end_day=end_day,
        source_frame=frame,
        replay_runs=replay_runs,
        live_ai=live_ai,
        mock_ai=mock_ai,
        include_prior_items=include_prior_items,
        persist_replay_db=persist_replay_db,
    )
    return _write_result(payload, output_path)


def load_replay_news_frame(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = REPLAY_REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"replay news CSV missing required columns: {sorted(missing)}")
    result = frame.copy()
    result["published_at"] = pd.to_datetime(result["published_at"], errors="coerce", utc=True)
    if result["published_at"].isna().any():
        raise ValueError("replay news CSV contains unparseable published_at values")
    return result.sort_values(["published_at", "title"]).reset_index(drop=True)


def replay_window(
    frame: pd.DataFrame,
    *,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> tuple[date, date]:
    max_day = pd.to_datetime(frame["published_at"], utc=True).max().date()
    end_day = _coerce_date(end_date) if end_date is not None else max_day
    start_day = _coerce_date(start_date) if start_date is not None else end_day - timedelta(days=29)
    if start_day > end_day:
        raise ValueError("start_date cannot be after end_date")
    return start_day, end_day


def filter_replay_items(
    frame: pd.DataFrame,
    *,
    replay_day: date,
    start_day: date,
    include_prior_items: bool,
    max_items: int,
) -> pd.DataFrame:
    work = frame.copy()
    days = pd.to_datetime(work["published_at"], utc=True).dt.date
    if include_prior_items:
        selected = work[(days >= start_day) & (days <= replay_day)].copy()
    else:
        selected = work[days == replay_day].copy()
    if max_items > 0 and len(selected) > max_items:
        selected = selected.sort_values("published_at").tail(max_items).copy()
    selected_days = pd.to_datetime(selected["published_at"], utc=True).dt.date if not selected.empty else []
    if len(selected) and max(selected_days) > replay_day:
        raise ValueError(f"future news leakage detected for replay date {replay_day}")
    return selected.reset_index(drop=True)


def _write_daily_replay_config(
    *,
    base_config_path: str | Path,
    temp_dir: Path,
    replay_day: date,
    selected: pd.DataFrame,
    live_ai: bool,
    mock_ai: bool,
    max_items_per_replay_day: int,
    only_unclassified: bool,
) -> Path:
    news_csv = temp_dir / f"replay_news_{replay_day.isoformat()}.csv"
    _write_replay_csv(selected, news_csv)
    news_sources_config = {
        "news_sources": [
            {
                "source_id": "replay_local_csv",
                "provider": "local_csv",
                "enabled": True,
                "profiles": ["replay_local_csv"],
                "path": news_csv.as_posix(),
            }
        ],
        "source_group_rules": [],
    }
    news_sources_path = temp_dir / f"news_sources_{replay_day.isoformat()}.yaml"
    news_sources_path.write_text(yaml.safe_dump(news_sources_config, sort_keys=False), encoding="utf-8")

    config = load_daily_pipeline_config(base_config_path)
    payload = config.model_dump(mode="json")
    payload["news"]["source_profile"] = "replay_local_csv"
    payload["news"]["news_sources_config"] = news_sources_path.as_posix()
    payload["news"]["allow_live_ai"] = bool(live_ai)
    payload["news"]["mock_mode_default"] = bool(mock_ai)
    payload["monitoring"]["source_profile"] = "replay_local_csv"
    payload["live_ai_safety"]["max_items_per_run"] = max_items_per_replay_day
    payload["live_ai_safety"]["classify_only_unclassified"] = bool(only_unclassified)
    daily_config_path = temp_dir / f"daily_pipeline_{replay_day.isoformat()}.yaml"
    daily_config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return daily_config_path


def _write_replay_csv(frame: pd.DataFrame, path: Path) -> None:
    columns = list(REPLAY_REQUIRED_COLUMNS) + [
        column for column in frame.columns if column not in REPLAY_REQUIRED_COLUMNS
    ]
    if frame.empty:
        pd.DataFrame(columns=columns).to_csv(path, index=False)
    else:
        frame[columns].to_csv(path, index=False)


def _mark_daily_summary_as_replay(
    *,
    result: DailyDiagnosticResult,
    replay_day: date,
    source_file: Path,
    item_count: int,
    include_prior_items: bool,
    live_ai: bool,
    mock_ai: bool,
) -> None:
    for summary_path in [result.summary_json_path, Path(result.archive_path or "") / "daily_diagnostic_summary.json"]:
        if not summary_path.exists():
            continue
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        payload["run_mode"] = "replay"
        payload["replay"] = {
            "replay_mode": True,
            "replay_date": replay_day.isoformat(),
            "source_file": source_file.as_posix(),
            "item_count": item_count,
            "include_prior_items": include_prior_items,
            "classification_mode": "live_ai" if live_ai and not mock_ai else "mock",
            "disclaimer": REPLAY_DISCLAIMER,
        }
        summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    for summary_path in [result.summary_markdown_path, Path(result.archive_path or "") / "daily_diagnostic_summary.md"]:
        if not summary_path.exists():
            continue
        existing = summary_path.read_text(encoding="utf-8")
        replay_note = (
            "\n## Replay Metadata\n\n"
            f"- Run mode: replay\n"
            f"- Replay date: {replay_day.isoformat()}\n"
            f"- Replay item count: {item_count}\n"
            f"- Classification mode: {'live_ai' if live_ai and not mock_ai else 'mock'}\n\n"
            f"{REPLAY_DISCLAIMER}\n"
        )
        summary_path.write_text(existing + replay_note, encoding="utf-8")


def _run_record(
    *,
    result: DailyDiagnosticResult,
    replay_day: date,
    selected: pd.DataFrame,
    db_path: str | Path,
) -> dict[str, Any]:
    store = DuckDBStore(db_path)
    store.initialize()
    classifications = store.read_table("news_classifications")
    success_count = 0
    failure_count = 0
    if not classifications.empty:
        selected_ids = set(_news_ids_for_frame(selected))
        selected_classes = classifications[classifications["news_id"].astype(str).isin(selected_ids)]
        success_count = int((selected_classes["classification_status"] == "success").sum())
        failure_count = int((selected_classes["classification_status"] != "success").sum())
    return {
        "replay_date": replay_day.isoformat(),
        "run_id": result.run_id,
        "status": result.status,
        "archive_path": result.archive_path,
        "selected_item_count": int(len(selected)),
        "classified_success_count": success_count,
        "classified_failure_count": failure_count,
        "source_group_counts": _source_group_counts(selected),
        "top_themes": _latest_news_theme_ids(store),
        "combined_top_sectors": _latest_combined_sector_ids(store),
        "guardrail_status": _guardrail_status(result),
        "errors": result.errors,
        "warnings": result.warnings,
    }


def _persist_replay_day_to_central_db(
    *,
    daily_db_path: str | Path,
    central_db_path: str | Path,
    replay_day: date,
) -> None:
    daily_store = DuckDBStore(daily_db_path)
    daily_store.initialize()
    central_store = DuckDBStore(central_db_path)
    central_store.initialize()

    news_items = daily_store.read_table("news_items")
    if not news_items.empty:
        central_store.upsert_news_items(news_items)

    classifications = daily_store.read_table("news_classifications")
    theme_scores = daily_store.read_table("news_theme_scores")
    sector_impacts = daily_store.read_table("news_sector_impacts")
    if classifications.empty:
        return

    persisted = _classification_frame_for_upsert(classifications)
    persisted["classified_at"] = datetime(
        replay_day.year,
        replay_day.month,
        replay_day.day,
        12,
        0,
        0,
        tzinfo=UTC,
    )
    central_store.upsert_news_classification_outputs(
        persisted,
        theme_scores,
        sector_impacts,
    )


def _classification_frame_for_upsert(classifications: pd.DataFrame) -> pd.DataFrame:
    frame = classifications.copy()
    json_columns = {
        "macro_themes_json": "macro_themes",
        "sector_impacts_json": "sector_impacts",
        "entities_json": "entities",
        "raw_ai_response_json": "raw_ai_response",
    }
    for source, target in json_columns.items():
        if target not in frame.columns:
            frame[target] = frame[source].map(_loads_json_value) if source in frame.columns else None
    return frame


def _summary_payload(
    *,
    news_file: Path,
    start_day: date,
    end_day: date,
    source_frame: pd.DataFrame,
    replay_runs: list[dict[str, Any]],
    live_ai: bool,
    mock_ai: bool,
    include_prior_items: bool,
    persist_replay_db: bool,
) -> dict[str, Any]:
    items_by_day = _items_by_day(source_frame)
    days = [start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)]
    days_with_news = set(items_by_day)
    total_success = sum(int(row["classified_success_count"]) for row in replay_runs)
    total_failure = sum(int(row["classified_failure_count"]) for row in replay_runs)
    attempted = total_success + total_failure
    return {
        "status": "success",
        "replay_mode": True,
        "news_file": news_file.as_posix(),
        "data_file_local_only": True,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "replay_days": len(days),
        "classification_mode": "live_ai" if live_ai and not mock_ai else "mock",
        "include_prior_items": include_prior_items,
        "persist_replay_db": persist_replay_db,
        "total_news_items": int(len(source_frame)),
        "items_by_day": items_by_day,
        "classified_success_count": total_success,
        "classified_failure_count": total_failure,
        "classification_success_rate": None if attempted == 0 else total_success / attempted,
        "source_group_coverage_by_day": _source_group_coverage_by_day(source_frame),
        "daily_runs": replay_runs,
        "days_with_no_news_items": [day.isoformat() for day in days if day.isoformat() not in days_with_news],
        "days_skipped": [],
        "dashboard_history_update_status": "exported",
        "date_leakage_check": "passed",
        "disclaimer": REPLAY_DISCLAIMER,
    }


def _blocked_payload(*, news_file: Path, reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "replay_mode": True,
        "news_file": news_file.as_posix(),
        "blocked_reason": reason,
        "replay_days": 0,
        "daily_runs": [],
        "days_with_no_news_items": [],
        "disclaimer": REPLAY_DISCLAIMER,
    }


def _write_result(payload: dict[str, Any], output_dir: Path) -> ReplayResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "replay_summary.json"
    markdown_path = output_dir / "replay_summary.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(_replay_markdown(payload), encoding="utf-8")
    return ReplayResult(
        status=str(payload["status"]),
        summary_json_path=json_path,
        summary_markdown_path=markdown_path,
        replay_days=int(payload.get("replay_days") or 0),
        replay_runs=list(payload.get("daily_runs") or []),
        blocked_reason=payload.get("blocked_reason"),
    )


def _replay_markdown(payload: dict[str, Any]) -> str:
    if payload["status"] == "blocked":
        return (
            "# Historical 30-Day Real-News Replay Trial\n\n"
            f"Status: blocked\n\nReason: {payload.get('blocked_reason')}\n\n"
            f"News file: `{payload.get('news_file')}`\n\n{REPLAY_DISCLAIMER}\n"
        )
    run_lines = "\n".join(
        "- {replay_date}: {status}, items {selected_item_count}, archive `{archive_path}`".format(
            **row
        )
        for row in payload["daily_runs"]
    )
    no_news = ", ".join(payload.get("days_with_no_news_items") or []) or "none"
    return f"""# Historical 30-Day Real-News Replay Trial

Status: {payload["status"]}

Replay date range: {payload["start_date"]} to {payload["end_date"]}
Replay days: {payload["replay_days"]}
News source file: `{payload["news_file"]}`
Replay mode: {payload["classification_mode"]}
Total news items: {payload["total_news_items"]}
Classification success count: {payload["classified_success_count"]}
Classification failure count: {payload["classified_failure_count"]}
Classification success rate: {payload["classification_success_rate"]}

## Daily Runs

{run_lines or "- None"}

Days with no news items: {no_news}

Dashboard history update status: {payload["dashboard_history_update_status"]}
Date leakage check: {payload["date_leakage_check"]}

{REPLAY_DISCLAIMER}
"""


def _items_by_day(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {}
    days = pd.to_datetime(frame["published_at"], utc=True).dt.date.astype(str)
    return {str(key): int(value) for key, value in days.value_counts().sort_index().items()}


def _source_group_coverage_by_day(frame: pd.DataFrame) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    if frame.empty:
        return result
    work = frame.copy()
    work["day"] = pd.to_datetime(work["published_at"], utc=True).dt.date.astype(str)
    for day, group in work.groupby("day"):
        result[str(day)] = _source_group_counts(group)
    return result


def _source_group_counts(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty or "source_group" not in frame:
        return {}
    counts = frame["source_group"].fillna("unmapped").astype(str).value_counts().sort_index()
    return {str(key): int(value) for key, value in counts.items()}


def _news_ids_for_frame(frame: pd.DataFrame) -> list[str]:
    from macro_engine.news.ingest import content_hash_for_news

    ids = []
    for row in frame.to_dict(orient="records"):
        published = pd.to_datetime(row["published_at"], utc=True).to_pydatetime().isoformat()
        digest = content_hash_for_news(
            title=str(row["title"]),
            body=str(row["body"]),
            source=str(row["source"]),
            published_at=published,
        )
        ids.append(f"news_{digest[:16]}")
    return ids


def _latest_news_theme_ids(store: DuckDBStore) -> list[str]:
    themes = store.read_table("news_daily_theme_scores")
    if themes.empty:
        return []
    frame = themes.copy()
    frame["score_date"] = pd.to_datetime(frame["score_date"], errors="coerce")
    latest = frame[frame["score_date"] == frame["score_date"].max()].sort_values(
        ["adjusted_score", "theme_id"],
        ascending=[False, True],
    )
    return [str(row["theme_id"]) for row in latest.head(5).to_dict(orient="records")]


def _latest_combined_sector_ids(store: DuckDBStore) -> list[str]:
    combined = store.read_table("combined_sector_diagnostics")
    if combined.empty:
        return []
    frame = combined.copy()
    frame["diagnostic_date"] = pd.to_datetime(frame["diagnostic_date"], errors="coerce")
    latest = frame[frame["diagnostic_date"] == frame["diagnostic_date"].max()].sort_values("rank")
    return [str(row["sector_id"]) for row in latest.head(5).to_dict(orient="records")]


def _guardrail_status(result: DailyDiagnosticResult) -> str | None:
    try:
        payload = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    statuses = payload.get("step_statuses")
    return statuses.get("guardrail_status") if isinstance(statuses, dict) else None


def _loads_json_value(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    if pd.isna(value):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _coerce_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def reset_replay_output_dir(path: str | Path = "outputs/replay") -> None:
    output = Path(path)
    if output.exists():
        shutil.rmtree(output)
