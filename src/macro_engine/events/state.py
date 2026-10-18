"""Persistent event identity (Phase 4a, docs/events_build/V23_PREREGISTRATION.md).

The 7-day pool of `builder.py` is a DISCOVERY window, not an event lifetime. This module keeps events in the
store so that an event keeps the id it was given when it was created, however many days its story runs.

Rules (operator-approved 2026-10-18, Opus audit conditions)
  * An event gets a stable `event_id` at creation (`evt_` + sha1 of its seed report id, as in the builder).
  * Bounded summary per open event: the FIRST 3 reports (anchors, pinned, never replaced), the most recent 5, and the
    core entity profile (entities present in >= `core_entity_share` of the event's reports). A linker sees only this.
  * An event is OPEN while `as_of_day - last_report_day <= open_days` (3). Ingest is daily, so the 72-hour rule is applied
    honestly at day granularity: `last_report_day` is the UTC day on which the event's latest report was first seen, and
    the event is closed on the first daily run on which the gap exceeds `open_days` days. A closed event never accepts a
    join and keeps its id.
  * NO silent retrospective reassignment. A stored report is never moved. When new evidence connects two events the
    state records a `proposed_link` (with the evidence); a deliberate supersession/alias is a separate record written
    only by `record_supersession`. Ids are never deleted or renamed; `resolve_id` follows aliases for lookup only.
  * Idempotent replay: a report already assigned is skipped, closing is a pure function of (last_report_day, as_of day),
    so applying the same day twice changes nothing. Days must be applied in non-decreasing order.
  * Time fields. Every report and every event carries BOTH `published_at` and `first_seen_at`. `first_seen_at` is when
    this system knew of the report (daily ingest; it can trail publication by up to a day or more, and a re-indexed
    aggregator echo gets a fresh one). The event onset used by E2 is `onset_published_at` (earliest `published_at`);
    `first_available_at` (earliest `first_seen_at`) is when the event could first have been known and is the
    look-ahead boundary. A missing `published_at` leaves `onset_published_at` null with the flag `published_at_missing`;
    E2 must not substitute `first_seen_at` silently.

Independent of the linker. `Linker.link(report, open_events) -> LinkDecision` is the interface Phase 4b implements;
`NeverLinkStub` is the trivial implementation for tests. The tables are additive (`event_state_*`) and are not read by
any existing output.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from macro_engine.events.builder import naive_utc

OPEN = "open"
CLOSED = "closed"

TABLES = (
    "event_state_meta",
    "event_state_events",
    "event_state_reports",
    "event_state_proposed_links",
    "event_state_aliases",
)


@dataclass(frozen=True)
class StateConfig:
    """Bounds of the per-event summary and the open window. Frozen by hash with the rest of the build (see prereg)."""

    anchor_count: int = 3
    recent_count: int = 5
    core_entity_share: float = 0.30
    open_days: int = 3


@dataclass(frozen=True)
class Report:
    """One collapsed report as the state sees it. `entity_keys` identify entities; themes are annotation only."""

    report_id: str
    title: str
    first_seen_at: datetime
    published_at: datetime | None = None
    publisher: str | None = None
    entity_keys: frozenset[str] = frozenset()
    theme_dirs: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class ReportRef:
    report_id: str
    title: str
    first_seen_at: datetime
    published_at: datetime | None
    entity_keys: frozenset[str]


@dataclass(frozen=True)
class EventSummary:
    """What a linker is shown for an open event: bounded, never the union of everything."""

    event_id: str
    anchors: tuple[ReportRef, ...]
    recent: tuple[ReportRef, ...]
    core_entities: frozenset[str]
    report_count: int
    first_available_at: datetime
    last_report_at: datetime


@dataclass(frozen=True)
class ProposedLink:
    """Evidence that `report` also connects the event it was assigned to with `other_event_id`."""

    other_event_id: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkDecision:
    """`event_id` names an OPEN event to join, or is None for a new event."""

    event_id: str | None
    evidence: dict[str, Any] = field(default_factory=dict)
    proposals: tuple[ProposedLink, ...] = ()


class Linker(Protocol):
    def link(self, report: Report, open_events: Sequence[EventSummary]) -> LinkDecision: ...


class NeverLinkStub:
    """Every report starts its own event. The trivial stub; Phase 4b implements the real linker."""

    def link(self, report: Report, open_events: Sequence[EventSummary]) -> LinkDecision:
        return LinkDecision(None, {"stub": "never_link"})


@dataclass
class DayResult:
    as_of_day: str
    created: list[str] = field(default_factory=list)
    joined: list[tuple[str, str]] = field(default_factory=list)  # (report_id, event_id)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_future: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    proposals: int = 0


def make_event_id(seed_report_id: str) -> str:
    return "evt_" + hashlib.sha1(seed_report_id.encode("utf-8")).hexdigest()[:16]


def _day(value: datetime | date | str) -> date:
    if isinstance(value, datetime):
        out = naive_utc(value)
        assert out is not None
        return out.date()
    if isinstance(value, date):
        return value
    out = naive_utc(value)
    assert out is not None
    return out.date()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _ref_json(r: ReportRef | Report) -> dict[str, Any]:
    return {"report_id": r.report_id, "title": r.title, "first_seen_at": _iso(r.first_seen_at),
            "published_at": _iso(r.published_at), "entity_keys": sorted(r.entity_keys)}


def _ref_from_json(d: dict[str, Any]) -> ReportRef:
    seen = naive_utc(d["first_seen_at"])
    assert seen is not None
    return ReportRef(d["report_id"], d["title"], seen, naive_utc(d["published_at"]), frozenset(d["entity_keys"]))


def _order(r: Report) -> tuple[Any, ...]:
    return (r.first_seen_at, r.published_at is None, r.published_at or datetime.min, r.report_id)


class EventState:
    """The persistent event store over a DuckDB connection (writable)."""

    def __init__(self, con: Any, config: StateConfig | None = None) -> None:
        self.con = con
        self.cfg = config or StateConfig()
        self.ensure_schema()

    # ------------------------------------------------------------------ schema
    def ensure_schema(self) -> None:
        c = self.con
        c.execute("CREATE TABLE IF NOT EXISTS event_state_meta (k VARCHAR PRIMARY KEY, v VARCHAR)")
        c.execute(
            """CREATE TABLE IF NOT EXISTS event_state_events (
                event_id VARCHAR PRIMARY KEY, seed_report_id VARCHAR, status VARCHAR, created_on DATE,
                closed_on DATE, first_available_at TIMESTAMP, onset_published_at TIMESTAMP,
                onset_first_seen_at TIMESTAMP, last_report_at TIMESTAMP, last_report_day DATE,
                report_count INTEGER, anchors_json VARCHAR, recent_json VARCHAR, entity_counts_json VARCHAR,
                core_entities_json VARCHAR, flags_json VARCHAR)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS event_state_reports (
                report_id VARCHAR PRIMARY KEY, event_id VARCHAR, title VARCHAR, publisher VARCHAR,
                published_at TIMESTAMP, first_seen_at TIMESTAMP, entity_keys_json VARCHAR,
                theme_dirs_json VARCHAR, assigned_on DATE, evidence_json VARCHAR)"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS event_state_proposed_links (
                event_id VARCHAR, other_event_id VARCHAR, report_id VARCHAR, proposed_on DATE,
                status VARCHAR, evidence_json VARCHAR, PRIMARY KEY (event_id, other_event_id, report_id))"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS event_state_aliases (
                event_id VARCHAR, superseded_by VARCHAR, kind VARCHAR, recorded_on DATE, evidence_json VARCHAR,
                PRIMARY KEY (event_id, superseded_by))"""
        )

    # ------------------------------------------------------------------ reads
    def _summary(self, row: tuple[Any, ...]) -> EventSummary:
        event_id, first_avail, last_at, count, anchors, recent, core = row
        first, last = naive_utc(first_avail), naive_utc(last_at)
        assert first is not None and last is not None
        return EventSummary(
            event_id=event_id,
            anchors=tuple(_ref_from_json(d) for d in json.loads(anchors)),
            recent=tuple(_ref_from_json(d) for d in json.loads(recent)),
            core_entities=frozenset(json.loads(core)),
            report_count=int(count),
            first_available_at=first,
            last_report_at=last,
        )

    def open_events(self) -> list[EventSummary]:
        rows = self.con.execute(
            "SELECT event_id, first_available_at, last_report_at, report_count, anchors_json, recent_json, "
            "core_entities_json FROM event_state_events WHERE status = ? ORDER BY event_id", [OPEN]
        ).fetchall()
        return [self._summary(r) for r in rows]

    def event(self, event_id: str) -> dict[str, Any] | None:
        cur = self.con.execute("SELECT * FROM event_state_events WHERE event_id = ?", [event_id])
        row = cur.fetchone()
        if row is None:
            return None
        rec = dict(zip([d[0] for d in cur.description], row, strict=True))
        for k in ("anchors_json", "recent_json", "entity_counts_json", "core_entities_json", "flags_json"):
            rec[k[: -len("_json")]] = json.loads(rec.pop(k))
        return rec

    def event_of_report(self, report_id: str) -> str | None:
        row = self.con.execute("SELECT event_id FROM event_state_reports WHERE report_id = ?", [report_id]).fetchone()
        return row[0] if row else None

    def proposed_links(self) -> list[dict[str, Any]]:
        cur = self.con.execute("SELECT * FROM event_state_proposed_links ORDER BY event_id, other_event_id, report_id")
        names = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            rec = dict(zip(names, row, strict=True))
            rec["evidence"] = json.loads(rec.pop("evidence_json"))
            out.append(rec)
        return out

    def resolve_id(self, event_id: str) -> str:
        """Follow supersession/alias records to the current id. For lookup only: the old id stays valid and displayed."""
        seen = {event_id}
        current = event_id
        while True:
            row = self.con.execute(
                "SELECT superseded_by FROM event_state_aliases WHERE event_id = ? ORDER BY recorded_on, superseded_by",
                [current]).fetchone()
            if row is None or row[0] in seen:
                return current
            seen.add(row[0])
            current = row[0]

    def snapshot(self) -> dict[str, list[tuple[Any, ...]]]:
        """Every state table's rows, for idempotence and lineage assertions."""
        return {t: self.con.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall() for t in TABLES}

    # ------------------------------------------------------------------ writes
    def record_supersession(self, old_id: str, new_id: str, evidence: dict[str, Any], on: datetime | date | str,
                            kind: str = "supersession") -> None:
        """Deliberately record that `new_id` supersedes/aliases `old_id`. Nothing is renamed, moved or deleted."""
        if kind not in ("supersession", "alias"):
            raise ValueError("kind must be supersession or alias")
        for eid in (old_id, new_id):
            if self.event(eid) is None:
                raise KeyError(f"unknown event {eid}")
        if old_id == new_id:
            raise ValueError("an event cannot supersede itself")
        self.con.execute(
            "INSERT OR IGNORE INTO event_state_aliases VALUES (?, ?, ?, ?, ?)",
            [old_id, new_id, kind, _day(on), json.dumps(evidence, sort_keys=True)],
        )

    def _check_order(self, day: date) -> None:
        row = self.con.execute("SELECT v FROM event_state_meta WHERE k = 'last_as_of_day'").fetchone()
        if row is not None and day < date.fromisoformat(row[0]):
            raise ValueError(f"as_of day {day} is earlier than the last applied day {row[0]}; days apply in order")
        self.con.execute("INSERT OR REPLACE INTO event_state_meta VALUES ('last_as_of_day', ?)", [day.isoformat()])

    def close_stale(self, as_of: datetime | date | str) -> list[str]:
        day = _day(as_of)
        rows = self.con.execute(
            "SELECT event_id, last_report_day FROM event_state_events WHERE status = ? ORDER BY event_id", [OPEN]
        ).fetchall()
        closed = []
        for event_id, last_day in rows:
            if (day - _day(last_day)).days > self.cfg.open_days:
                self.con.execute("UPDATE event_state_events SET status = ?, closed_on = ? WHERE event_id = ?",
                                 [CLOSED, day, event_id])
                closed.append(event_id)
        return closed

    def apply_day(self, as_of: datetime | date | str, reports: Iterable[Report], linker: Linker) -> DayResult:
        """Close stale events, then assign each not-yet-assigned report (arrival order) to an open event or a new one."""
        day = _day(as_of)
        cutoff = None if (isinstance(as_of, date) and not isinstance(as_of, datetime)) else naive_utc(as_of)
        self._check_order(day)
        result = DayResult(as_of_day=day.isoformat())
        result.closed = self.close_stale(day)
        for rep in sorted(reports, key=_order):
            if self.event_of_report(rep.report_id) is not None:
                result.skipped_existing.append(rep.report_id)
                continue
            if (rep.first_seen_at > cutoff) if cutoff is not None else (_day(rep.first_seen_at) > day):
                result.skipped_future.append(rep.report_id)
                continue
            self._assign(rep, day, linker, result)
        return result

    def _assign(self, rep: Report, day: date, linker: Linker, result: DayResult) -> None:
        open_events = self.open_events()
        decision = linker.link(rep, open_events)
        open_ids = {e.event_id for e in open_events}
        if decision.event_id is not None and decision.event_id not in open_ids:
            raise ValueError(f"linker chose {decision.event_id}, which is not an open event (closed events never accept joins)")
        if decision.event_id is None:
            event_id = make_event_id(rep.report_id)
            if self.event(event_id) is not None:
                raise ValueError(f"event id collision for {event_id}")
            self._create_event(event_id, rep, day)
            result.created.append(event_id)
        else:
            event_id = decision.event_id
            self._extend_event(event_id, rep)
            result.joined.append((rep.report_id, event_id))
        self.con.execute(
            "INSERT INTO event_state_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [rep.report_id, event_id, rep.title, rep.publisher, rep.published_at, rep.first_seen_at,
             json.dumps(sorted(rep.entity_keys)), json.dumps(sorted(list(t) for t in rep.theme_dirs)), day,
             json.dumps(decision.evidence, sort_keys=True, default=str)],
        )
        for prop in decision.proposals:
            if prop.other_event_id == event_id or prop.other_event_id not in open_ids:
                continue  # a link to itself, or to an event that is not open, is not a proposal between two events
            self.con.execute(
                "INSERT OR IGNORE INTO event_state_proposed_links VALUES (?, ?, ?, ?, ?, ?)",
                [event_id, prop.other_event_id, rep.report_id, day, "proposed",
                 json.dumps(prop.evidence, sort_keys=True, default=str)],
            )
            result.proposals += 1

    def _create_event(self, event_id: str, rep: Report, day: date) -> None:
        flags = ["published_at_missing"] if rep.published_at is None else []
        counts = {k: 1 for k in sorted(rep.entity_keys)}
        self.con.execute(
            "INSERT INTO event_state_events VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            [event_id, rep.report_id, OPEN, day, rep.first_seen_at, rep.published_at, rep.first_seen_at,
             rep.first_seen_at, _day(rep.first_seen_at), json.dumps([_ref_json(rep)]), json.dumps([_ref_json(rep)]),
             json.dumps(counts), json.dumps(self._core(counts, 1)), json.dumps(flags)],
        )

    def _core(self, counts: dict[str, int], n: int) -> list[str]:
        return sorted(k for k, c in counts.items() if c / n >= self.cfg.core_entity_share)

    def _extend_event(self, event_id: str, rep: Report) -> None:
        ev = self.event(event_id)
        assert ev is not None
        anchors = ev["anchors"]
        if len(anchors) < self.cfg.anchor_count:
            anchors = [*anchors, _ref_json(rep)]  # anchors are the first N reports and are never replaced
        recent = [*ev["recent"], _ref_json(rep)][-self.cfg.recent_count:]
        counts = dict(ev["entity_counts"])
        for k in rep.entity_keys:
            counts[k] = counts.get(k, 0) + 1
        n = int(ev["report_count"]) + 1
        flags = set(ev["flags"])
        if rep.published_at is None:
            flags.add("published_at_missing")
        onset_pub = min((p for p in (naive_utc(ev["onset_published_at"]), rep.published_at) if p is not None), default=None)
        onset_seen = min(naive_utc(ev["onset_first_seen_at"]), rep.first_seen_at)  # type: ignore[type-var]
        last = max(naive_utc(ev["last_report_at"]), rep.first_seen_at)  # type: ignore[type-var]
        self.con.execute(
            "UPDATE event_state_events SET first_available_at = ?, onset_published_at = ?, onset_first_seen_at = ?, "
            "last_report_at = ?, last_report_day = ?, report_count = ?, anchors_json = ?, recent_json = ?, "
            "entity_counts_json = ?, core_entities_json = ?, flags_json = ? WHERE event_id = ?",
            [onset_seen, onset_pub, onset_seen, last, _day(last), n, json.dumps(anchors), json.dumps(recent),
             json.dumps(dict(sorted(counts.items()))), json.dumps(self._core(counts, n)),
             json.dumps(sorted(flags)), event_id],
        )
