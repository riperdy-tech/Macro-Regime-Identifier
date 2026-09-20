"""Capital-market anchors: cost of capital.

Covers the three things that can actually go wrong here:
  1. the risk-free decomposition reads the right series and converts units once,
  2. the implied-ERP solve converges to the rate that reproduces the target,
  3. a missing input degrades LOUDLY to null instead of to a plausible number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from macro_engine.anchors.config import load_anchor_config
from macro_engine.anchors.cost_of_capital import (
    build_cost_of_capital_anchor,
    implied_erp,
    solve_implied_rate,
    two_stage_value,
    yoy_change,
)

AS_OF = pd.Timestamp("2026-04-30")


def _observations(series: dict[str, list[tuple[str, float]]]) -> pd.DataFrame:
    rows = []
    for series_id, points in series.items():
        for date, value in points:
            rows.append({"series_id": series_id, "date": date, "value": value})
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _config(**overrides):
    config = load_anchor_config("config/anchors.yaml")
    for key, value in overrides.items():
        setattr(config.cost_of_capital, key, value)
    return config


def test_risk_free_decomposition_converts_percent_to_decimal():
    """FRED quotes yields in percent; the payload must be decimal fractions."""
    observations = _observations(
        {
            "DGS10": [("2026-04-29", 4.45)],
            "DFII10": [("2026-04-29", 1.95)],
            "T10YIE": [("2026-04-29", 2.50)],
            "THREEFYTP10": [("2026-04-29", 0.62)],
            "CPIAUCSL": [("2025-04-01", 320.0), ("2026-04-01", 332.1)],
            "PCEPI": [("2025-04-01", 124.0), ("2026-04-01", 128.7)],
        }
    )
    anchor = build_cost_of_capital_anchor(
        observations=observations,
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )

    assert anchor.risk_free["nominal_10y"] == pytest.approx(0.0445)
    assert anchor.risk_free["real_10y"] == pytest.approx(0.0195)
    assert anchor.risk_free["breakeven_10y"] == pytest.approx(0.0250)
    assert anchor.risk_free["term_premium"] == pytest.approx(0.0062)
    assert anchor.term_premium_source == "observed:THREEFYTP10"
    # A 4.45% yield must never surface as 445%.
    assert anchor.risk_free["nominal_10y"] < 1.0


def test_breakeven_consistency_is_preserved_by_decomposition():
    """nominal - real == breakeven when the three legs are the same vintage.

    This is the arithmetic identity that catches a unit or series mix-up: if one leg
    were quoted differently, the identity breaks by orders of magnitude.
    """
    observations = _observations(
        {
            "DGS10": [("2026-04-29", 4.30)],
            "DFII10": [("2026-04-29", 1.80)],
            "T10YIE": [("2026-04-29", 2.50)],
        }
    )
    anchor = build_cost_of_capital_anchor(
        observations=observations,
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    implied_breakeven = anchor.risk_free["nominal_10y"] - anchor.risk_free["real_10y"]
    assert implied_breakeven == pytest.approx(anchor.risk_free["breakeven_10y"], abs=1e-9)


def test_inflation_yoy_uses_declared_periods():
    observations = _observations(
        {"CPIAUCSL": [("2025-04-01", 300.0), ("2026-04-01", 309.0)]}
    )
    value, date = yoy_change(observations, "CPIAUCSL", AS_OF, periods=12)
    assert value == pytest.approx(0.03)
    assert date == "2026-04-01"


def test_non_positive_index_is_refused_rather_than_turned_into_a_rate():
    """A negative price index is corruption; a 'yoy' from it is nonsense."""
    observations = _observations(
        {"CPIAUCSL": [("2025-04-01", -300.0), ("2026-04-01", 309.0)]}
    )
    value, _ = yoy_change(observations, "CPIAUCSL", AS_OF, periods=12)
    assert value is None


def test_solve_implied_rate_recovers_the_rate_that_reproduces_the_target():
    constituents = [
        {"cash_flow": 1.0e9, "growth": 0.05},
        {"cash_flow": 2.0e9, "growth": 0.03},
        {"cash_flow": 0.5e9, "growth": 0.08},
    ]
    terminal = 0.025
    true_rate = 0.085
    target = sum(
        two_stage_value(item["cash_flow"], item["growth"], true_rate, terminal)
        for item in constituents
    )
    rate, status = solve_implied_rate(
        constituents=constituents,
        target_value=target,
        terminal_growth=terminal,
        rate_bounds=(-0.02, 0.25),
        iterations=80,
    )
    assert status == "ok"
    assert rate == pytest.approx(true_rate, abs=1e-6)


def test_solve_implied_rate_reports_no_root_instead_of_widening_bounds():
    """A target outside the bracketed range is a finding, not an invitation to fudge."""
    constituents = [{"cash_flow": 1.0e9, "growth": 0.05}]
    rate, status = solve_implied_rate(
        constituents=constituents,
        target_value=1e18,
        terminal_growth=0.025,
        rate_bounds=(-0.02, 0.25),
        iterations=80,
    )
    assert rate is None
    assert status == "no_solution_in_bounds"


def test_implied_erp_subtracts_the_observed_risk_free_rate(tmp_path: Path):
    aggregate = {
        "asof": "2026-04-30",
        "source": "unit-test aggregate",
        "growth": 0.05,
        "coverage_share": 0.90,
        "constituents": [
            {"ticker": f"T{index}", "cash_flow": 1.0e9, "market_cap": 25.0e9}
            for index in range(40)
        ],
    }
    config = load_anchor_config("config/anchors.yaml")
    erp, coe, status, note = implied_erp(
        aggregate=aggregate,
        nominal_10y=0.045,
        config=config.cost_of_capital,
        terminal_growth=0.025,
    )
    assert status == "ok", note
    # ERP must be the premium OVER the observable, never the rate itself.
    assert erp == pytest.approx(coe - 0.045, abs=1e-12)
    assert erp != pytest.approx(coe)


def test_implied_erp_withholds_the_number_when_the_aggregate_is_too_thin():
    """Below the constituent floor there is no index-level ERP, only a sample."""
    aggregate = {
        "growth": 0.05,
        "constituents": [{"cash_flow": 1.0e9, "market_cap": 25.0e9} for _ in range(5)],
    }
    config = load_anchor_config("config/anchors.yaml")
    erp, coe, status, note = implied_erp(
        aggregate=aggregate,
        nominal_10y=0.045,
        config=config.cost_of_capital,
        terminal_growth=0.025,
    )
    assert erp is None and coe is None
    assert status == "insufficient_constituents"
    assert "5 usable constituents" in note


def test_implied_erp_requires_a_risk_free_reference():
    """Without an observable risk-free rate the premium is undefined, not zero."""
    aggregate = {
        "growth": 0.05,
        "coverage_share": 0.95,
        "constituents": [
            {"cash_flow": 1.0e9, "market_cap": 25.0e9} for _ in range(40)
        ],
    }
    config = load_anchor_config("config/anchors.yaml")
    erp, coe, status, _ = implied_erp(
        aggregate=aggregate,
        nominal_10y=None,
        config=config.cost_of_capital,
        terminal_growth=0.025,
    )
    assert erp is None and coe is None
    assert status == "no_risk_free"


def test_missing_inputs_degrade_loudly_and_publish_nulls(tmp_path: Path):
    # Point BOTH ERP sources at absent files so this is genuinely "no ERP evidence at all",
    # rather than depending on which inputs happen to be present in the repo.
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.erp.regime_estimate.history_path = str(tmp_path / "absent.csv")
    config.cost_of_capital.erp.equity_aggregate_path = str(tmp_path / "absent.json")
    anchor = build_cost_of_capital_anchor(
        observations=_observations({"DGS10": [("2026-04-29", 4.45)]}),
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.degraded is True
    assert anchor.risk_free["real_10y"] is None
    assert anchor.implied_erp is None
    assert anchor.implied_cost_of_equity is None
    assert anchor.market_implied_erp is None
    assert anchor.market_implied_coe is None
    assert anchor.erp_source == "unavailable"
    assert anchor.erp_basis == "unavailable"
    # Never a fabricated default: the reasons must name the missing series.
    joined = " ".join(anchor.provenance.degradation_reasons)
    assert "DFII10" in joined
    assert "T10YIE" in joined
    assert "no equity aggregate" in joined


def test_implied_coe_is_only_published_when_an_erp_was_measured(tmp_path: Path):
    """nominal 10y alone must not be dressed up as a cost of equity."""
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(
        json.dumps(
            {
                "growth": 0.05,
                "coverage_share": 0.90,
                "basis": "index",
                "constituents": [
                    {"cash_flow": 1.0e9, "market_cap": 25.0e9} for _ in range(40)
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.erp.equity_aggregate_path = str(aggregate_path)
    anchor = build_cost_of_capital_anchor(
        observations=_observations(
            {"DGS10": [("2026-04-29", 4.45)], "DFII10": [("2026-04-29", 1.90)]}
        ),
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        terminal_growth=0.025,
    )
    assert anchor.erp_source == "implied"
    assert anchor.erp_basis == "index"
    assert anchor.implied_erp is not None
    assert anchor.implied_cost_of_equity == pytest.approx(
        anchor.risk_free["nominal_10y"] + anchor.implied_erp, abs=1e-9
    )
    # An INDEX basis is the only thing that may set the market_implied_* fields.
    assert anchor.market_implied_erp == anchor.implied_erp
    assert anchor.market_implied_coe == anchor.implied_cost_of_equity


def test_a_universe_basis_never_sets_market_implied_fields(tmp_path: Path):
    """A screener universe is weaker evidence than an index and must not borrow the word."""
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(
        json.dumps(
            {
                "growth": 0.05,
                "coverage_share": 0.90,
                "basis": "universe",
                "source": "unit-test corpus",
                "constituents": [
                    {"cash_flow": 1.0e9, "market_cap": 25.0e9} for _ in range(40)
                ],
            }
        ),
        encoding="utf-8",
    )
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.erp.equity_aggregate_path = str(aggregate_path)
    anchor = build_cost_of_capital_anchor(
        observations=_observations(
            {"DGS10": [("2026-04-29", 4.45)], "DFII10": [("2026-04-29", 1.90)]}
        ),
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        terminal_growth=0.025,
    )
    assert anchor.erp_basis == "universe"
    assert anchor.implied_erp is not None
    assert anchor.implied_cost_of_equity is not None
    assert anchor.market_implied_erp is None
    assert anchor.market_implied_coe is None
    assert anchor.equity_aggregate["basis"] == "universe"
    assert anchor.equity_aggregate["coverage_share"] == 0.90


def test_sector_loadings_are_withheld_without_a_declared_panel():
    """Betas off an unverified panel are fabricated evidence, so none are published.

    The gate is asserted explicitly rather than relying on the shipped default: the shipped
    config now declares a VERIFIED panel, so a test that read the default would stop testing
    the gate the moment the panel was enabled (and it did).
    """
    prices = pd.DataFrame(
        {
            "ticker": ["SPY"] * 400 + ["XLK"] * 400,
            "date": list(pd.date_range("2024-01-01", periods=400, freq="D")) * 2,
            "close": list(range(400)) + list(range(400)),
        }
    )
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.sector_loadings.price_panel_source = "none"
    anchor = build_cost_of_capital_anchor(
        observations=_observations({"DGS10": [("2026-04-29", 4.45)]}),
        prices=prices,
        proxies={"information_technology": "XLK"},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.sector_loadings == {}
    assert anchor.loading_source is None
    assert any(
        "no market-observed price panel is declared" in reason
        for reason in anchor.provenance.degradation_reasons
    )


def test_curve_legs_from_different_vintages_are_flagged():
    """A partially-refreshed store must not produce a curve that never existed.

    Reproduces the real failure: DGS10 four months stale while TIPS and the breakeven are
    current, giving nominal - real = 1.84% against a published 2.33% breakeven.
    """
    observations = _observations(
        {
            "DGS10": [("2026-01-02", 4.45)],
            "DFII10": [("2026-04-29", 2.61)],
            "T10YIE": [("2026-04-30", 2.33)],
        }
    )
    anchor = build_cost_of_capital_anchor(
        observations=observations,
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    joined = " ".join(anchor.provenance.degradation_reasons)
    assert "span" in joined and "not one curve" in joined
    assert "does not describe a single day's curve" in joined
    assert anchor.degraded is True


def test_a_consistent_curve_produces_no_consistency_reasons():
    """Same-vintage legs reconcile, and the identity holds to the published breakeven."""
    observations = _observations(
        {
            "DGS10": [("2026-04-29", 4.94)],
            "DFII10": [("2026-04-29", 2.61)],
            "T10YIE": [("2026-04-30", 2.33)],
        }
    )
    anchor = build_cost_of_capital_anchor(
        observations=observations,
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.risk_free["nominal_10y"] - anchor.risk_free["real_10y"] == pytest.approx(
        anchor.risk_free["breakeven_10y"], abs=1e-9
    )
    assert not any(
        "risk_free consistency" in reason
        for reason in anchor.provenance.degradation_reasons
    )


def test_a_contemporaneous_but_disagreeing_curve_is_flagged():
    """Fresh data that still disagrees is not explained by freshness, so it must degrade."""
    observations = _observations(
        {
            "DGS10": [("2026-04-29", 4.94)],
            "DFII10": [("2026-04-29", 2.61)],
            "T10YIE": [("2026-04-29", 3.10)],  # 77bp away from nominal - real
        }
    )
    anchor = build_cost_of_capital_anchor(
        observations=observations,
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert any(
        "does not describe a single day's curve" in reason
        for reason in anchor.provenance.degradation_reasons
    )
    assert anchor.degraded is True


def test_sector_loadings_measure_beta_when_the_panel_is_declared():
    """With provenance declared, a synthetic 2x-return sector must measure ~2.0."""
    dates = pd.date_range("2024-01-01", periods=401, freq="D")
    benchmark_returns = [0.001 if index % 2 == 0 else -0.0004 for index in range(400)]
    benchmark, sector = [1.0], [1.0]
    for step in benchmark_returns:
        benchmark.append(benchmark[-1] * (1.0 + step))
        sector.append(sector[-1] * (1.0 + 2.0 * step))
    prices = pd.DataFrame(
        {
            "ticker": ["SPY"] * 401 + ["XLK"] * 401,
            "date": list(dates) * 2,
            "close": benchmark + sector,
        }
    )
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.sector_loadings.price_panel_source = "stored_sector_proxy_prices"
    anchor = build_cost_of_capital_anchor(
        observations=_observations({"DGS10": [("2026-04-29", 4.45)]}),
        prices=prices,
        proxies={"information_technology": "XLK"},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.sector_loadings["information_technology"] == pytest.approx(2.0, abs=1e-6)
    assert anchor.loading_source == "beta_vs_SPY_5y"


def test_erp_history_band_is_published_when_no_implied_erp_exists(tmp_path: Path):
    """The documented degraded ERP mode: a citable history distribution, NOT an implied value."""
    history = tmp_path / "erp_history.csv"
    history.write_text(
        "date,erp,measure,source\n"
        + "".join(
            f"{year}-12-31,{0.02 + 0.0005 * index:.6f},Implied ERP (FCFE),\"Unit test history, annual\"\n"
            for index, year in enumerate(range(1990, 2026))
        ),
        encoding="utf-8",
    )
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.erp.regime_estimate.history_path = str(history)
    config.cost_of_capital.erp.regime_estimate.min_points = 25
    config.cost_of_capital.erp.regime_estimate.lookback_years = 65

    anchor = build_cost_of_capital_anchor(
        observations=_observations(
            {"DGS10": [("2026-04-29", 4.45)], "DFII10": [("2026-04-29", 2.10)]}
        ),
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )

    assert anchor.erp_source == "regime_estimate"
    # The distinction the whole design rests on: a history is NOT an implied value.
    assert anchor.market_implied_erp is None
    assert anchor.market_implied_coe is None
    assert anchor.erp_history is not None
    assert anchor.erp_history["n"] == 36
    assert anchor.erp_history["start"] == "1990-12-31"
    assert anchor.erp_history["end"] == "2025-12-31"
    assert (
        anchor.erp_history["p10"]
        <= anchor.erp_history["p25"]
        <= anchor.erp_history["median"]
        <= anchor.erp_history["p75"]
        <= anchor.erp_history["p90"]
    )
    assert anchor.erp_history_source
    assert any("no implied ERP could be measured" in note for note in anchor.provenance.notes)


def test_a_missing_history_file_still_degrades_to_unavailable(tmp_path: Path):
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.erp.regime_estimate.history_path = str(tmp_path / "absent.csv")
    anchor = build_cost_of_capital_anchor(
        observations=_observations({"DGS10": [("2026-04-29", 4.45)]}),
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
    )
    assert anchor.erp_source == "unavailable"
    assert anchor.erp_history is None
    assert any(
        "erp_history" in reason for reason in anchor.provenance.degradation_reasons
    )


def test_anchor_config_declares_units_for_every_rate_series():
    """The units contract is what prevented a 445% '10y yield'; assert it is present."""
    config = load_anchor_config("config/anchors.yaml")
    risk_free = config.cost_of_capital.risk_free
    assert risk_free.nominal_10y.units == "percent"
    assert risk_free.real_10y.units == "percent"
    assert risk_free.breakeven_10y.units == "percent"
    assert all(ref.units == "percent" for ref in risk_free.term_premium_candidates)
    assert all(
        ref.units == "percent"
        for ref in config.growth.inflation_expectation.candidates
    )
