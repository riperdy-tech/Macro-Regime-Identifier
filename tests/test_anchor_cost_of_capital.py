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
    # A4: the four-month-old nominal yield is now null with its own reason, so the identity check has
    # nothing to compare (it is still covered by the contemporaneous-but-disagreeing test below).
    assert anchor.risk_free["nominal_10y"] is None
    assert "DGS10 observed 2026-01-02" in joined
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
    dates = pd.date_range(end=AS_OF - pd.Timedelta(days=3), periods=401, freq="D")
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
    assert anchor.provenance.input_dates["sector_proxy_prices"] == "2026-04-27"


def _panel(end: str, *, xle_end: str | None = None) -> pd.DataFrame:
    """SPY, XLK and XLE daily closes, 400 days ending `end` (XLE may end earlier); XLK moves at 2x SPY."""
    import numpy as np

    dates = pd.date_range(end=end, periods=400, freq="D")
    steps = np.where(np.arange(400) % 2 == 0, 0.001, -0.0004)
    spy = np.cumprod(1.0 + steps)
    xlk = np.cumprod(1.0 + 2.0 * steps)
    xle = np.cumprod(1.0 + 0.5 * steps)
    frames = []
    for ticker, closes in (("SPY", spy), ("XLK", xlk), ("XLE", xle)):
        frame = pd.DataFrame({"ticker": ticker, "date": dates, "close": closes})
        if ticker == "XLE" and xle_end is not None:
            frame = frame[frame["date"] <= pd.Timestamp(xle_end)]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _healthy_anchor(tmp_path: Path, prices: pd.DataFrame):
    """An anchor whose every other leg is measured, so only the loadings can degrade it."""
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(
        json.dumps(
            {
                "growth": 0.05,
                "coverage_share": 0.90,
                "basis": "index",
                "constituents": [{"cash_flow": 1.0e9, "market_cap": 25.0e9} for _ in range(40)],
            }
        ),
        encoding="utf-8",
    )
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.erp.equity_aggregate_path = str(aggregate_path)
    config.cost_of_capital.sector_loadings.price_panel_source = "stored_sector_proxy_prices"
    return build_cost_of_capital_anchor(
        observations=_observations(
            {
                "DGS10": [("2026-04-29", 4.94)],
                "DFII10": [("2026-04-29", 2.61)],
                "T10YIE": [("2026-04-29", 2.33)],
                "THREEFYTP10": [("2026-04-29", 0.50)],
                "CPIAUCSL": [("2025-03-01", 100.0), ("2026-03-01", 103.0)],
                "PCEPI": [("2025-03-01", 100.0), ("2026-03-01", 102.5)],
            }
        ),
        prices=prices,
        proxies={"information_technology": "XLK", "energy": "XLE"},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        terminal_growth=0.025,
        publication_lag_days={"CPIAUCSL": 44, "PCEPI": 58},
    )


def test_a_panel_three_days_old_gives_loadings_and_its_date(tmp_path: Path):
    anchor = _healthy_anchor(tmp_path, _panel("2026-04-27"))
    assert anchor.degraded is False, anchor.provenance.degradation_reasons
    assert anchor.sector_loadings["information_technology"] == pytest.approx(2.0, abs=1e-6)
    assert anchor.provenance.input_dates["sector_proxy_prices"] == "2026-04-27"


def test_a_panel_thirty_days_old_is_not_served_as_current(tmp_path: Path):
    anchor = _healthy_anchor(tmp_path, _panel("2026-03-31"))
    assert anchor.sector_loadings == {}
    assert anchor.loading_source is None
    assert anchor.degraded is True
    assert anchor.provenance.input_dates["sector_proxy_prices"] == "2026-03-31"
    assert any(
        "ends 2026-03-31" in reason and "no loadings published" in reason
        for reason in anchor.provenance.degradation_reasons
    )


def test_the_panel_age_limit_is_ten_calendar_days(tmp_path: Path):
    assert _healthy_anchor(tmp_path, _panel("2026-04-20")).degraded is False  # 10 days: kept
    assert _healthy_anchor(tmp_path, _panel("2026-04-19")).degraded is True   # 11 days: not


def test_one_stale_proxy_is_skipped_with_a_reason_and_the_rest_are_kept(tmp_path: Path):
    anchor = _healthy_anchor(tmp_path, _panel("2026-04-29", xle_end="2026-03-01"))
    assert list(anchor.sector_loadings) == ["information_technology"]
    assert anchor.degraded is False
    assert any(
        "energy" in reason and "proxy prices end more than" in reason
        for reason in anchor.provenance.degradation_reasons
    )


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


# ── A3: an empty month is no observation, and no NaN reaches a file ──────────


def _vintages(series_id: str, points: list[tuple[str, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {"series_id": series_id, "date": date, "realtime_start": "2025-01-01", "value": value}
            for date, value in points
        ]
    )
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def test_yoy_with_an_empty_base_month_is_none_with_the_date_calendar_mode():
    """An empty October 2025 must not be replaced by September 2025 as the base."""
    observations = _observations(
        {
            "CPIAUCSL": [
                ("2025-09-01", 300.0),
                ("2025-10-01", float("nan")),
                ("2026-10-01", 309.0),
            ]
        }
    )
    value, date = yoy_change(observations, "CPIAUCSL", pd.Timestamp("2026-11-10"), periods=12)
    assert value is None
    assert date == "2026-10-01"


def test_yoy_with_an_empty_base_month_is_none_with_the_date_point_in_time_mode():
    vintages = _vintages(
        "CPIAUCSL",
        [("2025-09-01", 300.0), ("2025-10-01", float("nan")), ("2026-10-01", 309.0)],
    )
    value, date = yoy_change(
        pd.DataFrame(),
        "CPIAUCSL",
        pd.Timestamp("2026-11-10"),
        periods=12,
        scoring_mode="point_in_time",
        vintages=vintages,
    )
    assert value is None
    assert date == "2026-10-01"


def test_yoy_empty_latest_month_falls_back_to_the_latest_real_reading():
    vintages = _vintages(
        "CPIAUCSL",
        [("2025-09-01", 300.0), ("2026-09-01", 306.0), ("2026-10-01", float("nan"))],
    )
    value, date = yoy_change(
        pd.DataFrame(),
        "CPIAUCSL",
        pd.Timestamp("2026-11-10"),
        periods=12,
        scoring_mode="point_in_time",
        vintages=vintages,
    )
    assert value == pytest.approx(0.02)
    assert date == "2026-09-01"


def test_a_missing_base_month_gets_the_reason_line_and_the_anchor_has_no_nan():
    observations = _observations(
        {
            "DGS10": [("2026-11-09", 4.45)],
            "DFII10": [("2026-11-09", 1.95)],
            "T10YIE": [("2026-11-09", 2.50)],
            "CPIAUCSL": [("2025-09-01", 300.0), ("2026-10-01", 309.0)],
            "PCEPI": [("2025-10-01", 124.0), ("2026-10-01", 128.7)],
        }
    )
    anchor = build_cost_of_capital_anchor(
        observations=observations,
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=load_anchor_config("config/anchors.yaml"),
        as_of=pd.Timestamp("2026-11-10"),
        built_at="2026-11-10T00:00:00+00:00",
    )
    assert anchor.inflation["cpi_yoy"] is None
    assert any("CPIAUCSL yoy unavailable" in reason for reason in anchor.provenance.degradation_reasons)
    json.dumps(anchor.model_dump(mode="json"), allow_nan=False)


def _strict_loads(text: str):
    def refuse(token):
        raise ValueError(f"non-JSON constant {token}")

    return json.loads(text, parse_constant=refuse)


def _build_with_empty_cpi_month(tmp_path: Path, *, write: bool):
    from macro_engine.anchors import service as anchor_service
    from macro_engine.storage.duckdb_store import DuckDBStore
    from test_anchor_service import MINIMAL_ANCHOR_CONFIG, _seed_observations

    as_of = pd.Timestamp("2026-04-30")
    db_path = tmp_path / "db.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    seeded = _seed_observations(as_of)
    cpi = pd.DataFrame(
        {
            "series_id": "CPIAUCSL",
            "date": pd.to_datetime(["2025-03-01", "2025-04-01", "2026-04-01"]),
            "value": [300.0, float("nan"), 309.0],
        }
    )
    for column in ("realtime_start", "realtime_end", "source", "fetched_at", "frequency", "units"):
        cpi[column] = seeded.iloc[0][column]
    store.upsert_raw_observations(pd.concat([seeded, cpi], ignore_index=True))
    config_path = tmp_path / "anchors.yaml"
    config_path.write_text(
        MINIMAL_ANCHOR_CONFIG.format(output_dir=(tmp_path / "out").as_posix()), encoding="utf-8"
    )
    return anchor_service.build_anchors(
        config_path=str(config_path),
        macro_config_path="config/phase_b_sources.yaml",
        sector_config_path="config/sectors.yaml",
        db_path=str(db_path),
        as_of="2026-04-30",
        write=write,
    )


def test_the_three_anchor_artifacts_parse_strictly(tmp_path: Path):
    _build_with_empty_cpi_month(tmp_path, write=True)
    for name in ("cost_of_capital_anchor.json", "long_run_growth_anchor.json", "sector_multiple_bands.json"):
        _strict_loads((tmp_path / "out" / name).read_text(encoding="utf-8"))


def test_a_nan_reaching_an_anchor_writer_is_a_loud_failure_and_no_file(tmp_path: Path):
    from macro_engine.anchors import service as anchor_service

    bundle = _build_with_empty_cpi_month(tmp_path, write=False)
    bundle.cost_of_capital.inflation["cpi_yoy"] = float("nan")
    with pytest.raises(ValueError):
        anchor_service.write_anchor_outputs(
            bundle=bundle, output_dir=tmp_path / "out2", only=["cost_of_capital"]
        )
    assert not (tmp_path / "out2" / "cost_of_capital_anchor.json").exists()


# ── A4: every input has an age; a leg past its limit is null with a reason ───

LAGS = {"CPIAUCSL": 44, "PCEPI": 58}


def _leg_anchor(tmp_path: Path, observations: pd.DataFrame, **kwargs):
    """Every other leg measured (equity aggregate present), so only a leg's age can degrade it."""
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(
        json.dumps(
            {
                "growth": 0.05,
                "coverage_share": 0.90,
                "basis": "index",
                "constituents": [{"cash_flow": 1.0e9, "market_cap": 25.0e9} for _ in range(40)],
            }
        ),
        encoding="utf-8",
    )
    config = load_anchor_config("config/anchors.yaml")
    config.cost_of_capital.erp.equity_aggregate_path = str(aggregate_path)
    return build_cost_of_capital_anchor(
        observations=observations,
        prices=pd.DataFrame(),
        proxies={},
        benchmark_ticker="SPY",
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        terminal_growth=0.025,
        **kwargs,
    )


def _legs(curve_date: str, tp_date: str, cpi_month: str = "2026-04-01", cpi_base: str = "2025-04-01"):
    return _observations(
        {
            "DGS10": [(curve_date, 4.45)],
            "DFII10": [(curve_date, 1.95)],
            "T10YIE": [(curve_date, 2.50)],
            "THREEFYTP10": [(tp_date, 0.62)],
            "CPIAUCSL": [(cpi_base, 300.0), (cpi_month, 309.0)],
            "PCEPI": [("2025-04-01", 124.0), ("2026-04-01", 128.7)],
        }
    )


def test_curve_legs_twenty_days_old_degrade_the_anchor_even_when_they_agree_with_each_other(tmp_path: Path):
    """All three legs on the same old day are mutually consistent, which is what hid the age."""
    anchor = _leg_anchor(tmp_path, _legs("2026-04-10", "2026-04-29"), publication_lag_days=LAGS)

    assert anchor.degraded is True
    assert anchor.risk_free["nominal_10y"] is None
    assert anchor.risk_free["real_10y"] is None
    assert anchor.risk_free["breakeven_10y"] is None
    assert anchor.risk_free["term_premium"] == pytest.approx(0.0062)
    reasons = " ".join(anchor.provenance.degradation_reasons)
    assert "DGS10" in reasons and "business days" in reasons
    # the oldest of ALL legs: the monthly price indices (period start 2026-04-01) are 29 days on
    assert anchor.oldest_leg_age_days == 29


def test_a_daily_leg_four_business_days_behind_is_kept(tmp_path: Path):
    anchor = _leg_anchor(tmp_path, _legs("2026-04-24", "2026-04-29"), publication_lag_days=LAGS)  # Friday: 4 business days

    assert anchor.risk_free["nominal_10y"] == pytest.approx(0.0445)
    assert anchor.degraded is False
    assert anchor.oldest_leg_age_days == 29


def test_a_monthly_leg_past_release_lag_plus_grace_is_null_and_the_yields_are_still_published(tmp_path: Path):
    # CPI for 2026-01: normal release 44 days after the period, plus 45 days = 89; as-of is 119 days on.
    anchor = _leg_anchor(
        tmp_path,
        _legs("2026-04-29", "2026-04-29", cpi_month="2026-01-01", cpi_base="2025-01-01"),
        publication_lag_days=LAGS,
    )

    assert anchor.inflation["cpi_yoy"] is None
    assert anchor.inflation["pce_yoy"] == pytest.approx(0.0379, abs=1e-4)
    # A late CPI is said and its leg is null, but it does not withhold the 10-year yield (DEGRADING_LEGS).
    assert anchor.degraded is False
    assert anchor.risk_free["nominal_10y"] == pytest.approx(0.0445)
    assert any("CPIAUCSL" in reason and "45" in reason for reason in anchor.provenance.degradation_reasons)
    assert anchor.oldest_leg_age_days == 119


def test_a_monthly_leg_inside_release_lag_plus_grace_is_kept(tmp_path: Path):
    anchor = _leg_anchor(
        tmp_path,
        _legs("2026-04-29", "2026-04-29", cpi_month="2026-03-01", cpi_base="2025-03-01"),
        publication_lag_days=LAGS,
    )

    assert anchor.inflation["cpi_yoy"] == pytest.approx(0.03)
    assert anchor.degraded is False


def test_adjusted_loadings_are_published_beside_the_raw_ones(tmp_path: Path):
    anchor = _healthy_anchor(tmp_path, _panel("2026-04-27"))
    assert anchor.sector_loadings["energy"] == pytest.approx(0.5, abs=1e-3)
    assert set(anchor.sector_loadings_adjusted) == set(anchor.sector_loadings)
    for sector_id, raw in anchor.sector_loadings.items():
        assert anchor.sector_loadings_adjusted[sector_id] == round(0.67 * raw + 0.33, 3)
    assert anchor.sector_loadings_adjusted["information_technology"] == pytest.approx(1.67, abs=1e-3)
    assert anchor.loading_adjustment == {"method": "blume", "weight_raw": 0.67, "weight_one": 0.33}


def test_adjusted_loadings_are_withheld_with_the_raw_ones(tmp_path: Path):
    anchor = _healthy_anchor(tmp_path, _panel("2026-03-31"))
    assert anchor.sector_loadings == {}
    assert anchor.sector_loadings_adjusted == {}
    assert anchor.loading_adjustment is None


def test_a_sector_without_a_raw_loading_has_no_adjusted_entry(tmp_path: Path):
    anchor = _healthy_anchor(tmp_path, _panel("2026-04-29", xle_end="2026-03-01"))
    assert "energy" not in anchor.sector_loadings
    assert "energy" not in anchor.sector_loadings_adjusted
    assert "information_technology" in anchor.sector_loadings_adjusted


def test_adjusted_loadings_survive_a_strict_json_parse(tmp_path: Path):
    anchor = _healthy_anchor(tmp_path, _panel("2026-04-27"))
    text = anchor.model_dump_json()
    parsed = json.loads(text, parse_constant=lambda name: (_ for _ in ()).throw(ValueError(name)))
    assert parsed["sector_loadings_adjusted"] == anchor.sector_loadings_adjusted
    assert parsed["loading_adjustment"]["weight_raw"] == 0.67
