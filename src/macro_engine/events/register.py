"""Stage 1 candidate register (docs/events_build/CANDIDATE_REGISTER_SPEC.md), built from the store, the persistent
event state (`state.py`) and the linker (`linker.py`).

A CANDIDATE is a grouping the linker proposed, with the evidence for it. No field, status value or string here calls a
candidate a verified or confirmed event, and a candidate never changes a number, a score or a verdict (D1). The artifact
is `outputs/event_register.json` (schema 1): additive, new, versioned; it never touches a protected artifact.

Daily and idempotent: `run_day` collapses the pool, assigns each not-yet-assigned report through `EventState.apply_day`
and records report details in two additive side tables; applying the same day twice changes nothing, and the register
document is a pure function of (state, side tables, `as_of`) with no wall-clock field, so writing it twice produces the
same bytes.

Report identity: a collapsed report is identified by its EARLIEST-seen member (stable as later copies arrive), not by
the builder's "earliest classified" canonical item; the classification payload is read from the canonical member as
the builder does. Items of a report that arrive later map to the report they were first grouped with and are never
moved. The 7-day pool is a discovery window, not an identity: when the earliest copy of a syndicated report ages out
of it, the surviving (and any late) copies keep the report id they were persisted under. `stabilize_pool` re-keys the
pool's reports from the persisted item -> report membership (`event_register_items`) before they reach the state, so a
report id is never recomputed from the current pool alone and an item instance sits in exactly one report.
"""
from __future__ import annotations

import dataclasses
import json
import os
from collections import Counter
from collections.abc import Iterable, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from macro_engine.events.builder import (
    NewsItem,
    _entity_refs,
    _collapse,
    _prepare,
    _Report,
    _theme_dirs,
    naive_utc,
)
from macro_engine.events.config import E1Config
from macro_engine.events.entities import EntityResolver
from macro_engine.events.linker import ArmAConfig, ArmBConfig, Background, make_background
from macro_engine.events.state import CLOSED, OPEN, DayResult, EventState, Linker, Report

SCHEMA_VERSION = 1
REGISTER_FILENAME = "event_register.json"
PROTECTED_NAMES = frozenset({
    "current_regime.json", "current_sector_ranking.json", "cost_of_capital_anchor.json",
    "long_run_growth_anchor.json", "sector_multiple_bands.json", "shock_register.json",
})
CORROBORATION = ("officially_sourced", "independently_corroborated", "single_source", "conflicting")
NOTE = ("Each entry is a candidate: a grouping proposed by the linker, with its evidence. A candidate asserts nothing "
        "about what happened and carries no claim about market impact. This register changes no number, score or "
        "verdict.")


# ----------------------------------------------------------------------------- E2 de-duplication key


def market_check_key(instrument: str, reference_close_date: date | str, window: int) -> str:
    """Key of a market check: (instrument, reference close date, window). Fragments of one story that resolve to the
    same key share one check and count once. Entity or theme facets are never part of it."""
    if isinstance(reference_close_date, str):
        reference_close_date = date.fromisoformat(reference_close_date)
    if isinstance(reference_close_date, datetime):
        reference_close_date = reference_close_date.date()
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError(f"window must be a positive number of sessions, got {window!r}")
    symbol = str(instrument).strip().upper()
    if not symbol:
        raise ValueError("instrument is empty")
    return f"{symbol}|{reference_close_date.isoformat()}|{window}"


# ----------------------------------------------------------------------------- pool -> reports


@dataclass
class PoolReport:
    """One collapsed report ready for the state, with everything the register needs to describe it."""

    report: Report
    item_ids: tuple[str, ...]
    members: tuple[dict[str, Any], ...]
    detail: dict[str, Any]


def _entity_detail(ref: Any) -> dict[str, Any]:
    return {"name": ref.name, "normalized": ref.normalized, "key": ref.key, "status": ref.status, "tier": ref.tier,
            "tickers": list(ref.tickers), "flags": list(ref.flags), "entity_type": ref.entity_type,
            "relevance": ref.relevance, "link_reason": ref.link_reason}


def _classification_status(rep: _Report, as_of: datetime) -> str:
    if rep.canonical.cls is not None:
        return "classified"
    for m in rep.members:
        if any(c.classified_at is not None and c.classified_at <= as_of and c.status != "success"
               for c in m.item.classifications):
            return "failed"
    return "unclassified"


def collapse_pool(
    items: Iterable[NewsItem], as_of: datetime | str, *, config: E1Config, resolver: EntityResolver | None,
    generic: frozenset[str], suffixes: Sequence[str], window_start: datetime | None = None,
) -> list[PoolReport]:
    """The reports of the 7-day discovery pool at `as_of`, syndication-collapsed, in arrival order. With
    `window_start`, items first seen before it are left out (a replay that begins at the window start)."""
    when = naive_utc(as_of)
    assert when is not None
    pool = [i for i in items if window_start is None or (i.first_seen_at is not None and i.first_seen_at >= window_start)]
    preps, _ = _prepare(pool, when, config)
    out: list[PoolReport] = []
    for rep in _collapse(preps, config):
        first = rep.members[0]
        cls = rep.canonical.cls
        refs = _entity_refs(cls, resolver, generic, suffixes, config.entity_relevance_min) if cls is not None else []
        flags = sorted({f for m in rep.members for f in m.flags})
        keys = frozenset(e.key for e in refs if e.linkable and e.key)
        report = Report(
            report_id=first.item.news_id, title=first.canon, first_seen_at=first.first_seen, published_at=first.published,
            publisher=first.publisher, entity_keys=keys, theme_dirs=_theme_dirs(cls) if cls is not None else frozenset(),
        )
        members = tuple(
            {"item_id": m.item.news_id, "publisher": m.publisher, "host": m.host, "first_seen_at": m.first_seen,
             "published_at": m.published} for m in rep.members)
        detail = {
            "display_title": first.item.title, "classification_status": _classification_status(rep, when),
            "classified_at": cls.classified_at.isoformat() if cls is not None and cls.classified_at else None,
            "classified_item_id": rep.canonical.item.news_id if cls is not None else None,
            "entities": [_entity_detail(e) for e in refs], "flags": flags,
            "official_hosts": sorted({m.host for m in rep.members if m.host in config.official_source_domains}),
        }
        out.append(PoolReport(report, tuple(m["item_id"] for m in members), members, detail))
    out.sort(key=lambda p: (p.report.first_seen_at, p.report.published_at is None,
                            p.report.published_at or datetime.min, p.report.report_id))
    return out


def background_for(
    items: Iterable[NewsItem], end: datetime | str, arm: ArmAConfig, *, config: E1Config,
    resolver: EntityResolver | None, generic: frozenset[str], suffixes: Sequence[str],
) -> Background:
    """The IDF background: reports first seen in [end - background_days, end), syndication-collapsed, judged with the
    classifications made before `end` (no item at or after `end` is read). Flags `background_thin` below the existing
    `background_min_items` (events.yaml) reports."""
    stop = naive_utc(end)
    assert stop is not None
    last = stop - timedelta(microseconds=1)
    cfg = dataclasses.replace(config, pool_window_days=arm.background_days)
    preps, _ = _prepare([i for i in items if i.first_seen_at is not None and i.first_seen_at < stop], last, cfg)
    rows: list[tuple[date, str, frozenset[str]]] = []
    reports = _collapse(preps, cfg)
    for rep in reports:
        cls = rep.canonical.cls
        keys = frozenset()
        if cls is not None:
            refs = _entity_refs(cls, resolver, generic, suffixes, config.entity_relevance_min)
            keys = frozenset(e.key for e in refs if e.linkable and e.key)
        rows.append((rep.members[0].first_seen.date(), rep.members[0].canon, keys))
    flags = ["background_thin"] if len(rows) < config.background_min_items else []
    return make_background(rows, arm, start=stop - timedelta(days=arm.background_days), end=stop, flags=flags)


# ----------------------------------------------------------------------------- side tables and the daily step


class RegisterStore:
    """Two additive tables beside `event_state_*`: per-report details and the item -> report map."""

    TABLES = ("event_register_reports", "event_register_items")

    def __init__(self, con: Any) -> None:
        self.con = con
        con.execute("CREATE TABLE IF NOT EXISTS event_register_reports (report_id VARCHAR PRIMARY KEY, detail_json VARCHAR)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS event_register_items (item_id VARCHAR PRIMARY KEY, report_id VARCHAR, "
            "publisher VARCHAR, host VARCHAR, first_seen_at TIMESTAMP, published_at TIMESTAMP)")

    def record(self, pool: Iterable[PoolReport], state: EventState) -> None:
        for pr in pool:
            if state.event_of_report(pr.report.report_id) is None:
                continue
            self.con.execute("INSERT OR IGNORE INTO event_register_reports VALUES (?, ?)",
                             [pr.report.report_id, json.dumps(pr.detail, sort_keys=True, default=str)])
            for m in pr.members:
                self.con.execute("INSERT OR IGNORE INTO event_register_items VALUES (?, ?, ?, ?, ?, ?)",
                                 [m["item_id"], pr.report.report_id, m["publisher"], m["host"], m["first_seen_at"],
                                  m["published_at"]])

    def known_items(self) -> dict[str, str]:
        """item_id -> the report id it was persisted under."""
        return {r[0]: r[1] for r in self.con.execute("SELECT item_id, report_id FROM event_register_items").fetchall()}

    def snapshot(self) -> dict[str, list[tuple[Any, ...]]]:
        return {t: self.con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in self.TABLES}


def stabilize_pool(pool: Sequence[PoolReport], known: MutableMapping[str, str] | dict[str, str]) -> list[PoolReport]:
    """Keep persisted report identity. A pool report holding items already persisted under a report id takes that id (the
    one most of its known items sit under; ties go to the smaller id) and drops any member persisted under a different
    report, which stays where it is (a stored item is never moved). A pool report with no known member is new and keeps
    the id of its earliest member."""
    out: list[PoolReport] = []
    for pr in pool:
        owners = Counter(known[m["item_id"]] for m in pr.members if m["item_id"] in known)
        if not owners:
            out.append(pr)
            continue
        top = max(owners.values())
        chosen = min(r for r, n in owners.items() if n == top)
        members = tuple(m for m in pr.members if known.get(m["item_id"], chosen) == chosen)
        if chosen == pr.report.report_id and len(members) == len(pr.members):
            out.append(pr)
            continue
        out.append(PoolReport(dataclasses.replace(pr.report, report_id=chosen), tuple(m["item_id"] for m in members),
                              members, pr.detail))
    return out


def remember_items(known: MutableMapping[str, str], pool: Iterable[PoolReport], state: EventState) -> None:
    """Extend `known` with the items of every pool report the state has assigned (what `RegisterStore.record` persists)."""
    for pr in pool:
        if state.event_of_report(pr.report.report_id) is None:
            continue
        for item_id in pr.item_ids:
            known.setdefault(item_id, pr.report.report_id)


def apply_pool(state: EventState, store: RegisterStore | None, linker: Linker, pool: list[PoolReport],
               as_of: datetime | date | str) -> DayResult:
    if store is not None:
        pool = stabilize_pool(pool, store.known_items())
    result = state.apply_day(as_of, [p.report for p in pool], linker)
    if store is not None:
        store.record(pool, state)
    return result


def run_day(
    state: EventState, store: RegisterStore | None, linker: Linker, items: Iterable[NewsItem], as_of: datetime | str, *,
    config: E1Config, resolver: EntityResolver | None, generic: frozenset[str], suffixes: Sequence[str],
    window_start: datetime | None = None,
) -> DayResult:
    pool = collapse_pool(items, as_of, config=config, resolver=resolver, generic=generic, suffixes=suffixes,
                         window_start=window_start)
    return apply_pool(state, store, linker, pool, as_of)


# ----------------------------------------------------------------------------- the register document


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _rows(con: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cur = con.execute(sql, list(params))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def _corroboration(reports: list[dict[str, Any]], official_hosts: frozenset[str], min_publishers: int) -> dict[str, Any]:
    """Evidence category. Reports are already syndication-collapsed, so wire copies are one source. `conflicting` is
    never produced: nothing in Stage 1 detects contradiction, and the register says so rather than guess."""
    official = sorted({h for r in reports for h in r["detail"].get("official_hosts", []) if h in official_hosts})
    publishers = sorted({r["publisher"] for r in reports if r["publisher"]})
    evidence: list[dict[str, Any]] = [{"kind": "publishers", "values": publishers, "count": len(publishers)}]
    if official:
        evidence.append({"kind": "official_source", "values": official})
        return {"category": "officially_sourced", "evidence": evidence}
    if len(publishers) >= min_publishers:
        return {"category": "independently_corroborated", "evidence": evidence}
    return {"category": "single_source", "evidence": evidence}


def build_register(
    con: Any, as_of: datetime | str, *, config: E1Config, arm: ArmAConfig | None = None, open_days: int = 3,
) -> dict[str, Any]:
    """The candidate register document as of `as_of`, from the state and side tables on `con`. Deterministic."""
    when = naive_utc(as_of)
    assert when is not None
    today = when.date()
    events = _rows(con, "SELECT * FROM event_state_events ORDER BY onset_first_seen_at, event_id")
    reports_by_event: dict[str, list[dict[str, Any]]] = {}
    for r in _rows(con, "SELECT r.*, d.detail_json FROM event_state_reports r "
                        "LEFT JOIN event_register_reports d USING (report_id) ORDER BY r.first_seen_at, r.report_id"):
        r["detail"] = json.loads(r.pop("detail_json")) if r.get("detail_json") else {}
        r["evidence"] = json.loads(r.pop("evidence_json"))
        reports_by_event.setdefault(r["event_id"], []).append(r)
    items_by_report: dict[str, list[dict[str, Any]]] = {}
    for it in _rows(con, "SELECT * FROM event_register_items ORDER BY first_seen_at, item_id"):
        items_by_report.setdefault(it["report_id"], []).append(it)
    links = _rows(con, "SELECT * FROM event_state_proposed_links ORDER BY event_id, other_event_id, report_id")

    candidates = []
    for ev in events:
        eid = ev["event_id"]
        reps = reports_by_event.get(eid, [])
        report_ids = [r["report_id"] for r in reps]
        item_ids = [it["item_id"] for r in reps for it in items_by_report.get(r["report_id"], [])]
        deduped = len(reps)

        by_publisher: dict[str, dict[str, Any]] = {}
        for r in reps:
            pub = r["publisher"] or "(unknown)"
            slot = by_publisher.setdefault(pub, {"publisher": pub, "report_ids": [], "syndication_provenance": []})
            slot["report_ids"].append(r["report_id"])
            copies = [it for it in items_by_report.get(r["report_id"], []) if it["item_id"] != r["report_id"]]
            slot["syndication_provenance"].append({
                "report_id": r["report_id"],
                "copy_items": [{"item_id": it["item_id"], "publisher": it["publisher"], "host": it["host"]} for it in copies]})

        statuses = [{"report_id": r["report_id"], "status": r["detail"].get("classification_status", "unclassified"),
                     "classified_at": r["detail"].get("classified_at")} for r in reps]
        n_cls = sum(1 for s in statuses if s["status"] == "classified")

        resolved: dict[str, dict[str, Any]] = {}
        unresolved: dict[str, dict[str, Any]] = {}
        resolver_flags: set[str] = set()
        themes: Counter[tuple[str, str]] = Counter()
        for r in reps:
            for e in r["detail"].get("entities", []):
                resolver_flags.update(e["flags"])
                if e["status"] == "named" and e["tickers"]:
                    slot = resolved.setdefault(e["key"], {"key": e["key"], "tickers": e["tickers"], "names": set(),
                                                         "flags": set(), "reports": 0})
                    slot["names"].add(e["name"])
                    slot["flags"].update(e["flags"])
                    slot["reports"] += 1
                else:
                    ukey = e["normalized"] or e["name"]
                    slot = unresolved.setdefault(ukey, {"normalized": ukey, "names": set(), "status": e["status"],
                                                       "flags": set(), "reports": 0})
                    slot["names"].add(e["name"])
                    slot["flags"].update(e["flags"])
                    slot["reports"] += 1
            themes.update(tuple(t) for t in json.loads(r["theme_dirs_json"]))

        def _clean(d: dict[str, Any]) -> dict[str, Any]:
            return {k: (sorted(v) if isinstance(v, set) else v) for k, v in d.items()}

        related = []
        for ln in links:
            if eid in (ln["event_id"], ln["other_event_id"]):
                other = ln["other_event_id"] if ln["event_id"] == eid else ln["event_id"]
                related.append({"candidate_id": other, "report_id": ln["report_id"], "proposed_on": _iso(ln["proposed_on"]),
                                "direction": "outgoing" if ln["event_id"] == eid else "incoming",
                                "status": ln["status"], "evidence": json.loads(ln["evidence_json"])})

        gap = (today - ev["last_report_day"]).days
        closed = gap > open_days
        flags = set(json.loads(ev["flags_json"]))
        for r in reps:
            flags.update(r["detail"].get("flags", []))
            flags.update(f for f in r["evidence"].get("flags", []) if f)
        if n_cls < deduped:
            flags.add("unclassified_reports")
        corroboration = _corroboration(reps, config.official_source_domains,
                                       config.min_distinct_publishers)
        if corroboration["category"] == "single_source":
            flags.add("single_source")
        if deduped == 1 and len(items_by_report.get(report_ids[0], [])) > 1:
            flags.add("syndication_only")
        if resolver_flags:
            flags.add("resolver_flags")

        candidates.append({
            "candidate_id": eid,
            "report_ids": report_ids,
            "item_ids": item_ids,
            "title": (reps[0]["detail"].get("display_title") if reps else None),
            "deduped_report_count": deduped,
            "distinct_publishers": [by_publisher[k] for k in sorted(by_publisher)],
            "corroboration": corroboration,
            "classification_status": {"classified_reports": n_cls, "total_reports": deduped,
                                      "coverage": (n_cls / deduped) if deduped else None, "reports": statuses},
            "entities_resolved": [_clean(resolved[k]) for k in sorted(resolved)],
            "entities_unresolved": [_clean(unresolved[k]) for k in sorted(unresolved)],
            "resolver_flags": sorted(resolver_flags),
            "themes": [{"theme_id": t, "direction": d, "reports": n, "role": "annotation_only"}
                       for (t, d), n in sorted(themes.items())],
            "related_candidates": related,
            "storyline": None,
            "onset": _iso(ev["onset_published_at"]),
            "first_available_at": _iso(ev["first_available_at"]),
            "last_report_at": _iso(ev["last_report_at"]),
            "status_window": {"status": CLOSED if closed else OPEN, "closed_on": _iso(ev["closed_on"]) if closed else None,
                              "open_days": open_days},
            "market_response": {"checks": []},
            "flags": sorted(flags),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": "event_register",
        "as_of": when.isoformat(),
        "note": NOTE,
        "linker": {"arm": "B" if isinstance(arm, ArmBConfig) else "A", **({"config": dataclasses.asdict(arm)} if arm is not None else {})},
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def write_register(path: str | Path, doc: dict[str, Any]) -> bool:
    """Write the register; returns False (and touches nothing) when the bytes would not change. Refuses any path whose
    name is a protected artifact."""
    target = Path(path)
    if target.name in PROTECTED_NAMES:
        raise ValueError(f"{target.name} is a protected artifact; the register is {REGISTER_FILENAME}")
    text = json.dumps(doc, indent=2, sort_keys=True, default=str) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == text:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)  # atomic: a reader sees the old file or the new one, never half of it
    finally:
        tmp.unlink(missing_ok=True)
    return True
