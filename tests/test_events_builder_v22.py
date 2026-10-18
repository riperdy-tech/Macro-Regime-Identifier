"""Plan erratum v2.2 (section 5.4) and the phase-3b fixes: same-report supply, entity background, tie-break,
config-held publisher rules, severity as a value."""
from __future__ import annotations

import dataclasses
import math
from datetime import datetime, timedelta

import pytest
from test_events_builder import A, CFG, J_HIGH, J_ZERO, T0, cls_at, event_of, mk

from macro_engine.events.builder import (
    Classification,
    NewsItem,
    build_events,
    canonical_title,
    extract_publisher,
)
from macro_engine.events.config import load_e1_config
from macro_engine.events.store_io import parse_classification_row

AS_OF = datetime(2026, 10, 14)
POOL_START = AS_OF - timedelta(days=7)
GEO = (("geopolitical_risk", "positive"),)
OIL = (("energy_supply_shock", "positive"),)
X = (("Houthis", 0.9, "other"),)
XY = (("Houthis", 0.9, "other"), ("Riyadh Port Authority", 0.9, "other"))
Y = (("Riyadh Port Authority", 0.9, "other"),)


def b22(items, background=None, cfg=CFG, linking="v2_2"):
    return build_events(items, AS_OF, config=cfg, linking=linking, background=background)


def bg_items(n_houthis: int, n_total: int) -> list[NewsItem]:
    """n_total classified items first seen up to n_total hours before the pool start; n_houthis name Houthis."""
    out = []
    for k in range(n_total):
        seen = POOL_START - timedelta(hours=1 + k)
        ents = X if k < n_houthis else ((f"Background Entity {k}", 0.9, "other"),)
        out.append(NewsItem(f"bg{k}", f"background title {k}", f"https://www.bg{k}.com/x", seen,
                            seen - timedelta(minutes=30), (cls_at(seen + timedelta(minutes=10), GEO, ents),)))
    return out


# ------------------------------------------------------------------ (a) one report supplies both


def union_case():
    a = mk("a", A, 1, themes=GEO, ents=X)  # X + GEO
    b = mk("b", J_HIGH, 2, themes=OIL, ents=XY)  # joins a by X and Jaccard >= 0.3; brings Y and OIL
    c = mk("c", J_ZERO, 3, themes=GEO, ents=Y)  # Y is b's, GEO is a's, no title overlap
    return [a, b, c]


def test_union_only_join_is_rejected_in_v2_2_and_annotated():
    res = b22(union_case())
    assert event_of(res, "a") is event_of(res, "b")
    ec = event_of(res, "c")
    assert ec is not event_of(res, "a")
    rep_c = next(r for r in ec["reports"] if "c" in r["item_ids"])
    assert any(f.startswith("union_link_rejected:" + event_of(res, "a")["event_id"]) for f in rep_c["flags"])


def test_union_join_is_kept_in_v2():
    res = b22(union_case(), linking="v2")
    assert event_of(res, "c") is event_of(res, "a")


def test_one_report_supplying_entity_and_theme_links_in_v2_2():
    res = b22([mk("a", A, 1, themes=GEO, ents=X), mk("c", J_ZERO, 3, themes=GEO, ents=X)])
    assert event_of(res, "a") is event_of(res, "c")


def test_one_report_supplying_entity_and_jaccard_links_in_v2_2():
    res = b22([mk("a", A, 1, themes=GEO, ents=X), mk("b", J_HIGH, 2, themes=OIL, ents=X)])
    assert event_of(res, "a") is event_of(res, "b")


def test_72h_rule_and_unclassified_rule_are_unchanged_in_v2_2():
    a = mk("a", A, 1)
    far = mk("far", J_HIGH, 1 + 73)
    un = mk("u", A + " today", 5, classified=False)
    res = b22([a, far, un])
    assert event_of(res, "a") is not event_of(res, "far")
    assert event_of(res, "u") is event_of(res, "a")


def test_linking_mode_default_comes_from_config_and_bad_value_raises():
    assert CFG.linking == "v2_2"
    res = build_events([mk("a", A, 1)], AS_OF, config=CFG)
    assert res.meta["linking"] == "v2_2"
    with pytest.raises(ValueError):
        build_events([], AS_OF, config=CFG, linking="v3")


# ------------------------------------------------------------------ (b) entity background


def pair():
    return [mk("a", A, 1, themes=GEO, ents=X), mk("b", J_HIGH, 2, themes=GEO, ents=X)]


def test_background_over_the_share_excludes_the_entity_and_says_so():
    cfg = dataclasses.replace(CFG, background_min_items=100)
    res = b22(pair(), background=bg_items(2, 100), cfg=cfg)  # 2% > 1%
    assert event_of(res, "a") is not event_of(res, "b")
    flags = event_of(res, "a")["flags"]
    assert "entity_not_linkable:background_share:houthis" in flags and "no_linkable_entity" in flags
    assert res.meta["background_items"] == 100 and res.meta["background_thin"] is False
    ent = event_of(res, "a")["entities_other"][0]
    assert ent["link_exclusion"] == "background_share"


def test_background_exactly_at_the_share_still_counts():
    cfg = dataclasses.replace(CFG, background_min_items=100)
    res = b22(pair(), background=bg_items(1, 100), cfg=cfg)  # 1% is "at most 1%"
    assert event_of(res, "a") is event_of(res, "b")


def test_background_applies_to_every_entity_with_no_exemption():
    cfg = dataclasses.replace(CFG, background_min_items=100)
    country = (("United Kingdom", 0.9, "country"),)
    out = []
    for k in range(100):
        seen = POOL_START - timedelta(hours=1 + k)
        ents = country if k < 5 else ((f"Other {k}", 0.9, "other"),)
        out.append(NewsItem(f"bg{k}", f"t{k}", f"https://x{k}.com/", seen, seen,
                            (cls_at(seen + timedelta(minutes=5), GEO, ents),)))
    items = [mk("a", A, 1, ents=country), mk("b", J_HIGH, 2, ents=country)]
    res = b22(items, background=out, cfg=cfg)
    assert event_of(res, "a") is not event_of(res, "b")


def test_thin_background_is_annotated_and_the_test_uses_what_exists():
    res = b22(pair(), background=bg_items(50, 100))  # 100 < 300 default minimum; 50% share still excludes
    assert res.meta["background_thin"] is True
    assert event_of(res, "a") is not event_of(res, "b")
    assert "background_thin" in event_of(res, "a")["flags"]
    none = b22(pair())  # no background at all: thin, nothing excluded
    assert none.meta["background_items"] == 0 and none.meta["background_thin"] is True
    assert event_of(none, "a") is event_of(none, "b")


def test_enough_background_is_not_thin():
    res = b22(pair(), background=bg_items(0, 300))
    assert res.meta["background_thin"] is False
    assert "background_thin" not in event_of(res, "a")["flags"]


def test_background_has_no_look_ahead():
    cfg = dataclasses.replace(CFG, background_min_items=100)
    good = bg_items(0, 50)
    bad: list[NewsItem] = []
    for k in range(50):  # would be 50 Houthis of 100 if counted
        why = k % 3
        if why == 0:  # first seen inside the pool window
            seen = POOL_START + timedelta(hours=1 + k)
            cls_t = seen + timedelta(minutes=5)
        elif why == 1:  # classified after the pool start
            seen = POOL_START - timedelta(hours=1 + k)
            cls_t = POOL_START + timedelta(hours=1)
        else:  # older than the background window
            seen = POOL_START - timedelta(days=cfg.background_days + 1 + k / 100)
            cls_t = seen + timedelta(minutes=5)
        bad.append(NewsItem(f"bad{k}", f"bad {k}", "https://bad.com/", seen, seen, (cls_at(cls_t, GEO, X),)))
    res = b22(pair(), background=good + bad, cfg=cfg)
    assert res.meta["background_items"] == 50
    assert event_of(res, "a") is event_of(res, "b")


def test_background_item_at_the_pool_start_is_background_not_pool():
    cfg = dataclasses.replace(CFG, background_min_items=1)
    it = NewsItem("edge", "edge", "https://e.com/", POOL_START, POOL_START, (cls_at(POOL_START, GEO, X),))
    res = b22(pair(), background=[it], cfg=cfg)
    assert res.meta["background_items"] == 1
    assert res.meta["items_in_pool"] == 2


def test_v2_ignores_the_background():
    res = b22(pair(), background=bg_items(50, 100), linking="v2")
    assert event_of(res, "a") is event_of(res, "b")
    assert res.meta["background_items"] is None


# ------------------------------------------------------------------ tie-break


def _tied(published_a, published_z):
    seen = T0 + timedelta(hours=1)
    cls = (cls_at(seen + timedelta(minutes=5), GEO, X),)
    return [NewsItem("a", A, "https://www.p-a.com/", seen, published_a, cls),
            NewsItem("z", A, "https://www.p-z.com/", seen, published_z, cls)]


def test_first_seen_ties_break_on_published_at_then_news_id():
    early, late = T0, T0 + timedelta(minutes=20)
    res = b22(_tied(late, early))  # z published earlier although its id sorts later
    assert event_of(res, "z")["reports"][0]["report_id"] == "z"
    res = b22(_tied(early, early))  # same published_at: news_id
    assert event_of(res, "a")["reports"][0]["report_id"] == "a"
    res = b22(_tied(None, early))  # a missing published_at sorts last
    assert event_of(res, "z")["reports"][0]["report_id"] == "z"


def test_tie_break_is_independent_of_input_order():
    items = _tied(T0 + timedelta(minutes=20), T0)
    assert b22(items).events == b22(list(reversed(items))).events


# ------------------------------------------------------------------ config-held rules and severity


def test_aggregator_hosts_and_suffix_pattern_come_from_config():
    assert "news.google.com" in CFG.aggregator_hosts and CFG.publisher_suffix_pattern
    cfg = dataclasses.replace(CFG, aggregator_hosts=frozenset({"agg.example"}))
    assert extract_publisher("Oil jumps - Reuters", "https://agg.example/a", cfg)[0] == "reuters"
    assert extract_publisher("Oil jumps - Reuters", "https://news.google.com/a", cfg)[0] == "news.google.com"
    cfg2 = dataclasses.replace(CFG, publisher_suffix_pattern=r"^(.*\S)\s+\|\s+([^|]{2,40})$")
    assert canonical_title("Oil jumps | Reuters", cfg2) == "oil jumps"
    assert canonical_title("Oil jumps - Reuters", cfg2) == "oil jumps - reuters"


def test_events_yaml_is_version_3_with_the_unratified_v2_2_block():
    cfg = load_e1_config()
    assert cfg.version == 3 and cfg.linking == "v2_2"
    assert (cfg.background_days, cfg.background_max_share, cfg.background_min_items) == (28.0, 0.01, 300)


def test_zero_severity_survives_the_store_and_the_record():
    row = {"classified_at": T0, "classification_status": "success", "severity": 0.0, "confidence": 0.0}
    c = parse_classification_row(row)
    assert c.severity == 0.0 and c.confidence == 0.0
    assert parse_classification_row({**row, "severity": float("nan")}).severity is None
    assert parse_classification_row({**row, "severity": "0.0"}).severity == 0.0
    seen = T0 + timedelta(hours=1)
    item = NewsItem("a", A, "https://www.a.com/", seen, seen, (Classification(
        classified_at=seen + timedelta(minutes=5), status="success", origin="live",
        themes=({"theme_id": "geopolitical_risk", "direction": "positive"},),
        entities=({"name": "Houthis", "relevance": 0.9},), severity="0.0"),))  # type: ignore[arg-type]
    ev = b22([item]).events[0]
    assert ev["max_severity"] == 0.0 and ev["mean_severity"] == 0.0 and not math.isnan(ev["mean_severity"])
