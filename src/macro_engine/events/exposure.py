"""E4 sector exposure of a report (plan v2 §8), MRI side.

MRI publishes, per report, the classifier's sector impacts that clear the exposure bar (confidence >= 0.6 and
|impact| >= 0.3, `config/events.yaml` exposure). The screener, which owns the ticker taxonomy, expands sectors and named
tickers to the `named` / `peer` / `sector` tiers. A contradiction with `config/sector_exposure_guardrails.yaml` is carried
as a flag on the impact: a warning for review, never a validation and never a filter (non-negotiable 3).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from macro_engine.news.guardrails import load_sector_guardrail_config

REPO_ROOT = Path(__file__).resolve().parents[3]
EVENTS_CONFIG_PATH = REPO_ROOT / "config" / "events.yaml"
GUARDRAILS_PATH = REPO_ROOT / "config" / "sector_exposure_guardrails.yaml"

_IMPACT_SIGN = {"tailwind": 1, "headwind": -1}
_THEME_SIGN = {"positive": 1, "negative": -1}


@lru_cache(maxsize=1)
def _thresholds() -> tuple[float, float]:
    doc = yaml.safe_load(EVENTS_CONFIG_PATH.read_text(encoding="utf-8"))["exposure"]
    return float(doc["sector_min_confidence"]["value"]), float(doc["sector_min_abs_impact"]["value"])


@lru_cache(maxsize=1)
def _guardrails() -> tuple[float, dict[tuple[str, str], int]]:
    cfg = load_sector_guardrail_config(GUARDRAILS_PATH)
    expected = {(e.theme_id, e.sector_id): (1 if e.expected == "positive" else -1) for e in cfg.expectations}
    return float(cfg.min_confidence_to_flag), expected


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN is not a value here


def sector_exposure(sector_impacts: Any, theme_dirs: Any) -> list[dict[str, Any]]:
    """The impacts of one classified report that clear the bar, each with any guardrail contradiction flags."""
    min_conf, min_abs = _thresholds()
    flag_conf, expected = _guardrails()
    out = []
    for imp in sector_impacts or ():
        if not isinstance(imp, dict) or not imp.get("sector_id"):
            continue
        score, conf = _num(imp.get("impact_score")), _num(imp.get("confidence"))
        if score is None or conf is None or conf < min_conf or abs(score) < min_abs:
            continue
        sid, direction = str(imp["sector_id"]), str(imp.get("impact_direction") or "")
        flags = []
        isign = _IMPACT_SIGN.get(direction)
        if isign is not None and conf >= flag_conf:
            for theme_id, tdir in sorted(theme_dirs or ()):
                exp = expected.get((theme_id, sid))
                tsign = _THEME_SIGN.get(tdir)
                if exp is not None and tsign is not None and isign != exp * tsign:
                    flags.append(f"guardrail_contradiction:{theme_id}")
        out.append({"sector_id": sid, "impact_score": score, "confidence": conf, "impact_direction": direction,
                    "flags": flags})
    return sorted(out, key=lambda e: e["sector_id"])
