"""Sector-validation price-panel provenance gate.

Every statistic the validation report publishes — rank IC, top-bottom spread, hit rate — is a
claim about how sector scores related to REALISED returns. Computed from a generated panel
those are not weak evidence, they are fabricated evidence wearing the same clothes, and
`sector_validation.json` is a REQUIRED dashboard artifact, so its existence alone reads as
`data_status: complete`.

The gate is falsification on the data, not a label on the source: a legitimate CSV load and a
generated sample both arrive as `source='csv'`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from macro_engine.sectors.validation import assess_price_panel
from macro_engine.sectors.validation_report import build_sector_validation_report


def _market_like_panel(days: int = 600, seed: int = 7) -> pd.DataFrame:
    """A panel that behaves like a broad equity index: ~18% vol and a real drawdown."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0004, 0.011, days)
    # Inject a genuine bear phase so the drawdown check has something real to find.
    returns[200:260] -= 0.006
    closes = 100 * np.cumprod(1 + returns)
    dates = pd.date_range("2022-01-03", periods=days, freq="B")
    return pd.DataFrame(
        {
            "ticker": ["SPY"] * days + ["XLK"] * days,
            "date": list(dates) * 2,
            "close": list(closes) + list(closes * 1.1),
            "source": ["stooq"] * (2 * days),
        }
    )


def _synthetic_like_panel() -> pd.DataFrame:
    """Reproduces the measured signature of the generated sample file this gate was written for."""
    dates = pd.date_range("2020-01-02", periods=800, freq="B")
    gently_rising = np.linspace(300, 380, 800)  # monotone: no drawdown, tiny vol
    return pd.DataFrame(
        {
            "ticker": ["SPY"] * 800,
            "date": dates,
            "close": gently_rising,
            "source": ["csv"] * 800,
        }
    )


def test_a_real_market_panel_passes():
    assessment = assess_price_panel(_market_like_panel(), benchmark_ticker="SPY")
    assert assessment.market_observed is True
    assert assessment.reasons == []
    assert 0.08 <= assessment.checks["annual_vol"] <= 0.45
    assert assessment.checks["max_drawdown"] <= -0.15


def test_the_synthetic_signature_is_rejected():
    """5%-vol, monotone, never-draws-down: cannot be a broad equity index."""
    assessment = assess_price_panel(_synthetic_like_panel(), benchmark_ticker="SPY")
    assert assessment.market_observed is False
    joined = " ".join(assessment.reasons)
    assert "annualised volatility" in joined
    assert "drawdown" in joined or "distinct returns" in joined


def test_source_label_alone_cannot_pass_a_panel():
    """A 'stooq' label on generated numbers must not buy a pass."""
    panel = _synthetic_like_panel()
    panel["source"] = "stooq"
    assert assess_price_panel(panel, benchmark_ticker="SPY").market_observed is False


def test_a_csv_label_on_real_behaviour_still_passes():
    """The gate must not reject a legitimate CSV load just for being a CSV."""
    panel = _market_like_panel()
    panel["source"] = "csv"
    assert assess_price_panel(panel, benchmark_ticker="SPY").market_observed is True


def test_missing_benchmark_is_reported_not_assumed():
    panel = _market_like_panel()
    assessment = assess_price_panel(panel[panel["ticker"] != "SPY"], benchmark_ticker="SPY")
    assert assessment.market_observed is False
    assert any("absent from the panel" in reason for reason in assessment.reasons)


def test_empty_panel_is_reported():
    assessment = assess_price_panel(pd.DataFrame(), benchmark_ticker="SPY")
    assert assessment.market_observed is False
    assert assessment.reasons == ["no price rows"]


def _summary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "horizon": "1m",
                "observation_count": 836,
                "rank_ic_spearman": -0.045,
                "top_quintile_avg_relative_return": -0.0004,
                "bottom_quintile_avg_relative_return": 0.005,
                "top_minus_bottom_spread": -0.0054,
                "hit_rate_top_positive": 0.493,
                "notes": "diagnostic_validation_not_trading_backtest",
            }
        ]
    )


def _returns() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=12, freq="MS")
    return pd.DataFrame(
        {
            "sector_id": ["energy"] * 12,
            "score_date": dates,
            "valid": [True] * 12,
            "relative_forward_1m_return": [0.01] * 12,
        }
    )


def test_report_withholds_validity_when_the_panel_fails_falsification():
    payload = build_sector_validation_report(
        returns=_returns(),
        summary=_summary(),
        prices=_synthetic_like_panel(),
        benchmark_ticker="SPY",
    )
    assert payload["valid"] is False
    assert payload["reason"] == "unverified_price_provenance"
    # The measurements are still present, and clearly marked as not asserted.
    assert payload["summary"]
    assert payload["price_panel"]["market_observed"] is False
    assert payload["price_panel"]["reasons"]


def test_report_asserts_validity_when_the_panel_is_real():
    payload = build_sector_validation_report(
        returns=_returns(),
        summary=_summary(),
        prices=_market_like_panel(),
        benchmark_ticker="SPY",
    )
    assert payload["valid"] is True
    assert payload["reason"] is None
    assert payload["price_panel"]["market_observed"] is True


def test_refreshing_the_panel_replaces_the_whole_ticker_history(tmp_path):
    """A refresh must not leave rows the new panel does not cover.

    The real failure: refreshing a synthetic panel with real prices left the OLD values on
    market holidays -- the days the new source has no row for. The stitched series printed SPY
    +83% on Juneteenth 2022 and an annualised volatility of 62% against a true ~19%.
    """
    from macro_engine.storage.duckdb_store import DuckDBStore

    store = DuckDBStore(tmp_path / "macro.duckdb")
    store.initialize()
    old = pd.DataFrame(
        {
            "ticker": ["SPY"] * 4,
            "date": pd.to_datetime(
                ["2022-06-17", "2022-06-20", "2022-06-21", "2022-06-22"]  # 06-20 = Juneteenth
            ).date,
            "close": [366.0, 671.0, 375.0, 380.0],
            "source": ["csv"] * 4,
            "fetched_at": pd.Timestamp("2022-07-01", tz="UTC"),
        }
    )
    store.upsert_sector_proxy_prices(old)
    assert len(store.read_sector_proxy_prices()) == 4

    # The real source has no row for the holiday.
    new = old[old["date"] != pd.Timestamp("2022-06-20").date()].copy()
    new["close"] = [360.0, 370.0, 372.0]
    new["source"] = "stooq"
    store.upsert_sector_proxy_prices(new)

    stored = store.read_sector_proxy_prices()
    assert len(stored) == 3, "the orphaned holiday row survived the refresh"
    assert not (pd.to_datetime(stored["date"]) == pd.Timestamp("2022-06-20")).any()
    assert set(stored["source"]) == {"stooq"}


def test_refresh_of_one_ticker_leaves_other_tickers_alone(tmp_path):
    """Ticker-scoped replacement, not a table wipe."""
    from macro_engine.storage.duckdb_store import DuckDBStore

    store = DuckDBStore(tmp_path / "macro.duckdb")
    store.initialize()
    dates = pd.to_datetime(["2024-01-02", "2024-01-03"]).date

    def frame(ticker: str, closes: list[float], source: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ticker": [ticker] * 2,
                "date": dates,
                "close": closes,
                "source": [source] * 2,
                "fetched_at": pd.Timestamp("2024-01-04", tz="UTC"),
            }
        )

    store.upsert_sector_proxy_prices(frame("SPY", [100.0, 101.0], "csv"))
    store.upsert_sector_proxy_prices(frame("XLK", [50.0, 51.0], "csv"))
    store.upsert_sector_proxy_prices(frame("SPY", [200.0, 202.0], "stooq"))

    stored = store.read_sector_proxy_prices()
    assert set(stored["ticker"]) == {"SPY", "XLK"}
    assert sorted(stored[stored["ticker"] == "SPY"]["close"]) == [200.0, 202.0]
    assert sorted(stored[stored["ticker"] == "XLK"]["close"]) == [50.0, 51.0]


def test_withheld_report_says_so_in_markdown():
    from macro_engine.sectors.validation_report import sector_validation_markdown

    payload = build_sector_validation_report(
        returns=_returns(),
        summary=_summary(),
        prices=_synthetic_like_panel(),
        benchmark_ticker="SPY",
    )
    markdown = sector_validation_markdown(payload)
    assert "NOT PUBLISHED AS A RESULT" in markdown
    assert "unverified_price_provenance" in markdown
