"""E0.3 entity -> ticker resolver (MRI_EVENTS_PLAN v2 section 8).

Exact matching only. A classifier entity name is normalised (lower case, accents folded, punctuation to
spaces, leading "the" and trailing legal suffixes removed) and looked up in:

  1. `config/entity_aliases.yaml` aliases (curated)  -> tier `alias`
  2. the screener's stocks.json names and SEC company_tickers_exchange.json titles -> tier `exact`

Several distinct tickers for one normalised name is `ambiguous` and is rejected; nothing fuzzy is ever
tried. Only `exact` and `alias` resolutions are `named`. Everything else keeps its name and carries a status
that says why it has no ticker, so a downstream consumer can annotate instead of silently losing the entity.

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


@dataclass(frozen=True)
class Resolution:
    name: str
    normalized: str
    status: str  # named | ambiguous | unresolved | generic | skipped_type
    tier: str | None = None  # exact | alias, only when status == named
    ticker: str | None = None
    candidates: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    entity_type: str | None = None

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
        if len(tokens) > 1 and tokens[-1] in suffixes:
            tokens = tokens[:-1]
        elif len(tokens) > 2 and tokens[-2] == "class" and tokens[-1] in _CLASS_SHARE:
            tokens = tokens[:-2]
        else:
            break
    return " ".join(tokens)


@dataclass
class EntityResolver:
    index: dict[str, dict[str, set[str]]]  # normalized name -> source label -> tickers
    aliases: dict[str, str]  # normalized alias -> ticker
    legal_suffixes: tuple[str, ...]
    generic: frozenset[str]
    non_company_types: frozenset[str]
    universe: frozenset[str]
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
            return Resolution(status=NAMED_STATUS, tier="alias", ticker=ticker, candidates=(ticker,),
                              sources=("alias",), **base)
        by_source = self.index.get(norm)
        if not by_source:
            return Resolution(status=UNRESOLVED_STATUS, **base)
        candidates = tuple(sorted(set().union(*by_source.values())))
        sources = tuple(sorted(by_source))
        if len(candidates) > 1:
            return Resolution(status=AMBIGUOUS_STATUS, candidates=candidates, sources=sources, **base)
        return Resolution(status=NAMED_STATUS, tier="exact", ticker=candidates[0], candidates=candidates,
                          sources=sources, **base)

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
        return sorted({r.ticker for r in resolutions if r.is_named and r.ticker})


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


def _read_sec(path: Path) -> list[tuple[str, str]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    fields = doc["fields"]
    i_name, i_ticker = fields.index("name"), fields.index("ticker")
    return [(str(r[i_name]), str(r[i_ticker])) for r in doc["data"] if r[i_name] and r[i_ticker]]


def build_resolver(
    *,
    stocks: Iterable[tuple[str, str]],
    sec: Iterable[tuple[str, str]] = (),
    config: dict[str, Any] | None = None,
) -> EntityResolver:
    """Build a resolver from (name, ticker) pairs. Used directly by tests and by `load_resolver`."""
    cfg = config or load_entity_config()
    suffixes = tuple(cfg["legal_suffixes"])
    index: dict[str, dict[str, set[str]]] = {}
    universe: set[str] = set()
    for label, pairs in (("stocks.json", stocks), ("sec_company_tickers", sec)):
        for name, ticker in pairs:
            norm = normalize_name(name, suffixes)
            if not norm:
                continue
            index.setdefault(norm, {}).setdefault(label, set()).add(ticker)
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
        aliases=aliases,
        legal_suffixes=suffixes,
        generic=generic,
        non_company_types=frozenset(cfg["non_company_entity_types"]),
        universe=frozenset(universe),
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
