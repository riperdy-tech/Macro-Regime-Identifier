"""Truncation-safe classification: detection, auto-retry at a larger budget,
usage-report metrics, and monitoring alert."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd

from macro_engine.news.classify import _response_was_truncated, classify_news_item
from macro_engine.news.config import load_news_monitoring_config, load_news_themes_config
from macro_engine.news.monitoring import build_classification_quality_run
from macro_engine.news.schema import NewsItem
from macro_engine.news.usage_report import build_live_ai_usage_report

THEMES = load_news_themes_config("config/news_themes.yaml")


def _item() -> NewsItem:
    return NewsItem(
        news_id="news_test1",
        source="cnbc_economy",
        source_url=None,
        title="Fed signals on inflation and rates",
        body="The Federal Reserve signaled its view on inflation and interest rates today.",
        published_at=datetime(2026, 5, 30, tzinfo=UTC),
        ingested_at=datetime(2026, 5, 30, tzinfo=UTC),
        provider="rss",
        raw_metadata={},
        content_hash="news_test1",
    )


def _valid_payload() -> dict:
    theme_id = sorted(THEMES.active_theme_ids)[0]
    return {
        "summary": "A concise diagnostic summary.",
        "macro_themes": [
            {
                "theme_id": theme_id,
                "direction": "neutral",
                "severity": 0.1,
                "confidence": 0.2,
                "time_horizon": "short_term",
                "rationale": "context",
            }
        ],
        "sector_impacts": [],
        "entities": [],
        "secular_theme": None,
        "overall_severity": 0.1,
        "overall_confidence": 0.2,
        "time_horizon": "short_term",
    }


def _truncated_payload() -> dict:
    p = _valid_payload()
    p["summary"] = ""  # empty summary = the symptom of a cut-off JSON
    p["_provider_usage"] = {"completion_tokens": 2048, "finish_reason": "length"}
    return p


# ---- detection -------------------------------------------------------------


def test_response_was_truncated():
    assert _response_was_truncated({"_provider_usage": {"finish_reason": "length"}}) is True
    assert _response_was_truncated({"_provider_usage": {"finish_reason": "stop"}}) is False
    assert _response_was_truncated({}) is False
    assert _response_was_truncated(None) is False


# ---- auto-retry at larger budget -------------------------------------------


class _FakeClassifier:
    provider_name = "deepseek"
    model_name = "deepseek-v4-flash"

    def __init__(self):
        self.config = SimpleNamespace(max_tokens=2048, truncation_retry_multiplier=2.0)
        self.retry_max_tokens: list[int | None] = []

    def classify(self, item, themes):
        return _truncated_payload()

    def classify_with_feedback(self, item, themes, *, validation_error, previous_response, max_tokens=None):
        self.retry_max_tokens.append(max_tokens)
        return _valid_payload()


def test_truncated_item_retried_with_larger_budget_and_succeeds():
    clf = _FakeClassifier()
    record = classify_news_item(_item(), classifier=clf, themes=THEMES, max_retries=1)
    assert record.classification_status == "success"
    # retry fired once, with max_tokens = ceil(2048 * 2.0)
    assert clf.retry_max_tokens == [4096]


def test_non_truncation_failure_retries_without_token_bump():
    class _BadJsonClassifier(_FakeClassifier):
        def classify(self, item, themes):
            p = _valid_payload()
            p["summary"] = ""  # invalid, but NOT truncated (no length finish_reason)
            return p

    clf = _BadJsonClassifier()
    record = classify_news_item(_item(), classifier=clf, themes=THEMES, max_retries=1)
    assert record.classification_status == "success"
    assert clf.retry_max_tokens == [None]  # default budget, no bump


# ---- usage report metrics --------------------------------------------------


def _classification_row(nid, completion, finish_reason):
    raw = {"response": {"_provider_usage": {"completion_tokens": completion, "finish_reason": finish_reason, "total_tokens": completion + 800, "prompt_tokens": 800}}}
    return {
        "news_id": nid,
        "ai_provider": "deepseek",
        "ai_model": "deepseek-v4-flash",
        "classified_at": "2026-05-30T00:00:00Z",
        "classification_status": "success" if finish_reason == "stop" else "error",
        "error_message": None if finish_reason == "stop" else "summary is required",
        "raw_ai_response_json": json.dumps(raw),
    }


def test_usage_report_reports_truncation_and_completion_stats():
    df = pd.DataFrame([
        _classification_row("a", 700, "stop"),
        _classification_row("b", 733, "stop"),
        _classification_row("c", 2048, "length"),
    ])
    payload = build_live_ai_usage_report(df)
    assert payload["truncation"]["truncation_count"] == 1
    assert payload["truncation"]["truncation_rate"] == round(1 / 3, 4)
    assert payload["completion_token_stats"]["max"] == 2048
    assert payload["completion_token_stats"]["count"] == 3


# ---- monitoring alert ------------------------------------------------------


def test_monitoring_flags_truncation():
    config = load_news_monitoring_config("config/news_monitoring.yaml")
    df = pd.DataFrame([
        _classification_row("a", 700, "stop"),
        _classification_row("b", 2048, "length"),
    ])
    run = build_classification_quality_run(config=config, classifications=df, run_id="r1")
    row = run.iloc[0]
    assert row["quality_status"] == "warning"
    details = json.loads(row["details_json"])
    assert details["truncation_count"] == 1
    assert any("truncation" in w for w in details["warnings"])
