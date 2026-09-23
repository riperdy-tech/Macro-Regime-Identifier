"""S5.1-S5.3: every shadow dimension is shadow by construction (MRI_S5_SPEC.md section 3).

Parametrised over the S5 dimensions actually present in `config/phase_b_sources.yaml` at
collection time, so this file does not change step to step: S5.1 exercises it for
`real_rates` alone, S5.2 adds `commodity`, S5.3 adds `dollar`.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import duckdb
import pandas as pd
import pytest
import yaml

from macro_engine.dimensions.composition import load_composition_registry
from macro_engine.regimes.config import load_regime_config

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = _REPO_ROOT / "config" / "phase_b_sources.yaml"
_COMPOSITION_PATH = _REPO_ROOT / "config" / "dimension_composition.yaml"
_EXPOSURE_PATH = _REPO_ROOT / "config" / "sector_exposures.yaml"

_PACK_SPEC = importlib.util.spec_from_file_location(
    "measure_s5_pack", _REPO_ROOT / "scripts" / "measure_s5_pack.py"
)
pack = importlib.util.module_from_spec(_PACK_SPEC)
_PACK_SPEC.loader.exec_module(pack)  # type: ignore[union-attr]


def _configured_s5_dimensions() -> list[str]:
    regime_config = load_regime_config(str(_CONFIG_PATH))
    configured_ids = {dimension.dimension_id for dimension in regime_config.dimensions}
    return sorted(name for name in pack.CANDIDATES if name in configured_ids)


_PRESENT = _configured_s5_dimensions()

if not _PRESENT:
    pytest.skip(
        "no S5 shadow dimension is configured in config/phase_b_sources.yaml yet",
        allow_module_level=True,
    )


# ── (a) required_for_regime is False ────────────────────────────────────────────────────────


@pytest.mark.parametrize("dimension_id", _PRESENT)
def test_required_for_regime_is_false(dimension_id):
    regime_config = load_regime_config(str(_CONFIG_PATH))
    dim = next(d for d in regime_config.dimensions if d.dimension_id == dimension_id)
    assert dim.required_for_regime is False


# ── (b) appears in no regime's dimensions list ──────────────────────────────────────────────


@pytest.mark.parametrize("dimension_id", _PRESENT)
def test_appears_in_no_regime(dimension_id):
    regime_config = load_regime_config(str(_CONFIG_PATH))
    for regime in regime_config.regimes:
        used = {d.dimension_id for d in regime.dimensions}
        assert dimension_id not in used, f"{dimension_id} is used by regime {regime.regime_id}"


# ── (c) appears in no sector's exposure map ─────────────────────────────────────────────────


@pytest.mark.parametrize("dimension_id", _PRESENT)
def test_appears_in_no_sector_exposure(dimension_id):
    with _EXPOSURE_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    exposures = data.get("sector_exposures", {})
    for sector_id, dimension_weights in exposures.items():
        assert dimension_id not in dimension_weights, (
            f"{dimension_id} is used by sector {sector_id} in {_EXPOSURE_PATH.name}"
        )


# ── (d) registered in config/dimension_composition.yaml at the pack's valid_from ───────────


@pytest.mark.parametrize("dimension_id", _PRESENT)
def test_registered_with_the_packs_valid_from(dimension_id):
    registry = load_composition_registry(str(_COMPOSITION_PATH))
    composition = registry.dimensions.get(dimension_id)
    assert composition is not None, f"{dimension_id} is not registered in {_COMPOSITION_PATH.name}"
    declared_valid_from = min(segment.valid_from for segment in composition.segments)
    expected = pd.Timestamp(pack.CANDIDATES[dimension_id]["valid_from"]).date()
    assert declared_valid_from == expected


# ── (e) live-store resolution, gated by MRI_STORE_CHECKS=1 ─────────────────────────────────


@pytest.mark.parametrize("dimension_id", _PRESENT)
def test_live_store_resolution_at_least_95_percent(dimension_id):
    if os.environ.get("MRI_STORE_CHECKS") != "1":
        pytest.skip("live-store check: set MRI_STORE_CHECKS=1 after rebuilding the store")
    db_path = _REPO_ROOT / "data" / "macro_engine.duckdb"
    if not db_path.exists():
        pytest.skip("real store not present")

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        frame = con.execute(
            "select date as evaluation_date, valid from dimension_scores where dimension_id = ?",
            [dimension_id],
        ).fetchdf()
    finally:
        con.close()
    valid_from = pack.CANDIDATES[dimension_id]["valid_from"]
    resolution = pack.resolution_ratio(frame, valid_from)
    assert resolution >= pack.RESOLUTION_MIN, (
        f"{dimension_id} resolves on {resolution:.4f} of evaluation months from {valid_from} "
        f"(< {pack.RESOLUTION_MIN})"
    )
