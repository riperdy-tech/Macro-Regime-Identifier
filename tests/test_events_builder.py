"""E1 event builder (plan v2 section 5) on synthetic data: every rule has a test for each clause."""
from __future__ import annotations

import dataclasses
import json
import unicodedata
from datetime import datetime, timedelta

import duckdb
import pytest

from macro_engine.events.builder import (
    CORROBORATED,
    DETECTED,
    REJECTED,
    Classification,
    NewsItem,
    build_events,
    canonical_title,
    extract_publisher,
    naive_utc,
)
from macro_engine.events.config import load_e1_config
from macro_engine.events.entities import build_resolver
from macro_engine.events.store_io import load_pool, parse_classification_row
from macro_engine.news.selection import _jaccard, _token_set

CFG = load_e1_config()
T0 = datetime(2026, 10, 9, 0, 0)
END = datetime(2026, 10, 14, 0, 0)

A = "Houthis fire missiles at Riyadh airport"
J_HIGH = "Houthis fire missiles at Jeddah port Saudi"  # 0.3 <= J < 0.6 with A
J_MID = "Houthis fire oil prices jump"  # 0.2 <= J < 0.3 with A
J_LOW = "Houthis target Aden palace"  # J < 0.2 with A
J_ZERO = "Central bank holds rates steady"
GEO = (("geopolitical_risk", "positive"),)
OIL = (("energy_supply_shock", "positive"),)
HOUTHI = (("Houthis", 0.9, "other"),)


def jac(a: str, b: str) -> float:
    return _jaccard(set(_token_set(canonical_title(a))), set(_token_set(canonical_title(b))))


def cls_at(at: datetime, themes=GEO, ents=HOUTHI, severity: float | None = 0.5, **kw) -> Classification:
    return Classification(
        classified_at=at,
        status="success",
        origin="live",
        themes=tuple({"theme_id": t, "direction": d, "severity": 0.3} for t, d in themes),
        entities=tuple({"name": n, "relevance": r, "entity_type": ty} for n, r, ty in ents),
        sector_impacts=({"sector_id": "energy", "impact_score": -0.2, "confidence": 0.7},),
        severity=severity,
        confidence=0.6,
        **kw,
    )


def mk(nid, title, hours, *, url=None, themes=GEO, ents=HOUTHI, classified=True, delay_min=10,
       severity=0.5, published=None, cls=None):
    seen = T0 + timedelta(hours=hours)
    url = url if url is not None else f"https://www.pub-{nid}.com/x"
    if cls is None and classified:
        cls = cls_at(seen + timedelta(minutes=delay_min), themes, ents, severity)
    pub = seen - timedelta(minutes=30) if published is None else published
    return NewsItem(nid, title, url, seen, pub, (cls,) if cls else ())


def build(items, as_of=None, **kw):
    """Default as_of: one hour after the newest item, so statuses reflect only what the test sets up."""
    if as_of is None:
        seen = [i.first_seen_at for i in items if i.first_seen_at is not None]
        as_of = max(seen) + timedelta(hours=1) if seen else END
    return build_events(items, as_of, config=CFG, **kw)


def event_of(res, nid):
    found = [e for e in res.events if any(nid in r["item_ids"] for r in e["reports"])]
    assert len(found) <= 1
    return found[0] if found else None


def test_title_fixtures_have_the_jaccard_the_tests_assume():
    assert CFG.link_jaccard_min <= jac(A, J_HIGH) < CFG.syndication_jaccard_min
    assert jac(A, J_HIGH) >= CFG.unclassified_link_jaccard_min
    assert CFG.new_development_jaccard_below <= jac(A, J_MID) < CFG.link_jaccard_min
    assert jac(A, J_LOW) < CFG.new_development_jaccard_below
    assert jac(A, J_ZERO) == 0.0


# ------------------------------------------------------------------ publisher and canonical title


def test_publisher_is_the_host_without_www():
    assert extract_publisher("t", "https://www.CNBC.com/a?x=1") == ("cnbc.com", "cnbc.com", None)


def test_aggregator_publisher_comes_from_title_suffix_all_dash_kinds():
    for dash in ("-", "–", "—"):
        pub, host, flag = extract_publisher(f"Oil jumps {dash} Reuters", "https://news.google.com/rss/articles/abc")
        assert (pub, host, flag) == ("reuters", "news.google.com", None)


def test_aggregator_without_suffix_is_flagged_not_dropped():
    pub, host, flag = extract_publisher("Oil jumps", "https://news.google.com/rss/articles/abc")
    assert pub == "news.google.com" and flag == "publisher_unparsed_aggregator"


def test_missing_url_gives_no_publisher_and_a_flag():
    assert extract_publisher("Oil jumps", None) == (None, None, "publisher_unknown")
    assert extract_publisher("Oil jumps", "") == (None, None, "publisher_unknown")


def test_canonical_title_unescapes_normalises_and_strips_one_suffix_on_aggregator_hosts():
    assert canonical_title("Oil &quot;jumps&quot;  again – Reuters", host="news.google.com") == 'oil "jumps" again'
    assert canonical_title("A – B – Reuters", host="news.google.com") == "a – b"
    assert canonical_title(None) == ""


# The 17 pooled titles (first seen 2026-10-08..10-16) that a suffix-shaped tail used to truncate although their host
# is not an aggregator (— and – as stored). Plan 5.2: only aggregator headlines carry a "- Publisher" suffix.
NON_AGGREGATOR_DASH_TITLES = (
    ("cnbc.com", "Cramer says higher rates are splitting the market in two — and AI stocks have a big advantage"),
    ("cnbc.com", "Luxury developers flocked to the 'Hamptons of Hungary' — but some projects are stalling"),
    ("marketwatch.com", "Washington and Wall Street chose to delay paying their bills — and to put them on your tab"),
    ("cnbc.com", "Why a Starbucks takeover of Chipotle would — and wouldn't — make sense for both companies"),
    ("cnbc.com", "What's behind the recovery rally in tech stocks — plus, Elon Musk's very good week"),
    ("cnbc.com", "Jim Cramer sets a Starbucks buy level — plus his take on 3 stocks we just bought"),
    ("marketwatch.com", "Elon Musk is now richer than any American ever — and it’s not even close"),
    ("argentinastar.com", "Stronger BoP outlook to keep Rupee far more stable ; FY27 surplus seen at USD 97 - 100 billion : Report"),
    ("cnbc.com", "AI may change the price of your Big Mac and groceries — what this means for shoppers"),
    ("marketwatch.com", "The bull market is turning 4 years old — but yields could crash the party"),
    ("cnbc.com", "IMF sees milder global slowdown as AI boom offsets war hit — warns resilience is nearing limits"),
    ("cnbc.com", "Thousands of homeowners face adjustable-rate mortgage resets — and much higher payments"),
    ("marketwatch.com", "UnitedHealth keeps medical costs under control — and raises its outlook again"),
    ("bbc.co.uk", "US inflation rises more than expected - here's what is happening with prices"),
    ("bbc.co.uk", "Kenya confirms second Ebola case - a relative of the first victim"),
    ("cnbc.com", "Trump's diesel dilemma — by the numbers"),
    ("scmp.com", "Hong Kong investors most eager in Asia to beat markets – but temper return hopes: report"),
)


def test_canonical_title_keeps_a_trailing_dash_tail_on_non_aggregator_hosts():
    assert len(NON_AGGREGATOR_DASH_TITLES) == 17
    for host, title in NON_AGGREGATOR_DASH_TITLES:
        assert host not in CFG.aggregator_hosts
        got = canonical_title(title, host=host)
        assert got == " ".join(unicodedata.normalize("NFKC", title).lower().split()), title
        # the same text on an aggregator host IS stripped: the rule keys on the host, not on the text
        assert len(canonical_title(title, host="news.google.com")) < len(got)


def test_canonical_title_without_a_host_never_strips():
    assert canonical_title("Oil jumps - Reuters") == "oil jumps - reuters"
    assert canonical_title("Oil jumps - Reuters", host="reuters.com") == "oil jumps - reuters"
    assert canonical_title("Oil jumps - Reuters", host="news.google.com") == "oil jumps"


def test_dash_tail_is_not_collapsed_into_a_report_on_a_non_aggregator_host():
    # identical heads, different tails: one report through an aggregator suffix, two on ordinary hosts
    one = build([mk("n1", "Rates hold steady - Reuters", 1, url="https://news.google.com/a"),
                 mk("n2", "Rates hold steady - Bloomberg", 2, url="https://news.google.com/b")])
    assert one.meta["reports"] == 1
    two = build([mk("n1", "Rates hold - the full story", 1, url="https://www.bbc.co.uk/a"),
                 mk("n2", "Rates hold - what comes next", 2, url="https://www.bbc.co.uk/b")])
    assert two.meta["reports"] == 2


# ------------------------------------------------------------------ syndication collapse and canonical item


def test_same_canonical_title_across_publishers_is_one_report():
    a = mk("a", A + " - Reuters", 1, url="https://news.google.com/a")
    b = mk("b", A.lower(), 2, url="https://www.cnbc.com/b")
    res = build([a, b])
    (ev,) = res.events
    assert ev["report_count"] == 1 and ev["item_count"] == 2
    assert ev["publisher_count"] == 1  # a wire story republished counts once
    assert ev["event_status"] == DETECTED


def test_title_jaccard_at_threshold_collapses_below_does_not():
    base = "alpha beta gamma delta epsilon zeta"
    near = "alpha beta gamma delta epsilon eta theta"  # inter 5, union 8 -> 0.625
    far = "alpha beta gamma eta theta iota kappa"  # 3/10 with base, 5/9 with near: under the threshold with both
    assert jac(base, near) >= CFG.syndication_jaccard_min > max(jac(base, far), jac(near, far))
    items = [mk("a", base, 1), mk("b", near, 2), mk("c", far, 3)]
    res = build(items)
    reports = sorted((r["item_ids"] for e in res.events for r in e["reports"]), key=len)
    assert sorted(len(r) for r in reports) == [1, 2]


def test_canonical_item_is_earliest_seen_classified_member():
    unclassified_first = mk("a", A, 1, classified=False)
    classified_second = mk("b", A, 2)
    classified_third = mk("c", A, 3)
    res = build([classified_third, unclassified_first, classified_second])
    (ev,) = res.events
    assert ev["reports"][0]["report_id"] == "b"
    assert ev["title"] == A


def test_canonical_item_falls_back_to_earliest_seen_when_none_classified():
    res = build([mk("b", A, 2, classified=False), mk("a", A, 1, classified=False)])
    assert res.events == []
    assert res.unattached[0]["report_id"] == "a"


# ------------------------------------------------------------------ linking, clause by clause


def test_link_by_shared_entity_and_shared_theme_even_with_no_title_overlap():
    res = build([mk("a", A, 1), mk("b", J_ZERO, 5)])
    assert event_of(res, "a") is event_of(res, "b")


def test_link_by_shared_entity_and_title_jaccard_when_themes_differ():
    res = build([mk("a", A, 1, themes=GEO), mk("b", J_HIGH, 5, themes=OIL)])
    assert event_of(res, "a") is event_of(res, "b")


def test_shared_entity_but_no_shared_theme_and_low_jaccard_does_not_link():
    res = build([mk("a", A, 1, themes=GEO), mk("b", J_MID, 5, themes=OIL)])
    assert event_of(res, "a") is not event_of(res, "b")
    assert event_of(res, "b")["related_event_ids"] == []  # Jaccard 0.2..0.3: neither link nor related


def test_same_theme_with_other_direction_is_not_a_shared_theme():
    res = build([mk("a", A, 1, themes=(("geopolitical_risk", "positive"),)),
                 mk("b", J_ZERO, 5, themes=(("geopolitical_risk", "negative"),))])
    assert event_of(res, "a") is not event_of(res, "b")


def test_shared_theme_without_shared_entity_does_not_link():
    res = build([mk("a", A, 1), mk("b", J_ZERO, 5, ents=(("Hezbollah", 0.9, "other"),))])
    assert event_of(res, "a") is not event_of(res, "b")


def test_entity_relevance_below_threshold_cannot_link_and_is_flagged():
    res = build([mk("a", A, 1), mk("b", J_ZERO, 5, ents=(("Houthis", 0.49, "other"),))])
    assert event_of(res, "a") is not event_of(res, "b")
    assert any("entity_not_linkable:relevance_below_min:houthis" in f for f in event_of(res, "b")["flags"])
    assert "no_linkable_entity" in event_of(res, "b")["flags"]


def test_entity_relevance_exactly_at_threshold_links_and_zero_is_a_value():
    at = mk("b", J_ZERO, 5, ents=(("Houthis", CFG.entity_relevance_min, "other"),))
    res = build([mk("a", A, 1), at])
    assert event_of(res, "a") is event_of(res, "b")
    zero = mk("c", J_ZERO, 5, ents=(("Houthis", 0.0, "other"),))
    res = build([mk("a", A, 1), zero])
    assert event_of(res, "a") is not event_of(res, "c")
    assert any("relevance_below_min" in f for f in event_of(res, "c")["flags"])


def test_entity_without_relevance_is_not_linkable_but_kept_on_the_record():
    cls = cls_at(T0 + timedelta(hours=5, minutes=10), ents=())
    cls = dataclasses.replace(cls, entities=({"name": "Houthis", "entity_type": "other"},))
    res = build([mk("a", A, 1), mk("b", J_ZERO, 5, cls=cls)])
    assert event_of(res, "a") is not event_of(res, "b")
    other = event_of(res, "b")["entities_other"]
    assert other[0]["normalized"] == "houthis" and other[0]["link_exclusion"] == "relevance_missing"


def test_horizon_is_measured_from_the_events_latest_report():
    hz = CFG.horizon_hours
    chain = [mk("a", A, 0), mk("b", J_ZERO, hz - 1), mk("c", J_ZERO + " today", 2 * hz - 2)]
    res = build(chain)  # c is 71h after b although 142h after a: still the same event
    assert event_of(res, "a") is event_of(res, "c")
    res = build([mk("a", A, 0), mk("b", J_ZERO, hz + 1)])
    assert event_of(res, "a") is not event_of(res, "b")
    res = build([mk("a", A, 0), mk("b", J_ZERO, hz)])  # exactly at the horizon still links
    assert event_of(res, "a") is event_of(res, "b")


def test_generic_entities_never_link_and_are_kept_as_names():
    gen = (("United States government", 0.9, "country"), ("the market", 0.9, "other"))
    res = build([mk("a", A, 1, ents=gen), mk("b", J_ZERO, 5, ents=gen)])
    assert event_of(res, "a") is not event_of(res, "b")
    ev = event_of(res, "a")
    assert {e["normalized"] for e in ev["entities_other"]} == {"united states government", "market"}
    assert {e["status"] for e in ev["entities_other"]} == {"generic"}
    assert "no_linkable_entity" in ev["flags"]
    assert any("generic_entity" in f for f in ev["flags"])


def test_generic_exclusion_uses_the_stop_list_argument_when_given():
    ents = (("Wall Street", 0.9, "other"),)
    default = build([mk("a", A, 1, ents=ents), mk("b", J_ZERO, 5, ents=ents)])
    assert event_of(default, "a") is not event_of(default, "b")  # on the stop list in the config file
    custom = build([mk("a", A, 1, ents=ents), mk("b", J_ZERO, 5, ents=ents)], generic_entities=["something else"])
    assert event_of(custom, "a") is event_of(custom, "b")


def test_a_company_counts_once_not_once_per_ticker():
    stocks = [("Alphabet Inc.", "GOOGL"), ("Alphabet Inc.", "GOOG"), ("CoreWeave, Inc.", "CRWV")]
    resolver = build_resolver(stocks=stocks)
    ents = (("Alphabet", 0.9, "company"), ("Alphabet Inc.", 0.8, "company"))
    res = build([mk("a", "Alphabet sinks", 1, ents=ents), mk("b", J_ZERO, 5, ents=(("Alphabet Inc", 0.7, "company"),))],
                resolver=resolver)
    ev = event_of(res, "a")
    assert event_of(res, "b") is ev
    assert len(ev["entities_named"]) == 1
    assert ev["entities_named"][0]["tickers"] == ["GOOG", "GOOGL"]
    assert ev["entities_named"][0]["reports"] == 3  # two mentions in a, one in b; one company


def test_unresolved_names_link_on_the_normalised_name():
    res = build([mk("a", A, 1, ents=(("The Houthis, Inc.", 0.9, "other"),)), mk("b", J_ZERO, 5, ents=(("houthis", 0.8, "other"),))],
                resolver=build_resolver(stocks=[("CoreWeave, Inc.", "CRWV")]))
    assert event_of(res, "a") is event_of(res, "b")


def test_best_event_wins_and_the_other_is_annotated():
    both = (("Houthis", 0.9, "other"), ("Hezbollah", 0.9, "other"))
    res = build([mk("a", A, 1, ents=(("Houthis", 0.9, "other"),), themes=OIL),
                 mk("b", "Beirut port blast", 2, ents=(("Hezbollah", 0.9, "other"),), themes=OIL),
                 mk("c", J_HIGH, 3, themes=OIL, ents=both)])
    # a and b share no entity, so they are two events; c shares an entity and a theme with each
    assert event_of(res, "a") is not event_of(res, "b")
    ev_c = event_of(res, "c")
    assert ev_c is event_of(res, "a")  # higher title Jaccard and shared theme
    rep_c = next(r for r in ev_c["reports"] if r["report_id"] == "c")
    assert any(f.startswith("also_linkable:") for f in rep_c["flags"])


# ------------------------------------------------------------------ new development


def test_new_development_is_a_new_event_related_to_the_old_one():
    res = build([mk("a", A, 1, themes=GEO), mk("b", J_LOW, 6, themes=OIL)])
    ea, eb = event_of(res, "a"), event_of(res, "b")
    assert ea is not eb
    assert eb["related_event_ids"] == [ea["event_id"]]
    assert ea["related_event_ids"] == []


def test_new_development_needs_the_shared_entity_and_the_horizon():
    res = build([mk("a", A, 1), mk("b", J_LOW, 6, themes=OIL, ents=(("Aden", 0.9, "place"),))])
    assert event_of(res, "b")["related_event_ids"] == []
    res = build([mk("a", A, 0), mk("b", J_LOW, CFG.horizon_hours + 5, themes=OIL)])
    assert event_of(res, "b")["related_event_ids"] == []


# ------------------------------------------------------------------ unclassified reports


def test_unclassified_report_links_through_jaccard_at_the_unclassified_threshold():
    res = build([mk("a", A, 1), mk("b", J_HIGH, 3, classified=False)])
    assert event_of(res, "b") is event_of(res, "a")


def test_unclassified_report_below_threshold_is_listed_not_dropped():
    # J_MID is 0.22 with A: enough for the classified rule's 0.2 but under the unclassified 0.4
    res = build([mk("a", A, 1), mk("b", J_MID, 3, classified=False)])
    assert event_of(res, "b") is None
    assert [u["report_id"] for u in res.unattached] == ["b"]
    assert res.unattached[0]["reason"] == "unclassified_no_link"


def test_unclassified_report_never_starts_an_event():
    res = build([mk("a", A, 1, classified=False), mk("b", J_HIGH, 2, classified=False)])
    assert res.events == []
    assert len(res.unattached) == 2


def test_unclassified_report_outside_horizon_does_not_link():
    res = build([mk("a", A, 0), mk("b", J_HIGH, CFG.horizon_hours + 2, classified=False)])
    assert event_of(res, "b") is None


# ------------------------------------------------------------------ corroboration and rejection


def test_two_distinct_publishers_corroborate_after_collapse():
    res = build([mk("a", A, 1, url="https://www.reuters.com/a"), mk("b", J_ZERO, 5, url="https://www.cnbc.com/b")])
    (ev,) = res.events
    assert ev["event_status"] == CORROBORATED and ev["publisher_count"] == 2
    assert ev["corroboration_basis"] == ["distinct_publishers"]


def test_same_publisher_twice_does_not_corroborate():
    res = build([mk("a", A, 1, url="https://www.reuters.com/a"), mk("b", J_ZERO, 5, url="https://reuters.com/b")])
    (ev,) = res.events
    assert ev["event_status"] == DETECTED and ev["publisher_count"] == 1


def test_syndicated_copy_from_another_outlet_is_not_a_second_publisher():
    res = build([mk("a", A, 1, url="https://www.reuters.com/a"), mk("b", A, 2, url="https://www.cnbc.com/b")])
    (ev,) = res.events
    assert ev["report_count"] == 1 and ev["event_status"] == DETECTED


def test_official_source_alone_corroborates():
    res = build([mk("a", A, 1, url="https://www.federalreserve.gov/newsevents/x")])
    (ev,) = res.events
    assert ev["event_status"] == CORROBORATED and ev["corroboration_basis"] == ["official_source"]
    assert res.meta["official_source_domains_status"] == "UNRATIFIED"


def test_official_source_inside_a_syndicated_report_counts():
    res = build([mk("a", A, 1, url="https://www.cnbc.com/a"), mk("b", A, 2, url="https://www.bls.gov/b")])
    (ev,) = res.events
    assert ev["event_status"] == CORROBORATED


def test_uncorroborated_event_is_rejected_after_72h_and_kept():
    hz = CFG.rejection_hours
    items = [mk("a", A, 1)]
    inside = build(items, as_of=T0 + timedelta(hours=1 + hz))
    assert event_of(inside, "a")["event_status"] == DETECTED  # exactly 72h is still within
    outside = build(items, as_of=T0 + timedelta(hours=1 + hz, minutes=1))
    ev = event_of(outside, "a")
    assert ev["event_status"] == REJECTED and ev["report_count"] == 1


def test_corroboration_inside_the_window_beats_rejection():
    hz = CFG.rejection_hours
    res = build([mk("a", A, 1), mk("b", J_ZERO, 1 + hz)], as_of=T0 + timedelta(hours=2 + hz))
    assert event_of(res, "a")["event_status"] == CORROBORATED


def test_late_corroboration_stays_rejected_and_is_annotated():
    hz = CFG.rejection_hours
    items = [mk("a", A, 1, url="https://www.pub-a.com/a"),
             mk("b", J_ZERO, hz, url="https://www.pub-a.com/same-publisher"),
             mk("c", "Gulf shipping insurers raise war risk premiums", 1 + 2 * hz - 2, url="https://www.cnbc.com/c")]
    res = build(items, as_of=T0 + timedelta(hours=2 * hz + 2))
    ev = event_of(res, "a")
    assert event_of(res, "c") is ev
    assert ev["event_status"] == REJECTED and "late_corroboration_after_rejection" in ev["flags"]


# ------------------------------------------------------------------ record fields (5.7)


def test_event_record_fields():
    a = mk("a", A, 1, severity=0.2, url="https://www.reuters.com/a", published=T0 + timedelta(minutes=30))
    b = mk("b", J_ZERO, 5, severity=0.6, url="https://www.cnbc.com/b", published=T0 + timedelta(hours=4))
    res = build([b, a])
    (ev,) = res.events
    assert ev["onset"] == "2026-10-09T00:30:00"
    assert ev["first_available_at"] == "2026-10-09T01:00:00"
    assert ev["title"] == A
    assert ev["max_severity"] == 0.6 and ev["mean_severity"] == pytest.approx(0.4)
    assert ev["themes"][0]["theme_id"] == "geopolitical_risk" and ev["themes"][0]["reports"] == 2
    assert ev["entities_other"][0]["names"] == ["Houthis"]
    assert ev["sector_impacts"][0]["sector_id"] == "energy"
    assert ev["report_count"] == 2 and ev["publisher_count"] == 2 and ev["related_event_ids"] == []
    json.dumps(res.to_dict())  # serialisable


def test_zero_severity_is_a_value():
    res = build([mk("a", A, 1, severity=0.0)])
    (ev,) = res.events
    assert ev["max_severity"] == 0.0 and ev["mean_severity"] == 0.0
    res = build([mk("a", A, 1, severity=None)])
    assert res.events[0]["max_severity"] is None


# ------------------------------------------------------------------ determinism, id stability, look-ahead


def _scenario():
    return [mk("a", A, 1, url="https://www.reuters.com/a"), mk("b", J_ZERO, 5, url="https://www.cnbc.com/b"),
            mk("c", J_LOW, 9, themes=OIL), mk("d", "Fed holds rates", 12, ents=(("Federal Reserve", 0.9, "central_bank"),),
                                              themes=(("monetary_tightening", "neutral"),))]


def test_build_is_deterministic_and_input_order_independent():
    items = _scenario()
    first = build(items).to_dict()
    assert build(list(reversed(items))).to_dict() == first
    assert build(items).to_dict() == first


def test_event_id_is_stable_when_as_of_moves_later():
    items = _scenario()
    early = build(items, as_of=T0 + timedelta(hours=6))
    late = build(items, as_of=END)
    ea, la = event_of(early, "a"), event_of(late, "a")
    assert ea["event_id"] == la["event_id"]
    assert ea["event_id"].startswith("evt_") and "2026" not in ea["event_id"]


def test_items_first_seen_after_as_of_do_not_exist():
    items = _scenario()
    early = build(items, as_of=T0 + timedelta(hours=6))
    assert event_of(early, "c") is None and event_of(early, "d") is None
    assert early.meta["items_in_pool"] == 2


def test_classification_after_as_of_is_ignored_with_no_trace():
    as_of = T0 + timedelta(hours=6)
    late = mk("b", J_ZERO, 5, delay_min=24 * 60)  # classified a day after as_of
    solo = build([mk("a", A, 1), dataclasses.replace(late, classifications=())], as_of=as_of)
    withlate = build([mk("a", A, 1), late], as_of=as_of)
    assert withlate.to_dict() == solo.to_dict()
    assert event_of(withlate, "b") is None  # unclassified at as_of, no Jaccard link
    assert [u["report_id"] for u in withlate.unattached] == ["b"]
    later = build([mk("a", A, 1), late], as_of=as_of + timedelta(days=2))
    assert event_of(later, "b") is event_of(later, "a")


def test_item_older_than_the_pool_window_is_outside():
    old = mk("old", A, -24 * 8)
    res = build([old, mk("a", J_ZERO, 1)])
    assert res.meta["excluded"]["outside_pool"] == 1 and event_of(res, "old") is None


def test_pool_window_comes_from_config():
    items = [mk("a", A, 1)]
    narrow = dataclasses.replace(CFG, pool_window_days=1)
    res = build_events(items, T0 + timedelta(days=3), config=narrow)
    assert res.events == []


def test_thresholds_come_from_config():
    items = [mk("a", A, 0), mk("b", J_ZERO, 10)]
    as_of = T0 + timedelta(hours=12)
    base = build_events(items, as_of, config=CFG)
    assert event_of(base, "a") is event_of(base, "b")
    tight = build_events(items, as_of, config=dataclasses.replace(CFG, horizon_hours=5))
    assert event_of(tight, "a") is not event_of(tight, "b")
    strict = build_events(items, as_of, config=dataclasses.replace(CFG, entity_relevance_min=0.95))
    assert event_of(strict, "a") is not event_of(strict, "b")


# ------------------------------------------------------------------ odd inputs


def test_classification_that_is_not_forward_evidence_is_unclassified_and_flagged():
    seen = T0 + timedelta(hours=1)
    stale = cls_at(seen + timedelta(days=20))  # classified 20 days after publication
    item = NewsItem("a", A, "https://www.x.com/a", seen, seen - timedelta(minutes=30), (stale,))
    res = build([item], as_of=seen + timedelta(days=21) - timedelta(days=0), )
    assert res.events == []
    mock = dataclasses.replace(cls_at(seen + timedelta(minutes=5)), origin=None, ai_provider="mock")
    res = build([NewsItem("m", A, "https://www.x.com/m", seen, seen, (mock,))])
    assert res.events == []
    assert res.meta["items_classified"] == 0


def test_item_without_published_at_cannot_carry_forward_evidence_and_is_flagged():
    item = mk("a", A, 1)
    item = dataclasses.replace(item, published_at=None)
    res = build([item, mk("b", J_ZERO, 2)])
    assert event_of(res, "a") is None
    assert "published_at_missing" in res.unattached[0]["flags"]


def test_unparseable_classification_is_unclassified_with_a_flag():
    seen = T0 + timedelta(hours=1)
    bad = Classification(classified_at=seen + timedelta(minutes=5), status="success", origin="live", parse_error=True)
    res = build([NewsItem("a", A, "https://www.x.com/a", seen, seen, (bad,))])
    assert res.events == [] and "classification_unparseable" in res.unattached[0]["flags"]


def test_failed_classification_is_just_unclassified():
    seen = T0 + timedelta(hours=1)
    err = Classification(classified_at=seen + timedelta(minutes=5), status="error")
    res = build([NewsItem("a", A, "https://www.x.com/a", seen, seen, (err,))])
    assert res.events == [] and len(res.unattached) == 1


def test_latest_success_wins_among_several_classifications():
    seen = T0 + timedelta(hours=1)
    old = cls_at(seen + timedelta(minutes=5), themes=OIL)
    new = cls_at(seen + timedelta(hours=2), themes=GEO)
    item = NewsItem("a", A, "https://www.x.com/a", seen, seen, (old, new))
    assert build([item], as_of=seen + timedelta(hours=3)).events[0]["themes"][0]["theme_id"] == "geopolitical_risk"
    assert build([item], as_of=seen + timedelta(hours=1)).events[0]["themes"][0]["theme_id"] == "energy_supply_shock"


@pytest.mark.parametrize("title", [None, "", "   "])
def test_missing_titles_do_not_crash_do_not_merge_and_are_flagged(title):
    res = build([mk("a", title, 1), mk("b", title, 2)])
    (ev,) = res.events  # same entity and theme link them, but each stays its own report
    assert ev["report_count"] == 2 and "title_missing" in ev["flags"]


def test_missing_url_is_kept_without_a_publisher_for_corroboration():
    res = build([mk("a", A, 1, url=""), dataclasses.replace(mk("b", J_ZERO, 2), source_url=None)])
    (ev,) = res.events
    assert ev["publisher_count"] == 0 and ev["event_status"] == DETECTED


def test_odd_entity_and_theme_rows_are_skipped_not_fatal():
    seen = T0 + timedelta(hours=1)
    cls = Classification(classified_at=seen + timedelta(minutes=5), status="success", origin="live",
                         themes=({"direction": "positive"}, {"theme_id": "x"}, {"theme_id": "geopolitical_risk", "direction": "positive"}),
                         entities=({"relevance": 0.9}, {"name": "Houthis", "relevance": "high"}, {"name": "Houthis", "relevance": 0.9}))
    res = build([NewsItem("a", A, "https://www.x.com/a", seen, seen, (cls,))])
    (ev,) = res.events
    assert [t["theme_id"] for t in ev["themes"]] == ["geopolitical_risk"]
    assert any("relevance_missing" in f for f in ev["flags"])


def test_empty_input_duplicates_and_missing_first_seen():
    assert build([]).events == []
    dup = [mk("a", A, 1), mk("a", A, 1)]
    assert build(dup).meta["excluded"]["duplicate_news_id"] == 1
    nofs = dataclasses.replace(mk("z", A, 1), first_seen_at=None)
    assert build([nofs]).meta["excluded"]["no_first_seen_at"] == 1


def test_timezone_aware_inputs_are_accepted():
    from datetime import UTC

    assert naive_utc(datetime(2026, 10, 9, 12, tzinfo=UTC)) == datetime(2026, 10, 9, 12)
    assert naive_utc("2026-10-09T12:00:00Z") == datetime(2026, 10, 9, 12)
    assert naive_utc(None) is None
    res = build([mk("a", A, 1)], as_of="2026-10-14T00:00:00Z")
    assert len(res.events) == 1


# ------------------------------------------------------------------ store loader


def _store():
    con = duckdb.connect(":memory:")
    con.execute("create table news_items (news_id varchar, title varchar, source_url varchar, published_at timestamp, first_seen_at timestamp)")
    con.execute("""create table news_classifications (news_id varchar, classified_at timestamp, classification_status varchar,
        origin varchar, ai_provider varchar, macro_themes_json varchar, sector_impacts_json varchar, entities_json varchar,
        severity double, confidence double, summary varchar)""")
    return con


def test_store_pool_reads_items_and_only_classifications_made_by_as_of():
    con = _store()
    con.execute("insert into news_items values ('a','Houthis fire missiles at Riyadh airport','https://www.reuters.com/a','2026-10-09 00:30:00','2026-10-09 01:00:00')")
    con.execute("insert into news_items values ('old','Old news','https://www.reuters.com/o','2026-09-01','2026-09-01 00:00:00')")
    themes = json.dumps([{"theme_id": "geopolitical_risk", "direction": "positive", "severity": 0.3}])
    ents = json.dumps([{"name": "Houthis", "entity_type": "other", "relevance": 0.9}])
    con.execute("insert into news_classifications values ('a','2026-10-09 01:10:00','success','live','deepseek',?,'[]',?,0.5,0.6,'s')", [themes, ents])
    con.execute("insert into news_classifications values ('a','2026-10-20 01:10:00','success','live','deepseek','[]','[]','[]',0.9,0.6,'later')")
    items = load_pool(con, "2026-10-14T00:00:00", 7)
    assert [i.news_id for i in items] == ["a"]
    assert len(items[0].classifications) == 1
    res = build(items)
    assert res.events[0]["themes"][0]["theme_id"] == "geopolitical_risk"
    assert res.events[0]["max_severity"] == 0.5


def test_store_row_with_bad_json_is_flagged_not_fatal():
    c = parse_classification_row({"classified_at": "2026-10-09 01:10:00", "classification_status": "success",
                                  "macro_themes_json": "{not json", "entities_json": None})
    assert c.parse_error and c.themes == ()
    ok = parse_classification_row({"classified_at": "2026-10-09 01:10:00", "classification_status": "success",
                                   "macro_themes_json": "[]", "entities_json": None, "sector_impacts_json": ""})
    assert not ok.parse_error
