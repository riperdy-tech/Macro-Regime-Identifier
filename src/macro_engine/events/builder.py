"""E1 event builder (MRI_EVENTS_PLAN v2 section 5), deterministic, no model call, no persistence.

Input: news items with their stored classifications, and an `as_of` instant. Output: event records.
Every threshold comes from `config/events.yaml` (see `config.py`). Reused from `news/selection.py`: only the
tokeniser (`_token_set`) and `_jaccard`.

What it does, in plan order
  5.1  pool: items first seen in the 7 days up to `as_of` (first_seen_at <= as_of). A classification counts only
       if `classified_at <= as_of`, it is a success, and it is forward evidence (`news/provenance.py`: live
       origin, made within 14 days of publication). Anything else leaves the item unclassified at `as_of`; a
       classification made after `as_of` does not exist for this build and leaves no trace on the output.
  5.2  publisher: the URL host (leading `www.` removed); for an aggregator host (news.google.com) the trailing
       " - Publisher" of the title.
  5.3  syndication collapse: same canonical title, or title Jaccard >= syndication threshold, is one REPORT
       (transitive). Canonical item: earliest-seen classified member, else earliest-seen member.
  5.4  linking, in arrival order (`first_seen_at`, the point at which the system knew of the report): a classified
       report joins an event when it shares an entity (relevance >= threshold on both sides, generic entities
       excluded), AND shares a (theme, direction) OR has title Jaccard >= link threshold with a report of the event,
       AND arrives within the horizon of the event's latest report. An unclassified report joins only through
       title Jaccard >= the unclassified threshold with a report already in the event. Entities are compared by
       company (a company with several tickers is one key) and, when unresolved or ambiguous, by normalised name.
  5.5  a classified report that shares an entity with an event in the horizon, shares no (theme, direction) and
       has title Jaccard < threshold with every report of it, and joins nothing, starts a new event whose
       `related_event_ids` names those events. Any other report that joins nothing starts a new event too.
  5.6  corroboration: >= N distinct publishers among the event's reports (each collapsed report counts once, by
       its canonical item's publisher), or one official-source report. Not corroborated within the rejection
       window of `first_available_at` -> `rejected_uncorroborated` (kept, and annotated if it later qualifies).
  5.7  record: see `_event_record`.

Choices the plan does not state, all annotated rather than gated
  * Only a classified report starts an event (plan section 7: the first classified report forms the event). An
    unclassified report that joins nothing is listed in `unattached` with its reason.
  * An item whose classification was dropped (not forward evidence, unparseable, no published_at) carries that
    as a flag on its report.
  * A report that fits several events joins the best one (highest title Jaccard, then most shared themes, then
    most recent, then smaller id) and carries `also_linkable` listing the others.
  * "Shared theme" is the same theme_id with the same direction string, in both the link and the new-development
    rules; theme confidence is not filtered.

Event ids derive from the earliest-seen member item of the event's first report (`evt_` + sha1[:16]), not from
the run time, so a replay with a later `as_of` gives the same id.
"""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from macro_engine.events.config import E1Config, host_key, load_generic_entities
from macro_engine.events.entities import (
    GENERIC_STATUS,
    NAMED_STATUS,
    EntityResolver,
    normalize_name,
)
from macro_engine.news.provenance import is_forward_evidence
from macro_engine.news.selection import _jaccard, _token_set

AGGREGATOR_HOSTS = frozenset({"news.google.com"})
_PUBLISHER_SUFFIX = re.compile(r"^(.*\S)\s+[-–—]\s+([^-–—]{2,40})$")

DETECTED = "detected"
CORROBORATED = "corroborated"
REJECTED = "rejected_uncorroborated"


# ----------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Classification:
    """One stored classification row, parsed. `parse_error` is set when a JSON column was unreadable."""

    classified_at: datetime | None
    status: str | None
    origin: str | None = None
    ai_provider: str | None = None
    themes: tuple[dict[str, Any], ...] = ()
    entities: tuple[dict[str, Any], ...] = ()
    sector_impacts: tuple[dict[str, Any], ...] = ()
    severity: float | None = None
    confidence: float | None = None
    summary: str | None = None
    parse_error: bool = False


@dataclass(frozen=True)
class NewsItem:
    news_id: str
    title: str | None
    source_url: str | None
    first_seen_at: datetime | None
    published_at: datetime | None = None
    classifications: tuple[Classification, ...] = ()


def naive_utc(value: datetime | str | None) -> datetime | None:
    """Naive UTC datetime (the store's convention) from a datetime, ISO string, pandas Timestamp or None."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if value != value:  # NaT
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


# ----------------------------------------------------------------------------- text and publisher


def canonical_title(title: str | None) -> str:
    """html.unescape, NFKC, lower case, whitespace collapsed, one trailing " - Publisher" removed."""
    text = unicodedata.normalize("NFKC", html.unescape(str(title or ""))).lower()
    text = " ".join(text.split())
    match = _PUBLISHER_SUFFIX.match(text)
    return match.group(1).strip() if match else text


def extract_publisher(title: str | None, source_url: str | None) -> tuple[str | None, str | None, str | None]:
    """(publisher key, url host key, flag). The flag explains a missing or unparsed publisher."""
    host = host_key(urlparse(str(source_url or "")).netloc)
    if host is None:
        return None, None, "publisher_unknown"
    if host in AGGREGATOR_HOSTS:
        match = _PUBLISHER_SUFFIX.match(" ".join(html.unescape(str(title or "")).split()))
        if match:
            return match.group(2).strip().casefold(), host, None
        return host, host, "publisher_unparsed_aggregator"
    return host, host, None


# ----------------------------------------------------------------------------- internals


@dataclass(frozen=True)
class EntityRef:
    name: str
    normalized: str
    key: str | None  # None for generic entities
    status: str
    tier: str | None
    tickers: tuple[str, ...]
    flags: tuple[str, ...]
    entity_type: str | None
    relevance: float | None
    link_reason: str | None  # why it cannot link; None when it can

    @property
    def linkable(self) -> bool:
        return self.link_reason is None


@dataclass
class _Prep:
    item: NewsItem
    first_seen: datetime
    published: datetime | None
    publisher: str | None
    host: str | None
    canon: str
    tokens: frozenset[str]
    cls: Classification | None
    flags: list[str] = field(default_factory=list)


@dataclass
class _Report:
    canonical: _Prep
    members: list[_Prep]
    arrival: datetime
    entities: list[EntityRef] = field(default_factory=list)
    link_keys: frozenset[str] = frozenset()
    theme_dirs: frozenset[tuple[str, str]] = frozenset()
    flags: list[str] = field(default_factory=list)

    @property
    def classified(self) -> bool:
        return self.canonical.cls is not None

    @property
    def report_id(self) -> str:
        return self.canonical.item.news_id


@dataclass
class _Event:
    event_id: str
    onset_item_id: str
    reports: list[_Report] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    latest_arrival: datetime | None = None
    corroborated_at: datetime | None = None
    corroboration_basis: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)

    @property
    def entity_keys(self) -> set[str]:
        return {k for r in self.reports for k in r.link_keys}

    @property
    def theme_dirs(self) -> set[tuple[str, str]]:
        return {t for r in self.reports for t in r.theme_dirs}


@dataclass
class BuildResult:
    as_of: str
    events: list[dict[str, Any]]
    unattached: list[dict[str, Any]]
    meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": self.as_of, "events": self.events, "unattached": self.unattached, "meta": self.meta}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def select_classification(
    item: NewsItem, as_of: datetime
) -> tuple[Classification | None, list[str]]:
    """The classification this item has at `as_of`, or None with the reasons it has none. Look-ahead free."""
    flags: list[str] = []
    visible = [c for c in item.classifications if c.classified_at is not None and c.classified_at <= as_of]
    successes = [c for c in visible if c.status == "success"]
    if not successes:
        return None, flags
    for cls in sorted(successes, key=lambda c: c.classified_at, reverse=True):
        if cls.parse_error:
            flags.append("classification_unparseable")
            continue
        origin = cls.origin or ("mock" if cls.ai_provider == "mock" else "live")
        if not is_forward_evidence(
            effective_origin=origin,
            classification_status=cls.status,
            classified_at=cls.classified_at,
            published_at=item.published_at,
        ):
            flags.append("classification_not_forward_evidence")
            continue
        return cls, []
    return None, sorted(set(flags))


def _entity_refs(
    cls: Classification,
    resolver: EntityResolver | None,
    generic: frozenset[str],
    suffixes: Sequence[str],
    relevance_min: float,
) -> list[EntityRef]:
    refs: list[EntityRef] = []
    for ent in cls.entities:
        name = ent.get("name") if isinstance(ent, dict) else None
        if not name:
            continue
        name = str(name)
        etype = ent.get("entity_type")
        rel = _num(ent.get("relevance"))
        if resolver is not None:
            res = resolver.resolve(name, etype)
            norm, status, tier, flags = res.normalized, res.status, res.tier, res.flags
            tickers = res.tickers
        else:
            norm = normalize_name(name, suffixes)
            status = GENERIC_STATUS if norm in generic else "unresolved"
            tier, flags, tickers = None, (), ()
        if status == GENERIC_STATUS or norm in generic:
            key, reason = None, "generic_entity"
        elif not norm:
            key, reason = None, "empty_name"
        else:
            key = "T:" + "|".join(sorted(tickers)) if status == NAMED_STATUS and tickers else "N:" + norm
            reason = None
            if rel is None:
                reason = "relevance_missing"
            elif rel < relevance_min:
                reason = "relevance_below_min"
        refs.append(EntityRef(name, norm, key, status, tier, tuple(tickers), tuple(flags),
                              str(etype) if etype is not None else None, rel, reason))
    return refs


def _theme_dirs(cls: Classification) -> frozenset[tuple[str, str]]:
    out = set()
    for th in cls.themes:
        if isinstance(th, dict) and th.get("theme_id") and th.get("direction") is not None:
            out.add((str(th["theme_id"]), str(th["direction"])))
    return frozenset(out)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _prepare(items: Iterable[NewsItem], as_of: datetime, cfg: E1Config) -> tuple[list[_Prep], dict[str, int]]:
    start = as_of - timedelta(days=cfg.pool_window_days)
    seen_ids: set[str] = set()
    out: list[_Prep] = []
    excluded = {"no_first_seen_at": 0, "outside_pool": 0, "duplicate_news_id": 0}
    for item in items:
        first_seen = naive_utc(item.first_seen_at)
        if first_seen is None:
            excluded["no_first_seen_at"] += 1
            continue
        if not (start < first_seen <= as_of):
            excluded["outside_pool"] += 1
            continue
        if item.news_id in seen_ids:
            excluded["duplicate_news_id"] += 1
            continue
        seen_ids.add(item.news_id)
        published = naive_utc(item.published_at)
        publisher, host, pflag = extract_publisher(item.title, item.source_url)
        canon = canonical_title(item.title)
        flags = [pflag] if pflag else []
        if not canon:
            flags.append("title_missing")
        if published is None:
            flags.append("published_at_missing")
        cls, cflags = select_classification(
            NewsItem(item.news_id, item.title, item.source_url, first_seen, published, item.classifications), as_of
        )
        flags.extend(cflags)
        out.append(_Prep(item, first_seen, published, publisher, host, canon, frozenset(_token_set(canon)), cls, flags))
    out.sort(key=lambda p: (p.first_seen, p.item.news_id))
    return out, excluded


def _collapse(preps: list[_Prep], cfg: E1Config) -> list[_Report]:
    uf = _UnionFind(len(preps))
    by_canon: dict[str, int] = {}
    for i, p in enumerate(preps):
        if p.canon:
            if p.canon in by_canon:
                uf.union(by_canon[p.canon], i)
            else:
                by_canon[p.canon] = i
    for i in range(len(preps)):
        for j in range(i + 1, len(preps)):
            if preps[i].tokens and _jaccard(set(preps[i].tokens), set(preps[j].tokens)) >= cfg.syndication_jaccard_min:
                uf.union(i, j)
    groups: dict[int, list[_Prep]] = defaultdict(list)
    for i, p in enumerate(preps):
        groups[uf.find(i)].append(p)
    reports = []
    for members in groups.values():
        members.sort(key=lambda p: (p.first_seen, p.item.news_id))
        classified = [m for m in members if m.cls is not None]
        canonical = (classified or members)[0]
        reports.append(_Report(canonical=canonical, members=members, arrival=members[0].first_seen))
    reports.sort(key=lambda r: (r.arrival, r.report_id))
    return reports


def _jmax(report: _Report, event: _Event) -> float:
    tokens = set(report.canonical.tokens)
    return max((_jaccard(tokens, set(r.canonical.tokens)) for r in event.reports), default=0.0)


def _make_event_id(onset_item_id: str) -> str:
    return "evt_" + hashlib.sha1(onset_item_id.encode("utf-8")).hexdigest()[:16]


def _is_official(report: _Report, cfg: E1Config) -> bool:
    return any(m.host in cfg.official_source_domains for m in report.members if m.host)


def _update_corroboration(event: _Event, cfg: E1Config) -> None:
    if event.corroborated_at is not None:
        return
    publishers = {r.canonical.publisher for r in event.reports if r.canonical.publisher}
    basis = []
    if len(publishers) >= cfg.min_distinct_publishers:
        basis.append("distinct_publishers")
    if cfg.official_source_corroborates and any(_is_official(r, cfg) for r in event.reports):
        basis.append("official_source")
    if basis:
        event.corroborated_at = event.latest_arrival
        event.corroboration_basis = basis


# ----------------------------------------------------------------------------- the build


def build_events(
    items: Iterable[NewsItem],
    as_of: datetime | str,
    *,
    config: E1Config,
    resolver: EntityResolver | None = None,
    generic_entities: Iterable[str] | None = None,
    legal_suffixes: Sequence[str] | None = None,
) -> BuildResult:
    """Build the event list as of `as_of` from `items`. Pure and deterministic; see the module docstring."""
    cfg = config
    when = naive_utc(as_of)
    assert when is not None
    if legal_suffixes is None:
        if resolver is not None:
            legal_suffixes = resolver.legal_suffixes
        else:
            from macro_engine.events.entities import load_entity_config

            legal_suffixes = load_entity_config()["legal_suffixes"]
    raw_generic = list(generic_entities) if generic_entities is not None else load_generic_entities(cfg.generic_entities_ref)
    generic = frozenset(normalize_name(g, legal_suffixes) for g in raw_generic)

    preps, excluded = _prepare(items, when, cfg)
    reports = _collapse(preps, cfg)
    horizon = timedelta(hours=cfg.horizon_hours)
    rejection = timedelta(hours=cfg.rejection_hours)

    for rep in reports:
        rep.flags = sorted({f for m in rep.members for f in m.flags})
        cls = rep.canonical.cls
        if cls is not None:
            rep.entities = _entity_refs(cls, resolver, generic, legal_suffixes, cfg.entity_relevance_min)
            rep.link_keys = frozenset(e.key for e in rep.entities if e.linkable and e.key)
            rep.theme_dirs = _theme_dirs(cls)
            for e in rep.entities:
                if not e.linkable:
                    rep.flags.append(f"entity_not_linkable:{e.link_reason}:{e.normalized}")
            if not rep.link_keys:
                rep.flags.append("no_linkable_entity")
            rep.flags = sorted(set(rep.flags))

    events: list[_Event] = []
    unattached: list[dict[str, Any]] = []
    for rep in reports:
        options = []
        related: list[str] = []
        for ev in events:
            if rep.arrival - ev.latest_arrival > horizon:
                continue
            jm = _jmax(rep, ev)
            if rep.classified:
                shared_e = rep.link_keys & ev.entity_keys
                shared_t = rep.theme_dirs & ev.theme_dirs
                if shared_e and (shared_t or jm >= cfg.link_jaccard_min):
                    options.append((-jm, -len(shared_t), -ev.latest_arrival.timestamp(), ev.event_id, ev))
                elif shared_e and not shared_t and jm < cfg.new_development_jaccard_below:
                    related.append(ev.event_id)
            elif jm >= cfg.unclassified_link_jaccard_min:
                options.append((-jm, 0, -ev.latest_arrival.timestamp(), ev.event_id, ev))
        if options:
            options.sort(key=lambda o: o[:4])
            best = options[0][4]
            if len(options) > 1:
                rep.flags = sorted({*rep.flags, "also_linkable:" + ",".join(sorted(o[4].event_id for o in options[1:]))})
            best.reports.append(rep)
            best.latest_arrival = max(best.latest_arrival, rep.arrival)  # type: ignore[type-var]
            _update_corroboration(best, cfg)
        elif rep.classified:
            ev = _Event(event_id=_make_event_id(rep.members[0].item.news_id), onset_item_id=rep.members[0].item.news_id,
                        reports=[rep], related=sorted(related), latest_arrival=rep.arrival)
            events.append(ev)
            _update_corroboration(ev, cfg)
        else:
            unattached.append({
                "report_id": rep.report_id,
                "item_ids": [m.item.news_id for m in rep.members],
                "title": rep.canonical.item.title,
                "first_seen_at": rep.arrival.isoformat(),
                "reason": "unclassified_no_link",
                "flags": rep.flags,
            })

    records = [_event_record(ev, when, cfg, rejection) for ev in events]
    records.sort(key=lambda r: (r["first_available_at"], r["event_id"]))
    meta = {
        "config_version": cfg.version,
        "official_source_domains": sorted(cfg.official_source_domains),
        "official_source_domains_status": cfg.official_domains_status,
        "items_in_pool": len(preps),
        "items_classified": sum(1 for p in preps if p.cls is not None),
        "reports": len(reports),
        "excluded": excluded,
    }
    return BuildResult(as_of=when.isoformat(), events=records, unattached=unattached, meta=meta)


# ----------------------------------------------------------------------------- the record (plan 5.7)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _event_record(ev: _Event, as_of: datetime, cfg: E1Config, rejection: timedelta) -> dict[str, Any]:
    reports = sorted(ev.reports, key=lambda r: (r.arrival, r.report_id))
    members = [m for r in reports for m in r.members]
    first_available = min(m.first_seen for m in members)
    published = [m.published for m in members if m.published is not None]
    flags = set(ev.flags)
    for r in reports:
        flags.update(r.flags)

    status = DETECTED
    if ev.corroborated_at is not None and ev.corroborated_at - first_available <= rejection:
        status = CORROBORATED
    elif ev.corroborated_at is not None:
        status = REJECTED
        flags.add("late_corroboration_after_rejection")
    elif as_of - first_available > rejection:
        status = REJECTED

    classified = [r for r in reports if r.classified]
    sev = [r.canonical.cls.severity for r in classified if r.canonical.cls and r.canonical.cls.severity is not None]

    theme_stats: dict[tuple[str, str], dict[str, Any]] = {}
    for r in classified:
        assert r.canonical.cls is not None
        for th in r.canonical.cls.themes:
            if not isinstance(th, dict) or not th.get("theme_id") or th.get("direction") is None:
                continue
            slot = theme_stats.setdefault((str(th["theme_id"]), str(th["direction"])),
                                          {"theme_id": str(th["theme_id"]), "direction": str(th["direction"]),
                                           "reports": 0, "max_severity": None})
            slot["reports"] += 1
            s = _num(th.get("severity"))
            if s is not None and (slot["max_severity"] is None or s > slot["max_severity"]):
                slot["max_severity"] = s

    ent_groups: dict[str, dict[str, Any]] = {}
    for r in classified:
        for e in r.entities:
            gkey = e.key if e.key is not None else "G:" + e.normalized
            g = ent_groups.setdefault(gkey, {
                "key": gkey, "names": set(), "normalized": e.normalized, "status": e.status, "tier": e.tier,
                "tickers": list(e.tickers), "flags": set(), "entity_types": set(), "max_relevance": None,
                "reports": 0, "link_exclusion": e.link_reason,
            })
            g["names"].add(e.name)
            g["flags"].update(e.flags)
            if e.entity_type:
                g["entity_types"].add(e.entity_type)
            g["reports"] += 1
            if e.relevance is not None and (g["max_relevance"] is None or e.relevance > g["max_relevance"]):
                g["max_relevance"] = e.relevance
            if e.linkable:
                g["link_exclusion"] = None
    entities_named, entities_other = [], []
    for g in sorted(ent_groups.values(), key=lambda x: x["key"]):
        out = {**g, "names": sorted(g["names"]), "flags": sorted(g["flags"]), "entity_types": sorted(g["entity_types"])}
        (entities_named if g["status"] == NAMED_STATUS and g["tickers"] else entities_other).append(out)

    sectors: dict[str, dict[str, Any]] = {}
    for r in classified:
        assert r.canonical.cls is not None
        for imp in r.canonical.cls.sector_impacts:
            if not isinstance(imp, dict) or not imp.get("sector_id"):
                continue
            slot = sectors.setdefault(str(imp["sector_id"]), {"sector_id": str(imp["sector_id"]), "reports": 0,
                                                              "_sum": 0.0, "_n": 0, "max_confidence": None})
            slot["reports"] += 1
            score, conf = _num(imp.get("impact_score")), _num(imp.get("confidence"))
            if score is not None:
                slot["_sum"] += score
                slot["_n"] += 1
            if conf is not None and (slot["max_confidence"] is None or conf > slot["max_confidence"]):
                slot["max_confidence"] = conf
    sector_out = []
    for sid in sorted(sectors):
        s = sectors[sid]
        sector_out.append({"sector_id": sid, "reports": s["reports"],
                           "mean_impact_score": (s["_sum"] / s["_n"]) if s["_n"] else None,
                           "max_confidence": s["max_confidence"]})

    seed = reports[0]
    publishers = sorted({r.canonical.publisher for r in reports if r.canonical.publisher})
    return {
        "event_id": ev.event_id,
        "event_status": status,
        "onset": _iso(min(published)) if published else None,
        "first_available_at": _iso(first_available),
        "latest_report_at": _iso(ev.latest_arrival),
        "corroborated_at": _iso(ev.corroborated_at) if status == CORROBORATED else None,
        "corroboration_basis": ev.corroboration_basis if ev.corroborated_at is not None else [],
        "title": seed.canonical.item.title,
        "themes": [theme_stats[k] for k in sorted(theme_stats)],
        "entities_named": entities_named,
        "entities_other": entities_other,
        "sector_impacts": sector_out,
        "report_count": len(reports),
        "classified_report_count": len(classified),
        "item_count": len(members),
        "publisher_count": len(publishers),
        "publishers": publishers,
        "max_severity": max(sev) if sev else None,
        "mean_severity": (sum(sev) / len(sev)) if sev else None,
        "related_event_ids": list(ev.related),
        "flags": sorted(flags),
        "reports": [
            {
                "report_id": r.report_id,
                "first_seen_at": _iso(r.arrival),
                "classified": r.classified,
                "publisher": r.canonical.publisher,
                "item_ids": [m.item.news_id for m in r.members],
                "flags": r.flags,
            }
            for r in reports
        ],
    }
