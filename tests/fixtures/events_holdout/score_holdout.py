"""Holdout scorer for the v2.3 pre-registration (docs/events_build/V23_PREREGISTRATION.md).

Pure functions over a clustering. It contains no clustering, similarity or classifier logic. A clustering maps
news_id -> cluster id; an item missing from the mapping is its own one-item cluster. Labels follow
LABELLING_GUIDE_V2.md: `episode_id`, `storyline_id`, `report_label`, optional `membership: peripheral`.

Definitions (binding once frozen)
  * `NONE` / `NOISE` episode ids are one-story episodes (one-item storylines): items are units keyed by `report_label`,
    so syndicated copies of one NONE story are one unit and never outsiders of each other (erratum P5a, 2026-10-18).
  * Peripheral items are excluded from every metric: never members, never outsiders.
  * Per-episode recall of a true episode E (>= 2 core items) = the share of E's core items that sit in the single
    predicted cluster holding most of them. The primary figure is the mean over true episodes.
  * Contamination = over predicted clusters that hold >= 2 distinct core items, the share of those items that are
    outsiders: cluster size minus the count of its most frequent episode. (Two unrelated one-item stories merged are
    1 outsider in 2.) Undefined with no multi-item cluster, and undefined FAILS.
  * Largest predicted cluster (all items, peripheral included) against the largest labelled episode (all members).
  * Syndication-only baseline: the clustering that merges only items sharing a `report_label`.
  * Story-resampled 90% CI: bootstrap over STORYLINES (a one-item storyline is its own unit), percentile interval
    [5th, 95th] is reported; the contamination GATE is max(point, basic-bootstrap upper = 2*point - 5th percentile)
    (erratum P5a, 2026-10-18: the percentile upper end is biased low for a skewed statistic). The same draws give the CI of the recall uplift.
  * All-item B-cubed and pairwise precision/recall are secondary (singleton-heavy streams flatter them).
"""
from __future__ import annotations

import csv
import random
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
RESERVED = ("NONE", "NOISE")
MAX_CONTAMINATION_UPPER = 0.15
MAX_CLUSTER_RATIO = 1.5
MIN_RECALL_UPLIFT = 0.15
CI_LEVEL = 0.90
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 20261019

Labels = Mapping[str, Mapping[str, Any]]


# ----------------------------------------------------------------------------- loading and validation


def load_ids(path: Path) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [r["news_id"] for r in csv.DictReader(fh)]


def load_labels(path: Path) -> dict[str, dict[str, Any]]:
    return dict(yaml.safe_load(path.read_text(encoding="utf-8"))["items"])


def validate_labels(labels: Labels, ids: list[str]) -> list[str]:
    """Problems that make a label file unscorable; an empty list means it can be scored."""
    problems: list[str] = []
    for i in ids:
        lab = labels.get(i)
        if lab is None:
            problems.append(f"{i}: no label")
            continue
        for key in ("episode_id", "storyline_id", "report_label"):
            if not lab.get(key):
                problems.append(f"{i}: missing {key}")
        if lab.get("episode_id") in RESERVED and lab.get("storyline_id") != lab.get("episode_id"):
            problems.append(f"{i}: a {lab.get('episode_id')} episode must have the same storyline_id")
        if lab.get("membership") not in (None, "core", "peripheral"):
            problems.append(f"{i}: membership must be core or peripheral")
    story_of: dict[str, set[str]] = defaultdict(set)
    episode_of_report: dict[str, set[str]] = defaultdict(set)
    for i in ids:
        lab = labels.get(i) or {}
        if lab.get("episode_id") and lab["episode_id"] not in RESERVED:
            story_of[lab["episode_id"]].add(str(lab.get("storyline_id")))
        if lab.get("report_label"):
            episode_of_report[lab["report_label"]].add(str(lab.get("episode_id")))
    problems += [f"episode {e} sits in several storylines {sorted(s)}" for e, s in story_of.items() if len(s) > 1]
    problems += [f"report {r} spans several episodes {sorted(e)}" for r, e in episode_of_report.items() if len(e) > 1]
    return problems


def _episode(labels: Labels, i: str) -> Any:
    e = labels[i]["episode_id"]
    # a NONE/NOISE story is one unit per REPORT: wire copies of one story are not outsiders of each other
    return ("single", labels[i]["report_label"]) if e in RESERVED else e


def _storyline(labels: Labels, i: str) -> Any:
    s = labels[i]["storyline_id"]
    return ("single", i) if s in RESERVED else s


def _core(labels: Labels, ids: list[str]) -> list[str]:
    return [i for i in ids if labels[i].get("membership") != "peripheral"]


# ----------------------------------------------------------------------------- the metrics


def _best_share(members: list[str], cid: Mapping[str, Any], w: Mapping[str, float]) -> float:
    """Weighted share of `members` that sit in the one cluster holding most of them."""
    by_cluster: Counter[Any] = Counter()
    for i in members:
        by_cluster[cid[i]] += w[i]
    total = sum(w[i] for i in members)
    return max(by_cluster.values()) / total if total else 0.0


def _groups(labels: Labels, core: list[str], key) -> dict[Any, list[str]]:
    out: dict[Any, list[str]] = defaultdict(list)
    for i in core:
        out[key(labels, i)].append(i)
    return out


def _recall(groups: Mapping[Any, list[str]], cid: Mapping[str, Any], w: Mapping[str, float]) -> float | None:
    """Mean over groups with >= 2 core members (and positive weight) of the best-cluster share, weighted by draws."""
    num = den = 0.0
    for key, members in groups.items():
        if isinstance(key, tuple) or len(members) < 2:
            continue
        weight = w[members[0]]
        if weight <= 0:
            continue
        num += weight * _best_share(members, cid, w)
        den += weight
    return num / den if den else None


def _contamination(core: list[str], cid: Mapping[str, Any], episode: Mapping[str, Any],
                   w: Mapping[str, float]) -> tuple[float | None, int, int]:
    clusters: dict[Any, list[str]] = defaultdict(list)
    for i in core:
        if w[i] > 0:
            clusters[cid[i]].append(i)
    outsiders = total = 0.0
    n_multi = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        n_multi += 1
        counts: Counter[Any] = Counter()
        for i in members:
            counts[episode[i]] += w[i]
        size = sum(w[i] for i in members)
        total += size
        outsiders += size - max(counts.values())
    return (outsiders / total if total else None), n_multi, int(total)


def _bcubed(core: list[str], cid: Mapping[str, Any], episode: Mapping[str, Any]) -> dict[str, float]:
    cluster_n, episode_n, cell = Counter(), Counter(), Counter()
    for i in core:
        cluster_n[cid[i]] += 1
        episode_n[episode[i]] += 1
        cell[(cid[i], episode[i])] += 1
    n = len(core)
    p = sum(cell[(cid[i], episode[i])] / cluster_n[cid[i]] for i in core) / n
    r = sum(cell[(cid[i], episode[i])] / episode_n[episode[i]] for i in core) / n
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def _pairwise(core: list[str], cid: Mapping[str, Any], episode: Mapping[str, Any]) -> dict[str, float | None]:
    def pairs(counter: Counter[Any]) -> int:
        return sum(c * (c - 1) // 2 for c in counter.values())

    both = pairs(Counter((cid[i], episode[i]) for i in core))
    in_cluster = pairs(Counter(cid[i] for i in core))
    in_episode = pairs(Counter(episode[i] for i in core))
    return {"precision": both / in_cluster if in_cluster else None, "recall": both / in_episode if in_episode else None,
            "cluster_pairs": in_cluster, "episode_pairs": in_episode}


def syndication_baseline(labels: Labels, ids: list[str]) -> dict[str, Any]:
    """Merge only items that share a `report_label`; everything else stays a singleton."""
    return {i: ("report", labels[i]["report_label"]) for i in ids}


def _cid(clusters: Mapping[str, Any], ids: list[str]) -> dict[str, Any]:
    return {i: clusters.get(i, ("single", i)) for i in ids}


def point_metrics(labels: Labels, clusters: Mapping[str, Any], ids: list[str],
                  weights: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Primary and secondary metrics at one set of item weights (all 1 unless bootstrapping)."""
    core = _core(labels, ids)
    w = {i: 1.0 for i in ids} if weights is None else dict(weights)
    cid = _cid(clusters, ids)
    episode = {i: _episode(labels, i) for i in ids}
    ep_groups = _groups(labels, core, _episode)
    st_groups = _groups(labels, core, _storyline)
    contamination, n_multi, items_in_multi = _contamination(core, cid, episode, w)
    return {
        "episode_recall": _recall(ep_groups, cid, w),
        "storyline_recall": _recall(st_groups, cid, w),
        "contamination": contamination,
        "multi_item_clusters": n_multi,
        "core_items_in_multi_item_clusters": items_in_multi,
    }


def fragmentation_table(labels: Labels, clusters: Mapping[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    core = _core(labels, ids)
    cid = _cid(clusters, ids)
    out = []
    for key, members in sorted(_groups(labels, core, _episode).items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        if isinstance(key, tuple) or len(members) < 2:
            continue
        frag = Counter(cid[i] for i in members)
        out.append({"episode_id": key, "storyline_id": labels[members[0]]["storyline_id"], "core_items": len(members),
                    "fragments": len(frag), "largest_fragment": max(frag.values()),
                    "recall": max(frag.values()) / len(members)})
    return out


def _largest(labels: Labels, clusters: Mapping[str, Any], ids: list[str]) -> tuple[int, int]:
    cid = _cid(clusters, ids)
    largest_cluster = max(Counter(cid.values()).values(), default=0)
    sizes = Counter(_episode(labels, i) for i in ids)
    largest_episode = max((n for k, n in sizes.items() if not isinstance(k, tuple)), default=0)
    return largest_cluster, largest_episode


def _percentile(sorted_values: list[float], q: float) -> float:
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _interval(values: list[float | None]) -> tuple[float, float] | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    tail = (1 - CI_LEVEL) / 2
    return _percentile(vals, tail), _percentile(vals, 1 - tail)


def contamination_gate(point: float | None, interval: tuple[float, float] | None) -> tuple[float | None, float | None]:
    """(basic-bootstrap upper, gate). The gate is the stricter of the point estimate and 2*point - 5th percentile."""
    if point is None or interval is None:
        return None, None
    basic = 2 * point - interval[0]
    return basic, max(point, basic)


def score(labels: Labels, clusters: Mapping[str, Any], ids: list[str], *, draws: int = BOOTSTRAP_DRAWS,
          seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """The full holdout score and the pre-registered PASS decision. Deterministic for a given seed."""
    problems = validate_labels(labels, ids)
    if problems:
        raise ValueError("labels are not scorable: " + "; ".join(problems[:5]))
    base_clusters = syndication_baseline(labels, ids)
    point = point_metrics(labels, clusters, ids)
    base = point_metrics(labels, base_clusters, ids)
    core = _core(labels, ids)
    cid = _cid(clusters, ids)
    episode = {i: _episode(labels, i) for i in ids}

    units: dict[Any, list[str]] = defaultdict(list)
    for i in ids:
        units[_storyline(labels, i)].append(i)
    keys = sorted(units, key=str)
    rng = random.Random(seed)
    boot: dict[str, list[float | None]] = {"episode_recall": [], "contamination": [], "uplift": [],
                                           "storyline_recall": []}
    for _ in range(draws):
        count = Counter(rng.choice(keys) for _ in keys)
        w = {i: 0.0 for i in ids}
        for k, c in count.items():
            for i in units[k]:
                w[i] = float(c)
        m = point_metrics(labels, clusters, ids, w)
        b = point_metrics(labels, base_clusters, ids, w)
        boot["episode_recall"].append(m["episode_recall"])
        boot["storyline_recall"].append(m["storyline_recall"])
        boot["contamination"].append(m["contamination"])
        boot["uplift"].append(None if m["episode_recall"] is None or b["episode_recall"] is None
                              else m["episode_recall"] - b["episode_recall"])
    ci = {k: _interval(v) for k, v in boot.items()}

    largest_cluster, largest_episode = _largest(labels, clusters, ids)
    uplift = (None if point["episode_recall"] is None or base["episode_recall"] is None
              else point["episode_recall"] - base["episode_recall"])
    pt_c = point["contamination"]
    basic_upper, contamination_upper = contamination_gate(pt_c, ci["contamination"])
    checks = {
        "contamination_upper_le_0_15": contamination_upper is not None and contamination_upper <= MAX_CONTAMINATION_UPPER,
        "largest_cluster_le_1_5x_largest_episode": largest_episode > 0 and largest_cluster <= MAX_CLUSTER_RATIO * largest_episode,
        "episode_recall_uplift_ge_0_15": uplift is not None and uplift >= MIN_RECALL_UPLIFT,
    }
    return {
        "items": len(ids), "core_items": len(core),
        "primary": {**point, "baseline_episode_recall": base["episode_recall"], "recall_uplift": uplift,
                    "largest_cluster": largest_cluster, "largest_episode": largest_episode,
                    "cluster_to_episode_ratio": largest_cluster / largest_episode if largest_episode else None},
        "ci90": ci,
        "contamination_gate": {"point": pt_c, "percentile_upper": ci["contamination"][1] if ci["contamination"] else None,
                               "basic_upper": basic_upper, "gate": contamination_upper},
        "secondary": {"bcubed": _bcubed(core, cid, episode), "pairwise": _pairwise(core, cid, episode)},
        "fragmentation": fragmentation_table(labels, clusters, ids),
        "checks": checks,
        "pass": all(checks.values()),
        "storyline_recall_info": {"value": point["storyline_recall"], "baseline": base["storyline_recall"]},
    }


def inter_labeller_ceiling(labels_a: Labels, labels_b: Labels, ids: list[str]) -> dict[str, Any]:
    """Labeller A's episode partition scored against labeller B's labels (and the reverse): the ceiling on any
    clustering, reported as the inter-labeller B-cubed and per-episode recall. Peripheral status is B's."""
    out = {}
    for name, (truth, other) in {"a_against_b": (labels_b, labels_a), "b_against_a": (labels_a, labels_b)}.items():
        part = {i: _episode(other, i) for i in ids}
        core = _core(truth, ids)
        episode = {i: _episode(truth, i) for i in ids}
        cid = _cid(part, ids)
        out[name] = {"bcubed": _bcubed(core, cid, episode),
                     "episode_recall": _recall(_groups(truth, core, _episode), cid, {i: 1.0 for i in ids})}
    return out
