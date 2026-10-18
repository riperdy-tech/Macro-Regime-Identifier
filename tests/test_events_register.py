"""Candidate register (Phase 4b): idempotency, no verified-event wording, single-source shown, syndication collapse,
E2 dedup key, and the guards on what the file may be."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import duckdb
import pytest
from test_events_builder import CFG, mk

from macro_engine.events.linker import ArmAConfig, ArmALinker, make_background
from macro_engine.events.register import (
    PROTECTED_NAMES,
    RegisterStore,
    build_register,
    collapse_pool,
    market_check_key,
    run_day,
    write_register,
)
from macro_engine.events.state import EventState

ARM = ArmAConfig(cutoff=0.35)
HOUTHI = (("Houthis", 0.9, "other"),)
KW = {"config": CFG, "resolver": None, "generic": frozenset(), "suffixes": ()}


def day_end(n: int) -> datetime:
    return datetime(2026, 10, 9) + timedelta(days=n)  # mk() times are relative to 2026-10-09 00:00


def items():
    return [
        mk("n1", "Houthi missile attack hits Riyadh airport", 2, url="https://www.reuters.com/a", ents=HOUTHI),
        # the same wire story on another site: one report, two items
        mk("n2", "Houthi missile attack hits Riyadh airport", 3, url="https://www.example-wire.com/b", ents=HOUTHI),
        mk("n3", "Houthi missile attack on Riyadh airport halts flights", 5, url="https://www.bbc.com/c", ents=HOUTHI),
        mk("n4", "Central bank holds rates steady in surprise move", 6, url="https://www.ft.com/d", ents=(), classified=False),
        mk("n5", "Fed minutes show officials split", 7, url="https://www.federalreserve.gov/e", ents=()),
    ]


@pytest.fixture()
def world():
    con = duckdb.connect(":memory:")
    state = EventState(con)
    store = RegisterStore(con)
    linker = ArmALinker(ARM, make_background([], ARM))
    return con, state, store, linker


def apply(world, n=1):
    con, state, store, linker = world
    return run_day(state, store, linker, items(), day_end(n), **KW)


def test_syndicated_copies_collapse_to_one_report():
    pool = collapse_pool(items(), day_end(1), **KW)
    by_first = {p.report.report_id: p for p in pool}
    assert by_first["n1"].item_ids == ("n1", "n2")
    assert "n2" not in by_first and len(pool) == 4  # n1+n2, n3, n4, n5


def test_report_identity_is_the_earliest_member_and_titles_are_canonical():
    pool = collapse_pool(items(), day_end(1), **KW)
    assert pool[0].report.report_id == "n1" and pool[0].report.title == "houthi missile attack hits riyadh airport"
    assert pool[0].detail["display_title"] == "Houthi missile attack hits Riyadh airport"


def test_unclassified_reports_are_first_class(world):
    res = apply(world)
    _, state, _, _ = world
    assert state.event_of_report("n4") is not None  # unclassified, still a candidate
    assert len(res.created) + len(res.joined) == 4


def test_register_idempotent_state_tables_document_and_bytes(world, tmp_path):
    con, state, store, _ = world
    apply(world)
    snap = (state.snapshot(), store.snapshot())
    doc1 = build_register(con, day_end(1), config=CFG, arm=ARM)
    path = tmp_path / "event_register.json"
    assert write_register(path, doc1) is True
    first = path.read_bytes()
    apply(world)  # the same day again
    assert (state.snapshot(), store.snapshot()) == snap
    doc2 = build_register(con, day_end(1), config=CFG, arm=ARM)
    assert doc2 == doc1
    assert write_register(path, doc2) is False and path.read_bytes() == first


def test_register_never_calls_a_candidate_a_verified_event(world):
    con, _, _, _ = world
    apply(world)
    doc = build_register(con, day_end(1), config=CFG, arm=ARM)
    text = json.dumps(doc).lower()
    assert "confirmed" not in text and "verified event" not in text and "rejected_uncorroborated" not in text

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield str(k).lower()
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)

    assert not [k for k in keys(doc) if "confirm" in k or "corroborated_at" in k]
    assert doc["schema_version"] == 1 and doc["artifact"] == "event_register"


def test_single_source_candidates_are_shown_with_the_category(world):
    con, _, _, _ = world
    apply(world)
    doc = build_register(con, day_end(1), config=CFG, arm=ARM)
    cands = {c["candidate_id"]: c for c in doc["candidates"]}
    assert doc["candidate_count"] == len(cands) >= 2
    single = [c for c in cands.values() if c["corroboration"]["category"] == "single_source"]
    assert single and all("single_source" in c["flags"] for c in single)
    n4 = next(c for c in cands.values() if "n4" in c["report_ids"])
    assert n4["classification_status"]["reports"][0]["status"] == "unclassified"
    assert "unclassified_reports" in n4["flags"]
    # a quiet single-source candidate becomes closed, never hidden
    late = build_register(con, day_end(30), config=CFG, arm=ARM)
    assert late["candidate_count"] == doc["candidate_count"]
    assert {c["status_window"]["status"] for c in late["candidates"]} == {"closed"}


def test_official_source_and_independent_publishers_set_the_category(world):
    con, _, _, _ = world
    apply(world)
    cands = build_register(con, day_end(1), config=CFG, arm=ARM)["candidates"]
    fed = next(c for c in cands if "n5" in c["report_ids"])
    assert fed["corroboration"]["category"] == "officially_sourced"
    houthi = next(c for c in cands if "n1" in c["report_ids"])
    assert houthi["deduped_report_count"] == 2 and houthi["corroboration"]["category"] == "independently_corroborated"


def test_wire_copies_are_one_source_and_carry_provenance(world):
    con, _, _, _ = world
    apply(world)
    houthi = next(c for c in build_register(con, day_end(1), config=CFG, arm=ARM)["candidates"] if "n1" in c["report_ids"])
    assert houthi["item_ids"] == ["n1", "n2", "n3"] and houthi["deduped_report_count"] == 2
    prov = {p["report_id"]: p for pub in houthi["distinct_publishers"] for p in pub["syndication_provenance"]}
    assert [c["item_id"] for c in prov["n1"]["copy_items"]] == ["n2"] and prov["n3"]["copy_items"] == []


def test_onset_is_published_at_and_first_available_is_first_seen(world):
    con, _, _, _ = world
    apply(world)
    houthi = next(c for c in build_register(con, day_end(1), config=CFG, arm=ARM)["candidates"] if "n1" in c["report_ids"])
    first_seen = datetime(2026, 10, 9, 2)
    assert houthi["onset"] == (first_seen - timedelta(minutes=30)).isoformat()
    assert houthi["first_available_at"] == first_seen.isoformat()
    assert houthi["market_response"] == {"checks": []} and houthi["storyline"] is None


def test_themes_are_annotation_only(world):
    con, _, _, _ = world
    apply(world)
    houthi = next(c for c in build_register(con, day_end(1), config=CFG, arm=ARM)["candidates"] if "n1" in c["report_ids"])
    assert houthi["themes"] and all(t["role"] == "annotation_only" for t in houthi["themes"])


def test_write_register_refuses_a_protected_artifact(tmp_path):
    for name in PROTECTED_NAMES:
        with pytest.raises(ValueError, match="protected"):
            write_register(tmp_path / name, {})
    assert not list(tmp_path.iterdir())


def test_pool_is_the_discovery_window_and_reports_before_window_start_are_left_out():
    far = mk("old", "Houthi missile attack hits Riyadh airport", 2, ents=HOUTHI)
    pool = collapse_pool([far, *items()], day_end(1), **KW, window_start=datetime(2026, 10, 9, 4))
    assert "old" not in {p.report.report_id for p in pool} and "n1" not in {p.report.report_id for p in pool}
    assert "n3" in {p.report.report_id for p in pool}


# ------------------------------------------------------------------ E2 de-duplication key


def test_market_check_key_is_instrument_reference_close_window():
    assert market_check_key("spy", date(2026, 10, 16), 2) == "SPY|2026-10-16|2"
    assert market_check_key(" XLE ", "2026-10-16", 2) == market_check_key("XLE", date(2026, 10, 16), 2)
    assert market_check_key("SPY", date(2026, 10, 16), 2) != market_check_key("SPY", date(2026, 10, 16), 1)
    assert market_check_key("SPY", date(2026, 10, 16), 2) != market_check_key("SPY", date(2026, 10, 15), 2)
    assert market_check_key("SPY", date(2026, 10, 16), 2) != market_check_key("XLE", date(2026, 10, 16), 2)


def test_fragments_of_one_story_sharing_a_key_count_once():
    fragments = [("cand_a", "XLE", date(2026, 10, 16), 2), ("cand_b", "XLE", "2026-10-16", 2),
                 ("cand_c", "SPY", date(2026, 10, 16), 2)]
    keys = {}
    for cid, inst, ref, win in fragments:
        keys.setdefault(market_check_key(inst, ref, win), []).append(cid)
    assert len(keys) == 2 and keys["XLE|2026-10-16|2"] == ["cand_a", "cand_b"]


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, None])
def test_market_check_key_rejects_a_bad_window(bad):
    with pytest.raises(ValueError):
        market_check_key("SPY", date(2026, 10, 16), bad)


def test_market_check_key_rejects_an_empty_instrument():
    with pytest.raises(ValueError):
        market_check_key("  ", date(2026, 10, 16), 2)
