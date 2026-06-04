"""WS news-selection: pure importance ranking within the classification budget."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd

from macro_engine.news.config import NewsSelectionConfig
from macro_engine.news.selection import (
    assign_event_ids,
    rank_and_select,
    score_items,
    select_within_budget,
)

NOW = datetime(2026, 5, 30, tzinfo=UTC)
LEX = {"inflation", "fed", "jobs", "oil", "rate"}


def _cfg(**over) -> NewsSelectionConfig:
    base = dict(
        daily_cap=10,
        min_priority=0.15,
        half_life_days=4.0,
        max_age_days=14,
        source_authority={"federal_reserve_press": 1.6, "nvidia_blog": 0.7},
        group_quota_weights={"macro_general": 1.0, "technology_ai": 1.0, "labor": 1.0},
        min_body_words=5,
        drop_likely_non_news=True,
    )
    base.update(over)
    return NewsSelectionConfig(**base)


def _row(nid, *, source, group, title, body, days_ago):
    return {
        "news_id": nid,
        "source": source,
        "source_url": None,
        "title": title,
        "body": body,
        "published_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "ingested_at": NOW.isoformat(),
        "provider": "rss",
        "raw_metadata_json": json.dumps({"source_group": group}),
        "content_hash": nid,
    }


def _df(rows):
    return pd.DataFrame(rows)


# ---- quality gate ----------------------------------------------------------


def test_drops_short_body_and_non_news():
    rows = [
        _row("a", source="cnbc_economy", group="macro_general",
             title="Fed signals inflation concern", body="one two three", days_ago=1),  # short
        _row("b", source="cnbc_markets", group="macro_general",
             title="Login to continue", body="please login subscribe to continue now here", days_ago=1),  # non-news
        _row("c", source="cnbc_economy", group="macro_general",
             title="Fed holds rate amid inflation", body="The Fed held rates today amid inflation worries again", days_ago=1),
    ]
    scored = score_items(_df(rows), config=_cfg(), lexicon=LEX, now=NOW)
    elig = dict(zip(scored["news_id"], scored["eligible"]))
    assert elig["a"] is False  # short body
    assert elig["b"] is False  # non-news marker
    assert elig["c"] is True


# ---- ordering: important-older beats trivial-newer (the core fix) -----------


def test_important_older_outranks_trivial_newer():
    rows = [
        _row("important", source="federal_reserve_press", group="macro_general",
             title="Fed warns on inflation and rate path",
             body="Federal Reserve officials warned about inflation and the rate path ahead", days_ago=3),
        _row("trivial", source="nvidia_blog", group="technology_ai",
             title="New developer blog post about a demo",
             body="A new blog post covering a small developer demo released today here", days_ago=0),
    ]
    scored = score_items(_df(rows), config=_cfg(), lexicon=LEX, now=NOW)
    s = dict(zip(scored["news_id"], scored["selection_score"]))
    assert s["important"] > s["trivial"]
    # cap=1: quotas floor to 0, global fill takes the single highest priority.
    sel = select_within_budget(scored, config=_cfg(daily_cap=1))
    assert list(sel["news_id"]) == ["important"]


# ---- per-group quotas guarantee breadth ------------------------------------


def test_group_quota_caps_overrepresented_group():
    rows = []
    for i in range(10):
        rows.append(_row(f"m{i}", source="cnbc_economy", group="macro_general",
                         title="Inflation and rate update today",
                         body="Macro inflation and rate update with plenty of words here now", days_ago=1))
    for i in range(2):
        rows.append(_row(f"l{i}", source="bls_latest", group="labor",
                         title="Jobs report shows hiring",
                         body="The latest jobs report shows continued hiring across sectors today", days_ago=1))
    cfg = _cfg(daily_cap=4, group_quota_weights={"macro_general": 1.0, "labor": 1.0})
    scored = score_items(_df(rows), config=cfg, lexicon=LEX, now=NOW)
    sel = select_within_budget(scored, config=cfg)
    counts = sel["source_group"].value_counts().to_dict()
    assert counts.get("macro_general", 0) == 2  # capped by quota, not all 10
    assert counts.get("labor", 0) == 2
    assert len(sel) == 4


# ---- min_priority threshold ------------------------------------------------


def test_threshold_drops_weak_items():
    rows = [
        _row("strong", source="federal_reserve_press", group="macro_general",
             title="Fed inflation rate decision",
             body="Federal Reserve inflation and rate decision detailed at length here today", days_ago=1),
        _row("weak", source="nvidia_blog", group="technology_ai",
             title="A small note about nothing in particular",
             body="A small note about nothing in particular with enough words to pass length", days_ago=13),
    ]
    cfg = _cfg(min_priority=0.5)
    scored = score_items(_df(rows), config=cfg, lexicon=LEX, now=NOW)
    elig = dict(zip(scored["news_id"], scored["eligible"]))
    assert elig["strong"] is True
    assert elig["weak"] is False  # low authority * stale * no keywords < 0.5


# ---- cap never exceeded ----------------------------------------------------


def test_selection_never_exceeds_cap():
    rows = [
        _row(f"x{i}", source="cnbc_economy", group="macro_general",
             title="Inflation rate fed jobs oil",
             body="Inflation rate fed jobs oil with plenty more words to satisfy length", days_ago=1)
        for i in range(50)
    ]
    cfg = _cfg(daily_cap=12)
    sel = select_within_budget(score_items(_df(rows), config=cfg, lexicon=LEX, now=NOW), config=cfg)
    assert len(sel) <= 12


def test_empty_input_safe():
    sel = rank_and_select(pd.DataFrame(), config=_cfg(), now=NOW)
    assert sel.empty


# ---- integration with real lexicon -----------------------------------------


# ---- keyword salience cap --------------------------------------------------


def test_keyword_hits_are_capped():
    # An article repeating every lexicon keyword cannot outscore the cap.
    rows = [
        _row("stuffed", source="cnbc_economy", group="macro_general",
             title="inflation fed jobs oil rate inflation fed jobs oil rate",
             body="inflation fed jobs oil rate inflation fed jobs oil rate and more words here", days_ago=1),
    ]
    scored = score_items(_df(rows), config=_cfg(max_keyword_hits=2), lexicon=LEX, now=NOW)
    s = float(scored.loc[scored["news_id"] == "stuffed", "selection_score"].iloc[0])
    # authority 1.0 * (1 + min(5, 2)) * fresh(1d, hl=4) = 3 * 0.5**0.25
    assert s == 3 * (0.5 ** 0.25)


# ---- novelty / event dedupe ------------------------------------------------


def test_near_duplicates_are_penalized():
    dup_body = "Federal Reserve officials warned inflation stays sticky and the rate path runs higher"
    rows = [
        _row("a", source="federal_reserve_press", group="macro_general",
             title="Fed warns inflation sticky", body=dup_body, days_ago=1),
        _row("b", source="cnbc_markets", group="macro_general",
             title="Fed warns inflation sticky", body=dup_body, days_ago=1),
    ]
    scored = score_items(_df(rows), config=_cfg(), lexicon=LEX, now=NOW)
    s = dict(zip(scored["news_id"], scored["selection_score"]))
    # 'a' (higher authority) is canonical at full weight; 'b' penalized to 0.4x base.
    assert s["a"] > s["b"]
    assert s["b"] == s["a"] / 1.6 * 0.4  # b base = a base / authority ratio, * penalty


def test_distinct_articles_keep_full_novelty():
    rows = [
        _row("a", source="cnbc_economy", group="macro_general",
             title="Inflation cools as prices ease",
             body="Consumer inflation cooled last month as goods prices eased broadly across the board", days_ago=1),
        _row("b", source="bls_latest", group="labor",
             title="Jobs report shows strong hiring",
             body="The latest jobs report shows employers added many positions and hiring stayed strong", days_ago=1),
    ]
    scored = score_items(_df(rows), config=_cfg(), lexicon=LEX, now=NOW)
    # Different narratives: neither penalized (both eligible, full base score).
    assert all(scored["eligible"])


def test_dedupe_disabled_keeps_duplicates_full():
    dup_body = "Federal Reserve officials warned inflation stays sticky and the rate path runs higher"
    rows = [
        _row("a", source="cnbc_markets", group="macro_general",
             title="Fed warns inflation sticky", body=dup_body, days_ago=1),
        _row("b", source="cnbc_markets", group="macro_general",
             title="Fed warns inflation sticky", body=dup_body, days_ago=1),
    ]
    scored = score_items(_df(rows), config=_cfg(dedupe_near_duplicates=False), lexicon=LEX, now=NOW)
    s = dict(zip(scored["news_id"], scored["selection_score"]))
    assert s["a"] == s["b"]  # identical, no penalty when disabled


# ---- event_id plumbing -----------------------------------------------------


def test_near_duplicates_share_event_id():
    dup_body = "Federal Reserve officials warned inflation stays sticky and the rate path runs higher"
    rows = [
        _row("a", source="federal_reserve_press", group="macro_general",
             title="Fed warns inflation sticky", body=dup_body, days_ago=1),
        _row("b", source="cnbc_markets", group="macro_general",
             title="Fed warns inflation sticky", body=dup_body, days_ago=1),
    ]
    scored = score_items(_df(rows), config=_cfg(), lexicon=LEX, now=NOW)
    ev = dict(zip(scored["news_id"], scored["event_id"]))
    # both collapse to the higher-priority canonical (a, fed press authority 1.6)
    assert ev["a"] == ev["b"] == "a"


def test_distinct_articles_have_own_event_id():
    rows = [
        _row("a", source="cnbc_economy", group="macro_general",
             title="Inflation cools as prices ease",
             body="Consumer inflation cooled last month as goods prices eased broadly across the board", days_ago=1),
        _row("b", source="bls_latest", group="labor",
             title="Jobs report shows strong hiring",
             body="The latest jobs report shows employers added many positions and hiring stayed strong", days_ago=1),
    ]
    scored = score_items(_df(rows), config=_cfg(), lexicon=LEX, now=NOW)
    ev = dict(zip(scored["news_id"], scored["event_id"]))
    assert ev["a"] == "a" and ev["b"] == "b"


def test_assign_event_ids_standalone_clusters_duplicates():
    dup = "Federal Reserve officials warned inflation stays sticky and the rate path runs higher"
    items = pd.DataFrame(
        [
            {"news_id": "a", "title": "Fed warns", "body": dup},
            {"news_id": "b", "title": "Fed warns", "body": dup},
            {"news_id": "c", "title": "Jobs report", "body": "employers added many positions and hiring stayed strong overall"},
        ]
    )
    ev = assign_event_ids(items, similarity_threshold=0.6)
    assert ev["a"] == ev["b"]            # near-duplicates clustered
    assert ev["c"] == "c"               # distinct narrative own event
    assert ev["a"] in {"a", "b"}        # canonical is a cluster member


def test_assign_event_ids_empty_safe():
    assert assign_event_ids(pd.DataFrame()) == {}


def test_rank_and_select_uses_real_keyword_lexicon():
    rows = [
        _row("macro", source="cnbc_economy", group="macro_general",
             title="Inflation accelerates as Fed weighs interest rate move",
             body="Inflation accelerates while the Fed weighs an interest rate move this month", days_ago=1),
        _row("blog", source="nvidia_blog", group="technology_ai",
             title="A developer demo recap",
             body="A developer demo recap with enough words present to pass the length gate", days_ago=1),
    ]
    sel = rank_and_select(_df(rows), config=_cfg(daily_cap=1), now=NOW)
    assert list(sel["news_id"]) == ["macro"]
