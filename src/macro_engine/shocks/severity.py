"""MRI Layer 2 (MRI-12) — severity classification from a shock's config/shocks.yaml entry.

One function, reused by the daily register (`register.py`) for both the primary and
proxy/policy legs of `rates_shock`. Severity 2 dominates severity 1; a signed shock can
fire on either side but never both at once.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SeverityResult:
    severity: int  # 0, 1 or 2
    direction: str  # "up", "down" or "none"


def compute_severity(value: float | None, thresholds: dict[str, Any], *, signed: bool) -> SeverityResult:
    """`thresholds` is the `severity_1`/`severity_2` (unsigned) or
    `severity_1_up`/`severity_2_up`/`severity_1_down`/`severity_2_down` (signed) flat
    dict produced by `shocks.config.to_measurement_thresholds` for one shock (or one leg
    of `rates_shock`)."""
    if value is None:
        return SeverityResult(severity=0, direction="none")

    if signed:
        sev2_up = thresholds["severity_2_up"]
        sev1_up = thresholds["severity_1_up"]
        sev2_dn = thresholds["severity_2_down"]
        sev1_dn = thresholds["severity_1_down"]
        if value >= sev2_up:
            return SeverityResult(severity=2, direction="up")
        if value >= sev1_up:
            return SeverityResult(severity=1, direction="up")
        if value <= sev2_dn:
            return SeverityResult(severity=2, direction="down")
        if value <= sev1_dn:
            return SeverityResult(severity=1, direction="down")
        return SeverityResult(severity=0, direction="none")

    sev2 = thresholds["severity_2"]
    sev1 = thresholds["severity_1"]
    if value >= sev2:
        return SeverityResult(severity=2, direction="up")
    if value >= sev1:
        return SeverityResult(severity=1, direction="up")
    return SeverityResult(severity=0, direction="none")


def is_retired(value: float | None, retire_level: float, *, signed: bool) -> bool:
    """The §3.4 retire condition on the raw transform value (never on `intensity`, which
    is normalised by `threshold_1` and would move the retire line every time a threshold
    changed). Matches `scripts/measure_shocks.py:measure_episode_decay` exactly, which is
    the function the `retire_level`s in `config/shocks.yaml` were measured with:
    signed shocks retire inside a symmetric band, one-sided shocks retire strictly below
    their (single, non-negative) retire level."""
    if value is None:
        return False
    if signed:
        return abs(value) <= retire_level
    return value < retire_level
