"""The v2.3 holdout scorer must behave on degenerate clusterings exactly as the pre-registration claims."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "events_holdout"))

import sample_holdout as sh  # noqa: E402
import score_holdout as sc  # noqa: E402

DRAWS = 200


def build_labels():
    labels, order = {}, []

    def add(i, episode, story, report=None, membership=None):
        order.append(i)
        labels[i] = {"episode_id": episode, "storyline_id": story, "report_label": report or f"r_{i}"}
        if membership:
            labels[i]["membership"] = membership

    for k in range(8):  # EP_A: 8 core items, items a0 and a1 are one syndicated report
        add(f"a{k}", "EP_A", "S1", "rep_a" if k < 2 else None)
    add("a_periph", "EP_A", "S1", membership="peripheral")
    for k in range(5):
        add(f"b{k}", "EP_B", "S1")  # a second episode of the same storyline
    for k in range(4):
        add(f"c{k}", "EP_C", "S2")
    for k in range(3):
        add(f"d{k}", "EP_D", "S3")
    for k in range(2):
        add(f"e{k}", "EP_E", "S4")
    for k in range(40):
        add(f"n{k}", "NONE", "NONE")
    for k in range(4):
        add(f"x{k}", "NOISE", "NOISE")
    return labels, order


LABELS, IDS = build_labels()


def perfect():
    return {i: LABELS[i]["episode_id"] for i in IDS if LABELS[i]["episode_id"] not in sc.RESERVED}


def run(clusters):
    return sc.score(LABELS, clusters, IDS, draws=DRAWS)


def test_labels_validate_and_reject_inconsistencies():
    assert sc.validate_labels(LABELS, IDS) == []
    bad = {**LABELS, "b0": {**LABELS["b0"], "storyline_id": "S9"}}
    assert any("several storylines" in p for p in sc.validate_labels(bad, IDS))
    bad = {**LABELS, "n0": {**LABELS["n0"], "storyline_id": "S1"}}
    assert any("NONE episode" in p for p in sc.validate_labels(bad, IDS))
    bad = {**LABELS, "c0": {**LABELS["c0"], "report_label": "rep_a"}}
    assert any("spans several episodes" in p for p in sc.validate_labels(bad, IDS))
    assert any("no label" in p for p in sc.validate_labels({}, ["zz"]))
    with pytest.raises(ValueError):
        sc.score({}, {}, ["zz"])


def test_perfect_clustering_passes():
    res = run(perfect())
    assert res["pass"], res["checks"]
    p = res["primary"]
    assert p["episode_recall"] == 1.0 and p["contamination"] == 0.0
    assert p["recall_uplift"] >= sc.MIN_RECALL_UPLIFT
    assert res["ci90"]["contamination"] == (0.0, 0.0)
    assert res["secondary"]["bcubed"]["precision"] == 1.0 == res["secondary"]["pairwise"]["recall"]
    assert p["largest_episode"] == 9 and p["largest_cluster"] == 9  # the peripheral item counts as a member


def test_syndication_only_baseline_is_the_floor_a_linker_must_beat():
    base = sc.point_metrics(LABELS, sc.syndication_baseline(LABELS, IDS), IDS)
    assert base["episode_recall"] == pytest.approx((2 / 8 + 1 / 5 + 1 / 4 + 1 / 3 + 1 / 2) / 5)
    assert base["contamination"] == 0.0  # the only merged items are one real report


def test_all_singletons_does_not_pass():
    res = run({})
    assert not res["pass"]
    assert res["primary"]["contamination"] is None  # no multi-item cluster: undefined, and undefined fails
    assert res["checks"]["contamination_upper_le_0_15"] is False
    assert res["checks"]["episode_recall_uplift_ge_0_15"] is False
    assert res["secondary"]["bcubed"]["precision"] == 1.0  # B-cubed flatters singletons: why it is secondary


def test_everything_merged_fails():
    res = run({i: "one" for i in IDS})
    assert not res["pass"]
    assert res["checks"]["contamination_upper_le_0_15"] is False  # recall is trivially 1.0; contamination and size fail
    assert res["checks"]["largest_cluster_le_1_5x_largest_episode"] is False
    assert res["primary"]["cluster_to_episode_ratio"] == len(IDS) / 9


def test_hub_with_ten_outsiders_fails_contamination():
    clusters = perfect()
    for k in range(10):
        clusters[f"n{k}"] = "EP_A"
    res = run(clusters)
    assert res["primary"]["contamination"] == pytest.approx(10 / (18 + 5 + 4 + 3 + 2))
    assert res["checks"]["contamination_upper_le_0_15"] is False
    assert res["checks"]["largest_cluster_le_1_5x_largest_episode"] is False
    assert res["primary"]["episode_recall"] == 1.0  # recall alone would have called this perfect
    assert not res["pass"]


def test_each_episode_split_into_five_fails_on_recall():
    clusters = {}
    for i in IDS:
        e = LABELS[i]["episode_id"]
        if e not in sc.RESERVED:
            clusters[i] = (e, int(i[1:]) % 5 if i[1:].isdigit() else 0)
    res = run(clusters)
    assert res["primary"]["contamination"] == 0.0 and res["checks"]["contamination_upper_le_0_15"]
    assert res["checks"]["episode_recall_uplift_ge_0_15"] is False and not res["pass"]
    frag = {r["episode_id"]: r for r in res["fragmentation"]}
    assert frag["EP_A"]["fragments"] >= 4 and frag["EP_A"]["largest_fragment"] <= 2


def test_peripheral_items_are_never_outsiders_and_never_members():
    clusters = perfect()
    clusters["a_periph"] = "EP_B"  # a peripheral item in another episode's cluster
    assert run(clusters)["primary"]["contamination"] == 0.0


def test_storyline_recall_is_information_and_reflects_episodes_kept_together():
    clusters = perfect()
    res = run(clusters)
    # S1 holds EP_A and EP_B in separate clusters: the storyline is split although every episode is whole
    assert res["storyline_recall_info"]["value"] < res["primary"]["episode_recall"]
    merged = {**clusters, **{f"b{k}": "EP_A" for k in range(5)}}
    assert run(merged)["storyline_recall_info"]["value"] == 1.0


def test_bootstrap_is_deterministic_and_brackets_the_point_estimate():
    clusters = perfect()
    clusters["n0"] = "EP_C"
    clusters["n1"] = "EP_D"
    a, b = run(clusters), run(clusters)
    assert a["ci90"] == b["ci90"]
    lo, hi = a["ci90"]["contamination"]
    assert lo <= a["primary"]["contamination"] + 1e-9 and a["primary"]["contamination"] - 1e-9 <= hi
    assert hi > lo


def test_inter_labeller_ceiling_is_reported_both_ways():
    other = {i: dict(v) for i, v in LABELS.items()}
    for k in range(5):  # the second labeller folds EP_B into EP_A
        other[f"b{k}"]["episode_id"] = "EP_A"
    ceil = sc.inter_labeller_ceiling(LABELS, other, IDS)
    assert ceil["a_against_b"]["bcubed"]["f1"] < 1.0 and ceil["b_against_a"]["bcubed"]["f1"] < 1.0
    same = sc.inter_labeller_ceiling(LABELS, LABELS, IDS)
    assert same["a_against_b"]["bcubed"]["f1"] == 1.0 and same["a_against_b"]["episode_recall"] == 1.0


# ------------------------------------------------------------------ sampler


def _store(rows):
    con = duckdb.connect(":memory:")
    con.execute("create table news_items (news_id varchar, published_at timestamp, first_seen_at timestamp, "
                "title varchar, source_url varchar)")
    for r in rows:
        con.execute("insert into news_items values (?, ?, ?, ?, ?)", list(r))
    return con


ROWS = [
    ("n3", datetime(2026, 10, 21, 8), datetime(2026, 10, 21, 9), "Oil - Reuters", "https://news.google.com/x"),
    ("n1", None, datetime(2026, 10, 20, 1), "Fed holds", "https://www.cnbc.com/a"),
    ("n2", datetime(2026, 10, 19, 22), datetime(2026, 10, 20, 1), "Kenya case - a relative", "https://www.bbc.co.uk/b"),
    ("n0", None, datetime(2026, 10, 19, 23), "before window", "https://www.cnbc.com/c"),
    ("n9", None, datetime(2026, 10, 26, 0, 5), "after window", "https://www.cnbc.com/d"),
]


def test_sampler_takes_every_item_of_the_window_in_a_deterministic_order(tmp_path):
    con = _store(ROWS)
    rows = sh.select(con, date(2026, 10, 20), 5, date(2026, 10, 19))
    assert [r[0] for r in rows] == ["n1", "n2", "n3"]  # all of them; ties on first_seen_at break on news_id
    out = tmp_path / "items.csv"
    sh.write(rows, str(out))
    sh.write(sh.select(con, date(2026, 10, 20), 5, date(2026, 10, 19)), str(tmp_path / "again.csv"))
    assert out.read_text(encoding="utf-8") == (tmp_path / "again.csv").read_text(encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    assert ",Reuters," in text and ",www.bbc.co.uk," in text  # the suffix names a publisher only on the aggregator


def test_sampler_refuses_a_window_before_the_freeze_or_not_yet_elapsed():
    con = _store(ROWS)
    with pytest.raises(SystemExit, match="before the freeze"):
        sh.select(con, date(2026, 10, 18), 5, date(2026, 10, 19))
    with pytest.raises(SystemExit, match="not fully elapsed"):
        sh.select(con, date(2026, 10, 22), 5, date(2026, 10, 19))
    with pytest.raises(SystemExit, match="not fully elapsed"):
        sh.select(_store([ROWS[0]]), date(2026, 10, 20), 5, date(2026, 10, 19))
