"""Persistent event identity (Phase 4a): stable ids, bounded summary, open/closed, lineage, idempotent replay."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest

from macro_engine.events.state import (
    CLOSED,
    OPEN,
    EventState,
    LinkDecision,
    NeverLinkStub,
    ProposedLink,
    Report,
    StateConfig,
    make_event_id,
)

D0 = datetime(2026, 10, 10, 6, 0)


def rep(rid: str, day: int, ents=("houthi",), hour: int = 0, title: str | None = None, published="auto") -> Report:
    seen = D0 + timedelta(days=day, hours=hour)
    pub = seen - timedelta(hours=3) if published == "auto" else published
    return Report(rid, title or f"title {rid}", seen, pub, "pub", frozenset(ents))


class KeyLinker:
    """Test stub of the Phase 4b interface: join the open event whose core profile shares a key with the report."""

    def __init__(self, propose_with: dict[str, str] | None = None, prefer: str | None = None):
        self.propose_with = propose_with or {}
        self.prefer = prefer
        self.seen_open: list[list[str]] = []

    def link(self, report, open_events):
        self.seen_open.append([e.event_id for e in open_events])
        for ev in sorted(open_events, key=lambda e: e.event_id != self.prefer):
            if report.entity_keys & ev.core_entities:
                props = ()
                other = self.propose_with.get(report.report_id)
                if other is not None:
                    props = (ProposedLink(other, {"why": "shared title terms"}),)
                return LinkDecision(ev.event_id, {"via": "core"}, props)
        return LinkDecision(None, {"via": "none"})


@pytest.fixture()
def state():
    return EventState(duckdb.connect(":memory:"))


def day_end(n: int) -> datetime:
    return datetime(2026, 10, 10 + n, 23, 59)


def test_event_gets_a_stable_id_at_creation(state):
    r = state.apply_day(day_end(0), [rep("r1", 0)], NeverLinkStub())
    assert r.created == [make_event_id("r1")]
    assert state.event(make_event_id("r1"))["seed_report_id"] == "r1"
    state.apply_day(day_end(1), [rep("r2", 1, ents=("x",))], NeverLinkStub())
    assert state.event(make_event_id("r1")) is not None


def test_a_story_longer_than_the_pool_keeps_one_id(state):
    lk = KeyLinker()
    for d in range(0, 12):  # 12 daily reports, far past the 7-day discovery window
        state.apply_day(day_end(d), [rep(f"r{d}", d)], lk)
    ids = [e.event_id for e in state.open_events()]
    assert ids == [make_event_id("r0")]
    assert state.event(ids[0])["report_count"] == 12


def test_summary_pins_first_three_keeps_recent_five_and_core_profile(state):
    lk = KeyLinker()
    for d in range(10):
        ents = ("houthi", "riyadh") if d < 3 else ("houthi",)
        state.apply_day(day_end(d), [rep(f"r{d}", d, ents=ents)], lk)
    ev = state.event(make_event_id("r0"))
    assert [a["report_id"] for a in ev["anchors"]] == ["r0", "r1", "r2"]
    assert [a["report_id"] for a in ev["recent"]] == ["r5", "r6", "r7", "r8", "r9"]
    # riyadh is in 3 of 10 reports = exactly 30%: in the profile; one more report drops it
    assert ev["core_entities"] == ["houthi", "riyadh"]
    state.apply_day(day_end(10), [rep("r10", 10, ents=("houthi",))], lk)
    assert state.event(make_event_id("r0"))["core_entities"] == ["houthi"]
    summary = state.open_events()[0]
    assert len(summary.anchors) == 3 and len(summary.recent) == 5


def test_open_for_three_days_then_closed_and_closed_never_accepts_joins(state):
    lk = KeyLinker()
    state.apply_day(day_end(0), [rep("r1", 0)], lk)
    eid = make_event_id("r1")
    for d in (1, 2, 3):
        assert state.apply_day(day_end(d), [], lk).closed == []
    assert state.event(eid)["status"] == OPEN
    res = state.apply_day(day_end(4), [rep("r2", 4)], lk)  # gap 4 days: closed before linking
    assert res.closed == [eid]
    assert state.event(eid)["status"] == CLOSED and state.event(eid)["closed_on"] == date(2026, 10, 14)
    assert res.created == [make_event_id("r2")] and res.joined == []
    assert eid not in lk.seen_open[-1]


def test_state_refuses_a_join_to_a_closed_event_even_if_the_linker_tries(state):
    state.apply_day(day_end(0), [rep("r1", 0)], NeverLinkStub())
    state.apply_day(day_end(6), [], NeverLinkStub())
    eid = make_event_id("r1")

    class Bad:
        def link(self, report, open_events):
            return LinkDecision(eid)

    with pytest.raises(ValueError, match="not an open event"):
        state.apply_day(day_end(7), [rep("r2", 7)], Bad())


def test_joining_refreshes_the_open_window(state):
    lk = KeyLinker()
    state.apply_day(day_end(0), [rep("r1", 0)], lk)
    state.apply_day(day_end(3), [rep("r2", 3)], lk)
    res = state.apply_day(day_end(6), [rep("r3", 6)], lk)  # 3 days after r2: still open
    assert res.closed == [] and res.joined == [("r3", make_event_id("r1"))]


def test_no_retrospective_reassignment_only_proposed_links(state):
    lk = KeyLinker()
    state.apply_day(day_end(0), [rep("a1", 0, ents=("houthi",)), rep("b1", 0, ents=("aramco",))], lk)
    a, b = make_event_id("a1"), make_event_id("b1")
    before = {r: state.event_of_report(r) for r in ("a1", "b1")}
    lk2 = KeyLinker(propose_with={"a2": b}, prefer=a)
    res = state.apply_day(day_end(1), [rep("a2", 1, ents=("houthi", "aramco"))], lk2)
    assert res.proposals == 1 and res.joined == [("a2", a)]
    assert {r: state.event_of_report(r) for r in ("a1", "b1")} == before  # nothing moved
    assert state.event(b)["report_count"] == 1 and state.event(a)["report_count"] == 2
    [pl] = state.proposed_links()
    assert (pl["event_id"], pl["other_event_id"], pl["report_id"], pl["status"]) == (a, b, "a2", "proposed")
    assert pl["evidence"] == {"why": "shared title terms"}
    assert state.resolve_id(b) == b  # a proposal is not an alias


def test_supersession_lineage_keeps_old_ids_and_resolves_forward(state):
    for rid, e in (("a1", "x"), ("b1", "y"), ("c1", "z")):
        state.apply_day(day_end(0), [rep(rid, 0, ents=(e,))], NeverLinkStub())
    a, b, c = (make_event_id(r) for r in ("a1", "b1", "c1"))
    state.record_supersession(a, b, {"reason": "operator merge"}, day_end(1))
    state.record_supersession(b, c, {"reason": "second merge"}, day_end(2), kind="alias")
    assert state.resolve_id(a) == c and state.resolve_id(b) == c and state.resolve_id(c) == c
    assert all(state.event(e) is not None for e in (a, b, c))  # nothing deleted or renamed
    with pytest.raises(KeyError):
        state.record_supersession(a, "evt_unknown", {}, day_end(2))
    state.record_supersession(c, a, {"reason": "cycle"}, day_end(3))  # a cycle must not loop forever
    assert state.resolve_id(a) in {a, b, c}


def test_replaying_the_same_day_twice_changes_nothing(state):
    lk = KeyLinker()
    state.apply_day(day_end(0), [rep("a1", 0), rep("z1", 0, ents=("other",))], lk)
    pool = [rep("a2", 1), rep("a1", 0)]
    state.apply_day(day_end(1), pool, lk)
    snap = state.snapshot()
    again = state.apply_day(day_end(1), pool, lk)
    assert state.snapshot() == snap
    assert again.created == [] and again.joined == [] and sorted(again.skipped_existing) == ["a1", "a2"]


def test_replay_in_a_fresh_store_gives_identical_state_regardless_of_input_order(state):
    other = EventState(duckdb.connect(":memory:"))
    day0 = [rep("r1", 0), rep("r2", 0, hour=2), rep("r3", 0, ents=("q",), hour=1)]
    state.apply_day(day_end(0), day0, KeyLinker())
    other.apply_day(day_end(0), list(reversed(day0)), KeyLinker())
    assert state.snapshot() == other.snapshot()


def test_days_must_apply_in_order_and_future_reports_wait(state):
    state.apply_day(day_end(2), [], NeverLinkStub())
    with pytest.raises(ValueError, match="in order"):
        state.apply_day(day_end(1), [], NeverLinkStub())
    res = state.apply_day(day_end(2), [rep("late", 5)], NeverLinkStub())
    assert res.skipped_future == ["late"] and state.event_of_report("late") is None


def test_onset_and_first_available_carry_published_and_first_seen_separately(state):
    lk = KeyLinker()
    seen = D0 + timedelta(days=1)
    first = Report("r1", "t", seen, seen - timedelta(days=2), "p", frozenset({"houthi"}))
    state.apply_day(day_end(1), [first], lk)
    echo = Report("r2", "t2", seen + timedelta(hours=5), seen - timedelta(days=3), "p", frozenset({"houthi"}))
    state.apply_day(day_end(1), [echo], lk)
    ev = state.event(make_event_id("r1"))
    assert ev["onset_published_at"] == seen - timedelta(days=3)  # E2 uses published_at
    assert ev["first_available_at"] == seen  # the look-ahead boundary is first_seen_at
    assert ev["onset_published_at"] != ev["first_available_at"]


def test_missing_published_at_is_flagged_not_filled(state):
    state.apply_day(day_end(0), [rep("r1", 0, published=None)], NeverLinkStub())
    ev = state.event(make_event_id("r1"))
    assert ev["onset_published_at"] is None and "published_at_missing" in ev["flags"]


def test_config_bounds_are_honoured():
    st = EventState(duckdb.connect(":memory:"), StateConfig(anchor_count=1, recent_count=2))
    lk = KeyLinker()
    for d in range(3):
        st.apply_day(day_end(d), [rep(f"r{d}", d)], lk)
    ev = st.event(make_event_id("r0"))
    assert [a["report_id"] for a in ev["anchors"]] == ["r0"] and len(ev["recent"]) == 2


def test_published_at_missing_only_while_no_report_has_the_date(state):
    state.apply_day(date(2026, 10, 10), [rep("a", 0, published=None)], NeverLinkStub())
    eid = make_event_id("a")
    assert "published_at_missing" in state.event(eid)["flags"]
    state.apply_day(date(2026, 10, 10), [rep("b", 0, hour=1, published=None)], KeyLinker())
    assert "published_at_missing" in state.event(eid)["flags"]            # still no report with a date
    state.apply_day(date(2026, 10, 10), [rep("c", 0, hour=2)], KeyLinker())
    ev = state.event(eid)
    assert ev["onset_published_at"] is not None and "published_at_missing" not in ev["flags"]
    state.apply_day(date(2026, 10, 10), [rep("d", 0, hour=3, published=None)], KeyLinker())
    assert "published_at_missing" not in state.event(eid)["flags"]        # a later undated report does not re-flag


def test_event_and_report_rows_are_written_in_one_transaction(state):
    class BadLinker:
        def link(self, report, open_events):
            return LinkDecision(None, {"bad": object()}, ())            # evidence is not JSON-able at the default: str()

    state.apply_day(date(2026, 10, 10), [rep("a", 0)], NeverLinkStub())
    before = state.snapshot()
    state.con.execute("DROP TABLE event_state_reports")                 # make the SECOND write fail
    state.con.execute("CREATE TABLE event_state_reports (report_id VARCHAR PRIMARY KEY)")
    with pytest.raises(Exception):
        state.apply_day(date(2026, 10, 10), [rep("b", 0, hour=1)], NeverLinkStub())
    n_events = state.con.execute("select count(*) from event_state_events").fetchone()[0]
    assert n_events == len(before["event_state_events"]) == 1           # the event row of the failed assignment rolled back
