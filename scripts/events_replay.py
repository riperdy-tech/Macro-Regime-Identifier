"""Replay the E1 reference set day by day and score it (plan section 5 acceptance, LABELLING.md v1.1).

For each UTC day D the builder runs with as_of = the end of D (00:00Z of D+1) over the fixture items first seen up
to then. An item's final event is the last event it was assigned to (ids derive from the onset item, so they
persist across days). The result is scored with tests/fixtures/events_reference/acceptance.py.

Classification source (pluggable, no classifier is ever called here):
  stored             run (a): classified only where classification_state.csv says `classified` and
                     classified_at <= as_of; payloads (themes, entities, ...) are read from the store.
  as_if_all_classified   run (b): every item that has a payload is treated as classified at the end of its own
                     first-seen UTC day. Items with no payload stay unclassified. Idealised; labelled as such.

Pool (what the builder sees each day):
  store     DEFAULT. Every item in the news store (production builds from the whole store), the same as-of per day and
            the same no-look-ahead; only the 300 reference items are scored. The entity-background test (linking v2_2)
            reads the store's earlier items.
  fixture   the 300 reference items alone (kept for the unit tests and for reproducing run (a) as recorded).
Linking: v2_2 (default, erratum v2.2) or v2 (plan 5.4 as transcribed; with --pool fixture it reproduces run (a)).

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
from macro_engine.events.store_io import load_all_items, parse_classification_row  # noqa: E402

FINAL_AS_OF = datetime(2026, 10, 17)
MODES = ("stored", "as_if_all_classified")
POOLS = ("store", "fixture")
LINKINGS = ("v2", "v2_2")
DEFAULT_DB = REPO_ROOT / "data" / "macro_engine.duckdb"


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


class ClassificationMatchError(AssertionError):
    """A fixture item the state file marks `classified` has no store row with the recorded classified_at."""


def assert_state_matched(
    ids: list[str], state: dict[str, tuple[str, datetime | None]], got: dict[str, tuple[Classification, ...]]
) -> int:
    """Every `classified` fixture item must have been matched to a store payload; raises listing each miss."""
    wanted = [i for i in ids if state.get(i, ("", None))[0] == "classified"]
    missing = [i for i in wanted if i not in got]
    if missing:
        raise ClassificationMatchError(
            f"{len(missing)} of {len(wanted)} classified fixture items have no store classification at the "
            f"recorded classified_at: {missing[:10]}{' ...' if len(missing) > 10 else ''}"
        )
    return len(wanted)


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
    linking: str | None = None,
    use_background: bool = False,
    score_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Run the builder at each as-of and return {clusters, meta, daily, id_changes} (see module docstring).

    `classifications` None means the items already carry theirs (store pool). `use_background` hands the whole item
    list to the builder as the entity-background source. `score_ids` restricts which items are assigned/returned.
    """
    if classifications is None:
        with_cls = list(items)
    else:
        with_cls = [
            NewsItem(i.news_id, i.title, i.source_url, i.first_seen_at, i.published_at,
                     classifications.get(i.news_id, ()))
            for i in items
        ]
    first_seen = {i.news_id: i.first_seen_at for i in with_cls}
    when = as_ofs or day_ends(items)
    window = timedelta(days=config.pool_window_days)
    assign: dict[str, str | None] = {}
    meta: dict[str, dict[str, Any]] = {}
    daily = []
    id_changes = 0
    prev_ids: dict[str, str] = {}  # onset item id of a previous event -> its id (same item -> same id by construction)
    last = None
    for t in when:
        res = build_events(with_cls, t, config=config, resolver=resolver, linking=linking,
                           background=with_cls if use_background else None)
        pool_ids = [i.news_id for i in with_cls if i.first_seen_at and t - window < i.first_seen_at <= t
                    and (score_ids is None or i.news_id in score_ids)]
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
            fs = first_seen.get(first_item)
            if fs is not None and fs > t - window and eid not in today_ids:
                id_changes += 1
        prev_ids = {ev["event_id"]: ev["reports"][0]["item_ids"][0] for ev in res.events}
        hub = max(res.events, key=lambda e: (e["item_count"], e["event_id"]), default=None)
        daily.append({"as_of": t.isoformat(), "pool_items": res.meta["items_in_pool"], "events": len(res.events),
                      "largest_event_items": hub["item_count"] if hub else 0,
                      "largest_event_reference_items": (
                          sum(1 for r in hub["reports"] for n in r["item_ids"] if score_ids is None or n in score_ids)
                          if hub else 0),
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


def run(mode: str, db: str | None, out: str | None, use_resolver: bool = True, *, pool: str = "store",
        linking: str = "v2_2") -> dict[str, Any]:
    if mode not in MODES:
        raise SystemExit(f"mode must be one of {MODES}")
    if pool not in POOLS or linking not in LINKINGS:
        raise SystemExit(f"pool must be one of {POOLS} and linking one of {LINKINGS}")
    if pool == "store" and db is None:
        db = str(DEFAULT_DB)
    config = load_e1_config()
    ref_items = load_fixture_items()
    state = load_state()
    ids, labels = acc.load_reference()
    stored = stored_classifications([i.news_id for i in ref_items], state, db)
    if db is not None:
        assert_state_matched([i.news_id for i in ref_items], state, stored)
    resolver = None
    if use_resolver:
        from macro_engine.events.entities import load_resolver

        resolver = load_resolver()
    if pool == "fixture":
        items = ref_items
        classifications: dict[str, tuple[Classification, ...]] | None = (
            stored if mode == "stored" else as_if_all_classified(items, stored))
        score_ids = None
    else:
        import duckdb

        con = duckdb.connect(str(db), read_only=True)
        try:
            items = load_all_items(con)
        finally:
            con.close()
        in_store = {i.news_id for i in items}
        absent = [i for i in ids if i not in in_store]
        if absent:
            raise SystemExit(f"{len(absent)} reference items are not in the store: {absent[:5]}")
        score_ids = set(ids)
        if mode == "stored":
            classifications = None
        else:
            everything = {i.news_id: tuple(c for c in i.classifications if c.status == "success")
                          for i in items if any(c.status == "success" for c in i.classifications)}
            classifications = as_if_all_classified(items, everything)
    as_ofs = day_ends(ref_items)
    result = replay(items, classifications, config=config, resolver=resolver, as_ofs=as_ofs, linking=linking,
                    use_background=(pool == "store"), score_ids=score_ids)
    scored = acc.accept(result["clusters"], result["meta"])
    pool_set = set(result["final_pool_ids"])
    pool_clusters = {k: v for k, v in result["clusters"].items() if k in pool_set}
    pool_rates = acc.rates(pool_clusters, labels, [i for i in ids if i in pool_set])
    final = result["final"]
    hub = max(final.events, key=lambda e: (e["item_count"], e["event_id"]), default=None)
    n_payload = len(stored) if pool == "fixture" else sum(
        1 for i in items if i.news_id in score_ids and any(c.status == "success" for c in i.classifications))
    report = {
        "mode": mode,
        "pool": pool,
        "linking": linking,
        "config_version": config.version,
        "items": len(items),
        "reference_items": len(ref_items),
        "items_with_payload": len(classifications) if classifications is not None else sum(
            1 for i in items if any(c.status == "success" for c in i.classifications)),
        "reference_items_with_payload": n_payload,
        "scored": scored,
        "final_pool_rates": pool_rates,
        "fragmentation": fragmentation(result["clusters"], labels, ids),
        "gated_detail": gated_detail(result["clusters"], result["meta"], labels, ids),
        "hub": {
            "final_largest_event_items": hub["item_count"] if hub else 0,
            "final_largest_event_id": hub["event_id"] if hub else None,
            "max_largest_event_items_any_day": max((d["largest_event_items"] for d in result["daily"]), default=0),
        },
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
        f"mode={report['mode']} pool={report['pool']} linking={report['linking']} items={report['items']} "
        f"with_payload={report['items_with_payload']} reference_with_payload={report['reference_items_with_payload']}",
        f"hub: final largest event {report['hub']['final_largest_event_items']} items, "
        f"max on any day {report['hub']['max_largest_event_items_any_day']}",
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
    ap.add_argument("--pool", choices=POOLS, default="store", help="store (default): whole store; fixture: the 300 only")
    ap.add_argument("--linking", choices=LINKINGS, default="v2_2")
    ap.add_argument("--db", default=None, help="news store (default for --pool store: data/macro_engine.duckdb)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-resolver", action="store_true", help="skip the stocks.json / SEC entity resolver")
    args = ap.parse_args(argv)
    report = run(args.mode, args.db, args.out, use_resolver=not args.no_resolver, pool=args.pool,
                 linking=args.linking)
    print(summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
