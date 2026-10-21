"""Arm A linker (docs/events_build/V23_PREREGISTRATION.md): sparse lexical, linked against ANCHORS.

Implements the `Linker.link(report, open_events) -> LinkDecision` interface of `state.py`.

  * Title similarity: cosine of character 3-5-gram TF-IDF vectors of the canonical title. The IDF comes from a
    PRE-POOL background (items first seen in the `background_days` days before the pool start, syndication-collapsed,
    day-granular: an n-gram counts once per calendar day, so a one-day burst cannot starve its weight). No item at or
    after the background end is read, so there is no look-ahead.
  * Entity term (secondary, additive): cosine of IDF-weighted entity-key vectors, IDF = ln((D + 1) / (d + 1)), d =
    distinct calendar days on which the key occurs in the background, D = distinct background days. A key never seen has
    the largest weight; a key present every day has none. Continuous weight, no frequency cutoff.
  * Score of a report against an event = the maximum over the event's ANCHORS (its first reports, pinned by the state)
    of `title_cosine + entity_weight * entity_cosine`. Never the union and never a centroid, so a chain A-B-C where C
    resembles only B does not join A's event unless C resembles an anchor. The recent-5 are not used (not
    pre-registered).
  * Link only if score >= cutoff AND the event is inside the open horizon AND, on the best anchor, the title cosine
    >= `title_cosine_floor` or a shared entity key has IDF >= `entity_idf_floor`.
  * Themes, directions, sectors and severities are never read. An unclassified report (no entities) links on its title.
  * Ambiguity is annotated, not merged: when other eligible events score within `ambiguity_margin` of the best, the
    report joins the best one, the decision says `ambiguous`, and the others are recorded as proposed links. Events
    scoring at least `near_miss_fraction * cutoff` that were not linked are recorded as near-miss proposals.
  * A report below the cutoff for every event returns `event_id=None` (a new candidate).

Arm B (`ArmBLinker`, same file) is arm A plus a dense term: per anchor, `title_cosine + entity_weight * entity_cosine +
dense_weight * dense_cosine`, the dense cosine taken between STORED title vectors (`embeddings.py`; the model is never
called here). The best anchor is chosen on that sum, so one anchor supplies all the evidence. A report or anchor with
no stored vector adds nothing for that anchor and the decision carries the flag `dense_missing`: the arm A score is
used, nothing is dropped and nothing crashes. The gates (title floor, entity floor) are arm A's, unchanged.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from macro_engine import peer_paths
from macro_engine.events.embeddings import dense_cosine
from macro_engine.events.state import EventSummary, LinkDecision, ProposedLink, Report, ReportRef

DEFAULT_V23_PATH = peer_paths.REPO_ROOT / "config" / "events_v23.yaml"
_WORD = re.compile(r"[^\W_]+")

SparseVec = dict[str, float]


@dataclass(frozen=True)
class ArmAConfig:
    ngram_min: int = 3
    ngram_max: int = 5
    title_idf_offset: float = 1.0
    background_days: float = 28.0
    anchor_count: int = 3
    entity_weight: float = 0.25
    cutoff: float = 0.35
    title_cosine_floor: float = 0.10
    entity_idf_floor: float = 2.0
    ambiguity_margin: float = 0.05
    near_miss_fraction: float = 0.8


@dataclass(frozen=True)
class ArmBConfig(ArmAConfig):
    """Arm A's parameters (with arm B's own cutoff and entity weight) plus the dense weight."""

    dense_weight: float = 0.5


def load_arm_a_config(path: str | Path = DEFAULT_V23_PATH) -> ArmAConfig:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    raw = doc["arm_a"]
    fields = ArmAConfig.__dataclass_fields__
    unknown = sorted(set(raw) - set(fields))
    if unknown:
        raise ValueError(f"events_v23.yaml: unknown arm_a keys {unknown}")
    return ArmAConfig(**{k: type(fields[k].default)(v) for k, v in raw.items()})


def load_arm_b_config(path: str | Path = DEFAULT_V23_PATH) -> ArmBConfig:
    """Arm A's section, overridden by the `arm_b` section (its cutoff, entity weight and dense weight)."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    merged = {**doc["arm_a"], **(doc.get("arm_b") or {})}
    fields = ArmBConfig.__dataclass_fields__
    unknown = sorted(set(merged) - set(fields))
    if unknown:
        raise ValueError(f"events_v23.yaml: unknown arm_b keys {unknown}")
    return ArmBConfig(**{k: type(fields[k].default)(v) for k, v in merged.items()})


def configured_arm(path: str | Path = DEFAULT_V23_PATH) -> str:
    """The `arm:` key of events_v23.yaml ("A" or "B"; "A" when absent)."""
    arm = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("arm", "A")
    if arm not in ("A", "B"):
        raise ValueError(f"events_v23.yaml: arm must be A or B, got {arm!r}")
    return str(arm)


# ----------------------------------------------------------------------------- text features


def char_ngrams(text: str, lo: int = 3, hi: int = 5) -> list[str]:
    """Character n-grams inside each word (the word padded with one space each side). Repeats are kept."""
    out: list[str] = []
    for word in _WORD.findall(text.casefold()):
        padded = f" {word} "
        for n in range(lo, hi + 1):
            out.extend(padded[k:k + n] for k in range(max(1, len(padded) - n + 1)))
    return out


def _normalise(vec: SparseVec) -> SparseVec:
    norm = math.sqrt(sum(x * x for x in vec.values()))
    return {t: x / norm for t, x in vec.items()} if norm > 0 else {}


def cosine(a: SparseVec, b: SparseVec) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


# ----------------------------------------------------------------------------- the background


@dataclass(frozen=True)
class Background:
    """Pre-pool statistics. `title_days` and `entity_days` are per-key counts of distinct calendar days."""

    title_days: dict[str, int]
    entity_days: dict[str, int]
    days: int                      # distinct calendar days that hold at least one background report
    reports: int
    start: datetime | None = None
    end: datetime | None = None
    flags: tuple[str, ...] = ()

    def title_idf(self, gram: str, offset: float) -> float:
        return math.log((self.days + 1) / (self.title_days.get(gram, 0) + 1)) + offset

    def entity_idf(self, key: str) -> float:
        return math.log((self.days + 1) / (self.entity_days.get(key, 0) + 1))


def make_background(
    reports: Iterable[tuple[date, str, Iterable[str]]], arm: ArmAConfig, *,
    start: datetime | None = None, end: datetime | None = None, flags: Sequence[str] = (),
) -> Background:
    """`reports`: (calendar day, canonical title, entity keys) of the syndication-collapsed background reports."""
    title_by_day: dict[date, set[str]] = {}
    entity_by_day: dict[date, set[str]] = {}
    n = 0
    for day, title, keys in reports:
        n += 1
        title_by_day.setdefault(day, set()).update(char_ngrams(title, arm.ngram_min, arm.ngram_max))
        entity_by_day.setdefault(day, set()).update(keys)
    title_days: Counter[str] = Counter()
    entity_days: Counter[str] = Counter()
    for grams in title_by_day.values():
        title_days.update(grams)
    for keys_ in entity_by_day.values():
        entity_days.update(keys_)
    days = len(set(title_by_day) | set(entity_by_day))
    return Background(dict(title_days), dict(entity_days), days, n, start, end, tuple(flags))


# ----------------------------------------------------------------------------- the linker


@dataclass
class _Scored:
    event_id: str
    score: float
    title_cos: float
    entity_cos: float
    shared_entities: list[tuple[str, float]]
    anchor_report_id: str
    gate_title: bool
    gate_entity: bool
    first_available_at: datetime = field(repr=False, default=datetime.min)
    dense_cos: float | None = None   # arm B only
    dense_used: bool = False         # True for an arm B score (the dense term is reported even when it is missing)

    @property
    def eligible(self) -> bool:
        return self.gate_title or self.gate_entity

    def evidence(self) -> dict[str, Any]:
        out = {"score": round(self.score, 6), "title_cosine": round(self.title_cos, 6),
               "entity_cosine": round(self.entity_cos, 6), "anchor": self.anchor_report_id,
               "shared_entities": [[k, round(w, 4)] for k, w in self.shared_entities],
               "title_floor_met": self.gate_title, "entity_floor_met": self.gate_entity}
        if self.dense_used:
            out["dense_cosine"] = None if self.dense_cos is None else round(self.dense_cos, 6)
        return out


class ArmALinker:
    """Pure apart from caches: `link` depends only on (report, open events, background, config)."""

    def __init__(self, arm: ArmAConfig, background: Background, open_days: int = 3,
                 cache: tuple[dict[Any, Any], dict[Any, Any]] | None = None) -> None:
        """`cache` = (title vectors, pair cosines) may be shared by linkers with the SAME background and n-gram
        settings (a tuning grid varies cutoffs and weights, not the vectors)."""
        self.arm = arm
        self.bg = background
        self.open_days = open_days
        self._tv, self._pair = cache if cache is not None else ({}, {})

    # -- vectors
    def title_vec(self, report_id: str, title: str) -> SparseVec:
        key = (report_id, title)
        vec = self._tv.get(key)
        if vec is None:
            counts = Counter(char_ngrams(title, self.arm.ngram_min, self.arm.ngram_max))
            vec = _normalise({g: (1.0 + math.log(c)) * self.bg.title_idf(g, self.arm.title_idf_offset)
                              for g, c in counts.items()})
            self._tv[key] = vec
        return vec

    def entity_vec(self, keys: Iterable[str]) -> SparseVec:
        weights = {k: self.bg.entity_idf(k) for k in keys}
        return _normalise({k: w for k, w in weights.items() if w > 0})

    # -- scoring
    def _title_cosine(self, report: Report, anchor: ReportRef) -> float:
        key = (report.report_id, report.title, anchor.report_id, anchor.title)
        out = self._pair.get(key)
        if out is None:
            out = cosine(self.title_vec(report.report_id, report.title), self.title_vec(anchor.report_id, anchor.title))
            self._pair[key] = out
        return out

    def _within_horizon(self, report: Report, ev: EventSummary) -> bool:
        return (report.first_seen_at.date() - ev.last_report_at.date()).days <= self.open_days

    def _dense_term(self, report: Report, anchor: ReportRef) -> tuple[float, float | None, bool]:
        """(score added, dense cosine, whether the term is reported). Arm A has none."""
        return 0.0, None, False

    def _arm_label(self) -> str:
        return "A"

    def _report_dense_missing(self, report: Report) -> bool:
        return False

    def score_event(self, report: Report, ev: EventSummary) -> _Scored | None:
        """Best anchor of `ev` for `report`, or None if the event has no anchor other than the report itself."""
        best: _Scored | None = None
        for anchor in ev.anchors[: self.arm.anchor_count]:
            if anchor.report_id == report.report_id:
                continue
            tcos = self._title_cosine(report, anchor)
            shared = sorted(((k, self.bg.entity_idf(k)) for k in report.entity_keys & anchor.entity_keys),
                            key=lambda kw: (-kw[1], kw[0]))
            ecos = cosine(self.entity_vec(report.entity_keys), self.entity_vec(anchor.entity_keys)) if shared else 0.0
            extra, dense, used = self._dense_term(report, anchor)
            cand = _Scored(ev.event_id, tcos + self.arm.entity_weight * ecos + extra, tcos, ecos, shared,
                           anchor.report_id, tcos >= self.arm.title_cosine_floor,
                           any(w >= self.arm.entity_idf_floor for _, w in shared), ev.first_available_at, dense, used)
            if best is None or (cand.score, cand.anchor_report_id) > (best.score, best.anchor_report_id):
                best = cand
        return best

    def link(self, report: Report, open_events: Sequence[EventSummary]) -> LinkDecision:
        scored = [s for s in (self.score_event(report, ev) for ev in open_events if self._within_horizon(report, ev))
                  if s is not None]
        # best first; ties go to the older event, then the id: deterministic
        scored.sort(key=lambda s: (-s.score, s.first_available_at, s.event_id))
        eligible = [s for s in scored if s.score >= self.arm.cutoff and s.eligible]
        eligible_ids = {s.event_id for s in eligible}
        near_floor = self.arm.near_miss_fraction * self.arm.cutoff
        flags = list(self.bg.flags)
        if not report.entity_keys:
            flags.append("no_entities")
        if self._report_dense_missing(report) or (scored and scored[0].dense_used and scored[0].dense_cos is None):
            flags.append("dense_missing")
        base: dict[str, Any] = {"arm": self._arm_label(), "cutoff": self.arm.cutoff, "flags": flags}
        near = [ProposedLink(s.event_id, {"kind": "near_miss", **s.evidence()})
                for s in scored if s.event_id not in eligible_ids and s.score >= near_floor]
        if not eligible:
            best_seen = scored[0].evidence() if scored else None
            return LinkDecision(None, {**base, "decision": "new_candidate", "best_below_cutoff": best_seen},
                                tuple(near[:3]))
        best = eligible[0]
        others = [s for s in eligible[1:] if best.score - s.score <= self.arm.ambiguity_margin]
        props = [ProposedLink(s.event_id, {"kind": "ambiguous_candidate", **s.evidence()}) for s in others]
        evidence = {**base, "decision": "link", "ambiguous": bool(others), **best.evidence(),
                    "ambiguous_with": [s.event_id for s in others]}
        return LinkDecision(best.event_id, evidence, tuple([*props, *near][:5]))


class ArmBLinker(ArmALinker):
    """Arm A plus the cosine of stored dense title vectors (see the module docstring). `vectors` maps a report id (the
    news id of its earliest member, which is also the id of the embedded title) to its L2-normalised vector."""

    def __init__(self, arm: ArmBConfig, background: Background, vectors: Mapping[str, Any], open_days: int = 3,
                 cache: tuple[dict[Any, Any], dict[Any, Any]] | None = None) -> None:
        super().__init__(arm, background, open_days, cache)
        self.vectors = vectors

    def _arm_label(self) -> str:
        return "B"

    def _report_dense_missing(self, report: Report) -> bool:
        return report.report_id not in self.vectors

    def _dense_term(self, report: Report, anchor: ReportRef) -> tuple[float, float | None, bool]:
        dense = dense_cosine(self.vectors.get(report.report_id), self.vectors.get(anchor.report_id))
        weight = self.arm.dense_weight  # type: ignore[attr-defined]
        return (0.0 if dense is None else weight * dense), dense, True
