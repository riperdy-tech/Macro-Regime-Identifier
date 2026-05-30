"""WS-C dynamic secular theme discovery + promotion gate tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from macro_engine.news.config import load_news_themes_config
from macro_engine.news.theme_discovery import (
    ThemeCandidate,
    build_discovery_prompt,
    evaluate_promotion,
    parse_candidate_response,
    promote_candidates,
    select_discovery_candidates,
)

EXISTING = {"ai_compute", "cloud_software"}


def _news(n: int, *, source_prefix: str = "src", start_day: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "news_id": [f"news_{i}" for i in range(n)],
            "title": [f"Headline {i}" for i in range(n)],
            "body": [f"Body text {i}" for i in range(n)],
            "source": [f"{source_prefix}_{i}" for i in range(n)],
            "published_at": pd.to_datetime(
                [f"2026-05-{start_day + i:02d}" for i in range(n)]
            ),
        }
    )


# ---- select_discovery_candidates ------------------------------------------


def test_select_picks_null_and_low_confidence():
    news = _news(3)
    cls = pd.DataFrame(
        {
            "news_id": ["news_0", "news_1", "news_2"],
            "secular_theme": ["ai_compute", None, "ai_compute"],
            "confidence": [0.9, 0.8, 0.10],
            "classified_at": pd.to_datetime(["2026-05-10"] * 3),
        }
    )
    out = select_discovery_candidates(news, cls, max_confidence=0.35)
    ids = set(out["news_id"])
    assert "news_1" in ids  # null secular_theme
    assert "news_2" in ids  # low confidence
    assert "news_0" not in ids  # placed + high confidence


def test_select_empty_classifications_returns_all():
    news = _news(2)
    out = select_discovery_candidates(news, pd.DataFrame(), max_confidence=0.35)
    assert len(out) == 2


# ---- parse_candidate_response ---------------------------------------------


def test_parse_computes_support_from_our_data_not_model():
    news = _news(10, start_day=1)
    text = json.dumps(
        {
            "candidates": [
                {
                    "theme_id": "fusion_power",
                    "label": "Fusion Power",
                    "description": "Commercial fusion energy buildout.",
                    "article_indices": [0, 1, 2, 3, 4, 5, 6, 7],
                    "distinct_articles": 999,  # model lie - must be ignored
                }
            ]
        }
    )
    cands = parse_candidate_response(text, news, existing_theme_ids=EXISTING)
    assert len(cands) == 1
    c = cands[0]
    assert c.theme_id == "fusion_power"
    assert c.distinct_articles == 8  # from data, not the 999 claim
    assert c.source_diversity == 8
    assert c.days_observed == 8


def test_parse_drops_existing_and_malformed():
    news = _news(3)
    text = json.dumps(
        {
            "candidates": [
                {"theme_id": "ai_compute", "label": "x", "description": "y", "article_indices": [0]},
                {"theme_id": "BadID!", "label": "x", "description": "y", "article_indices": [0]},
                {"theme_id": "ok_theme", "label": "", "description": "y", "article_indices": [0]},
            ]
        }
    )
    cands = parse_candidate_response(text, news, existing_theme_ids=EXISTING)
    assert cands == []


# ---- evaluate_promotion ----------------------------------------------------


def test_gate_passes_when_all_thresholds_met():
    cand = ThemeCandidate(
        "fusion_power", "Fusion", "desc",
        distinct_articles=8, days_observed=5, source_diversity=3,
    )
    ok, reason = evaluate_promotion(cand, existing_theme_ids=EXISTING)
    assert ok and reason == "ok"


@pytest.mark.parametrize(
    "articles,days,sources,frag",
    [
        (7, 5, 3, "insufficient_articles"),
        (8, 4, 3, "insufficient_days"),
        (8, 5, 2, "insufficient_source_diversity"),
    ],
)
def test_gate_rejects_below_threshold(articles, days, sources, frag):
    cand = ThemeCandidate(
        "fusion_power", "Fusion", "desc",
        distinct_articles=articles, days_observed=days, source_diversity=sources,
    )
    ok, reason = evaluate_promotion(cand, existing_theme_ids=EXISTING)
    assert not ok and frag in reason


def test_gate_rejects_already_registered():
    cand = ThemeCandidate(
        "ai_compute", "AI", "desc",
        distinct_articles=99, days_observed=99, source_diversity=99,
    )
    ok, reason = evaluate_promotion(cand, existing_theme_ids=EXISTING)
    assert not ok and reason == "already_registered"


# ---- promote_candidates (additive write) ----------------------------------


def test_promote_is_additive_and_keeps_existing(tmp_path: Path):
    src = Path("config/news_themes.yaml")
    dst = tmp_path / "news_themes.yaml"
    shutil.copy(src, dst)
    before = load_news_themes_config(str(dst))
    before_ids = set(before.secular_theme_ids)

    passing = ThemeCandidate(
        "fusion_power", "Fusion Power", "Commercial fusion buildout.",
        distinct_articles=10, days_observed=6, source_diversity=4,
    )
    failing = ThemeCandidate(
        "noise_theme", "Noise", "too thin",
        distinct_articles=2, days_observed=1, source_diversity=1,
    )
    summary = promote_candidates(str(dst), [passing, failing])

    assert summary["new_count"] == 1
    assert "fusion_power" in summary["promoted"]
    assert any(r["theme_id"] == "noise_theme" for r in summary["rejected"])

    after = load_news_themes_config(str(dst))
    after_ids = set(after.secular_theme_ids)
    assert before_ids <= after_ids  # existing themes preserved
    assert "fusion_power" in after_ids
    assert "noise_theme" not in after_ids
    assert after.secular_themes["fusion_power"]["label"] == "Fusion Power"


def test_promote_no_survivors_leaves_file_untouched(tmp_path: Path):
    src = Path("config/news_themes.yaml")
    dst = tmp_path / "news_themes.yaml"
    shutil.copy(src, dst)
    original = dst.read_text(encoding="utf-8")
    failing = ThemeCandidate("noise", "n", "d", 1, 1, 1)
    summary = promote_candidates(str(dst), [failing])
    assert summary["new_count"] == 0
    assert dst.read_text(encoding="utf-8") == original  # unchanged on disk


# ---- regression: existing 9 themes intact ----------------------------------


def test_existing_nine_secular_themes_present():
    themes = load_news_themes_config("config/news_themes.yaml")
    expected = {
        "ai_compute", "physical_ai", "glp1_metabolic", "cloud_software",
        "energy_transition", "cybersecurity", "quantum_computing",
        "space_economy", "nuclear_renaissance",
    }
    assert expected <= set(themes.secular_theme_ids)


# ---- prompt builder smoke --------------------------------------------------


def test_build_prompt_excludes_existing_ids():
    news = _news(2)
    system, user = build_discovery_prompt(news, existing_theme_ids=EXISTING)
    assert "ai_compute" in system and "cloud_software" in system
    assert "[0]" in user and "[1]" in user
