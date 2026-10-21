"""Invariants of the frozen events specification, via scripts/validate_events_spec.py."""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_events_spec as v  # noqa: E402


def _ids():
    return [t["theme_id"] for t in v.load_yaml(v.THEMES)["macro_themes"]]


def _pieces():
    return v.load_yaml(v.EVENTS), v.load_yaml(v.THEME_MAP)


def _theme_errs(tm, events=None, store=None):
    events = events or v.load_yaml(v.EVENTS)
    return v.check_theme_map(tm, _ids(), store, set(events["unratified"]))


def test_committed_spec_is_valid():
    assert v.validate() == []


def test_theme_names_match_the_taxonomy_exactly():
    _, tm = _pieces()
    assert set(tm["themes"]) == set(_ids())
    assert len(_ids()) == 18
    kinds = [t["kind"] for t in tm["themes"].values()]
    assert kinds.count("display_only") == 7 and kinds.count("market_checkable") == 11


def test_a_missing_theme_is_caught():
    _, tm = _pieces()
    del tm["themes"]["credit_easing"]
    assert any("credit_easing" in e for e in _theme_errs(tm))


def test_duplicate_theme_key_is_caught(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("themes:\n  a: {kind: display_only}\n  a: {kind: display_only}\n", encoding="utf-8")
    with pytest.raises(yaml.constructor.ConstructorError):
        v.load_yaml(p)


def test_shared_family_between_primary_and_secondary_is_caught():
    _, tm = _pieces()
    tm["themes"]["monetary_tightening"]["secondary"] = {"family": "rates", "instrument": "DGS2", "expected_sign": -1}
    assert any("share family" in e for e in _theme_errs(tm))


def test_instrument_family_mismatch_and_bad_sign_are_caught():
    _, tm = _pieces()
    tm["themes"]["disinflation"]["primary"]["family"] = "rates"
    tm["themes"]["inflation_pressure"]["secondary"]["expected_sign"] = 0
    errs = _theme_errs(tm)
    assert any("disinflation.primary family" in e for e in errs)
    assert any("inflation_pressure.secondary expected_sign" in e for e in errs)


def test_a_changed_plan_number_is_caught():
    ev, _ = _pieces()
    bad = copy.deepcopy(ev)
    bad["e2"]["z_threshold"]["value"] = 3
    assert any("e2.z_threshold" in e for e in v.check_events(bad))


def test_zero_is_a_value_not_an_absence():
    ev, _ = _pieces()
    bad = copy.deepcopy(ev)
    bad["e3"]["sunset_calendar_days"]["value"] = 0
    errs = v.check_events(bad)
    assert any("sunset_calendar_days = 0" in e for e in errs)
    assert not any("sunset_calendar_days missing" in e for e in errs)


def test_missing_plan_ref_is_caught():
    ev, _ = _pieces()
    bad = copy.deepcopy(ev)
    del bad["e1"]["link"]["horizon_hours"]["plan_ref"]
    assert any("horizon_hours has no plan_ref" in e for e in v.check_events(bad))


def test_unratified_key_used_in_main_block_is_caught():
    ev, _ = _pieces()
    bad = copy.deepcopy(ev)
    bad["e2"]["mad_zero"] = {"value": 1, "plan_ref": "§6"}
    assert any("mad_zero" in e for e in v.check_events(bad))


def test_unratified_key_used_in_theme_map_is_caught():
    ev, tm = _pieces()
    tm["notes"] = {"wti_series": 1}
    assert any("wti_series" in e for e in _theme_errs(tm, ev))


def test_official_domains_must_stay_unratified():
    ev, _ = _pieces()
    bad = copy.deepcopy(ev)
    bad["e1"]["corroboration"]["official_source_domains"]["status"] = "RATIFIED"
    assert any("UNRATIFIED" in e for e in v.check_events(bad))


def test_present_instrument_must_exist_in_the_store_but_pending_may_not():
    _, tm = _pieces()
    store = {
        "raw_observations": {"DGS2", "T10YIE", "T10Y2Y", "BAMLH0A0HYM2", "BAA10Y", "VIXCLS", "DCOILWTICO"},
        "market_observations": {"SPY", "XLE", "KBE"},
    }
    assert _theme_errs(tm, store=store) == []
    store["market_observations"].discard("KBE")
    assert any("KBE" in e for e in _theme_errs(tm, store=store))
    tm["instruments"]["KBE"]["status"] = "pending"
    assert _theme_errs(tm, store=store) == []


def test_cotag_rule_scores_under_energy_not_twice():
    _, tm = _pieces()
    tm["themes"]["geopolitical_risk"]["co_tag_rule"]["scored_under"] = "geopolitical_risk"
    assert any("co_tag" in e for e in _theme_errs(tm))


def test_company_specific_never_claims_bond_data():
    _, tm = _pieces()
    tm["company_specific"]["bond_data"] = "available"
    assert any("company_specific" in e for e in _theme_errs(tm))


@pytest.mark.skipif(os.environ.get("MRI_STORE_CHECKS") != "1", reason="reads the live store")
def test_instruments_exist_in_the_live_store():
    db = Path(__file__).resolve().parent.parent / "data" / "macro_engine.duckdb"
    assert v.validate(store_series=v.read_store_series(db)) == []


# --- reference-set v1.1 fixes (C1, C4, C5) ---


def test_flipping_the_monetary_tightening_primary_sign_fails():
    _, tm = _pieces()
    tm["themes"]["monetary_tightening"]["primary"]["expected_sign"] = -1
    errs = _theme_errs(tm)
    assert any("monetary_tightening.primary" in e and "plan section 6" in e for e in errs)


def test_swapping_growth_slowdown_with_yield_curve_flattening_fails():
    _, tm = _pieces()
    t = tm["themes"]
    t["growth_slowdown"], t["yield_curve_flattening"] = t["yield_curve_flattening"], t["growth_slowdown"]
    errs = _theme_errs(tm)
    assert any("growth_slowdown must be display_only" in e for e in errs)
    assert any("yield_curve_flattening must be market_checkable" in e for e in errs)


def test_swapping_two_market_checkable_themes_fails():
    _, tm = _pieces()
    t = tm["themes"]
    t["monetary_easing"], t["monetary_tightening"] = t["monetary_tightening"], t["monetary_easing"]
    assert any("monetary_easing.primary" in e for e in _theme_errs(tm))


def test_changing_a_secondary_family_or_the_cotag_fails():
    _, tm = _pieces()
    tm["themes"]["financial_stability_risk"]["secondary"] = None
    tm["themes"]["geopolitical_risk"]["co_tag_rule"]["expected_sign"] = -1
    errs = _theme_errs(tm)
    assert any("financial_stability_risk.secondary" in e for e in errs)
    assert any("geopolitical_risk.co_tag_rule" in e for e in errs)


def test_stop_list_is_referenced_not_copied_and_must_exist():
    ev, _ = _pieces()
    assert ev["e1"]["link"]["generic_entity_stop_list"]["value"] == "config/entity_aliases.yaml#generic_entities"
    assert v.check_stop_list_reference(ev) == []
    bad = copy.deepcopy(ev)
    del bad["e1"]["link"]["generic_entity_stop_list"]
    assert any("generic_entity_stop_list" in e for e in v.check_events(bad))
    copied = copy.deepcopy(ev)
    copied["e1"]["link"]["generic_entity_stop_list"]["value"] = ["united states"]
    assert any("generic_entity_stop_list" in e for e in v.check_events(copied))


def test_stop_list_missing_from_the_aliases_file_fails(tmp_path):
    ev, _ = _pieces()
    p = tmp_path / "entity_aliases.yaml"
    p.write_text("other: [a]\n", encoding="utf-8")
    assert any("generic_entities" in e for e in v.check_stop_list_reference(ev, p))


def test_events_version_and_p_ref_erratum_are_pinned():
    ev, _ = _pieces()
    assert ev["version"] == 3
    bad = copy.deepcopy(ev)
    bad["version"] = 1
    assert any("version must be 3" in e for e in v.check_events(bad))
    old = copy.deepcopy(ev)
    old["e2"]["p_ref_rule"]["value"] = "last_daily_observation_dated_before_onset_date_us_calendar"
    assert any("e2.p_ref_rule" in e for e in v.check_events(old))
    tz = copy.deepcopy(ev)
    tz["e2"]["p_ref_close_time_local"]["value"] = "15:00"
    assert any("p_ref_close_time_local" in e for e in v.check_events(tz))
