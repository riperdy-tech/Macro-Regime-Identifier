"""E0.3 entity -> ticker resolver (MRI_EVENTS_PLAN v2 section 8).

Exact matching only. A classifier entity name is normalised (lower case, accents folded, punctuation to
spaces, leading "the" and trailing legal suffixes removed) and looked up in:

  1. `config/entity_aliases.yaml` aliases (curated nicknames / rebrands)  -> tier `alias`
  2. the screener's stocks.json names and SEC company_tickers_exchange.json titles -> tier `exact`

Ambiguity is per company, not per ticker. Candidate lines are grouped by SEC `cik` (a stocks.json row whose
ticker has no SEC cik falls back to its issuer name with any trailing "Class X" removed). One company with
several common lines (BRK-A/BRK-B, GOOG/GOOGL) is ONE entity and resolves to all of them; its preferred,
warrant and unit lines are recorded in `other_lines` and never named while a common line exists. Several
distinct companies sharing a normalised name are `ambiguous` and rejected; nothing fuzzy is ever tried.
Only `exact` and `alias` resolutions are `named`. Everything else keeps its name and carries a status that
says why it has no ticker, so a downstream consumer can annotate instead of silently losing the entity.

The universe files are located through `peer_paths` (environment variable, then STOCKS_ROOT probe); this
module never downloads and never builds a path of its own.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from macro_engine import peer_paths

DEFAULT_ALIASES_PATH = peer_paths.REPO_ROOT / "config" / "entity_aliases.yaml"

NAMED_STATUS = "named"
AMBIGUOUS_STATUS = "ambiguous"
UNRESOLVED_STATUS = "unresolved"
GENERIC_STATUS = "generic"
SKIPPED_TYPE_STATUS = "skipped_type"

_CLASS_SHARE = {"a", "b", "c"}
# Non-common lines of an issuer: preferreds (MS-PK), warrants, units, rights.
_NON_COMMON_TICKER = re.compile(r"-(P[A-Z]{0,2}|W|WS|WT|WI|U|UN|R|RT)$")
# stocks.json names the security itself for non-common lines ("... Depositary Shares ... Preferred Stock").
_NON_COMMON_NAME = re.compile(r"\b(preferred|depositary|warrants?|rights|units?|notes?)\b", re.IGNORECASE)
# SEC EDGAR appends the state/country of incorporation to some titles ("WELLS FARGO & COMPANY/MN").
_SEC_TITLE_REGION = re.compile(r"\s*/[A-Za-z]{2,5}/?\s*$")


@dataclass(frozen=True)
class Resolution:
    name: str
    normalized: str
    status: str  # named | ambiguous | unresolved | generic | skipped_type
    tier: str | None = None  # exact | alias, only when status == named
    ticker: str | None = None  # set only when the company has exactly one named line
    candidates: tuple[str, ...] = ()  # every ticker considered (ambiguous: all competing companies)
    sources: tuple[str, ...] = ()
    entity_type: str | None = None
    tickers: tuple[str, ...] = ()  # named: the company's common lines
    other_lines: tuple[str, ...] = ()  # named: preferred/warrant/unit lines of the same issuer

    @property
    def is_named(self) -> bool:
        return self.status == NAMED_STATUS


def normalize_name(name: str, legal_suffixes: Iterable[str]) -> str:
    suffixes = set(legal_suffixes)
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("&", " and ")
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    while True:
        if len(tokens) > 1 and tokens[-1] == "and":
            tokens = tokens[:-1]  # "X & Co." -> "x and" once the suffix goes: drop the dangling connector
        elif len(tokens) > 1 and tokens[-1] in suffixes:
            tokens = tokens[:-1]
        elif len(tokens) > 2 and tokens[-2] == "class" and tokens[-1] in _CLASS_SHARE:
            tokens = tokens[:-2]
        else:
            break
    return " ".join(tokens)


@dataclass
class EntityResolver:
    index: dict[str, dict[str, set[str]]]  # normalized name -> company key -> tickers
    sources: dict[str, dict[str, set[str]]]  # normalized name -> source label -> tickers
    company_of: dict[str, str]  # ticker -> company key
    lines_of: dict[str, set[str]]  # company key -> every known ticker line
    aliases: dict[str, str]  # normalized alias -> ticker
    legal_suffixes: tuple[str, ...]
    generic: frozenset[str]
    non_company_types: frozenset[str]
    universe: frozenset[str]
    common_hint: frozenset[str] = frozenset()  # tickers listed in stocks.json (common stock)
    warnings: list[str] = field(default_factory=list)

    def resolve(self, name: str, entity_type: str | None = None) -> Resolution:
        norm = normalize_name(name, self.legal_suffixes)
        kind = (entity_type or "").strip().casefold() or None
        base = {"name": name, "normalized": norm, "entity_type": entity_type}
        if not norm:
            return Resolution(status=UNRESOLVED_STATUS, **base)
        if norm in self.generic:
            return Resolution(status=GENERIC_STATUS, **base)
        if kind in self.non_company_types:
            return Resolution(status=SKIPPED_TYPE_STATUS, **base)
        if norm in self.aliases:
            ticker = self.aliases[norm]
            return self._named(base, "alias", self.company_of.get(ticker), ("alias",), fallback=ticker)
        by_company = self.index.get(norm)
        if not by_company:
            return Resolution(status=UNRESOLVED_STATUS, **base)
        sources = tuple(sorted(self.sources.get(norm, {})))
        if len(by_company) > 1:
            candidates = tuple(sorted(set().union(*by_company.values())))
            return Resolution(status=AMBIGUOUS_STATUS, candidates=candidates, sources=sources, **base)
        (company,) = by_company
        return self._named(base, "exact", company, sources)

    def _named(self, base: dict[str, Any], tier: str, company: str | None, sources: tuple[str, ...],
               fallback: str | None = None) -> Resolution:
        lines = sorted(self.lines_of[company]) if company else [fallback]
        common = [t for t in lines if t in self.common_hint] or [t for t in lines if not _NON_COMMON_TICKER.search(t)]
        common = common or lines
        other = tuple(t for t in lines if t not in common)
        return Resolution(status=NAMED_STATUS, tier=tier, ticker=common[0] if len(common) == 1 else None,
                          candidates=tuple(common), sources=sources, tickers=tuple(common), other_lines=other,
                          **base)

    def resolve_entities(self, entities: Iterable[dict[str, Any]]) -> list[Resolution]:
        """Resolve classifier entities (`{"name", "entity_type", "relevance"}` dicts), order preserved."""
        out = []
        for ent in entities or []:
            name = ent.get("name")
            if not name:
                continue
            out.append(self.resolve(name, ent.get("entity_type")))
        return out

    @staticmethod
    def named_tickers(resolutions: Iterable[Resolution]) -> list[str]:
        return sorted({t for r in resolutions if r.is_named for t in r.tickers})


def load_entity_config(path: str | Path = DEFAULT_ALIASES_PATH) -> dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {
        "legal_suffixes": [str(s).casefold() for s in doc.get("legal_suffixes", [])],
        "aliases": {str(k): str(v) for k, v in (doc.get("aliases") or {}).items()},
        "generic_entities": [str(s) for s in doc.get("generic_entities", [])],
        "non_company_entity_types": [str(s).casefold() for s in doc.get("non_company_entity_types", [])],
    }


def _read_stocks(path: Path) -> list[tuple[str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [(str(r["name"]), str(r["symbol"])) for r in rows if r.get("name") and r.get("symbol")]


def _read_sec(path: Path) -> list[tuple[str, str, int]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    fields = doc["fields"]
    i_name, i_ticker, i_cik = fields.index("name"), fields.index("ticker"), fields.index("cik")
    return [(str(r[i_name]), str(r[i_ticker]), r[i_cik]) for r in doc["data"] if r[i_name] and r[i_ticker]]


def _issuer_key(name: str) -> str:
    """Company key for a line with no SEC cik: its name with any trailing "Class X" removed."""
    tokens = re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", name).casefold()).split()
    if len(tokens) > 2 and tokens[-2] == "class" and tokens[-1] in _CLASS_SHARE:
        tokens = tokens[:-2]
    return "issuer:" + " ".join(tokens)


def build_resolver(
    *,
    stocks: Iterable[tuple[Any, ...]],
    sec: Iterable[tuple[Any, ...]] = (),
    config: dict[str, Any] | None = None,
) -> EntityResolver:
    """Build a resolver from (name, ticker[, cik]) rows. Used directly by tests and by `load_resolver`.
    A cik defines the company; a ticker that has a cik in any source is keyed by it in every source."""
    cfg = config or load_entity_config()
    suffixes = tuple(cfg["legal_suffixes"])
    stocks, sec = list(stocks), list(sec)
    cik_of: dict[str, str] = {}
    for row in (*stocks, *sec):
        if len(row) > 2 and row[2] is not None:
            cik_of[row[1]] = f"cik:{int(row[2])}"

    index: dict[str, dict[str, set[str]]] = {}
    sources: dict[str, dict[str, set[str]]] = {}
    company_of: dict[str, str] = {}
    lines_of: dict[str, set[str]] = {}
    universe: set[str] = set()
    for label, rows in (("stocks.json", stocks), ("sec_company_tickers", sec)):
        for row in rows:
            name, ticker = row[0], row[1]
            if label == "sec_company_tickers":
                name = _SEC_TITLE_REGION.sub("", name)
            norm = normalize_name(name, suffixes)
            if not norm:
                continue
            company = company_of.setdefault(ticker, cik_of.get(ticker) or _issuer_key(name))
            lines_of.setdefault(company, set()).add(ticker)
            index.setdefault(norm, {}).setdefault(company, set()).add(ticker)
            sources.setdefault(norm, {}).setdefault(label, set()).add(ticker)
            universe.add(ticker)

    warnings: list[str] = []
    aliases: dict[str, str] = {}
    for raw, ticker in cfg["aliases"].items():
        if ticker not in universe:
            warnings.append(f"alias_ignored_unknown_ticker:{raw}->{ticker}")
            continue
        aliases[normalize_name(raw, suffixes)] = ticker
    generic = frozenset(normalize_name(g, suffixes) for g in cfg["generic_entities"])
    return EntityResolver(
        index=index,
        sources=sources,
        company_of=company_of,
        lines_of=lines_of,
        aliases=aliases,
        legal_suffixes=suffixes,
        generic=generic,
        non_company_types=frozenset(cfg["non_company_entity_types"]),
        universe=frozenset(universe),
        common_hint=frozenset(row[1] for row in stocks if not _NON_COMMON_NAME.search(row[0])),
        warnings=warnings,
    )


def load_resolver(
    *,
    stocks_path: str | Path | None = None,
    sec_path: str | Path | None = None,
    aliases_path: str | Path = DEFAULT_ALIASES_PATH,
) -> EntityResolver:
    """Resolver over the real universe. `stocks_path` defaults to `<screener data dir>/stocks.json` and
    `sec_path` to a local SEC copy, both through `peer_paths`; a missing stocks.json raises (naming every
    candidate tried), a missing SEC copy is allowed and recorded in `warnings`."""
    stocks_file = Path(stocks_path) if stocks_path else peer_paths.screener_data_dir(required=True) / "stocks.json"
    sec_file = Path(sec_path) if sec_path else peer_paths.sec_company_tickers_path()
    resolver = build_resolver(
        stocks=_read_stocks(stocks_file),
        sec=_read_sec(sec_file) if sec_file is not None and sec_file.exists() else (),
        config=load_entity_config(aliases_path),
    )
    if sec_file is None or not sec_file.exists():
        resolver.warnings.append("sec_company_tickers_unavailable")
    return resolver
