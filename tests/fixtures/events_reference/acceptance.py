"""E1 acceptance scorer for the reference set v1.1 (definitions are binding; see LABELLING.md).

Pure functions over a clustering. It contains no clustering, Jaccard or classifier logic. A clustering is a mapping
news_id -> cluster id for the 300 reference items; an item missing from the mapping is its own one-item cluster.
`clusters_meta` maps cluster id -> {"status": str, "related": [cluster id, ...]} and is read by the zero-tolerance gates.
"""
from __future__ import annotations

import csv
import itertools
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
GATED = ("E_HOUTHI_SAUDI", "E_HOUTHI_ADEN_PALACE", "E_COREWEAVE_DISTRESS", "E_CPI_SEP")
SINGLE_EVENT_EXAMPLES = ("E_HOUTHI_SAUDI", "E_COREWEAVE_DISTRESS", "E_CPI_SEP")
MAX_FALSE_SPLIT = 0.10
MAX_FALSE_MERGE = 0.05


def load_reference(directory: Path = HERE) -> tuple[list[str], dict[str, dict[str, Any]]]:
    with open(directory / "items.csv", encoding="utf-8") as fh:
        ids = [r["news_id"] for r in csv.DictReader(fh)]
    labels = yaml.safe_load((directory / "labels.yaml").read_text(encoding="utf-8"))["items"]
    return ids, labels


def _is_peripheral(label: dict[str, Any]) -> bool:
    return label.get("membership") == "peripheral"


def _is_event(label: dict[str, Any]) -> bool:
    return label["event"] not in ("NONE", "noise")


def rate_universe(labels: dict[str, dict[str, Any]], ids: list[str]) -> list[str]:
    """Items the two pairwise rates are computed over: core members of non-gated events, plus NONE and noise."""
    return [
        i for i in ids
        if not _is_peripheral(labels[i]) and labels[i]["event"] not in GATED
    ]


def pair_counts(clusters: dict[str, Any], labels: dict[str, dict[str, Any]], ids: list[str]) -> dict[str, int]:
    uni = rate_universe(labels, ids)
    cid = {i: clusters.get(i, ("single", i)) for i in uni}
    l_pairs = p_pairs = split = merge = 0
    for a, b in itertools.combinations(uni, 2):
        same_label = _is_event(labels[a]) and labels[a]["event"] == labels[b]["event"]
        same_cluster = cid[a] == cid[b]
        l_pairs += same_label
        p_pairs += same_cluster
        split += same_label and not same_cluster
        merge += same_cluster and not same_label
    return {"universe_items": len(uni), "label_pairs": l_pairs, "cluster_pairs": p_pairs, "false_split_pairs": split, "false_merge_pairs": merge}


def rates(clusters: dict[str, Any], labels: dict[str, dict[str, Any]], ids: list[str]) -> dict[str, Any]:
    c = pair_counts(clusters, labels, ids)
    fs = c["false_split_pairs"] / c["label_pairs"] if c["label_pairs"] else None
    fm = c["false_merge_pairs"] / c["cluster_pairs"] if c["cluster_pairs"] else None
    return {**c, "false_split": fs, "false_merge": fm}


def _members(labels: dict[str, dict[str, Any]], ids: list[str], event: str) -> tuple[list[str], list[str]]:
    core = [i for i in ids if labels[i]["event"] == event and not _is_peripheral(labels[i])]
    periph = [i for i in ids if labels[i]["event"] == event and _is_peripheral(labels[i])]
    return core, periph


def gates(clusters: dict[str, Any], clusters_meta: dict[Any, dict[str, Any]], labels: dict[str, dict[str, Any]], ids: list[str]) -> dict[str, bool]:
    """Zero-tolerance gates for the three single-event examples and the Aden palace event."""
    cid = {i: clusters.get(i, ("single", i)) for i in ids}
    out: dict[str, bool] = {}
    cluster_of_event: dict[str, Any] = {}
    for event in (*SINGLE_EVENT_EXAMPLES, "E_HOUTHI_ADEN_PALACE"):
        core, _ = _members(labels, ids, event)
        homes = {cid[i] for i in core}
        ok = len(homes) == 1
        if ok:
            home = next(iter(homes))
            cluster_of_event[event] = home
            outsiders = [
                i for i in ids
                if cid[i] == home and labels[i]["event"] != event and not _is_peripheral(labels[i])
            ]
            ok = not outsiders
            if event in SINGLE_EVENT_EXAMPLES:
                ok = ok and (clusters_meta.get(home) or {}).get("status") == "corroborated"
        out[event] = ok
    aden = cluster_of_event.get("E_HOUTHI_ADEN_PALACE")
    houthi = cluster_of_event.get("E_HOUTHI_SAUDI")
    out["E_HOUTHI_ADEN_PALACE"] = bool(
        out["E_HOUTHI_ADEN_PALACE"] and houthi is not None and aden != houthi
        and houthi in ((clusters_meta.get(aden) or {}).get("related") or [])
    )
    return out


def accept(clusters: dict[str, Any], clusters_meta: dict[Any, dict[str, Any]], directory: Path = HERE) -> dict[str, Any]:
    ids, labels = load_reference(directory)
    r = rates(clusters, labels, ids)
    g = gates(clusters, clusters_meta, labels, ids)
    r["false_split_ok"] = r["false_split"] is not None and r["false_split"] <= MAX_FALSE_SPLIT
    r["false_merge_ok"] = r["false_merge"] is not None and r["false_merge"] <= MAX_FALSE_MERGE  # undefined fails
    r["gates"] = g
    r["pass"] = r["false_split_ok"] and r["false_merge_ok"] and all(g.values())
    return r
