"""scripts/events_replay.py: the daily replay over the reference fixtures and the v1.1 scorer, end to end."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import events_replay as rp  # noqa: E402

from macro_engine.events.builder import Classification, NewsItem  # noqa: E402
from macro_engine.events.config import load_e1_config  # noqa: E402

CFG = load_e1_config()
IDS, LABELS = rp.acc.load_reference()


def _oracle(items):
    """Test-only classification source built from the labels: every member of a labelled event names the
    same entity and theme. It exercises the replay and scorer plumbing; it says nothing about E1 quality."""
    out = {}
    for it in items:
        lab = LABELS[it.news_id]
        if lab["event"] in ("NONE", "noise"):
            continue
        out[it.news_id] = (Classification(
            classified_at=it.first_seen_at + timedelta(minutes=1), status="success", origin="live",
            themes=({"theme_id": "geopolitical_risk", "direction": "positive"},),
            entities=({"name": f"Actor of {lab['event']}", "relevance": 0.9, "entity_type": "other"},),
            severity=0.5, confidence=0.6),)
    return out


def test_fixture_items_load_with_hosts_and_state():
    items = rp.load_fixture_items()
    state = rp.load_state()
    assert len(items) == 300 and set(state) == {i.news_id for i in items}
    assert all(i.source_url is None or i.source_url.startswith("https://") for i in items)


def test_day_ends_cover_the_window_up_to_the_final_as_of():
    days = rp.day_ends(rp.load_fixture_items())
    assert days[0] == datetime(2026, 10, 9) and days[-1] == rp.FINAL_AS_OF
    assert all(b - a == timedelta(days=1) for a, b in zip(days, days[1:], strict=False))


def test_replay_with_no_classifications_runs_the_scorer_end_to_end():
    items = rp.load_fixture_items()
    res = rp.replay(items, {}, config=CFG)
    scored = rp.acc.accept(res["clusters"], res["meta"])
    assert res["clusters"] == {}  # unclassified reports never start an event
    assert scored["pass"] is False and set(scored["gates"]) == set(rp.acc.GATED)
    assert scored["false_merge"] is None  # no multi-item event in U: undefined fails
    assert len(res["daily"]) == 9


def test_replay_with_an_oracle_source_forms_events_and_scores_them():
    items = rp.load_fixture_items()
    res = rp.replay(items, _oracle(items), config=CFG)
    scored = rp.acc.accept(res["clusters"], res["meta"])
    assert res["clusters"]
    assert scored["false_split"] is not None and scored["false_merge"] is not None
    assert set(scored["gates"]) == set(rp.acc.GATED)
    detail = rp.gated_detail(res["clusters"], res["meta"], LABELS, IDS)
    assert detail["E_HOUTHI_SAUDI"]["core"] == 27
    frag = rp.fragmentation(res["clusters"], LABELS, IDS)
    assert set(frag) >= {"E_HOUTHI_SAUDI", "E_CPI_SEP"}
    assert res["ids_lost_while_pooled"] == 0  # ids derive from the onset item, so they persist


def test_as_if_all_classified_restamps_to_the_end_of_the_first_seen_day():
    items = rp.load_fixture_items()
    src = {items[0].news_id: (Classification(classified_at=datetime(2030, 1, 1), status="success", origin="live"),)}
    out = rp.as_if_all_classified(items, src)
    fs = items[0].first_seen_at
    assert out[items[0].news_id][0].classified_at == datetime(fs.year, fs.month, fs.day) + timedelta(days=1)
    assert set(out) == {items[0].news_id}


def test_stored_classifications_use_the_state_file_stamp(tmp_path):
    items = rp.load_fixture_items()
    nid = items[0].news_id
    db = tmp_path / "s.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""create table news_classifications (news_id varchar, classified_at timestamp, classification_status varchar,
        origin varchar, ai_provider varchar, macro_themes_json varchar, sector_impacts_json varchar, entities_json varchar,
        severity double, confidence double, summary varchar)""")
    con.execute("insert into news_classifications values (?, '2026-10-08 02:27:48', 'success', 'live', 'x', '[]', '[]', '[]', 0.1, 0.2, 's')", [nid])
    con.execute("insert into news_classifications values (?, '2026-10-08 05:00:00', 'success', 'live', 'x', '[]', '[]', '[]', 0.9, 0.2, 's')", [nid])
    con.close()
    state = {nid: ("classified", datetime(2026, 10, 8, 2, 27, 48))}
    got = rp.stored_classifications([nid], state, db)
    assert len(got[nid]) == 1 and got[nid][0].severity == 0.1
    assert rp.stored_classifications([nid], state, None) == {}
    assert rp.stored_classifications([nid], {nid: ("unclassified", None)}, db) == {}


def test_assert_state_matched_raises_naming_every_miss():
    state = {"n1": ("classified", datetime(2026, 10, 8)), "n2": ("failed", None), "n3": ("classified", datetime(2026, 10, 9))}
    got = {"n1": (Classification(classified_at=datetime(2026, 10, 8), status="success"),)}
    with pytest.raises(rp.ClassificationMatchError, match="1 of 2.*n3"):
        rp.assert_state_matched(["n1", "n2", "n3"], state, got)
    assert rp.assert_state_matched(["n1", "n2"], state, got) == 1


def test_run_fails_loudly_when_the_store_misses_a_classified_fixture_item(tmp_path):
    db = tmp_path / "empty.duckdb"
    con = duckdb.connect(str(db))
    con.execute("""create table news_classifications (news_id varchar, classified_at timestamp, classification_status varchar,
        origin varchar, ai_provider varchar, macro_themes_json varchar, sector_impacts_json varchar, entities_json varchar,
        severity double, confidence double, summary varchar)""")
    con.close()
    with pytest.raises(rp.ClassificationMatchError):
        rp.run("stored", str(db), None, use_resolver=False, pool="fixture")


def test_store_pool_scores_only_the_reference_items_but_builds_over_everything():
    items = rp.load_fixture_items()
    first = items[0].first_seen_at
    extra = NewsItem("extra_1", "Houthis fire missiles at Riyadh airport again", "https://www.x.com/", first, first)
    ref_ids = {i.news_id for i in items}
    res = rp.replay([*items, extra], None, config=CFG, linking="v2_2", use_background=True, score_ids=ref_ids)
    assert "extra_1" not in res["clusters"] and "extra_1" not in res["final_pool_ids"]
    assert all("largest_event_items" in d for d in res["daily"])
    plain = rp.replay(items, None, config=CFG, linking="v2_2", score_ids=ref_ids)
    assert res["daily"][0]["pool_items"] == plain["daily"][0]["pool_items"] + 1


@pytest.mark.skipif(os.environ.get("MRI_STORE_CHECKS") != "1", reason="reads the live store")
def test_v2_fixture_mode_reproduces_the_recorded_run_a_numbers():
    rep = rp.run("stored", str(rp.DEFAULT_DB), None, pool="fixture", linking="v2")
    s = rep["scored"]
    assert (s["false_split_pairs"], s["label_pairs"]) == (37, 45)
    assert (s["false_merge_pairs"], s["cluster_pairs"]) == (123, 131)
    assert rep["ids_lost_while_pooled"] == 3
