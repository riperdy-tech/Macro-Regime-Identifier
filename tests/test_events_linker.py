"""Arm A linker (Phase 4b): anchors not union, background without look-ahead, ambiguity annotated, title-only linking."""
from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime, timedelta

import duckdb
import pytest
from test_events_builder import CFG, cls_at, mk

from macro_engine.events.linker import (
    ArmAConfig,
    ArmALinker,
    Background,
    char_ngrams,
    cosine,
    load_arm_a_config,
    make_background,
)
from macro_engine.events.register import background_for
from macro_engine.events.state import EventState, EventSummary, NeverLinkStub, Report, ReportRef

D0 = datetime(2026, 10, 10, 6, 0)
BG_TITLES = [f"background title number {k} about other topics" for k in range(20)]


def empty_bg() -> Background:
    return make_background([], ArmAConfig())


def bg_of(rows) -> Background:
    return make_background(rows, ArmAConfig())


def rep(rid: str, title: str, hours: float = 0, ents=()) -> Report:
    seen = D0 + timedelta(hours=hours)
    return Report(rid, title, seen, seen - timedelta(hours=1), "pub", frozenset(ents))


def lk(background: Background | None = None, **arm) -> ArmALinker:
    return ArmALinker(ArmAConfig(**arm), background or empty_bg())


@pytest.fixture()
def state():
    return EventState(duckdb.connect(":memory:"))


def end_of(n: int) -> datetime:
    return datetime(2026, 10, 10 + n, 23, 59)


# ------------------------------------------------------------------ the config file


def test_events_v23_yaml_loads_into_the_arm_config():
    arm = load_arm_a_config()
    assert (arm.ngram_min, arm.ngram_max, arm.anchor_count) == (3, 5, 3)
    assert 0.0 <= arm.entity_weight and arm.cutoff > 0 and arm.background_days > 0


def test_unknown_arm_key_is_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("arm_a:\n  cutoff: 0.3\n  surprise: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="surprise"):
        load_arm_a_config(p)


# ------------------------------------------------------------------ features


def test_char_ngrams_are_within_words_and_cover_the_range():
    grams = char_ngrams("Oil up")
    assert " oi" in grams and "oil" in grams and "oil " in grams and " oil " in grams
    assert all(3 <= len(g) <= 5 for g in grams)
    assert not any("l u" in g for g in grams)  # no n-gram spans two words


def test_day_granular_df_a_burst_counts_once():
    burst = bg_of([(date(2026, 10, 1), "zebra crossing alert", ())] * 50 + [(date(2026, 10, 2), "other news", ())])
    spread = bg_of([(date(2026, 10, 1 + k), "zebra crossing alert", ()) for k in range(5)]
                   + [(date(2026, 10, 9), "other news", ())])
    assert burst.title_days["zeb"] == 1
    assert spread.title_days["zeb"] == 5
    assert burst.days == 2
    assert burst.title_idf("zeb", 1.0) > spread.title_idf("zeb", 1.0)


def test_entity_idf_is_a_continuous_weight_not_a_cutoff():
    bg = bg_of([(date(2026, 10, 1 + k), "t", ("N:fed",)) for k in range(10)]
               + [(date(2026, 10, 1), "t", ("N:rare",))])
    assert bg.days == 10
    assert bg.entity_idf("N:fed") == 0.0 or bg.entity_idf("N:fed") < bg.entity_idf("N:rare") < bg.entity_idf("N:never")
    assert bg.entity_idf("N:never") == pytest.approx(math.log(11))
    assert bg.entity_idf("N:fed") == pytest.approx(math.log(11 / 11))


# ------------------------------------------------------------------ background: no look-ahead


def test_background_reads_nothing_at_or_after_its_end():
    end = datetime(2026, 10, 10)
    before = mk("b1", "ordinary harbour news", -30, ents=(("Ordinary Co", 0.9, "other"),))
    late_cls = mk("b2", "another harbour story", -20, classified=False)
    late_cls = dataclasses.replace(late_cls, classifications=(
        cls_at(end + timedelta(hours=1), ents=(("Leaky Entity", 0.9, "other"),)),))
    after = mk("b3", "quixotic zephyr headline", 5, ents=(("Future Entity", 0.9, "other"),))
    items = [
        dataclasses.replace(before, first_seen_at=end - timedelta(hours=30)),
        dataclasses.replace(late_cls, first_seen_at=end - timedelta(hours=20)),
        dataclasses.replace(after, first_seen_at=end + timedelta(hours=5)),
    ]
    bg = background_for(items, end, ArmAConfig(background_days=28), config=CFG, resolver=None,
                        generic=frozenset(), suffixes=())
    assert bg.reports == 2
    assert "zep" not in bg.title_days and "N:future entity" not in bg.entity_days
    assert "N:leaky entity" not in bg.entity_days  # classified after `end`: does not exist for this background
    assert "N:ordinary co" in bg.entity_days
    assert bg.end == end and bg.start == end - timedelta(days=28)
    assert "background_thin" in bg.flags  # fewer than the configured minimum of reports: annotated, not gated


def test_background_window_is_bounded_below():
    end = datetime(2026, 10, 30)
    old = dataclasses.replace(mk("old", "ancient story", 0), first_seen_at=end - timedelta(days=20))
    new = dataclasses.replace(mk("new", "recent story", 0), first_seen_at=end - timedelta(days=5))
    bg14 = background_for([old, new], end, ArmAConfig(background_days=14), config=CFG, resolver=None,
                          generic=frozenset(), suffixes=())
    bg28 = background_for([old, new], end, ArmAConfig(background_days=28), config=CFG, resolver=None,
                          generic=frozenset(), suffixes=())
    assert (bg14.reports, bg28.reports) == (1, 2)


# ------------------------------------------------------------------ linking against anchors


A1 = "Houthi missile attack hits Riyadh airport"
A2 = "Houthi missile attack on Riyadh airport halts flights"
A3 = "Riyadh airport hit in Houthi missile attack, flights halted"
D4 = "Houthi missile attack Riyadh airport tanker insurance premiums surge"
C5 = "tanker insurance premiums surge Gulf shipping rates"


def build_event(state, linker, extra=()):
    state.apply_day(end_of(0), [rep("a1", A1, 0), rep("a2", A2, 1), rep("a3", A3, 2), *extra], linker)


def test_a_report_links_to_an_event_it_resembles(state):
    linker = lk(cutoff=0.35)
    build_event(state, linker)
    [ev] = state.open_events()
    assert ev.report_count == 3 and [a.report_id for a in ev.anchors] == ["a1", "a2", "a3"]


def test_chain_a_b_c_where_c_resembles_only_b_does_not_join(state):
    """D4 joins through the anchors; C5 resembles D4 (a later report) but no anchor, so it starts its own candidate."""
    linker = lk(cutoff=0.35)
    build_event(state, linker, extra=(rep("d4", D4, 3),))
    ev = state.open_events()[0]
    assert ev.report_count == 4 and "d4" not in {a.report_id for a in ev.anchors}
    assert cosine(linker.title_vec("c5", C5), linker.title_vec("d4", D4)) >= 0.35  # a union/chain rule would join
    state.apply_day(end_of(1), [rep("c5", C5, 30)], linker)
    assert state.event_of_report("c5") != state.event_of_report("a1")
    assert len(state.open_events()) == 2


def test_new_candidate_when_below_the_cutoff_for_every_event(state):
    linker = lk(cutoff=0.35)
    build_event(state, linker)
    res = state.apply_day(end_of(1), [rep("z", "Central bank holds rates steady in surprise move", 30)], linker)
    assert len(res.created) == 1 and res.joined == []


def test_unclassified_reports_link_on_their_title(state):
    linker = lk(cutoff=0.35)
    build_event(state, linker)
    unclassified = rep("u1", "Houthi missile attack Riyadh airport latest", 30)  # no entity keys at all
    assert unclassified.entity_keys == frozenset()
    dec = linker.link(unclassified, state.open_events())
    assert dec.event_id is not None and "no_entities" in dec.evidence["flags"]
    assert dec.evidence["entity_cosine"] == 0.0 and dec.evidence["decision"] == "link"


def test_themes_and_directions_never_enter_the_score():
    ev = EventSummary("evt_x", (ReportRef("a1", A1, D0, D0, frozenset()),), (), frozenset(), 1, D0, D0)
    one = rep("r", A2, 5)
    other = dataclasses.replace(one, theme_dirs=frozenset({("geopolitical_risk", "positive"), ("x", "negative")}))
    linker = lk(cutoff=0.2)
    assert linker.link(one, [ev]) == linker.link(other, [ev])


def test_evidence_carries_score_parts_and_anchor(state):
    linker = lk(cutoff=0.35)
    build_event(state, linker)
    dec = linker.link(rep("q", "Houthi missile attack Riyadh airport", 30), state.open_events())
    for key in ("score", "title_cosine", "entity_cosine", "anchor", "shared_entities", "ambiguous", "decision"):
        assert key in dec.evidence
    assert dec.evidence["anchor"] in {"a1", "a2", "a3"}


def test_the_floor_gate_needs_title_or_entity_evidence():
    ev = EventSummary("evt_x", (ReportRef("a1", A1, D0, D0, frozenset()),), (), frozenset(), 1, D0, D0)
    r = rep("r", A2, 5)
    assert lk(cutoff=0.2, title_cosine_floor=0.0).link(r, [ev]).event_id == "evt_x"
    blocked = lk(cutoff=0.2, title_cosine_floor=0.99, entity_idf_floor=99.0).link(r, [ev])
    assert blocked.event_id is None  # score reaches the cutoff but neither floor is met: annotated, not linked


def test_a_shared_rare_entity_can_supply_the_evidence_floor():
    shared = frozenset({"N:rare entity"})
    ev = EventSummary("evt_x", (ReportRef("a1", "alpha bravo", D0, D0, shared),), (), shared, 1, D0, D0)
    r = rep("r", "alpha bravo charlie", 5, ents=shared)
    linker = lk(cutoff=0.1, title_cosine_floor=0.99, entity_idf_floor=0.0, entity_weight=0.5)
    dec = linker.link(r, [ev])
    assert dec.event_id == "evt_x" and dec.evidence["entity_floor_met"] and not dec.evidence["title_floor_met"]


def test_horizon_event_outside_the_open_window_is_not_linked():
    old = D0 - timedelta(days=5)
    ev = EventSummary("evt_x", (ReportRef("a1", A1, old, old, frozenset()),), (), frozenset(), 1, old, old)
    assert lk(cutoff=0.1).link(rep("r", A1, 0), [ev]).event_id is None
    fresh = D0 - timedelta(days=3)
    ev2 = dataclasses.replace(ev, last_report_at=fresh)
    assert lk(cutoff=0.1).link(rep("r", A1, 0), [ev2]).event_id == "evt_x"


# ------------------------------------------------------------------ ambiguity: annotated, not merged


def test_ambiguity_is_annotated_not_merged(state):
    linker = lk(cutoff=0.3, ambiguity_margin=0.2)
    state.apply_day(end_of(0), [rep("p1", "Fed holds rates steady as inflation cools", 0)], NeverLinkStub())
    state.apply_day(end_of(0), [rep("q1", "Fed holds rates steady as hiring cools", 1)], NeverLinkStub())
    e_p, e_q = state.event_of_report("p1"), state.event_of_report("q1")
    assert e_p != e_q
    new = rep("n1", "Fed holds rates steady as inflation and hiring cool", 30)
    dec = linker.link(new, state.open_events())
    assert dec.evidence["ambiguous"] is True and len(dec.evidence["ambiguous_with"]) == 1
    state.apply_day(end_of(1), [new], linker)
    assert state.event_of_report("n1") in (e_p, e_q)  # joined the best, one only
    assert state.event(e_p)["report_count"] + state.event(e_q)["report_count"] == 3  # events NOT merged
    assert len(state.open_events()) == 2
    [pl] = state.proposed_links()  # the other candidate is recorded as related, with the evidence
    assert pl["evidence"]["kind"] == "ambiguous_candidate" and {pl["event_id"], pl["other_event_id"]} == {e_p, e_q}


def test_a_clear_winner_is_not_ambiguous(state):
    linker = lk(cutoff=0.3, ambiguity_margin=0.02)
    state.apply_day(end_of(0), [rep("p1", "Houthi missile attack Riyadh airport", 0),
                                rep("q1", "Central bank holds rates steady", 1)], NeverLinkStub())
    dec = linker.link(rep("n", "Houthi missile attack Riyadh airport again", 30), state.open_events())
    assert dec.event_id == state.event_of_report("p1") and dec.evidence["ambiguous"] is False


def test_near_misses_are_recorded_as_proposals_not_links(state):
    linker = lk(cutoff=0.9, near_miss_fraction=0.3)
    build_event(state, lk(cutoff=0.35))
    dec = linker.link(rep("q", "Houthi missile attack Aden palace", 30), state.open_events())
    assert dec.event_id is None and dec.proposals and dec.proposals[0].evidence["kind"] == "near_miss"


def test_deterministic_tie_goes_to_the_older_event(state):
    linker = lk(cutoff=0.3, ambiguity_margin=0.5)
    state.apply_day(end_of(0), [rep("p1", "same headline text here", 0), rep("q1", "same headline text here", 1)],
                    NeverLinkStub())
    dec = linker.link(rep("n", "same headline text here", 30), state.open_events())
    assert dec.event_id == state.event_of_report("p1")
