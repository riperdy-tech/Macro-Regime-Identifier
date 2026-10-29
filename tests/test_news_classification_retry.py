"""N1 C4: a failed live classification is retried, bounded, and a later success wins
for every consumer of news_classifications."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from macro_engine.events.builder import select_classification
from macro_engine.events.store_io import load_all_items
from macro_engine.news.config import NewsThemesConfig
from macro_engine.news.confidence_calibration import build_confidence_ledger
from macro_engine.news.coverage_report import rolling_classified_share
from macro_engine.news.history import export_news_history, hydrate_news_history
from macro_engine.news.schema import NewsItem
from macro_engine.news.scoring import build_stored_news_scores
from macro_engine.news.service import classify_stored_news
from macro_engine.shocks.narrative import _summary_by_news_id
from macro_engine.storage.duckdb_store import DuckDBStore

NOW = datetime.now(UTC)

_PAYLOAD = {
    "summary": "Retried success",
    "macro_themes": [
        {"theme_id": "inflation_pressure", "direction": "positive", "severity": 0.9,
         "confidence": 0.9, "time_horizon": "short_term"}
    ],
    "sector_impacts": [
        {"sector_id": "energy", "impact_direction": "tailwind", "impact_score": 0.8,
         "confidence": 0.85, "rationale": "retry"}
    ],
    "entities": [],
    "secular_theme": None,
    "overall_severity": 0.9,
    "overall_confidence": 0.9,
    "time_horizon": "short_term",
}


class _FakeLive:
    provider_name = "deepseek"
    model_name = "deepseek-chat"

    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.seen: list[str] = []

    def classify(self, item: NewsItem, themes: NewsThemesConfig) -> dict:
        self.seen.append(item.news_id)
        if self.fail:
            raise RuntimeError("deepseek request exceeded hard timeout 75s")
        return dict(_PAYLOAD)


@pytest.fixture
def live(monkeypatch):
    holder = {"classifier": _FakeLive(fail=True)}
    monkeypatch.setattr("macro_engine.news.service.should_use_mock_classifier", lambda cfg: False)
    monkeypatch.setattr(
        "macro_engine.news.service.DeepSeekNewsClassifier", lambda cfg: holder["classifier"]
    )

    def use(fail: bool) -> _FakeLive:
        holder["classifier"] = _FakeLive(fail=fail)
        return holder["classifier"]

    return use


def _item(news_id: str, published_at: datetime) -> dict:
    return {
        "news_id": news_id,
        "source": "reuters",
        "source_url": f"https://reuters.com/{news_id}",
        "title": f"Title {news_id}",
        "body": f"Body {news_id}",
        "published_at": published_at,
        "ingested_at": published_at,
        "provider": "rss",
        "raw_metadata": {},
        "content_hash": f"hash_{news_id}",
    }


def _store(tmp_path: Path, *items: dict) -> tuple[DuckDBStore, Path]:
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_news_items(pd.DataFrame(list(items)))
    return store, db_path


def _rows(store: DuckDBStore, news_id: str) -> pd.DataFrame:
    frame = store.read_table("news_classifications")
    return frame[frame["news_id"] == news_id].sort_values("classified_at")


def test_error_is_retried_once_then_given_up(tmp_path: Path, live):
    store, db_path = _store(tmp_path, _item("n1", NOW - timedelta(days=1)))

    first = live(fail=True)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    assert first.seen == ["n1"]

    retry = live(fail=True)
    res = classify_stored_news(db_path=db_path, only_unclassified=True)
    assert retry.seen == ["n1"]
    assert res["retried"] == 1
    assert res["inserted"] == 1
    assert list(_rows(store, "n1")["classification_status"]) == ["error", "error"]

    given_up = live(fail=True)
    res = classify_stored_news(db_path=db_path, only_unclassified=True)
    assert given_up.seen == []
    assert res["selected_count"] == 0
    assert len(_rows(store, "n1")) == 2


def test_success_after_error_is_kept_and_not_retried_again(tmp_path: Path, live):
    store, db_path = _store(tmp_path, _item("n1", NOW - timedelta(days=1)))
    live(fail=True)
    classify_stored_news(db_path=db_path, only_unclassified=True)

    live(fail=False)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    assert list(_rows(store, "n1")["classification_status"]) == ["error", "success"]

    again = live(fail=False)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    assert again.seen == []
    assert len(_rows(store, "n1")) == 2


def test_error_outside_contamination_window_is_not_retried(tmp_path: Path, live):
    store, db_path = _store(tmp_path, _item("old", NOW - timedelta(days=15)))
    live(fail=True)
    classify_stored_news(db_path=db_path, only_unclassified=True)

    retry = live(fail=False)
    res = classify_stored_news(db_path=db_path, only_unclassified=True)
    assert retry.seen == []
    assert res["selected_count"] == 0


def test_retry_also_applies_when_not_only_unclassified(tmp_path: Path, live):
    _, db_path = _store(tmp_path, _item("n1", NOW - timedelta(days=1)))
    live(fail=True)
    classify_stored_news(db_path=db_path)

    retry = live(fail=False)
    classify_stored_news(db_path=db_path)
    assert retry.seen == ["n1"]


def test_retries_share_the_item_cap(tmp_path: Path, live):
    store, db_path = _store(tmp_path, _item("n1", NOW - timedelta(days=2)))
    live(fail=True)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    store.upsert_news_items(pd.DataFrame([_item("n2", NOW - timedelta(days=1))]))

    capped = live(fail=False)
    res = classify_stored_news(db_path=db_path, only_unclassified=True, limit=1)
    assert res["selected_count"] == 1
    assert len(capped.seen) == 1


def test_mock_row_never_lands_on_a_failed_real_item(tmp_path: Path, live, monkeypatch):
    store, db_path = _store(tmp_path, _item("n1", NOW - timedelta(days=1)))
    live(fail=True)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    error_row = _rows(store, "n1").iloc[0].to_dict()

    mock_row = {**error_row, "classification_id": "c_mock", "ai_provider": "mock",
                "classification_status": "success"}
    res = store.write_news_classifications(pd.DataFrame([mock_row]), pd.DataFrame(), pd.DataFrame())
    assert res["skipped_protected"] == 1
    # Re-writing the stored error row itself (a hydrate of the same snapshot) is a no-op.
    res = store.write_news_classifications(pd.DataFrame([error_row]), pd.DataFrame(), pd.DataFrame())
    assert res["skipped_protected"] == 1
    assert len(_rows(store, "n1")) == 1


def test_writer_keeps_an_error_row_listed_after_its_success(tmp_path: Path, live):
    """A hydrated partition may list an id's success before its earlier error."""
    store, db_path = _store(tmp_path, _item("n1", NOW - timedelta(days=1)))
    live(fail=True)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    live(fail=False)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    rows = _rows(store, "n1")
    reversed_frame = rows.iloc[::-1].reset_index(drop=True)

    cold = DuckDBStore(tmp_path / "cold.duckdb")
    cold.initialize()
    res = cold.write_news_classifications(reversed_frame, pd.DataFrame(), pd.DataFrame())
    assert res["inserted"] == 2
    assert list(_rows(cold, "n1")["classification_status"]) == ["error", "success"]


def _error_then_success(tmp_path: Path, live) -> tuple[DuckDBStore, Path]:
    store, db_path = _store(tmp_path, _item("n1", NOW - timedelta(days=1)))
    live(fail=True)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    live(fail=False)
    classify_stored_news(db_path=db_path, only_unclassified=True)
    assert list(_rows(store, "n1")["classification_status"]) == ["error", "success"]
    return store, db_path


def test_every_consumer_prefers_the_later_success(tmp_path: Path, live):
    store, db_path = _error_then_success(tmp_path, live)
    success_id = _rows(store, "n1").iloc[1]["classification_id"]

    # Effective reader (scoring, accumulation, reports, calibration): one row, the success.
    classifications, theme_scores, sector_impacts = store.read_effective_news_classifications()
    assert list(classifications["classification_id"]) == [success_id]
    assert len(theme_scores) == 1 and len(sector_impacts) == 1

    # Scoring.
    result = build_stored_news_scores(db_path=db_path)
    assert result.runs.iloc[0]["scored_item_count"] == 1

    # Confidence ledger: impacts attach to the success row, not the error.
    ledger = build_confidence_ledger(sector_impacts, classifications)
    assert set(ledger["classification_id"]) == {success_id}

    # Events store reader and builder.
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        items = load_all_items(con)
    finally:
        con.close()
    # A day ahead: the writer stores an aware classified_at in the session's local
    # wall clock (a separate, pre-existing defect), so "now" may precede it locally.
    as_of = (datetime.now(UTC) + timedelta(days=1)).replace(tzinfo=None)
    chosen, _ = select_classification(items[0], as_of)
    assert chosen is not None and chosen.status == "success"

    # Shocks narrative.
    raw = store.read_table("news_classifications")
    assert _summary_by_news_id(raw) == {"n1": "Retried success"}

    # Coverage report.
    coverage = rolling_classified_share(
        store.read_table("news_items"), raw, as_of=NOW.date(), window_days=14
    )
    assert coverage["classified"] == 1


def test_history_round_trip_keeps_both_rows_and_success_wins(tmp_path: Path, live):
    store, db_path = _error_then_success(tmp_path, live)
    history_dir = tmp_path / "history"
    export_news_history(db_path=db_path, history_dir=history_dir)

    cold = DuckDBStore(tmp_path / "cold.duckdb")
    cold.initialize()
    hydrate_news_history(db_path=tmp_path / "cold.duckdb", history_dir=history_dir)
    assert list(_rows(cold, "n1")["classification_status"]) == ["error", "success"]
    effective, _, _ = cold.read_effective_news_classifications()
    assert list(effective["classification_status"]) == ["success"]

    # Hydrating again onto the warm store adds nothing.
    again = hydrate_news_history(db_path=db_path, history_dir=history_dir)
    assert again.get("classifications_inserted", 0) == 0
    assert len(_rows(store, "n1")) == 2
