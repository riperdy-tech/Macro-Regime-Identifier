#!/usr/bin/env python3
"""
MRI v0.2 — point-in-time vs calendar as-of: what would actually change?

`scoring_mode` decides what an evaluation date is allowed to have known:

  calendar_asof (default) — the latest observation on or before the date, minus a fixed
                            per-series `publication_lag_days` approximation.
  point_in_time           — the value ALFRED says was ACTUALLY published on that date.

Point-in-time is strictly stronger evidence, but only where vintages exist. ALFRED's archive
depth is not uniform: the CPI/PAYEMS block reaches 1990, DGS10 starts 2005-07, the 10-year
breakevens 2014-02, the ACM term premium 2016-06, and high-yield spreads 2023-10. So "switch to
point-in-time" is not one decision — it is a portfolio of them, and this script measures the
portfolio instead of asserting it.

Method: build the anchors twice at each as-of date -- once per scoring mode, with `write=False`
so no published artifact is touched -- using a temporary copy of the macro config whose
`scoring_mode` line is flipped. Nothing here writes to the repository.

Usage:
    python scripts/validate_pit_vs_calendar.py
    python scripts/validate_pit_vs_calendar.py --as-of 2015-06-30 --as-of 2026-09-20
    python scripts/validate_pit_vs_calendar.py --json data/logs/pit_vs_calendar.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure the repo root is on sys.path so we can import macro_engine
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from macro_engine.anchors.service import build_anchors  # noqa: E402
from macro_engine.evaluation.config import load_evaluation_config  # noqa: E402

DEFAULT_MACRO_CONFIG = "config/phase_b_sources.yaml"
DEFAULT_DB_PATH = "data/macro_engine.duckdb"

# Dates chosen at the archive-depth boundaries rather than at random: before DGS10's first
# vintage, between the DGS10 and breakeven boundaries, between breakevens and the term premium,
# and the present.
DEFAULT_AS_OF = [
    "1995-06-30",
    "2000-06-30",
    "2008-06-30",
    "2015-06-30",
    "2020-06-30",
    "2024-06-30",
    "2026-09-20",
]

CALENDAR_LEGS = [
    ("risk_free.nominal_10y", ("cost_of_capital", "risk_free", "nominal_10y")),
    ("risk_free.real_10y", ("cost_of_capital", "risk_free", "real_10y")),
    ("risk_free.breakeven_10y", ("cost_of_capital", "risk_free", "breakeven_10y")),
    ("risk_free.term_premium", ("cost_of_capital", "risk_free", "term_premium")),
    ("inflation.cpi_yoy", ("cost_of_capital", "inflation", "cpi_yoy")),
    ("inflation.pce_yoy", ("cost_of_capital", "inflation", "pce_yoy")),
    ("implied_erp", ("cost_of_capital", "implied_erp")),
    ("implied_cost_of_equity", ("cost_of_capital", "implied_cost_of_equity")),
    ("growth.nominal_gdp_trend", ("long_run_growth", "nominal_gdp_trend")),
    ("growth.terminal_g", ("long_run_growth", "terminal_g_suggestion")),
]


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        if payload is None:
            return None
        payload = payload.get(key) if isinstance(payload, dict) else getattr(payload, key, None)
    return payload


def _config_text(macro_config: Path, mode: str, boundary: str | None) -> str:
    """The macro config with `scoring_mode` forced, for one comparison arm.

    Arms are forced explicitly rather than by flipping a literal: the shipped default is now a
    point-in-time HYBRID, so a string replace would silently produce the same arm twice.
    """
    text = macro_config.read_text(encoding="utf-8")
    if not re.search(r"(?m)^scoring_mode:", text):
        raise SystemExit(f"no 'scoring_mode:' line in {macro_config}")
    text = re.sub(r"(?m)^scoring_mode:.*$", f"scoring_mode: {mode}", text)
    if re.search(r"(?m)^point_in_time_start:", text):
        replacement = (
            f"point_in_time_start: '{boundary}'" if boundary else "point_in_time_start: null"
        )
        text = re.sub(r"(?m)^point_in_time_start:.*$", replacement, text)
    elif boundary:
        text += f"\npoint_in_time_start: '{boundary}'\n"
    return text


def _build(*, config_path: str, as_of: str, db_path: str) -> dict[str, Any]:
    bundle = build_anchors(
        config_path=str(_REPO_ROOT / "config/anchors.yaml"),
        macro_config_path=config_path,
        sector_config_path=str(_REPO_ROOT / "config/sectors.yaml"),
        db_path=str(_REPO_ROOT / db_path),
        as_of=as_of,
        write=False,  # never touch published artifacts from a validation run
    )
    return bundle.model_dump(mode="json")


def _fmt(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--as-of", action="append", default=None, help="evaluation date (repeatable)")
    parser.add_argument("--macro-config", default=DEFAULT_MACRO_CONFIG)
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--blanket",
        action="store_true",
        help="also build a point-in-time-everywhere arm, to show what a switch without the "
        "boundary would do to pre-archive dates",
    )
    parser.add_argument("--json", default=None, help="also write the full comparison here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    macro_config = _REPO_ROOT / args.macro_config
    as_of_dates = args.as_of or DEFAULT_AS_OF
    base = load_evaluation_config(macro_config)
    boundary = base.point_in_time_start

    # Three arms, because the decision has three candidates:
    #   calendar  -- the old basis, kept as the reference
    #   hybrid    -- the shipped configuration (point-in-time from `boundary`)
    #   blanket   -- point-in-time everywhere, to show what it costs before the archive begins
    arms = {
        "calendar": _config_text(macro_config, "calendar_asof", boundary),
        "hybrid": _config_text(macro_config, "point_in_time", boundary),
    }
    if args.blanket:
        arms["blanket"] = _config_text(macro_config, "point_in_time", None)

    print(f"configured basis: {base.scoring_mode} (point_in_time_start={boundary})")
    report: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="mri-pit-") as tmp:
        paths: dict[str, Path] = {}
        for name, text in arms.items():
            path = Path(tmp) / f"phase_b_sources_{name}.yaml"
            path.write_text(text, encoding="utf-8")
            paths[name] = path

        for as_of in as_of_dates:
            built = {
                name: _build(config_path=str(path), as_of=as_of, db_path=args.db_path)
                for name, path in paths.items()
            }
            reference = built["calendar"]

            legs: dict[str, dict[str, Any]] = {}
            for label, path in CALENDAR_LEGS:
                cal_value = _dig(reference, path)
                legs[label] = {"calendar": cal_value}
                for name, payload in built.items():
                    if name == "calendar":
                        continue
                    value = _dig(payload, path)
                    legs[label][name] = value
                    legs[label][f"{name}_delta"] = (
                        value - cal_value
                        if isinstance(value, float) and isinstance(cal_value, float)
                        else None
                    )

            entry = {
                "as_of": as_of,
                "configured": base.scoring_mode,
                "boundary": boundary,
                "degraded": {name: payload["degraded"] for name, payload in built.items()},
                "reasons": {
                    name: payload["cost_of_capital"]["provenance"]["degradation_reasons"]
                    for name, payload in built.items()
                },
                "input_dates": {
                    name: payload["cost_of_capital"]["provenance"]["input_dates"]
                    for name, payload in built.items()
                },
                "scoring_mode_applied": {
                    name: payload["scoring_mode"] for name, payload in built.items()
                },
                "legs": legs,
            }
            report.append(entry)

            applied = entry["scoring_mode_applied"]
            print(f"\n=== as-of {as_of} ===")
            print(f"  applied rule : hybrid={applied['hybrid']} blanket={applied.get('blanket')}")
            print(f"  degraded     : {entry['degraded']}")
            for label, leg in legs.items():
                parts = [f"calendar={_fmt(leg['calendar'])}"]
                for name in arms:
                    if name == "calendar":
                        continue
                    delta = leg.get(f"{name}_delta")
                    marker = f" ({delta:+.4f})" if isinstance(delta, float) else ""
                    parts.append(f"{name}={_fmt(leg.get(name))}{marker}")
                if len({leg.get(name) for name in arms}) > 1:
                    print(f"    {label:26s} " + "  ".join(parts))
            for name in arms:
                if name == "calendar":
                    continue
                missing = entry["reasons"][name]
                if missing:
                    print(f"    {name} could not measure:")
                    for reason in missing:
                        print(f"      - {reason}")

    print("\n=== summary ===")
    for entry in report:
        for name in arms:
            if name == "calendar":
                continue
            changed = [
                label
                for label, leg in entry["legs"].items()
                if leg["calendar"] != leg.get(name)
            ]
            print(
                f"  {entry['as_of']} {name:9s}: {len(changed)}/{len(entry['legs'])} legs differ"
                f" | degraded={entry['degraded'][name]}"
            )

    if args.json:
        out = _REPO_ROOT / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
