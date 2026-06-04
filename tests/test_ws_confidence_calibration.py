"""LLM confidence calibration instrumentation: ledger + bucket scoring."""

from __future__ import annotations

import pandas as pd

from macro_engine.news.confidence_calibration import (
    CALIBRATION_ARTIFACT_SCHEMA_VERSION,
    attach_forward_returns,
    bucket_calibration,
    build_calibration_artifact,
    build_confidence_ledger,
    merge_ledger,
)
from macro_engine.news.confidence_consumer import apply_confidence_calibration

PROXIES = {"energy": "XLE"}
BENCHMARK = "SPY"
HORIZONS = [1]


def _classifications():
    return pd.DataFrame(
        [
            {"classification_id": "c1", "news_id": "n1", "classified_at": "2026-01-02T00:00:00Z"},
            {"classification_id": "c2", "news_id": "n2", "classified_at": "2026-01-02T00:00:00Z"},
            {"classification_id": "c3", "news_id": "n3", "classified_at": "2026-01-02T00:00:00Z"},
        ]
    )


def _sector_impacts():
    return pd.DataFrame(
        [
            {"news_id": "n1", "sector_id": "energy", "impact_direction": "tailwind",
             "impact_score": 0.6, "confidence": 0.90, "rationale": ""},
            {"news_id": "n2", "sector_id": "energy", "impact_direction": "headwind",
             "impact_score": -0.5, "confidence": 0.85, "rationale": ""},
            {"news_id": "n3", "sector_id": "energy", "impact_direction": "neutral",
             "impact_score": 0.0, "confidence": 0.20, "rationale": ""},
        ]
    )


def _prices():
    # XLE +10% over the month, SPY +2% -> relative +8% for energy.
    return pd.DataFrame(
        [
            {"ticker": "XLE", "date": "2026-01-02", "close": 100.0},
            {"ticker": "XLE", "date": "2026-02-02", "close": 110.0},
            {"ticker": "SPY", "date": "2026-01-02", "close": 100.0},
            {"ticker": "SPY", "date": "2026-02-02", "close": 102.0},
        ]
    )


def test_ledger_maps_direction_to_expected_sign():
    ledger = build_confidence_ledger(_sector_impacts(), _classifications())
    by_news = dict(zip(ledger["news_id"], ledger["expected_sign"]))
    assert by_news["n1"] == 1.0      # tailwind
    assert by_news["n2"] == -1.0     # headwind
    assert pd.isna(by_news["n3"])    # neutral: no directional call
    assert set(ledger["prediction_date"]) == {pd.Timestamp("2026-01-02").date()}


def test_attach_forward_returns_computes_relative():
    ledger = build_confidence_ledger(_sector_impacts(), _classifications())
    enriched = attach_forward_returns(
        ledger, _prices(), proxies=PROXIES, benchmark_ticker=BENCHMARK, horizons_months=HORIZONS
    )
    rel = dict(zip(enriched["news_id"], enriched["relative_forward_1m_return"]))
    # 0.10 - 0.02 = 0.08 for every energy row.
    assert abs(rel["n1"] - 0.08) < 1e-9
    assert abs(rel["n2"] - 0.08) < 1e-9


def test_bucket_scoring_hit_and_miss():
    ledger = build_confidence_ledger(_sector_impacts(), _classifications())
    enriched = attach_forward_returns(
        ledger, _prices(), proxies=PROXIES, benchmark_ticker=BENCHMARK, horizons_months=HORIZONS
    )
    table = bucket_calibration(enriched, horizon_months=1)
    top = table[table["confidence_bucket"] == "0.8-1.0"].iloc[0]
    # n1 tailwind + rel + => hit; n2 headwind + rel + => miss. n3 neutral excluded.
    assert top["n"] == 2
    assert top["hit_rate"] == 0.5
    assert abs(top["avg_signed_relative_return"]) < 1e-9   # +0.08 and -0.08 cancel
    assert abs(top["avg_raw_relative_return"] - 0.08) < 1e-9
    # quiet buckets present but empty
    low = table[table["confidence_bucket"] == "0.0-0.3"].iloc[0]
    assert low["n"] == 0
    assert pd.isna(low["hit_rate"])


def test_empty_inputs_safe():
    ledger = build_confidence_ledger(pd.DataFrame(), _classifications())
    assert ledger.empty
    enriched = attach_forward_returns(
        ledger, _prices(), proxies=PROXIES, benchmark_ticker=BENCHMARK, horizons_months=HORIZONS
    )
    table = bucket_calibration(enriched, horizon_months=1)
    assert list(table["n"]) == [0, 0, 0, 0]  # four default buckets, all empty


def _enriched():
    ledger = build_confidence_ledger(_sector_impacts(), _classifications())
    return attach_forward_returns(
        ledger, _prices(), proxies=PROXIES, benchmark_ticker=BENCHMARK, horizons_months=HORIZONS
    )


def test_artifact_not_ready_below_threshold():
    art = build_calibration_artifact(_enriched(), horizons_months=HORIZONS)  # default min 200
    assert art["schema_version"] == CALIBRATION_ARTIFACT_SCHEMA_VERSION
    assert art["ready"] is False
    assert art["horizons"] == []
    assert art["fitted"] is False


def test_artifact_ready_when_threshold_met():
    art = build_calibration_artifact(_enriched(), horizons_months=HORIZONS, min_directional_calls=2)
    assert art["ready"] is True
    assert "1m" in art["horizons"]
    assert art["directional_calls"] >= 2


def test_ready_artifact_still_identity_until_fitted():
    # 5->6 bridge: even a ready artifact yields identity (no transform trained).
    art = build_calibration_artifact(_enriched(), horizons_months=HORIZONS, min_directional_calls=2)
    out = apply_confidence_calibration(
        0.77, horizon="1m", calibration_artifact=art, enabled=True, min_directional_calls=2
    )
    assert out == 0.77


def test_merge_ledger_accumulates_and_dedupes():
    ledger = build_confidence_ledger(_sector_impacts(), _classifications())
    # Re-run with an updated confidence for c1 -> latest wins, no row duplication.
    updated = ledger.copy()
    updated.loc[updated["classification_id"] == "c1", "confidence"] = 0.55
    merged = merge_ledger(ledger, updated)
    assert len(merged) == len(ledger)  # same keys, no growth
    c1 = merged[merged["classification_id"] == "c1"].iloc[0]
    assert c1["confidence"] == 0.55
