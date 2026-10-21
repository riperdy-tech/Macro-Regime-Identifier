"""E0.3 entity -> ticker resolver (MRI_EVENTS_PLAN v2 section 8).

Exact matching only. A classifier entity name is normalised (lower case, accents folded, punctuation to
spaces, leading "the" and trailing legal suffixes removed) and looked up in:

  1. `config/entity_aliases.yaml` aliases (curated nicknames / rebrands)  -> tier `alias`
  2. the screener's stocks.json names and SEC company_tickers_exchange.json titles -> tier `exact`

Ambiguity is per company, not per ticker. Candidate lines are grouped by SEC `cik`. A name belongs to the
source that owns it: an SEC title files under its SEC cik; a stocks.json name files under the cik SEC gives
that ticker only when the two sources' names for the ticker share a token, otherwise the ticker is marked
`name_conflict` (both names keep their own entity, the flag rides on the resolution, nothing is dropped). A
stocks.json line SEC does not know joins the cik group of the one SEC company with the same normalised name,
else it falls back to its issuer name with any trailing "Class X" removed. One company with several common
lines (BRK-A/BRK-B, GOOG/GOOGL) is ONE entity and resolves to all of them.

A line is `common` only if positively identified, and that decision is made per company (per resolution group),
never globally: a ticker that two sources file under two companies can be common in one and demoted in the other
(RCD is Ready Capital's stale SEC line and the Invesco ETF's own). The tests are: SEC lists it on an exchange (OTC
only when stocks.json also carries it) or, annotated `listing_unconfirmed:<T>`, SEC does not know it at all but
stocks.json gives it a market capitalisation above zero (a delisted or taken-private name must not vanish);
its ticker form is a common form (a single-letter class suffix, or a Nasdaq/OTC/unknown-exchange fifth letter that
is not a preferred/rights/unit/warrant marker); its stocks.json name carries no security-description word
(notes, ETN, depositary, units, zones, "%"...) unless SEC's own title for that ticker carries the same word (it is
then part of the company's name: Preferred Bank); and it does not lack a market capitalisation while a sibling line
of the same company has one (a line stocks.json lacks counts as having none). In a company where no line has a
market capitalisation that test cannot work, so two structural tests apply instead (a ticker that is a sibling
ticker plus a non-common letter; a name that extends a sibling's name) and the resolution carries
`market_cap_unknown` (plus `common_lines_unverified` when it still names several lines). Every other line is recorded in `other_lines` and is never named. A company with no
common line resolves `no_common_line`. Several distinct companies sharing a normalised name are `ambiguous`
and rejected; nothing fuzzy is ever tried. Only `exact` and `alias` resolutions are `named`. Everything else
keeps its name and carries a status that says why it has no ticker, so a downstream consumer can annotate
instead of silently losing the entity.

For E1: link on `tickers` (the company's common lines), never the single `ticker` (None for multi-line
companies), and link on `normalized` names as well as on named entities, since unresolved and ambiguous
entities still carry a stable normalised name.

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
NO_COMMON_STATUS = "no_common_line"

_CLASS_SHARE = {"a", "b", "c"}
# Hyphen/dot suffixes: a single letter is a share class (BRK-B) except the preferred/unit/warrant/rights markers.
_TICKER_SUFFIX = re.compile(r"[-.]([A-Z0-9]+)$")
_NON_CLASS_LETTERS = frozenset("PWUR")
# Nasdaq fifth-letter convention (also used by OTC): these mark a class/foreign/ADR/voting common line. The rest
# (preferred M-P, bonds G-I, rights R, units U, warrants W/T, when-issued V, funds X, beneficial S, Z) are not.
_FIFTH_LETTER_COMMON = frozenset("ABCDEFJKLQY")
_ORGANISED_EXCHANGES = frozenset({"nasdaq", "nyse", "cboe", "txse"})
# stocks.json names the security itself for non-common lines.
_NON_COMMON_NAME = re.compile(
    r"\b(preferred|depositary|warrants?|rights|units?|notes?|etns?|zones|series|debentures?|bonds?|subordinated)\b|%",
    re.IGNORECASE,
)
_NAME_STOP_TOKENS = frozenset({"and", "of", "the"})
# SEC EDGAR appends the state/country of incorporation to some titles ("WELLS FARGO & COMPANY/MN").
_SEC_TITLE_REGION = re.compile(r"\s*/[A-Za-z]{2,5}/?\s*$")


@dataclass(frozen=True)
class Resolution:
    name: str
    normalized: str
    status: str  # named | ambiguous | unresolved | generic | skipped_type | no_common_line
    tier: str | None = None  # exact | alias, only when status == named
    ticker: str | None = None  # set only when the company has exactly one named line
    candidates: tuple[str, ...] = ()  # every ticker considered (ambiguous: all competing companies)
    sources: tuple[str, ...] = ()
    entity_type: str | None = None
    tickers: tuple[str, ...] = ()  # named: the company's common lines
    other_lines: tuple[str, ...] = ()  # named: preferred/warrant/unit lines of the same issuer
    flags: tuple[str, ...] = ()  # annotations, e.g. name_conflict:RCD (the two sources name the ticker differently)

    @property
    def is_named(self) -> bool:
        return self.status == NAMED_STATUS


def normalize_name(name: str, legal_suffixes: Iterable[str]) -> str:
    suffixes = set(legal_suffixes)
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"['’]", "", text)  # "McDonald's" and SEC "MCDONALDS" are one name
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
    common_by_company: dict[str, frozenset[str]] = field(default_factory=dict)  # company key -> its common lines
    common_lines: frozenset[str] = frozenset()  # union of the above: common in at least one company
    unconfirmed: frozenset[str] = frozenset()  # common lines SEC does not know (stocks.json market cap > 0)
    cap_unknown: frozenset[str] = frozenset()  # companies in which no line has a known market capitalisation
    name_conflict: frozenset[str] = frozenset()  # tickers the two sources name with no token in common
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
        in_company = self.common_by_company.get(company, self.common_lines) if company else self.common_lines
        common = [t for t in lines if t in in_company]
        other = tuple(t for t in lines if t not in common)
        flags = tuple(f"name_conflict:{t}" for t in lines if t in self.name_conflict)
        flags += tuple(f"listing_unconfirmed:{t}" for t in common if t in self.unconfirmed)
        if company in self.cap_unknown:
            flags += ("market_cap_unknown",) + (("common_lines_unverified",) if len(common) > 1 else ())
        if not common:
            return Resolution(status=NO_COMMON_STATUS, sources=sources, other_lines=other, flags=flags, **base)
        return Resolution(status=NAMED_STATUS, tier=tier, ticker=common[0] if len(common) == 1 else None,
                          candidates=tuple(common), sources=sources, tickers=tuple(common), other_lines=other,
                          flags=flags, **base)

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


def _read_stocks(path: Path) -> list[tuple[Any, ...]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [(str(r["name"]), str(r["symbol"]), None, r.get("marketCap")) for r in rows if r.get("name") and r.get("symbol")]


def _read_sec(path: Path) -> list[tuple[Any, ...]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    fields = doc["fields"]
    i_name, i_ticker, i_cik = fields.index("name"), fields.index("ticker"), fields.index("cik")
    i_exch = fields.index("exchange") if "exchange" in fields else None
    return [(str(r[i_name]), str(r[i_ticker]), r[i_cik], r[i_exch] if i_exch is not None else None)
            for r in doc["data"] if r[i_name] and r[i_ticker]]


def _name_tokens(norm: str) -> set[str]:
    return {t for t in norm.split() if t not in _NAME_STOP_TOKENS}


def _common_form(ticker: str, exchange: str | None) -> bool:
    """True when the ticker's own shape is a common-stock shape (class suffix or Nasdaq fifth letter)."""
    suffix = _TICKER_SUFFIX.search(ticker)
    if suffix:
        s = suffix.group(1)
        return len(s) == 1 and s.isalpha() and s not in _NON_CLASS_LETTERS
    if len(ticker) == 5 and ticker.isalpha() and (exchange or "").casefold() in {"nasdaq", "otc", ""}:
        return ticker[-1] in _FIFTH_LETTER_COMMON
    return True


def _extends_name(ticker: str, other: str, stock_names: dict[str, list[str]], suffixes: Iterable[str]) -> bool:
    """True when every stocks.json name of `ticker` strictly extends, token for token, a name of `other`."""
    mine = [normalize_name(n, suffixes).split() for n in stock_names.get(ticker, ())]
    theirs = [normalize_name(n, suffixes).split() for n in stock_names.get(other, ())]
    return bool(mine and theirs) and all(
        any(len(m) > len(o) and m[: len(o)] == o for o in theirs) for m in mine
    )


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
    """Build a resolver from stocks rows (name, ticker[, cik[, market_cap]]) and SEC rows
    (name, ticker, cik[, exchange]). Used directly by tests and by `load_resolver`."""
    cfg = config or load_entity_config()
    suffixes = tuple(cfg["legal_suffixes"])
    stocks, sec = list(stocks), list(sec)

    index: dict[str, dict[str, set[str]]] = {}
    sources: dict[str, dict[str, set[str]]] = {}
    company_of: dict[str, str] = {}
    lines_of: dict[str, set[str]] = {}
    universe: set[str] = set()
    sec_exchange: dict[str, str | None] = {}
    sec_tokens: dict[str, set[str]] = {}
    sec_titles: dict[str, str] = {}
    stock_names: dict[str, list[str]] = {}
    market_cap: dict[str, float] = {}
    name_conflict: set[str] = set()

    def file(norm: str, company: str, ticker: str, label: str) -> None:
        lines_of.setdefault(company, set()).add(ticker)
        index.setdefault(norm, {}).setdefault(company, set()).add(ticker)
        sources.setdefault(norm, {}).setdefault(label, set()).add(ticker)
        universe.add(ticker)

    # SEC owns its titles: the cik it gives a ticker is the company for every SEC name of that ticker.
    for row in sec:
        name, ticker = _SEC_TITLE_REGION.sub("", row[0]), row[1]
        norm = normalize_name(name, suffixes)
        if not norm:
            continue
        cik = row[2] if len(row) > 2 else None
        company = company_of.setdefault(ticker, f"cik:{int(cik)}" if cik is not None else _issuer_key(name))
        exchange = row[3] if len(row) > 3 else None
        if sec_exchange.get(ticker) is None:
            sec_exchange[ticker] = exchange
        sec_tokens.setdefault(ticker, set()).update(_name_tokens(norm))
        sec_titles[ticker] = f"{sec_titles.get(ticker, '')} {name}".casefold()
        file(norm, company, ticker, "sec_company_tickers")

    stocks_rows = []
    for row in stocks:
        name, ticker = row[0], row[1]
        cap = row[3] if len(row) > 3 else None
        if cap is not None:
            market_cap[ticker] = float(cap)
        if name.strip().casefold() == ticker.casefold():
            continue  # a symbol in the name field is a missing name, not an entity name
        norm = normalize_name(name, suffixes)
        if norm:
            stocks_rows.append((name, ticker, row[2] if len(row) > 2 else None, norm))
            stock_names.setdefault(ticker, []).append(name)

    def cik_groups(norm: str) -> set[str]:
        return {c for c in index.get(norm, {}) if c.startswith("cik:")}

    # Lines SEC knows (or that carry their own cik) go first, so that a line SEC does not know can then join the
    # one company, whichever source named it, whose normalised name it shares (EDAP -> FOCL, ISSC -> IA).
    for known in (True, False):
        for name, ticker, cik, norm in stocks_rows:
            if (cik is not None or ticker in sec_tokens) != known:
                continue
            if cik is not None:
                company = company_of.setdefault(ticker, f"cik:{int(cik)}")
            elif ticker in sec_tokens:
                if _name_tokens(norm) & sec_tokens[ticker]:
                    company = company_of[ticker]
                else:  # the sources name this ticker differently: keep each name on its own entity, flag it
                    company = _issuer_key(name)
                    name_conflict.add(ticker)
            else:
                groups = cik_groups(norm)
                company = company_of.setdefault(ticker, next(iter(groups)) if len(groups) == 1 else _issuer_key(name))
            file(norm, company, ticker, "stocks.json")

    sec_known = bool(sec_exchange)

    def positive_line(ticker: str) -> tuple[bool, bool]:
        """(common-looking, listing unconfirmed): facts about one line that do not depend on its company."""
        exchange = sec_exchange.get(ticker)
        kind = (exchange or "").casefold()
        unconfirmed = False
        if not sec_known:
            listed = True
        elif ticker in sec_exchange:
            listed = kind in _ORGANISED_EXCHANGES or (kind == "otc" and ticker in stock_names)
        else:  # SEC does not know the ticker: annotate when stocks.json gives it a market cap, never gate
            cap = market_cap.get(ticker)
            listed = unconfirmed = ticker in stock_names and cap is not None and cap > 0
        title = sec_titles.get(ticker, "")
        described = any(  # a description word counts unless SEC's own title for the ticker carries it too
            m.group(0) == "%" or not re.search(rf"\b{re.escape(m.group(0).casefold())}\b", title)
            for n in stock_names.get(ticker, ()) for m in _NON_COMMON_NAME.finditer(n)
        )
        return bool(listed and _common_form(ticker, exchange) and not described), unconfirmed

    state = {t: positive_line(t) for t in universe}
    common_by_company: dict[str, frozenset[str]] = {}
    cap_unknown: set[str] = set()
    for company, lines in lines_of.items():
        positive = {t for t in lines if state[t][0]}
        if any(market_cap.get(t, 0.0) > 0 for t in lines):
            # a common line has a market capitalisation; a derivative line (ETN, preferred) of a company that has
            # one carries none, or is absent from stocks.json altogether
            keep = {t for t in positive if market_cap.get(t, 0.0) > 0}
        else:
            cap_unknown.add(company)
            keep = set(positive)
            for t in positive:
                if any(len(t) == len(u) + 1 and t.startswith(u) and t[-1].isalpha()
                       and t[-1] not in _FIFTH_LETTER_COMMON for u in positive):
                    keep.discard(t)  # a sibling ticker plus a preferred/unit/rights/warrant letter (PMT, PMTU/V/W)
                elif any(_extends_name(t, u, stock_names, suffixes) for u in positive if u != t):
                    keep.discard(t)  # its name is a sibling's name plus a description (TVC, TVE "PARRS A 2029")
        common_by_company[company] = frozenset(keep)
    common: set[str] = set().union(*common_by_company.values())
    unconfirmed = {t for t in common if state[t][1]}

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
        common_by_company=common_by_company,
        common_lines=frozenset(common),
        unconfirmed=frozenset(unconfirmed),
        cap_unknown=frozenset(cap_unknown),
        name_conflict=frozenset(name_conflict),
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
