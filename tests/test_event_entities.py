"""E0.3 entity resolver: exact + alias only, ambiguous rejected, generic entities listed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from macro_engine import peer_paths
from macro_engine.events.entities import (
    AMBIGUOUS_STATUS,
    GENERIC_STATUS,
    NAMED_STATUS,
    SKIPPED_TYPE_STATUS,
    UNRESOLVED_STATUS,
    build_resolver,
    load_entity_config,
    load_resolver,
    normalize_name,
)

STOCKS = [
    ("CoreWeave, Inc.", "CRWV"),
    ("Dell Technologies Inc.", "DELL"),
    ("Alphabet Inc.", "GOOGL"),
    ("Alphabet Inc.", "GOOG"),
    ("Meta Platforms, Inc.", "META"),
    ("The Coca-Cola Company", "KO"),
    ("Amazon.com, Inc.", "AMZN"),
    ("Foo Holdings Class A", "FOOA"),  # no cik anywhere: issuer-name fallback groups the classes
    ("Foo Holdings Class B", "FOOB"),
    ("JPMorgan Chase & Co.", "JPM"),
    ("Morgan Stanley", "MS"),
    ("Bar Inc.", "BAR1"),  # two distinct companies, same normalised name
    ("Bar Corp", "BAR2"),
]
SEC = [
    ("CoreWeave, Inc.", "CRWV", 1769628, "Nasdaq"),  # same ticker from the second source: still ONE candidate
    ("AMAZON COM INC", "AMZN", 1018724, "Nasdaq"),
    ("Dell Technologies Inc.", "DELL", 1571996, "NYSE"),
    ("Alphabet Inc.", "GOOGL", 1652044, "Nasdaq"),
    ("Alphabet Inc.", "GOOG", 1652044, "Nasdaq"),
    ("Meta Platforms, Inc.", "META", 1326801, "Nasdaq"),
    ("COCA COLA CO", "KO", 21344, "NYSE"),
    ("Foo Holdings Class A", "FOOA", None, "NYSE"),
    ("Foo Holdings Class B", "FOOB", None, "NYSE"),
    ("JPMORGAN CHASE & CO", "JPM", 19617, "NYSE"),
    ("Bar Inc.", "BAR1", 10, "NYSE"),
    ("Bar Corp", "BAR2", 20, "NYSE"),
    ("Acme Corp", "ACME1", 41, "NYSE"),
    ("Acme Corporation", "ACME2", 42, "NYSE"),  # normalises to the same name as "Acme Corp": two companies
    ("MORGAN STANLEY", "MS-PK", 895421, "NYSE"),  # preferred line of the same issuer
    ("MORGAN STANLEY", "MS", 895421, "NYSE"),
    ("WELLS FARGO & COMPANY/MN", "WFC", 72971, "NYSE"),
    ("BERKSHIRE HATHAWAY INC", "BRK-B", 1067983, "NYSE"),
    ("BERKSHIRE HATHAWAY INC", "BRK-A", 1067983, "NYSE"),
    ("Quux Corp", "QUUX", 30, "NYSE"),
    ("Quux Corp", "QUUX-WT", 30, "NYSE"),
    ("Quux Corp", "QUUX-U", 30, "NYSE"),
]


@pytest.fixture(scope="module")
def resolver():
    return build_resolver(stocks=STOCKS, sec=SEC, config=load_entity_config())


def test_corewave_resolves_exactly_and_dual_source_is_one_candidate(resolver):
    r = resolver.resolve("CoreWeave", "company")
    assert (r.status, r.tier, r.ticker) == (NAMED_STATUS, "exact", "CRWV")
    assert r.sources == ("sec_company_tickers", "stocks.json")


def test_dell_technologies_keeps_technologies_and_strips_inc(resolver):
    assert resolver.resolve("Dell Technologies").ticker == "DELL"
    assert resolver.resolve("Dell").status == UNRESOLVED_STATUS  # exact only: no prefix guessing


def test_aramco_is_unresolved(resolver):
    for name in ("Aramco", "Saudi Aramco"):
        r = resolver.resolve(name, "company")
        assert r.status == UNRESOLVED_STATUS and r.ticker is None


def test_ambiguous_name_is_rejected_not_picked(resolver):
    r = resolver.resolve("Acme Corp", "company")
    assert r.status == AMBIGUOUS_STATUS and r.ticker is None
    assert r.candidates == ("ACME1", "ACME2")
    assert resolver.named_tickers([r]) == []


def test_share_classes_of_one_issuer_are_one_entity_named_as_a_set(resolver):
    r = resolver.resolve("Alphabet")  # same cik: GOOG and GOOGL, no alias needed
    assert (r.status, r.tier, r.ticker, r.tickers) == (NAMED_STATUS, "exact", None, ("GOOG", "GOOGL"))
    assert resolver.named_tickers([r]) == ["GOOG", "GOOGL"]
    b = resolver.resolve("Berkshire Hathaway")
    assert b.status == NAMED_STATUS and b.tickers == ("BRK-A", "BRK-B")
    f = resolver.resolve("Foo Holdings")  # no cik: issuer-name fallback with "Class X" removed
    assert f.status == NAMED_STATUS and f.tickers == ("FOOA", "FOOB")


def test_distinct_companies_sharing_a_normalised_name_stay_ambiguous(resolver):
    r = resolver.resolve("Bar")
    assert r.status == AMBIGUOUS_STATUS and r.candidates == ("BAR1", "BAR2") and r.tickers == ()


def test_preferred_warrant_and_unit_lines_are_recorded_not_named(resolver):
    ms = resolver.resolve("Morgan Stanley")
    assert (ms.status, ms.ticker, ms.tickers, ms.other_lines) == (NAMED_STATUS, "MS", ("MS",), ("MS-PK",))
    q = resolver.resolve("Quux")  # SEC only, no stocks.json line: pattern drops warrant and unit
    assert q.tickers == ("QUUX",) and q.other_lines == ("QUUX-U", "QUUX-WT")


def test_alias_is_tiered_alias_and_expands_to_the_company_lines(resolver):
    r = resolver.resolve("Google")
    assert (r.status, r.tier, r.tickers) == (NAMED_STATUS, "alias", ("GOOG", "GOOGL"))
    assert resolver.resolve("Meta").tier == "alias" and resolver.resolve("Meta").ticker == "META"


def test_ampersand_and_sec_region_suffix_normalise(resolver):
    assert normalize_name("JPMorgan Chase & Co.", load_entity_config()["legal_suffixes"]) == "jpmorgan chase"
    assert resolver.resolve("JPMorgan Chase").ticker == "JPM"
    assert resolver.resolve("Wells Fargo").ticker == "WFC"  # SEC "WELLS FARGO & COMPANY/MN"


def test_alias_to_unknown_ticker_is_ignored_and_reported():
    cfg = load_entity_config()
    cfg["aliases"] = {"mystery": "NOPE"}
    res = build_resolver(stocks=[("Real Inc", "REAL")], config=cfg)
    assert res.resolve("Mystery").status == UNRESOLVED_STATUS
    assert res.warnings == ["alias_ignored_unknown_ticker:mystery->NOPE"]


def test_punctuation_accents_case_the_and_sec_uppercase_titles(resolver):
    assert resolver.resolve("amazon.com").ticker == "AMZN"
    assert resolver.resolve("Coca-Cola").ticker == "KO"
    assert resolver.resolve("COCA COLA CO").ticker == "KO"
    assert normalize_name("Nestlé S.A.", ["inc"]) == "nestle s a"


def test_generic_entities_and_non_company_types_are_annotated_not_dropped(resolver):
    g = resolver.resolve("United States government", "other")
    assert g.status == GENERIC_STATUS and g.ticker is None
    assert resolver.resolve("U.S. Government").status == GENERIC_STATUS
    assert resolver.resolve("investors").status == GENERIC_STATUS
    for discriminating in ("Federal Reserve", "The Fed", "Treasury", "White House", "China", "European Union"):
        assert resolver.resolve(discriminating).status == UNRESOLVED_STATUS  # linkable by E1, not stop-listed
    c = resolver.resolve("Meta", "country")
    assert c.status == SKIPPED_TYPE_STATUS  # the type says it is not a company, the name is still recorded
    assert c.name == "Meta"


def test_resolve_entities_preserves_order_and_skips_nameless():
    res = build_resolver(stocks=STOCKS, sec=SEC, config=load_entity_config())
    out = res.resolve_entities(
        [{"name": "CoreWeave", "entity_type": "company"}, {"name": ""}, {"name": "Iran", "entity_type": "country"},
         {"name": "Dell Technologies", "entity_type": "company"}]
    )
    assert [r.status for r in out] == [NAMED_STATUS, SKIPPED_TYPE_STATUS, NAMED_STATUS]
    assert res.named_tickers(out) == ["CRWV", "DELL"]


def test_load_resolver_with_explicit_files_and_missing_sec_warns(tmp_path: Path):
    stocks = tmp_path / "stocks.json"
    stocks.write_text(json.dumps([{"symbol": "CRWV", "name": "CoreWeave, Inc."}]), encoding="utf-8")
    res = load_resolver(stocks_path=stocks, sec_path=tmp_path / "absent.json")
    assert res.resolve("CoreWeave").ticker == "CRWV"
    assert "sec_company_tickers_unavailable" in res.warnings

    sec = tmp_path / "sec.json"
    sec.write_text(json.dumps({"fields": ["cik", "name", "ticker", "exchange"],
                               "data": [[1, "Zed Corp", "ZED", "NYSE"]]}), encoding="utf-8")
    both = load_resolver(stocks_path=stocks, sec_path=sec)
    assert both.resolve("Zed").ticker == "ZED" and "sec_company_tickers_unavailable" not in both.warnings


def test_missing_stocks_json_raises_naming_candidates(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SCREENER_DATA_DIR", raising=False)
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="SCREENER_DATA_DIR"):
        load_resolver()


def test_sec_path_env_is_used_verbatim_and_probe_picks_newest(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SEC_COMPANY_TICKERS_PATH", str(tmp_path / "x.json"))
    assert peer_paths.sec_company_tickers_path() == tmp_path / "x.json"
    monkeypatch.delenv("SEC_COMPANY_TICKERS_PATH")
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))
    assert peer_paths.sec_company_tickers_path() is None
    for d in ("company_tickers_2026-10-01", "company_tickers_2026-10-10"):
        f = tmp_path / "stocks-workspace" / "data" / "sec_bulk" / d / "company_tickers_exchange.json"
        f.parent.mkdir(parents=True)
        f.write_text("{}", encoding="utf-8")
    assert "2026-10-10" in str(peer_paths.sec_company_tickers_path())


def _res(stocks, sec):
    return build_resolver(stocks=stocks, sec=sec, config=load_entity_config())


def test_non_common_lines_are_other_lines_by_structure_not_by_stocks_json_presence():
    sec = [("Acme Inc", t, 7, ex) for t, ex in (("ACME", "Nasdaq"), ("ACMEP", "Nasdaq"), ("ACMEW", "Nasdaq"),
                                                  ("ACMEU", "Nasdaq"), ("ACMEL", "Nasdaq"), ("ACMEB", "Nasdaq"),
                                                  ("ACMD", "NYSE"), ("ACME-B", "NYSE"), ("ACME-PA", "NYSE"),
                                                  ("ACMX", None), ("ACMO", "OTC"))]
    stocks = [("Acme Inc", t) for t in ("ACME", "ACMEP", "ACMEW", "ACMEU", "ACMEL", "ACMEB", "ACMD", "ACME-B",
                                         "ACME-PA", "ACMX")]
    r = _res(stocks, sec).resolve("Acme")
    # fifth letters P/W/U and the -PA suffix are not common; L/B and the -B class are; no exchange = unconfirmed;
    # an OTC line stocks.json does not carry is unconfirmed too
    assert set(r.tickers) == {"ACME", "ACME-B", "ACMEB", "ACMEL", "ACMD"}
    assert set(r.other_lines) == {"ACMEP", "ACMEW", "ACMEU", "ACME-PA", "ACMX", "ACMO"}


def test_security_description_words_and_a_missing_market_cap_demote_a_line():
    sec = [("Brite Financial Inc", t, 8, "Nasdaq") for t in ("BRT", "BRTX", "BRTY")] +           [("Comco Corp", t, 9, "NYSE") for t in ("COM", "COMZ")]
    stocks = [("Brite Financial, Inc.", "BRT", None, 3.0e9), ("Brite Financial, Inc.", "BRTX", None, 0),
              ("Brite Financial, Inc. 5.5% Fixed-Rate Notes due 2030", "BRTY", None, 3.0e9),
              ("Comcast Holdings ZONES", "COMZ", None, 5.0e9), ("Comco Corporation", "COM", None, 5.0e9)]
    res = _res(stocks, sec)
    b = res.resolve("Brite Financial")
    assert b.tickers == ("BRT",) and set(b.other_lines) == {"BRTX", "BRTY"}  # '%'/notes and a zero cap beside a capped sibling
    assert res.resolve("Comco").tickers == ("COM",) and "COMZ" in res.resolve("Comco").other_lines
    only = _res([("Solo Inc", "SOLO", None, 0)], [("Solo Inc", "SOLO", 5, "NYSE")]).resolve("Solo")
    assert only.tickers == ("SOLO",)  # 0.0 is a value, but with no capped sibling nothing is demoted


def test_company_with_no_positively_identified_common_line_is_annotated_not_named():
    r = _res([("Ghost Inc", "GHOST", None, 4.0e6)], [("Other Co", "OTH", 1, "NYSE")]).resolve("Ghost")
    assert r.status == "no_common_line" and r.tickers == () and r.other_lines == ("GHOST",)


def test_name_conflict_keeps_each_source_name_on_its_own_entity_and_flags_the_ticker():
    sec = [("Ready Capital Corp", "RC", 1527590, "NYSE"), ("Ready Capital Corp", "RCD", 1527590, "NYSE")]
    stocks = [("Ready Capital Corporation", "RC"),
              ("Invesco S&P 500 Equal Weight Consumer Discretionary ETF", "RCD")]
    res = _res(stocks, sec)
    rc, etf = res.resolve("Ready Capital"), res.resolve("Invesco S&P 500 Equal Weight Consumer Discretionary ETF")
    assert rc.tickers == ("RC", "RCD") and rc.flags == ("name_conflict:RCD",)
    assert etf.tickers == ("RCD",) and etf.flags == ("name_conflict:RCD",)  # not merged into Ready Capital
    assert res.resolve("McDonald's").status == UNRESOLVED_STATUS
    mc = _res([("McDonald's Corporation", "MCD")], [("MCDONALDS CORP", "MCD", 63908, "NYSE")])
    assert mc.resolve("McDonald's").ticker == "MCD" and mc.resolve("McDonald's").flags == ()  # agree: no conflict


def test_symbol_in_the_name_field_is_a_missing_name_not_an_entity_name():
    res = _res([("EM", "EM"), ("Real Inc", "REAL")], [("Real Inc", "REAL", 3, "NYSE")])
    assert res.resolve("EM").status == UNRESOLVED_STATUS


def test_stocks_only_line_joins_the_one_sec_company_with_the_same_name():
    sec = [("Rega Group of America Inc", "RGA", 5, "NYSE")]
    stocks = [("Rega Group of America, Incorporated", "RGA"), ("Rega Group of America, Incorporated", "RZB")]
    r = _res(stocks, sec).resolve("Rega Group of America")
    assert r.status == NAMED_STATUS and r.tickers == ("RGA",) and r.other_lines == ("RZB",)  # not ambiguous
    two = [("Dup Inc", "DUP1", 1, "NYSE"), ("Dup Corp", "DUP2", 2, "NYSE")]
    amb = _res([("Dup Incorporated", "DUPX")], two).resolve("Dup")
    assert amb.status == AMBIGUOUS_STATUS  # two SEC companies share the name: the stray line does not pick one


_REAL = peer_paths.screener_data_dir()
_REAL_OK = _REAL is not None and (_REAL / "stocks.json").exists()


@pytest.mark.skipif(not _REAL_OK, reason="screener stocks.json not reachable")
def test_required_cases_on_the_real_universe():
    res = load_resolver()
    assert res.warnings == []  # every shipped alias targets a ticker that exists
    assert res.resolve("CoreWeave", "company").ticker == "CRWV"
    assert res.resolve("Dell Technologies", "company").ticker == "DELL"
    assert res.resolve("Aramco", "company").status == UNRESOLVED_STATUS
    assert res.resolve("Berkshire Hathaway", "company").tickers == ("BRK-A", "BRK-B")
    assert res.resolve("Alphabet", "company").tickers == ("GOOG", "GOOGL")
    for name, ticker in (("Boeing", "BA"), ("Oracle", "ORCL"), ("Morgan Stanley", "MS"), ("Citigroup", "C"),
                         ("Ford", "F"), ("JPMorgan", "JPM"), ("JPMorgan Chase & Co.", "JPM")):
        assert res.resolve(name, "company").tickers == (ticker,), name
    for t in ("BHFAL", "BHFAP", "CCZ", "AFGB", "AFGE", "FMCCG", "RZB", "FTAIM", "LILAP"):
        assert t not in res.common_lines, t
    assert res.resolve("Brighthouse Financial", "company").tickers == ("BHF",)
    assert res.resolve("Comcast", "company").tickers == ("CMCSA",)
    assert res.resolve("Reinsurance Group of America", "company").tickers == ("RGA",)
    assert res.resolve("FTAI Aviation", "company").tickers == ("FTAI",)
    assert res.resolve("Ready Capital", "company").tickers == ("RC", "RCD")
    assert "name_conflict:RCD" in res.resolve("Ready Capital").flags
    assert res.resolve("Invesco S&P 500 Equal Weight Consumer Discretionary ETF").tickers == ("RCD",)
