"""The E1 acceptance scorer must fail degenerate clusterings (reference set v1.1, LABELLING.md)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "events_reference"))

import acceptance as acc  # noqa: E402

IDS, LABELS = acc.load_reference()


def _perfect(include_peripheral: bool = True):
    clusters, meta = {}, {}
    for i in IDS:
        lab = LABELS[i]
        if lab["event"] in ("NONE", "noise"):
            continue
        if lab.get("membership") == "peripheral" and not include_peripheral:
            continue
        clusters[i] = lab["event"]
    for event in set(clusters.values()):
        meta[event] = {"status": "corroborated", "related": []}
    meta["E_HOUTHI_ADEN_PALACE"]["related"] = ["E_HOUTHI_SAUDI"]
    return clusters, meta


def test_reference_set_shape():
    assert len(IDS) == 300
    r = acc.rates({}, LABELS, IDS)
    assert (r["universe_items"], r["label_pairs"]) == (242, 45)
    assert sum(1 for v in LABELS.values() if v.get("membership") == "peripheral") == 22


def test_perfect_clustering_passes_with_or_without_peripheral_members():
    for flag in (True, False):
        res = acc.accept(*_perfect(flag))
        assert res["pass"], res
        assert res["false_split"] == 0 and res["false_merge"] == 0


def test_split_everything_but_the_examples_fails():
    clusters, meta = _perfect()
    clusters = {i: c for i, c in clusters.items() if c in acc.GATED}
    res = acc.accept(clusters, meta)
    assert all(res["gates"].values())  # the four gated events are all right
    assert res["false_split"] == 1.0 and not res["false_split_ok"]
    assert res["false_merge"] is None and not res["false_merge_ok"]
    assert not res["pass"]


def test_split_everything_fails():
    res = acc.accept({}, {})
    assert not res["pass"] and not any(res["gates"].values())


def test_merge_everything_fails():
    clusters = dict.fromkeys(IDS, "ALL")
    res = acc.accept(clusters, {"ALL": {"status": "corroborated", "related": []}})
    assert res["false_merge"] > 0.9 and not res["false_merge_ok"]
    assert not res["pass"]


def test_merge_everything_but_the_examples_fails():
    clusters, meta = _perfect()
    for i in IDS:
        if LABELS[i]["event"] not in acc.GATED and LABELS[i].get("membership") != "peripheral":
            clusters[i] = "REST"
    res = acc.accept(clusters, meta)
    assert not res["false_merge_ok"] and not res["pass"]


def test_one_false_merge_and_one_false_split_stay_inside_the_thresholds():
    clusters, meta = _perfect()
    none_ids = [i for i in IDS if LABELS[i]["event"] == "NONE"][:2]
    clusters[none_ids[0]] = clusters[none_ids[1]] = "X"
    res = acc.accept(clusters, meta)
    assert res["false_merge_pairs"] == 1 and res["pass"]


def test_gate_failures():
    clusters, meta = _perfect()
    core_cw = [i for i in IDS if LABELS[i]["event"] == "E_COREWEAVE_DISTRESS" and LABELS[i].get("membership") != "peripheral"]
    broken = dict(clusters)
    broken[core_cw[0]] = "elsewhere"
    assert not acc.accept(broken, meta)["gates"]["E_COREWEAVE_DISTRESS"]
    uncorroborated = {k: dict(v) for k, v in meta.items()}
    uncorroborated["E_CPI_SEP"]["status"] = "single_report"
    assert not acc.accept(clusters, uncorroborated)["gates"]["E_CPI_SEP"]
    outsider = next(i for i in IDS if LABELS[i]["event"] == "noise")
    polluted = dict(clusters)
    polluted[outsider] = "E_HOUTHI_SAUDI"
    assert not acc.accept(polluted, meta)["gates"]["E_HOUTHI_SAUDI"]
    unrelated = {k: dict(v) for k, v in meta.items()}
    unrelated["E_HOUTHI_ADEN_PALACE"]["related"] = []
    assert not acc.accept(clusters, unrelated)["gates"]["E_HOUTHI_ADEN_PALACE"]
    merged = dict(clusters)
    for i, c in clusters.items():
        if c == "E_HOUTHI_ADEN_PALACE":
            merged[i] = "E_HOUTHI_SAUDI"
    assert not acc.accept(merged, meta)["gates"]["E_HOUTHI_ADEN_PALACE"]
