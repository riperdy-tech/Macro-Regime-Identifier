"""GDELT historical backfill: offline parsing/conversion + backfill ledger mode."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from macro_engine.news.confidence_calibration import (
    build_calibration_artifact,
    build_confidence_ledger,
)
from macro_engine.news.gdelt_backfill import (
    GdeltBackfillConfig,
    build_doc_url,
    fetch_gdelt_news_items,
    gdelt_articles_to_news_items,
    make_default_fetch,
    parse_gdelt_articles,
)


def _gdelt_json(titles):
    return json.dumps(
        {
            "articles": [
                {
                    "title": t,
                    "seendate": "20250115T120000Z",
                    "url": f"https://example.invalid/{i}",
                    "domain": "example.invalid",
                    "language": "English",
                    "sourcecountry": "United States",
                }
                for i, t in enumerate(titles)
            ]
        }
    )


def _config():
    return GdeltBackfillConfig.model_validate(
        {
            "start_date": "2025-01-01",
            "end_date": "2025-01-15",
            "window_days": 7,
            "max_records_per_window": 250,
            "request_delay_seconds": 0.0,
            "queries": [
                {"source_group": "energy_commodities", "query": "oil prices"},
                {"source_group": "inflation_rates", "query": "inflation"},
            ],
        }
    )


# ---- parsing ---------------------------------------------------------------


def test_parse_handles_empty_and_garbage():
    assert parse_gdelt_articles("") == []
    assert parse_gdelt_articles("not json") == []
    assert parse_gdelt_articles(json.dumps({"foo": 1})) == []
    assert len(parse_gdelt_articles(_gdelt_json(["a"]))) == 1


def test_build_doc_url_has_date_window_and_query():
    from datetime import UTC, datetime

    url = build_doc_url("oil prices", datetime(2025, 1, 1, tzinfo=UTC),
                        datetime(2025, 1, 8, tzinfo=UTC), max_records=250)
    assert "startdatetime=20250101000000" in url
    assert "enddatetime=20250108000000" in url
    assert "query=oil" in url and "mode=ArtList" in url


def test_articles_to_news_items_uses_headline_as_body():
    items = gdelt_articles_to_news_items(
        parse_gdelt_articles(_gdelt_json(["Oil jumps on supply cut"])),
        source_group="energy_commodities",
    )
    assert len(items) == 1
    it = items[0]
    assert it.provider == "gdelt"
    assert it.body == it.title == "Oil jumps on supply cut"
    assert it.raw_metadata["source_group"] == "energy_commodities"
    assert it.raw_metadata["body_is_headline"] is True
    assert it.published_at is not None and it.published_at.year == 2025


def test_unknown_source_group_rejected():
    with pytest.raises(ValueError):
        GdeltBackfillConfig.model_validate(
            {"start_date": "2025-01-01", "end_date": "2025-01-08",
             "queries": [{"source_group": "not_a_group", "query": "x"}]}
        )


# ---- fetch loop (injected network) -----------------------------------------


def test_fetch_loop_dedupes_and_counts_windows():
    calls = {"n": 0}

    def fake_fetch(url: str) -> str:
        calls["n"] += 1
        # same two headlines every window/query -> dedupe by content_hash
        return _gdelt_json(["Repeated macro headline", "Another macro headline"])

    result = fetch_gdelt_news_items(_config(), fetch=fake_fetch)
    # 2 windows (Jan1-8, Jan8-15) x 2 queries = 4 fetches
    assert calls["n"] == 4
    assert result.requested_windows == 4
    assert result.fetched_articles == 8  # 2 per fetch x 4
    # deduped to the 2 unique headlines
    assert len(result.news_items) == 2


def test_progress_callback_fires_per_request():
    msgs = []
    fetch_gdelt_news_items(
        _config(), fetch=lambda u: _gdelt_json(["x"]), progress=msgs.append
    )
    assert len(msgs) == 4  # 2 windows x 2 queries
    assert "[4/4]" in msgs[-1]


def test_default_fetch_retries_on_429(monkeypatch):
    import urllib.error

    import macro_engine.news.gdelt_backfill as gb

    monkeypatch.setattr(gb.time, "sleep", lambda *_: None)  # no real waiting
    calls = {"n": 0}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"articles":[]}'

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        return _Resp()

    monkeypatch.setattr(gb, "urlopen", fake_urlopen)
    out = make_default_fetch(max_retries_429=4)("https://example.invalid")
    assert out == '{"articles":[]}'
    assert calls["n"] == 3  # two 429s then success


def test_default_fetch_gives_up_after_max_retries(monkeypatch):
    import urllib.error

    import macro_engine.news.gdelt_backfill as gb

    monkeypatch.setattr(gb.time, "sleep", lambda *_: None)

    def always_429(req, timeout=30):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(gb, "urlopen", always_429)
    with pytest.raises(urllib.error.HTTPError):
        make_default_fetch(max_retries_429=2)("https://example.invalid")


def test_fetch_loop_records_errors_without_aborting():
    def boom(url: str) -> str:
        raise RuntimeError("network down")

    result = fetch_gdelt_news_items(_config(), fetch=boom)
    assert result.news_items == []
    assert len(result.errors) == 4  # one per window x query


# ---- backfill calibration mode (published_at basis) ------------------------


def test_ledger_published_at_basis_uses_news_dates():
    impacts = pd.DataFrame(
        [{"news_id": "n1", "sector_id": "energy", "impact_direction": "tailwind",
          "impact_score": 0.5, "confidence": 0.9, "rationale": ""}]
    )
    classes = pd.DataFrame(
        [{"classification_id": "c1", "news_id": "n1", "classified_at": "2026-05-31T00:00:00Z"}]
    )
    items = pd.DataFrame([{"news_id": "n1", "published_at": "2025-01-15T00:00:00Z"}])

    live = build_confidence_ledger(impacts, classes)
    backfill = build_confidence_ledger(
        impacts, classes, news_items=items, date_basis="published_at"
    )
    assert live.iloc[0]["prediction_date"] == pd.Timestamp("2026-05-31").date()
    assert backfill.iloc[0]["prediction_date"] == pd.Timestamp("2025-01-15").date()


def test_published_at_basis_requires_news_items():
    impacts = pd.DataFrame(
        [{"news_id": "n1", "sector_id": "energy", "impact_direction": "tailwind",
          "impact_score": 0.5, "confidence": 0.9, "rationale": ""}]
    )
    classes = pd.DataFrame(
        [{"classification_id": "c1", "news_id": "n1", "classified_at": "2026-05-31T00:00:00Z"}]
    )
    with pytest.raises(ValueError):
        build_confidence_ledger(impacts, classes, date_basis="published_at")


def test_artifact_marks_provisional():
    art = build_calibration_artifact(pd.DataFrame(), horizons_months=[1], provisional=True)
    assert art["provisional"] is True
    assert art["fitted"] is False
    # default stays non-provisional
    art2 = build_calibration_artifact(pd.DataFrame(), horizons_months=[1])
    assert art2["provisional"] is False
