"""Validator for the frozen events specification (MRI_EVENTS_PLAN v2 section 9 step 2).

Checks the plan invariants of config/events.yaml and config/event_theme_map.yaml. Pure checks: no E1/E2/E3 logic.

    python scripts/validate_events_spec.py [--db data/macro_engine.duckdb]

Exits 0 when clean, 1 with one line per violation. With --db, instruments marked `present` must have a series
by that id in the named table (read-only); without it the store is not consulted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EVENTS = REPO_ROOT / "config" / "events.yaml"
THEME_MAP = REPO_ROOT / "config" / "event_theme_map.yaml"
THEMES = REPO_ROOT / "config" / "news_themes.yaml"

# Every number the plan fixes, transcribed: dotted path under the file root -> expected value.
EXPECTED_PARAMS: dict[str, Any] = {
    "e1.pool_window_days": 7,
    "e1.syndication.title_jaccard_min": 0.6,
    "e1.link.title_jaccard_min": 0.3,
    "e1.link.unclassified_title_jaccard_min": 0.4,
    "e1.link.entity_relevance_min": 0.5,
    "e1.link.horizon_hours": 72,
    "e1.new_development.title_jaccard_below": 0.2,
    "e1.corroboration.min_distinct_publishers": 2,
    "e1.corroboration.official_source_corroborates": True,
    "e1.corroboration.rejection_hours": 72,
    "e2.primary_window_sessions": 2,
    "e2.info_window_sessions": [1, 3],
    "e2.robust_sd.mad_scale": 1.4826,
    "e2.robust_sd.baseline_sessions": 504,
    "e2.robust_sd.min_observations": 250,
    "e2.z_threshold": 2,
    "e2.p_ref_rule": "last_session_close_at_or_before_onset_instant",  # plan erratum v2.1
    "e2.p_ref_close_time_local": "16:00",
    "e2.p_ref_timezone": "America/New_York",
    "e2.response_current_fraction": 0.5,
    "e2.company_specific.window_sessions": 2,
    "e3.active_to_decaying_quiet_trading_days": 5,
    "e3.decaying_to_closed_quiet_trading_days": 10,
    "e3.sunset_calendar_days": 60,
    "exposure.sector_min_confidence": 0.6,
    "exposure.sector_min_abs_impact": 0.3,
}
MAIN_BLOCKS = ("e1", "e2", "e3", "exposure")
EVENTS_VERSION = 3
STOP_LIST_REF = "config/entity_aliases.yaml#generic_entities"

# Golden copy of the plan section 6 theme table: theme -> (primary, secondary), each (family, instrument, expected_sign),
# or "display_only". Transcribed from the plan, independent of config/event_theme_map.yaml, so that a changed sign or a
# swapped family fails the validator.
_P = "primary"
GOLDEN_THEMES: dict[str, Any] = {
    "monetary_tightening": {_P: ("rates", "DGS2", 1), "secondary": ("equity", "SPY", -1)},
    "monetary_easing": {_P: ("rates", "DGS2", -1), "secondary": ("equity", "SPY", 1)},
    "inflation_pressure": {_P: ("breakeven", "T10YIE", 1), "secondary": ("rates", "DGS2", 1)},
    "disinflation": {_P: ("breakeven", "T10YIE", -1), "secondary": ("rates", "DGS2", -1)},
    "yield_curve_steepening": {_P: ("curve", "T10Y2Y", 1), "secondary": None},
    "yield_curve_flattening": {_P: ("curve", "T10Y2Y", -1), "secondary": None},
    "credit_stress": {_P: ("credit", "BAMLH0A0HYM2", 1), "secondary": ("vol", "VIXCLS", 1), "same_family": ["BAA10Y"]},
    "credit_easing": {_P: ("credit", "BAMLH0A0HYM2", -1), "secondary": None},
    "energy_supply_shock": {_P: ("oil", "WTI", 1), "secondary": ("energy_equity", "XLE", 1)},
    "geopolitical_risk": {
        _P: ("vol", "VIXCLS", 1),
        "secondary": None,
        "co_tag": ("oil", "WTI", 1, "energy_supply_shock"),
    },
    "financial_stability_risk": {_P: ("vol", "VIXCLS", 1), "secondary": ("banks", "KBE", -1)},
    "growth_acceleration": "display_only",
    "growth_slowdown": "display_only",
    "labor_strength": "display_only",
    "labor_weakness": "display_only",
    "fiscal_expansion": "display_only",
    "fiscal_drag": "display_only",
    "commodity_pressure": "display_only",
}
N_THEMES = 18
N_DISPLAY_ONLY = 7


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe loader that rejects a repeated mapping key (PyYAML silently keeps the last one)."""


def _no_dup_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup_mapping)


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass


def _all_keys(node: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(str(k))
            out |= _all_keys(v)
    elif isinstance(node, list):
        for v in node:
            out |= _all_keys(v)
    return out


def _walk_params(node: Any, path: str):
    """Yield (path, mapping) for every parameter mapping, i.e. one carrying a `plan_ref` or a `value` key."""
    if isinstance(node, dict):
        if "value" in node or "plan_ref" in node:
            yield path, node
            return
        for k, v in node.items():
            yield from _walk_params(v, f"{path}.{k}" if path else str(k))


def check_events(events: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if events.get("version") != EVENTS_VERSION:
        errs.append(f"events.yaml: version must be {EVENTS_VERSION}")
    if "frozen_at" not in events:
        errs.append("events.yaml: frozen_at missing")
    for block in MAIN_BLOCKS:
        if not isinstance(events.get(block), dict):
            errs.append(f"events.yaml: block {block!r} missing")
    for block in MAIN_BLOCKS:
        for path, param in _walk_params(events.get(block) or {}, block):
            if param.get("value") is None and "value" in param:
                errs.append(f"events.yaml: {path}.value is null")
            if "value" not in param:
                errs.append(f"events.yaml: {path} has no value")
            if not param.get("plan_ref"):
                errs.append(f"events.yaml: {path} has no plan_ref")
    for path, expected in EXPECTED_PARAMS.items():
        node: Any = events
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        got = node.get("value") if isinstance(node, dict) else None
        if got is None:
            errs.append(f"events.yaml: {path} missing")
        elif got != expected or isinstance(got, bool) != isinstance(expected, bool):
            errs.append(f"events.yaml: {path} = {got!r}, plan says {expected!r}")
    errs += check_stop_list_reference(events)
    domains = ((events.get("e1") or {}).get("corroboration") or {}).get("official_source_domains") or {}
    if domains.get("status") != "UNRATIFIED":
        errs.append("events.yaml: e1.corroboration.official_source_domains must be marked UNRATIFIED")
    if not domains.get("value"):
        errs.append("events.yaml: e1.corroboration.official_source_domains has no domains")
    unratified = events.get("unratified")
    if not isinstance(unratified, dict) or not unratified:
        errs.append("events.yaml: unratified block missing or empty")
    else:
        for name, item in unratified.items():
            if not isinstance(item, dict) or not item.get("rationale"):
                errs.append(f"events.yaml: unratified.{name} has no rationale")
        main_keys: set[str] = set()
        for block in MAIN_BLOCKS:
            main_keys |= _all_keys(events.get(block))
        for name in unratified:
            if name in main_keys:
                errs.append(f"events.yaml: unratified key {name!r} is also used by name in a main block")
    return errs


def check_stop_list_reference(events: dict[str, Any], aliases_path: Path | None = None) -> list[str]:
    """Plan 5.4's generic-entity stop list is referenced from events.yaml and lives, once, in entity_aliases.yaml."""
    link = ((events.get("e1") or {}).get("link") or {})
    param = link.get("generic_entity_stop_list")
    if not isinstance(param, dict) or param.get("value") != STOP_LIST_REF:
        return [f"events.yaml: e1.link.generic_entity_stop_list must point at {STOP_LIST_REF!r}"]
    path, _, key = STOP_LIST_REF.partition("#")
    aliases = load_yaml(aliases_path or REPO_ROOT / path)
    stop = aliases.get(key)
    if not isinstance(stop, list) or not stop:
        return [f"{path}: {key} missing or empty"]
    return []


def check_golden_themes(theme_map: dict[str, Any]) -> list[str]:
    """Compare the theme map with the golden copy of the plan section 6 table (families, instruments, signs)."""
    errs: list[str] = []
    themes = theme_map.get("themes") or {}

    def leg_tuple(leg: Any) -> Any:
        if not leg:
            return None
        return (leg.get("family"), leg.get("instrument"), leg.get("expected_sign"))

    for name, gold in GOLDEN_THEMES.items():
        theme = themes.get(name)
        if theme is None:
            continue  # a missing theme is reported by check_theme_map
        if gold == "display_only":
            if theme.get("kind") != "display_only":
                errs.append(f"theme map: {name} must be display_only (plan section 6)")
            continue
        if theme.get("kind") != "market_checkable":
            errs.append(f"theme map: {name} must be market_checkable (plan section 6)")
            continue
        for slot in ("primary", "secondary"):
            if leg_tuple(theme.get(slot)) != gold[slot]:
                errs.append(f"theme map: {name}.{slot} = {leg_tuple(theme.get(slot))!r}, plan section 6 says {gold[slot]!r}")
        if gold.get("same_family") is not None and sorted((theme.get("primary") or {}).get("same_family_instruments") or []) != sorted(gold["same_family"]):
            errs.append(f"theme map: {name}.primary same_family_instruments differ from plan section 6 {gold['same_family']!r}")
        co = theme.get("co_tag_rule")
        if "co_tag" in gold:
            got = (co.get("family"), co.get("instrument"), co.get("expected_sign"), co.get("scored_under")) if co else None
            if got != gold["co_tag"]:
                errs.append(f"theme map: {name}.co_tag_rule = {got!r}, plan section 6 says {gold['co_tag']!r}")
        elif co:
            errs.append(f"theme map: {name} has a co_tag_rule the plan does not")
    return errs


def check_theme_map(
    theme_map: dict[str, Any],
    theme_ids: list[str],
    store_series: dict[str, set[str]] | None = None,
    unratified_names: set[str] | None = None,
) -> list[str]:
    errs: list[str] = []
    if len(theme_ids) != N_THEMES or len(set(theme_ids)) != len(theme_ids):
        errs.append(f"news_themes.yaml: expected {N_THEMES} distinct macro_themes, found {len(theme_ids)}")
    themes = theme_map.get("themes") or {}
    instruments = theme_map.get("instruments") or {}
    if set(themes) != set(theme_ids):
        errs.append(
            f"theme map: themes differ from news_themes.yaml: missing {sorted(set(theme_ids) - set(themes))}, "
            f"extra {sorted(set(themes) - set(theme_ids))}"
        )
    errs += check_golden_themes(theme_map)
    kinds = [t.get("kind") for t in themes.values()]
    if any(k not in ("market_checkable", "display_only") for k in kinds):
        errs.append("theme map: every theme kind must be market_checkable or display_only")
    if kinds.count("display_only") != N_DISPLAY_ONLY:
        errs.append(f"theme map: expected {N_DISPLAY_ONLY} display_only themes, found {kinds.count('display_only')}")

    def check_leg(where: str, leg: dict[str, Any]) -> None:
        inst = instruments.get(leg.get("instrument"))
        if inst is None:
            errs.append(f"theme map: {where} instrument {leg.get('instrument')!r} not in instruments")
            return
        if leg.get("family") != inst.get("family"):
            errs.append(f"theme map: {where} family {leg.get('family')!r} != instrument family {inst.get('family')!r}")
        if leg.get("expected_sign") not in (1, -1):
            errs.append(f"theme map: {where} expected_sign must be 1 or -1")
        for extra in leg.get("same_family_instruments") or []:
            if (instruments.get(extra) or {}).get("family") != leg.get("family"):
                errs.append(f"theme map: {where} same_family instrument {extra!r} is not in family {leg.get('family')!r}")

    for name, theme in themes.items():
        if theme.get("kind") != "market_checkable":
            continue
        primary = theme.get("primary")
        if not primary:
            errs.append(f"theme map: {name} has no primary")
            continue
        check_leg(f"{name}.primary", primary)
        secondary = theme.get("secondary")
        if secondary:
            check_leg(f"{name}.secondary", secondary)
            if secondary.get("family") == primary.get("family"):
                errs.append(f"theme map: {name} primary and secondary share family {primary.get('family')!r}")
        co = theme.get("co_tag_rule")
        if co:
            check_leg(f"{name}.co_tag_rule", co)
            if co.get("family") == primary.get("family"):
                errs.append(f"theme map: {name} co_tag family equals its primary family")
            target = themes.get(co.get("scored_under"))
            if target is None or co.get("when_cotagged") != co.get("scored_under"):
                errs.append(f"theme map: {name} co_tag_rule must score under the theme it is co-tagged with")
            elif (target.get("primary") or {}).get("instrument") != co.get("instrument"):
                errs.append(f"theme map: {name} co_tag instrument is not the primary of {co.get('scored_under')}")
    fam_of_id: dict[tuple[str, str], str] = {}
    for key, inst in instruments.items():
        ident = (str(inst.get("store")), str(inst.get("id")))
        if ident in fam_of_id and fam_of_id[ident] != inst.get("family"):
            errs.append(f"theme map: stored series {ident} appears in two families")
        fam_of_id[ident] = inst.get("family")
        if inst.get("transform") not in ("change_bp", "log_return_pct", "log_change_pct"):
            errs.append(f"theme map: instrument {key} has no valid transform")
        if inst.get("status") not in ("present", "pending"):
            errs.append(f"theme map: instrument {key} status must be present or pending")
        if inst.get("transform") == "change_bp" and not inst.get("bp_factor"):
            errs.append(f"theme map: instrument {key} is in bp but has no bp_factor")
        if store_series is not None and inst.get("status") == "present":
            if str(inst.get("id")) not in store_series.get(str(inst.get("store")), set()):
                errs.append(f"theme map: instrument {key} ({ident[0]}.{ident[1]}) not found in the store")
    used = set()
    for theme in themes.values():
        for leg in (theme.get("primary"), theme.get("secondary"), theme.get("co_tag_rule")):
            if leg:
                used.add(leg.get("instrument"))
    for key in instruments:
        if key not in used and (instruments[key].get("role") is None):
            errs.append(f"theme map: instrument {key} is never used by a theme")
    cs = theme_map.get("company_specific") or {}
    if cs.get("recorded_as") != "equity_reaction_only" or cs.get("bond_data") != "unavailable":
        errs.append("theme map: company_specific must record equity_reaction_only with bond_data unavailable")
    if cs.get("check") != "abnormal_return_vs_sector_etf":
        errs.append("theme map: company_specific check must be abnormal_return_vs_sector_etf")
    if unratified_names:
        clash = unratified_names & _all_keys(theme_map)
        for name in sorted(clash):
            errs.append(f"theme map: unratified key {name!r} is used by name in the theme map")
    return errs


def read_store_series(db_path: Path) -> dict[str, set[str]]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {
            table: {r[0] for r in con.execute(f"select distinct series_id from {table}").fetchall()}
            for table in ("raw_observations", "market_observations")
        }
    finally:
        con.close()


def validate(
    events_path: Path = EVENTS,
    theme_map_path: Path = THEME_MAP,
    themes_path: Path = THEMES,
    store_series: dict[str, set[str]] | None = None,
) -> list[str]:
    events = load_yaml(events_path)
    theme_map = load_yaml(theme_map_path)
    theme_ids = [t["theme_id"] for t in load_yaml(themes_path).get("macro_themes", [])]
    errs = check_events(events)
    errs += check_theme_map(theme_map, theme_ids, store_series, set((events.get("unratified") or {}).keys()))
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=None, help="DuckDB store to check `present` instruments against")
    args = ap.parse_args(argv)
    store = read_store_series(args.db) if args.db else None
    errs = validate(store_series=store)
    for e in errs:
        print(e)
    print("events spec: OK" if not errs else f"events spec: {len(errs)} violation(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
