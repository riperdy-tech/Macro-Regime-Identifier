"""Replay the E1 reference set day by day and score it (plan section 5 acceptance, LABELLING.md v1.1).

For each UTC day D the builder runs with as_of = the end of D (00:00Z of D+1) over the fixture items first seen up
to then. An item's final event is the last event it was assigned to (ids derive from the onset item, so they
persist across days). The result is scored with tests/fixtures/events_reference/acceptance.py.

Classification source (pluggable, no classifier is ever called here):
  stored             run (a): classified only where classification_state.csv says `classified` and
                     classified_at <= as_of; payloads (themes, entities, ...) are read from the store.
  as_if_all_classified   run (b): every item that has a payload is treated as classified at the end of its own
                     first-seen UTC day. Items with no payload stay unclassified. Idealised; labelled as such.

Usage:  python scripts/events_replay.py --mode stored --db data/macro_engine.duckdb --out report.json
The report is written to --out as JSON and a summary is printed.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "events_reference"
sys.path.insert(0, str(FIXTURE_DIR))

import acceptance as acc  # noqa: E402

from macro_engine.events.builder import Classification, NewsItem, build_events, naive_utc  # noqa: E402
from macro_engine.events.config import E1Config, load_e1_config  # noqa: E402
from macro_engine.events.store_io import parse_classification_row  # noqa: E402

FINAL_AS_OF = datetime(2026, 10, 17)
MODES = ("stored", "as_if_all_classified")


def load_fixture_items(directory: Path = FIXTURE_DIR) -> list[NewsItem]:
    """Reference items. The fixture keeps the URL host only, which is all the builder reads of a URL."""
    out = []
    with open(directory / "items.csv", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            domain = r["source_url_domain"]
            out.append(NewsItem(
                news_id=r["news_id"],
                title=r["title"],
                source_url=f"https://{domain}/" if domain else None,
                first_seen_at=naive_utc(r["first_seen_at"]),
                published_at=naive_utc(r["published_at"]) if r["published_at"] else None,
            ))
    return out


def load_state(directory: Path = FIXTURE_DIR) -> dict[str, tuple[str, datetime | None]]:
    with open(directory / "classification_state.csv", encoding="utf-8") as fh:
        return {r["news_id"]: (r["state"], naive_utc(r["classified_at"]) if r["classified_at"] else None)
                for r in csv.DictReader(fh)}


def stored_classifications(
    ids: list[str], state: dict[str, tuple[str, datetime | None]], db_path: str | Path | None
) -> dict[str, tuple[Classification, ...]]:
    """Payload from the store for each item the state file marks `classified`, stamped with the state file's
    classified_at. Without a store the result is empty (every item unclassified)."""
    if db_path is None:
        return {}
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    cols = ["news_id", "classified_at", "classification_status", "origin", "ai_provider", "macro_themes_json",
            "sector_impacts_json", "entities_json", "severity", "confidence", "summary"]
    wanted = [i for i in ids if state.get(i, ("", None))[0] == "classified"]
    out: dict[str, tuple[Classification, ...]] = {}
    for i in wanted:
        rows = con.execute(
            f"select {', '.join(cols)} from news_classifications where news_id = ? and classification_status = 'success'",
            [i],
        ).fetchall()
        match = [parse_classification_row(dict(zip(cols, r, strict=True))) for r in rows]
        stamp = state[i][1]
        match = [c for c in match if c.classified_at == stamp]
        if match:
            out[i] = tuple(match)
    return out


def as_if_all_classified(
    items: list[NewsItem], source: dict[str, tuple[Classification, ...]]
) -> dict[str, tuple[Classification, ...]]:
    """Run (b): re-stamp each payload to the end of its item's own first-seen UTC day (ignoring the real time)."""
    out: dict[str, tuple[Classification, ...]] = {}
    for it in items:
        if it.news_id not in source or it.first_seen_at is None:
            continue
        day_end = datetime(it.first_seen_at.year, it.first_seen_at.month, it.first_seen_at.day) + timedelta(days=1)
        out[it.news_id] = tuple(
            Classification(**{**c.__dict__, "classified_at": day_end}) for c in source[it.news_id]
        )
    return out


def day_ends(items: list[NewsItem], final: datetime = FINAL_AS_OF) -> list[datetime]:
    first = min(i.first_seen_at for i in items if i.first_seen_at is not None)
    day = datetime(first.year, first.month, first.day) + timedelta(days=1)
    out = []
    while day <= final:
        out.append(day)
        day += timedelta(days=1)
    return out


def replay(
    items: list[NewsItem],
    classifications: dict[str, tuple[Classification, ...]],
    *,
    config: E1Config,
    resolver: Any = None,
    as_ofs: list[datetime] | None = None,
) -> dict[str, Any]:
    """Run the builder at each as-of and return {clusters, meta, daily, id_changes} (see module docstring)."""
    with_cls = [
        NewsItem(i.news_id, i.title, i.source_url, i.first_seen_at, i.published_at, classifications.get(i.news_id, ()))
        for i in items
    ]
    when = as_ofs or day_ends(items)
    window = timedelta(days=config.pool_window_days)
    assign: dict[str, str | None] = {}
    meta: dict[str, dict[str, Any]] = {}
    daily = []
    id_changes = 0
    prev_ids: dict[str, str] = {}  # onset item id of a previous event -> its id (same item -> same id by construction)
    last = None
    for t in when:
        res = build_events(with_cls, t, config=config, resolver=resolver)
        pool_ids = [i.news_id for i in with_cls if i.first_seen_at and t - window < i.first_seen_at <= t]
        member_of: dict[str, str] = {}
        for ev in res.events:
            meta[ev["event_id"]] = {"status": ev["event_status"], "related": list(ev["related_event_ids"])}
            for rep in ev["reports"]:
                for nid in rep["item_ids"]:
                    member_of[nid] = ev["event_id"]
        for nid in pool_ids:
            assign[nid] = member_of.get(nid)
        # an event that was present yesterday and whose first item is still pooled must keep its id today
        today_ids = {ev["event_id"] for ev in res.events}
        for eid, first_item in prev_ids.items():
            fs = next((i.first_seen_at for i in with_cls if i.news_id == first_item), None)
            if fs is not None and fs > t - window and eid not in today_ids:
                id_changes += 1
        prev_ids = {ev["event_id"]: ev["reports"][0]["item_ids"][0] for ev in res.events}
        daily.append({"as_of": t.isoformat(), "pool_items": len(pool_ids), "events": len(res.events),
                      "corroborated": sum(1 for e in res.events if e["event_status"] == "corroborated"),
                      "unattached": len(res.unattached), "classified_items": res.meta["items_classified"]})
        last = res
    clusters = {k: v for k, v in assign.items() if v is not None}
    return {"clusters": clusters, "meta": meta, "daily": daily, "ids_lost_while_pooled": id_changes,
            "final": last, "final_pool_ids": pool_ids if when else []}


def fragmentation(clusters: dict[str, str], labels: dict[str, Any], ids: list[str]) -> dict[str, dict[str, Any]]:
    out = {}
    events = sorted({labels[i]["event"] for i in ids if labels[i]["event"] not in ("NONE", "noise")})
    for ev in events:
        core = [i for i in ids if labels[i]["event"] == ev and labels[i].get("membership") != "peripheral"]
        homes = Counter(clusters.get(i, "(unattached)") for i in core)
        out[ev] = {"core": len(core), "clusters": len(homes), "largest_share": max(homes.values()) / len(core) if core else None}
    return out


def gated_detail(clusters: dict[str, str], meta: dict[str, Any], labels: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    out = {}
    for ev in acc.GATED:
        core = [i for i in ids if labels[i]["event"] == ev and labels[i].get("membership") != "peripheral"]
        homes = Counter(clusters.get(i, "(unattached)") for i in core)
        detail = {}
        for home, n in homes.items():
            members = [i for i in ids if clusters.get(i) == home]
            outsiders = [i for i in members if labels[i]["event"] != ev and labels[i].get("membership") != "peripheral"]
            detail[home] = {"core_members_here": n, "cluster_items": len(members), "outsiders": len(outsiders),
                            "outsider_events": dict(Counter(labels[i]["event"] for i in outsiders)),
                            "status": (meta.get(home) or {}).get("status"),
                            "related": (meta.get(home) or {}).get("related")}
        out[ev] = {"core": len(core), "homes": detail}
    return out


def run(mode: str, db: str | None, out: str | None, use_resolver: bool = True) -> dict[str, Any]:
    if mode not in MODES:
        raise SystemExit(f"mode must be one of {MODES}")
    config = load_e1_config()
    items = load_fixture_items()
    state = load_state()
    ids, labels = acc.load_reference()
    stored = stored_classifications([i.news_id for i in items], state, db)
    classifications = stored if mode == "stored" else as_if_all_classified(items, stored)
    resolver = None
    if use_resolver:
        from macro_engine.events.entities import load_resolver

        resolver = load_resolver()
    result = replay(items, classifications, config=config, resolver=resolver)
    scored = acc.accept(result["clusters"], result["meta"])
    pool_set = set(result["final_pool_ids"])
    pool_clusters = {k: v for k, v in result["clusters"].items() if k in pool_set}
    pool_rates = acc.rates(pool_clusters, labels, [i for i in ids if i in pool_set])
    report = {
        "mode": mode,
        "config_version": config.version,
        "items": len(items),
        "items_with_payload": len(classifications),
        "scored": scored,
        "final_pool_rates": pool_rates,
        "fragmentation": fragmentation(result["clusters"], labels, ids),
        "gated_detail": gated_detail(result["clusters"], result["meta"], labels, ids),
        "daily": result["daily"],
        "ids_lost_while_pooled": result["ids_lost_while_pooled"],
        "clusters": result["clusters"],
        "cluster_meta": result["meta"],
    }
    if out:
        Path(out).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


def summary(report: dict[str, Any]) -> str:
    s = report["scored"]
    lines = [
        f"mode={report['mode']} items={report['items']} with_payload={report['items_with_payload']}",
        f"false_split={s['false_split']} ({s['false_split_pairs']}/{s['label_pairs']}) ok={s['false_split_ok']}",
        f"false_merge={s['false_merge']} ({s['false_merge_pairs']}/{s['cluster_pairs']}) ok={s['false_merge_ok']}",
        "gates=" + json.dumps(s["gates"]),
        f"PASS={s['pass']}",
        f"ids_lost_while_pooled={report['ids_lost_while_pooled']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=MODES, default="stored")
    ap.add_argument("--db", default=None, help="news store holding the classification payloads")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-resolver", action="store_true", help="skip the stocks.json / SEC entity resolver")
    args = ap.parse_args(argv)
    report = run(args.mode, args.db, args.out, use_resolver=not args.no_resolver)
    print(summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
