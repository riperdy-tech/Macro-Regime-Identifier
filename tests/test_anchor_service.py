"""End-to-end (service-level) test of P0_0 §1.2 rule 5 for the growth anchor: "the
label drives no number", enforced not just by the absence of a parameter
(tests/test_anchor_growth.py) but by mutating the STORED regime label and rebuilding
the whole anchor bundle through `anchors/service.build_anchors`.

This is the S2 gate test the review demands: "flipping the regime label leaves
terminal_g_suggestion unchanged" (P0_0_MRI_TARGET_ARCHITECTURE.md §8 S2.1;
MRI_S1_APPROVAL.md §10, "the label-mutation test passes").
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from macro_engine.anchors import service as anchor_service
from macro_engine.storage.duckdb_store import DuckDBStore

MINIMAL_ANCHOR_CONFIG = """
anchors:
  output_dir: {output_dir}
cost_of_capital:
  risk_free:
    nominal_10y: {{series: DGS10, units: percent}}
    real_10y: {{series: DFII10, units: percent}}
    breakeven_10y: {{series: T10YIE, units: percent}}
  inflation: {{cpi: CPIAUCSL, pce: PCEPI, yoy_periods: 12}}
growth:
  real_potential:
    series: GDPPOT
    trend_window_years: 10
    min_observations: 12
  inflation_expectation:
    candidates:
      - {{series: T5YIFR, units: percent}}
      - {{series: T10YIE, units: percent}}
    min_observations: 12
  terminal_g:
    max_share_of_nominal_trend: 0.85
    floor: 0.000
    ceiling: 0.050
    round_to: 0.0025
  rung:
    confirm_months: 3
  downstream_prior_in_use: 0.025
"""


def _seed_observations(as_of: pd.Timestamp) -> pd.DataFrame:
    import math

    quarters = pd.date_range("2006-01-01", as_of, freq="QS")
    gdppot = pd.DataFrame(
        {
            "series_id": "GDPPOT",
            "date": quarters,
            "value": [100.0 * math.exp(0.018 * (i / 4)) for i in range(len(quarters))],
        }
    )
    months = pd.date_range(end=as_of, periods=24 * 21, freq="B")
    t5yifr = pd.DataFrame({"series_id": "T5YIFR", "date": months, "value": 2.30})
    frame = pd.concat([gdppot, t5yifr], ignore_index=True)
    frame["realtime_start"] = frame["date"]
    frame["realtime_end"] = pd.Timestamp("9999-12-31")
    frame["source"] = "test_fixture"
    frame["fetched_at"] = as_of
    frame["frequency"] = "d"
    frame["units"] = "lin"
    return frame


def _seed_timeline(months: list[str], label: str) -> pd.DataFrame:
    n = len(months)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(months),
            "dominant_regime": [label] * n,
            "dominant_probability": [0.5] * n,
            "reported_regime": [label] * n,
            "second_regime": [None] * n,
            "second_probability": [None] * n,
            "confidence": [0.5] * n,
            "entropy": [0.5] * n,
            "valid_regime_count": [5] * n,
            "valid": [True] * n,
            "reason": [None] * n,
        }
    )


def _build_with_label(tmp_path: Path, label: str) -> object:
    as_of = pd.Timestamp("2026-04-30")
    db_path = tmp_path / f"db_{label}.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_raw_observations(_seed_observations(as_of))
    store.replace_diagnostic_outputs(
        _seed_timeline(["2026-02-01", "2026-03-01", "2026-04-01"], label),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    config_path = tmp_path / f"anchors_{label}.yaml"
    config_path.write_text(
        MINIMAL_ANCHOR_CONFIG.format(output_dir=(tmp_path / "out").as_posix()),
        encoding="utf-8",
    )

    return anchor_service.build_anchors(
        config_path=str(config_path),
        macro_config_path="config/phase_b_sources.yaml",
        sector_config_path="config/sectors.yaml",
        db_path=str(db_path),
        as_of="2026-04-30",
        write=False,
    )


def test_flipping_the_stored_regime_label_leaves_terminal_g_suggestion_unchanged(tmp_path: Path):
    goldilocks = _build_with_label(tmp_path, "goldilocks")
    recession = _build_with_label(tmp_path, "recession")

    g = goldilocks.long_run_growth
    r = recession.long_run_growth

    assert g.terminal_g_suggestion is not None
    assert g.terminal_g_suggestion == pytest.approx(r.terminal_g_suggestion)
    assert g.terminal_g_rung == pytest.approx(r.terminal_g_rung)
    assert g.raw_trend_g == pytest.approx(r.raw_trend_g)
    assert g.nominal_gdp_trend == pytest.approx(r.nominal_gdp_trend)
    assert g.components == r.components
    assert g.regime_applied is None and r.regime_applied is None
    assert g.regime_adjustment is None and r.regime_adjustment is None
    assert g.regime_sensitivity == {} and r.regime_sensitivity == {}
