"""Day-by-day replay of a labelled item set through the arm A linker and the persistent event state, scored with the
v2.3 holdout scorer (tests/fixtures/events_holdout/score_holdout.py).

For each UTC day D from the first labelled item's first-seen day to the last, `as_of` is 00:00Z of D+1. The pool is the
whole store's items first seen in the 7 days up to `as_of` and not before the first labelled day (distractors stay in);
reports are syndication-collapsed with the builder's rule and assigned in arrival order through `EventState.apply_day`.
Only STORED classification state is used (a classification counts when `classified_at <= as_of`); no overlay is read and
no classifier is called. The IDF background is the reports first seen in the `background_days` days before the first
labelled day, judged with what was classified before then. No item first seen after --max-first-seen (default
2026-10-18) is read.

Label sets
  reference   tests/fixtures/events_reference (v1.1 single `event` labels; each event id is used as episode and storyline,
              NONE stays NONE, noise becomes NOISE). A DEVELOPMENT set: every figure is optimistic.
  holdout     --items items.csv --labels labels.yaml, the holdout format of LABELLING_GUIDE_V2.md (episode_id, storyline_id,
              report_label, optional membership), with --db the store that holds the items.

Linkers
  A           the arm A linker, parameters from --config (config/events_v23.yaml).
  B           arm A plus the stored dense title vectors (table event_embeddings, written by scripts/events_embed.py);
              parameters from the `arm_b` section laid over `arm_a`. The model is never called.
  syndication the builder's collapse only: every collapsed report is its own cluster (the system baseline).

    python scripts/events_v23_replay.py --set reference --out run.json
    python scripts/events_v23_replay.py --set reference --tune --out grid.json     (60-cell grid of V23_DEV_TUNING.md)
    python scripts/events_v23_replay.py --set reference --tune-b --out gridb.json  (54-cell grid of V23_DEV_TUNING_B.md)
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HOLDOUT_DIR = REPO_ROOT / "tests" / "fixtures" / "events_holdout"
REFERENCE_DIR = REPO_ROOT / "tests" / "fixtures" / "events_reference"
DEFAULT_DB = REPO_ROOT / "data" / "macro_engine.duckdb"
sys.path.insert(0, str(HOLDOUT_DIR))

import score_holdout as sh  # noqa: E402

from macro_engine.events.builder import NewsItem, naive_utc  # noqa: E402
from macro_engine.events.config import load_e1_config, load_generic_entities  # noqa: E402
from macro_engine.events.entities import load_resolver, normalize_name  # noqa: E402
from macro_engine.events.embeddings import EmbeddingStore, load_model_pin  # noqa: E402
from macro_engine.events.linker import (  # noqa: E402
    ArmAConfig,
    ArmALinker,
    ArmBConfig,
    ArmBLinker,
    load_arm_a_config,
    load_arm_b_config,
)
from macro_engine.events.register import apply_pool, background_for, collapse_pool  # noqa: E402
from macro_engine.events.state import EventState, NeverLinkStub, StateConfig  # noqa: E402
from macro_engine.events.store_io import load_all_items  # noqa: E402

DEFAULT_MAX_FIRST_SEEN = datetime(2026, 10, 18)

# The grid of docs/events_build/V23_DEV_TUNING.md, fixed before any cell was run (5 x 3 x 2 x 2 = 60 cells).
GRID_CUTOFFS = (0.25, 0.30, 0.35, 0.45, 0.55)
GRID_ENTITY_WEIGHTS = (0.0, 0.25, 0.5)
GRID_FLOORS = ((0.10, 2.0), (0.20, 3.0))   # (title_cosine_floor, entity_idf_floor)
GRID_BACKGROUND_DAYS = (14, 28)

# The arm B grid of docs/events_build/V23_DEV_TUNING_B.md, fixed before any cell was run (3 x 6 x 3 = 54 cells).
GRID_B_DENSE_WEIGHTS = (0.5, 1.0, 1.5)
GRID_B_CUTOFFS = (0.40, 0.55, 0.70, 0.85, 1.00, 1.20)
GRID_B_ENTITY_WEIGHTS = (0.0, 0.25, 0.5)


# ----------------------------------------------------------------------------- label sets


def load_reference_labels() -> tuple[list[str], dict[str, dict[str, Any]]]:
    with open(REFERENCE_DIR / "items.csv", encoding="utf-8") as fh:
        ids = [r["news_id"] for r in csv.DictReader(fh)]
    raw = yaml.safe_load((REFERENCE_DIR / "labels.yaml").read_text(encoding="utf-8"))["items"]
    labels: dict[str, dict[str, Any]] = {}
    for i in ids:
        ev = raw[i]["event"]
        ep = "NONE" if ev == "NONE" else "NOISE" if ev == "noise" else ev
        labels[i] = {"episode_id": ep, "storyline_id": ep, "report_label": raw[i]["report"]}
        if raw[i].get("membership"):
            labels[i]["membership"] = raw[i]["membership"]
    return ids, labels


def load_holdout_labels(items_csv: Path, labels_yaml: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    return sh.load_ids(items_csv), sh.load_labels(labels_yaml)


# ----------------------------------------------------------------------------- context shared by every cell


class ReplayContext:
    """Everything that does not depend on a cell's parameters, computed once: items, the daily pools, backgrounds."""

    def __init__(self, items: list[NewsItem], ids: list[str], *, resolver: Any = None,
                 vectors: dict[str, Any] | None = None) -> None:
        self.cfg = load_e1_config()
        self.vectors = vectors if vectors is not None else {}
        self.resolver = resolver
        legal = resolver.legal_suffixes if resolver is not None else ()
        self.suffixes = legal
        self.generic = frozenset(normalize_name(g, legal) for g in load_generic_entities(self.cfg.generic_entities_ref))
        self.items = items
        by_id = {i.news_id: i for i in items}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise SystemExit(f"{len(missing)} labelled items are not in the store: {missing[:5]}")
        seen = [by_id[i].first_seen_at for i in ids]
        if any(s is None for s in seen):
            raise SystemExit("a labelled item has no first_seen_at")
        first = min(seen)
        last = max(seen)
        self.start = datetime(first.year, first.month, first.day)
        self.days = []
        d = self.start + timedelta(days=1)
        while d <= datetime(last.year, last.month, last.day) + timedelta(days=1):
            self.days.append(d)
            d += timedelta(days=1)
        self.pools = {d: collapse_pool(items, d, config=self.cfg, resolver=resolver, generic=self.generic,
                                       suffixes=self.suffixes, window_start=self.start) for d in self.days}
        self._bg: dict[float, Any] = {}
        self._caches: dict[float, tuple[dict, dict]] = {}

    def background(self, arm: ArmAConfig):
        if arm.background_days not in self._bg:
            self._bg[arm.background_days] = background_for(
                self.items, self.start, arm, config=self.cfg, resolver=self.resolver, generic=self.generic,
                suffixes=self.suffixes)
        return self._bg[arm.background_days]

    def cache(self, arm: ArmAConfig) -> tuple[dict, dict]:
        return self._caches.setdefault(arm.background_days, ({}, {}))


def replay(ctx: ReplayContext, ids: list[str], arm: ArmAConfig | None) -> dict[str, Any]:
    """One run. `arm` None = the syndication-only system baseline (every collapsed report is its own cluster)."""
    import duckdb

    con = duckdb.connect(":memory:")
    state = EventState(con, StateConfig())
    if arm is None:
        linker: Any = NeverLinkStub()
    elif isinstance(arm, ArmBConfig):
        linker = ArmBLinker(arm, ctx.background(arm), ctx.vectors, state.cfg.open_days, ctx.cache(arm))
    else:
        linker = ArmALinker(arm, ctx.background(arm), state.cfg.open_days, ctx.cache(arm))
    item_event: dict[str, str] = {}
    daily = []
    for d in ctx.days:
        pool = ctx.pools[d]
        res = apply_pool(state, None, linker, pool, d)
        for pr in pool:
            eid = state.event_of_report(pr.report.report_id)
            for item_id in pr.item_ids:
                if eid is not None:
                    item_event.setdefault(item_id, eid)
        sizes = Counter(item_event.values())
        daily.append({"as_of": d.isoformat(), "pool_reports": len(pool), "created": len(res.created),
                      "joined": len(res.joined), "closed": len(res.closed), "proposals": res.proposals,
                      "largest_cluster_items": max(sizes.values(), default=0)})
    clusters = {i: item_event[i] for i in ids if i in item_event}
    ambiguous = sum(1 for r in con.execute("SELECT evidence_json FROM event_state_reports").fetchall()
                    if json.loads(r[0]).get("ambiguous"))
    out = {"clusters": clusters, "daily": daily, "events": len(set(item_event.values())), "ambiguous_links": ambiguous,
           "proposed_links": len(state.proposed_links()), "unmapped_labelled": [i for i in ids if i not in item_event]}
    con.close()
    return out


def score_run(labels: dict[str, dict[str, Any]], ids: list[str], run: dict[str, Any], draws: int = sh.BOOTSTRAP_DRAWS) -> dict[str, Any]:
    return sh.score(labels, run["clusters"], ids, draws=draws)


# ----------------------------------------------------------------------------- reporting


def headline(scored: dict[str, Any]) -> dict[str, Any]:
    p = scored["primary"]
    ci = scored["ci90"]
    return {
        "episode_recall": p["episode_recall"], "baseline_episode_recall": p["baseline_episode_recall"],
        "recall_uplift": p["recall_uplift"], "uplift_ci90": ci["uplift"],
        "contamination": p["contamination"], "contamination_ci90": ci["contamination"],
        "contamination_upper": ci["contamination"][1] if ci["contamination"] else None,
        "largest_cluster": p["largest_cluster"], "largest_episode": p["largest_episode"],
        "cluster_to_episode_ratio": p["cluster_to_episode_ratio"],
        "multi_item_clusters": p["multi_item_clusters"], "checks": scored["checks"], "pass": scored["pass"],
        "storyline_recall_info": scored["storyline_recall_info"],
        "bcubed": scored["secondary"]["bcubed"], "pairwise": scored["secondary"]["pairwise"],
    }


def grid_cells() -> list[dict[str, Any]]:
    return [{"cutoff": c, "entity_weight": w, "title_cosine_floor": tf, "entity_idf_floor": ef, "background_days": b}
            for c in GRID_CUTOFFS for w in GRID_ENTITY_WEIGHTS for tf, ef in GRID_FLOORS for b in GRID_BACKGROUND_DAYS]


def grid_cells_b() -> list[dict[str, Any]]:
    return [{"dense_weight": d, "cutoff": c, "entity_weight": w}
            for d in GRID_B_DENSE_WEIGHTS for c in GRID_B_CUTOFFS for w in GRID_B_ENTITY_WEIGHTS]


def select_cell(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The rule of V23_DEV_TUNING.md. `rows` are in grid order. Returns {cell, rule_branch, feasible}."""
    def upper(r: dict[str, Any]) -> float:
        u = r["headline"]["contamination_upper"]
        return float("inf") if u is None else u

    feasible = [r for r in rows if r["headline"]["recall_uplift"] is not None and r["headline"]["recall_uplift"] >= sh.MIN_RECALL_UPLIFT]
    order = {id(r): n for n, r in enumerate(rows)}
    if feasible:
        best = min(feasible, key=lambda r: (upper(r), r["headline"]["largest_cluster"], r["cell"]["cutoff"],
                                            r["cell"]["entity_weight"], order[id(r)]))
        return {"cell": best, "rule_branch": "feasible_set_min_contamination_upper", "feasible": len(feasible)}
    best = max(rows, key=lambda r: ((r["headline"]["recall_uplift"] if r["headline"]["recall_uplift"] is not None else -1.0),
                                    -upper(r), -order[id(r)]))
    return {"cell": best, "rule_branch": "no_feasible_cell_largest_uplift", "feasible": 0}


def tune(ctx: ReplayContext, labels: dict[str, dict[str, Any]], ids: list[str], base: ArmAConfig) -> dict[str, Any]:
    rows = []
    for n, cell in enumerate(grid_cells(), 1):
        arm = dataclasses.replace(base, **cell)
        run = replay(ctx, ids, arm)
        rows.append({"n": n, "cell": cell, "headline": headline(score_run(labels, ids, run)), "events": run["events"],
                     "ambiguous_links": run["ambiguous_links"], "proposed_links": run["proposed_links"]})
        h = rows[-1]["headline"]
        print(f"cell {n:2d}/60 {cell} uplift={h['recall_uplift']} contam_up={h['contamination_upper']} "
              f"largest={h['largest_cluster']}", flush=True)
    chosen = select_cell(rows)
    return {"grid": rows, "selection": {"rule_branch": chosen["rule_branch"], "feasible_cells": chosen["feasible"],
                                        "chosen_n": chosen["cell"]["n"], "chosen_cell": chosen["cell"]["cell"]},
            "any_cell_passes_all": [r["n"] for r in rows if r["headline"]["pass"]]}


def tune_b(ctx: ReplayContext, labels: dict[str, dict[str, Any]], ids: list[str], base: ArmBConfig) -> dict[str, Any]:
    rows = []
    cells = grid_cells_b()
    for n, cell in enumerate(cells, 1):
        arm = dataclasses.replace(base, **cell)
        run = replay(ctx, ids, arm)
        rows.append({"n": n, "cell": cell, "headline": headline(score_run(labels, ids, run)), "events": run["events"],
                     "ambiguous_links": run["ambiguous_links"], "proposed_links": run["proposed_links"]})
        h = rows[-1]["headline"]
        print(f"cell {n:2d}/{len(cells)} {cell} uplift={h['recall_uplift']} contam_up={h['contamination_upper']} "
              f"largest={h['largest_cluster']}", flush=True)
    chosen = select_cell(rows)
    return {"grid": rows, "selection": {"rule_branch": chosen["rule_branch"], "feasible_cells": chosen["feasible"],
                                        "chosen_n": chosen["cell"]["n"], "chosen_cell": chosen["cell"]["cell"]},
            "any_cell_passes_all": [r["n"] for r in rows if r["headline"]["pass"]]}


def load_stored_vectors(db: Path) -> dict[str, Any]:
    """Stored vectors of the pinned model (read-only). The model itself is never loaded."""
    import duckdb

    pin = load_model_pin()
    con = duckdb.connect(str(db), read_only=True)
    try:
        return EmbeddingStore(con, create=False).load_vectors(None, pin.name, pin.model_sha256)
    finally:
        con.close()


def event_behaviour(labels: dict[str, dict[str, Any]], ids: list[str], clusters: dict[str, str],
                    needles: dict[str, str]) -> dict[str, Any]:
    """For named labelled episodes: core items, the clusters they sit in, and outsiders in those clusters."""
    out = {}
    for name, ep in needles.items():
        core = [i for i in ids if labels[i]["episode_id"] == ep and labels[i].get("membership") != "peripheral"]
        homes = Counter(clusters.get(i, f"single:{i}") for i in core)
        detail = {}
        for home, n in homes.most_common():
            members = [i for i in ids if clusters.get(i) == home and labels[i].get("membership") != "peripheral"]
            detail[home] = {"core_here": n, "cluster_core_items": len(members),
                            "outsider_episodes": dict(Counter(labels[i]["episode_id"] for i in members
                                                             if labels[i]["episode_id"] != ep).most_common(6))}
        out[name] = {"episode": ep, "core_items": len(core), "clusters": len(homes), "homes": detail}
    return out


# ----------------------------------------------------------------------------- entry point


def load_store_items(db: Path, max_first_seen: datetime) -> list[NewsItem]:
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        items = load_all_items(con)
    finally:
        con.close()
    return [i for i in items if i.first_seen_at is not None and i.first_seen_at < max_first_seen]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", choices=("reference", "holdout"), default="reference")
    ap.add_argument("--items", type=Path, help="holdout items.csv")
    ap.add_argument("--labels", type=Path, help="holdout labels.yaml (episode_id / storyline_id / report_label)")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--config", type=Path, default=None, help="events_v23.yaml (default config/events_v23.yaml)")
    ap.add_argument("--linker", choices=("A", "B", "syndication"), default="A")
    ap.add_argument("--tune-b", action="store_true", help="run the 54-cell arm B development grid (reference set only)")
    ap.add_argument("--cell", default=None, help="arm B only: JSON of fields to override, e.g. '{\"dense_weight\": 1.0}'")
    ap.add_argument("--tune", action="store_true", help="run the 60-cell development grid (reference set only)")
    ap.add_argument("--max-first-seen", default=DEFAULT_MAX_FIRST_SEEN.isoformat(),
                    help="no item first seen at or after this instant is read (default 2026-10-18)")
    ap.add_argument("--draws", type=int, default=sh.BOOTSTRAP_DRAWS)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.set == "reference":
        ids, labels = load_reference_labels()
    else:
        if args.items is None or args.labels is None:
            raise SystemExit("--set holdout needs --items and --labels")
        ids, labels = load_holdout_labels(args.items, args.labels)
    if (args.tune or args.tune_b) and args.set != "reference":
        raise SystemExit("--tune is development-set only: it never runs on a holdout")
    use_b = args.linker == "B" or args.tune_b
    cfg_path = args.config
    if use_b:
        base: ArmAConfig = load_arm_b_config(cfg_path) if cfg_path else load_arm_b_config()
        if args.cell:
            base = dataclasses.replace(base, **json.loads(args.cell))
    else:
        base = load_arm_a_config(cfg_path) if cfg_path else load_arm_a_config()
    items = load_store_items(args.db, naive_utc(args.max_first_seen))  # type: ignore[arg-type]
    ctx = ReplayContext(items, ids, resolver=load_resolver(), vectors=load_stored_vectors(args.db) if use_b else None)

    if args.tune_b:
        result: dict[str, Any] = {"set": "reference", "optimistic": True, **tune_b(ctx, labels, ids, base)}  # type: ignore[arg-type]
    elif args.tune:
        result = {"set": "reference", "optimistic": True, **tune(ctx, labels, ids, base)}
    else:
        run = replay(ctx, ids, base if args.linker in ("A", "B") else None)
        scored = score_run(labels, ids, run, args.draws)
        result = {"set": args.set, "linker": args.linker, "optimistic": args.set == "reference",
                  "arm_config": dataclasses.asdict(base) if args.linker in ("A", "B") else None,
                  "headline": headline(scored), "fragmentation": scored["fragmentation"], "daily": run["daily"],
                  "events": run["events"], "ambiguous_links": run["ambiguous_links"],
                  "proposed_links": run["proposed_links"], "clusters": run["clusters"]}
        if args.set == "reference":
            result["behaviour"] = event_behaviour(labels, ids, run["clusters"], {
                "houthi_saudi": "E_HOUTHI_SAUDI", "houthi_aden": "E_HOUTHI_ADEN_PALACE",
                "cpi_sep": "E_CPI_SEP", "coreweave": "E_COREWEAVE_DISTRESS"})
        print(json.dumps({k: result[k] for k in ("set", "linker", "headline")}, indent=2, default=str))
    if args.out:
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
