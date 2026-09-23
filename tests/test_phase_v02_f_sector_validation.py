from __future__ import annotations

import json
from pathlib import Path
import random

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner
import yaml

from macro_engine.cli import app
from macro_engine.sectors.validation import (
    SectorValidationConfig,
    _fetch_stooq_ticker,
    calculate_validation_returns,
    normalize_price_frame,
    run_sector_validation,
    summarize_validation_returns,
    to_stooq_symbol,
)
from macro_engine.sectors.validation_report import (
    build_sector_validation_report,
    sector_validation_markdown,
)
from macro_engine.storage.duckdb_store import DuckDBStore

runner = CliRunner()


def _validation_config(tmp_path: Path | None = None) -> SectorValidationConfig:
    csv_path = "prices.csv" if tmp_path is None else str(tmp_path / "prices.csv")
    return SectorValidationConfig.model_validate(
        {
            "price_provider": {
                "provider": "csv",
                "csv_path": csv_path,
            },
            "benchmark_ticker": "SPY",
            "horizons_months": [1, 3],
            "proxies": {
                "energy": "XLE",
                "utilities": "XLU",
            },
        }
    )


def _sector_scores() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "sector_id": "energy",
                "date": "2026-01-01",
                "raw_sector_score": 1.2,
                "confidence_adjusted_score": 1.0,
                "rank": 1,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.2,
                "valid": True,
                "reason": "ok",
            },
            {
                "sector_id": "utilities",
                "date": "2026-01-01",
                "raw_sector_score": -1.0,
                "confidence_adjusted_score": -0.8,
                "rank": 2,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.2,
                "valid": True,
                "reason": "ok",
            },
            {
                "sector_id": "energy",
                "date": "2026-02-01",
                "raw_sector_score": 0.8,
                "confidence_adjusted_score": 0.6,
                "rank": 1,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.2,
                "valid": True,
                "reason": "ok",
            },
            {
                "sector_id": "utilities",
                "date": "2026-02-01",
                "raw_sector_score": -0.6,
                "confidence_adjusted_score": -0.4,
                "rank": 2,
                "macro_reported_regime": "reflation",
                "macro_raw_dominant_regime": "reflation",
                "macro_confidence": 0.2,
                "valid": True,
                "reason": "ok",
            },
        ]
    )


def _prices() -> pd.DataFrame:
    return normalize_price_frame(
        pd.DataFrame(
            [
                {"ticker": "XLE", "date": "2026-01-01", "close": 100.0},
                {"ticker": "XLE", "date": "2026-02-01", "close": 110.0},
                {"ticker": "XLE", "date": "2026-03-01", "close": 121.0},
                {"ticker": "XLE", "date": "2026-04-01", "close": 130.0},
                {"ticker": "XLE", "date": "2026-05-01", "close": 143.0},
                {"ticker": "XLU", "date": "2026-01-01", "close": 100.0},
                {"ticker": "XLU", "date": "2026-02-01", "close": 101.0},
                {"ticker": "XLU", "date": "2026-03-01", "close": 102.0},
                {"ticker": "XLU", "date": "2026-04-01", "close": 103.0},
                {"ticker": "XLU", "date": "2026-05-01", "close": 104.0},
                {"ticker": "SPY", "date": "2026-01-01", "close": 100.0},
                {"ticker": "SPY", "date": "2026-02-01", "close": 105.0},
                {"ticker": "SPY", "date": "2026-03-01", "close": 110.0},
                {"ticker": "SPY", "date": "2026-04-01", "close": 115.0},
                {"ticker": "SPY", "date": "2026-05-01", "close": 120.0},
            ]
        ),
        source="mock",
    )


def test_forward_and_relative_return_calculation():
    returns = calculate_validation_returns(
        sector_scores=_sector_scores(),
        prices=_prices(),
        config=_validation_config(),
    )

    energy = returns[
        (returns["sector_id"] == "energy")
        & (pd.to_datetime(returns["score_date"]) == pd.Timestamp("2026-01-01"))
    ].iloc[0]
    assert energy["forward_1m_return"] == pytest.approx(0.10)
    assert energy["relative_forward_1m_return"] == pytest.approx(0.05)
    assert energy["forward_3m_return"] == pytest.approx(0.30)
    assert energy["relative_forward_3m_return"] == pytest.approx(0.15)
    assert bool(energy["valid"]) is True


def test_missing_price_handling_marks_invalid():
    prices = _prices()[_prices()["ticker"] != "XLU"]
    returns = calculate_validation_returns(
        sector_scores=_sector_scores(),
        prices=prices,
        config=_validation_config(),
    )

    utilities = returns[returns["sector_id"] == "utilities"]
    assert utilities["valid"].sum() == 0
    assert set(utilities["reason"]) == {"missing_forward_prices"}


def test_validation_does_not_use_prices_far_after_score_date():
    early_scores = _sector_scores().assign(date="1990-01-01")

    returns = calculate_validation_returns(
        sector_scores=early_scores,
        prices=_prices(),
        config=_validation_config(),
    )

    assert returns["valid"].sum() == 0
    assert set(returns["reason"]) == {"missing_forward_prices"}


def test_validation_summary_calculates_rank_ic_and_spreads():
    returns = calculate_validation_returns(
        sector_scores=_sector_scores(),
        prices=_prices(),
        config=_validation_config(),
    )
    summary = summarize_validation_returns(returns, [1, 3])

    one_month = summary[summary["horizon"] == "1m"].iloc[0]
    assert one_month["observation_count"] == 4
    assert one_month["rank_ic_spearman"] == pytest.approx(1.0)
    assert one_month["top_quintile_avg_relative_return"] > 0
    assert one_month["bottom_quintile_avg_relative_return"] < 0
    assert one_month["top_minus_bottom_spread"] > 0
    assert one_month["hit_rate_top_positive"] == pytest.approx(1.0)


def test_sector_validation_report_generation_is_diagnostic():
    """The report is still produced and still safe — but this panel cannot carry a RESULT.

    The fixture is 5 monthly points per ticker with near-monotone drift and `source='mock'`.
    That is not market data, and the provenance gate now says so instead of publishing rank
    ICs computed from it. Asserting `valid is True` here would be asserting that a
    5-observation mock is a broad equity index.
    """
    result = run_sector_validation(
        sector_scores=_sector_scores(),
        prices=_prices(),
        config=_validation_config(),
    )
    payload = build_sector_validation_report(
        returns=result.returns,
        summary=result.summary,
        prices=_prices(),
    )
    markdown = sector_validation_markdown(payload)

    assert payload["valid"] is False
    assert payload["reason"] == "unverified_price_provenance"
    assert payload["price_panel"]["market_observed"] is False
    assert "NOT PUBLISHED AS A RESULT" in markdown
    assert "Proxy tickers are validation references only" in markdown
    forbidden = [
        "Buy ",
        "Sell ",
        "Overweight",
        "Underweight",
        "Avoid ",
        "recommendation",
        "trade",
        "portfolio allocation",
    ]
    assert not any(term in markdown for term in forbidden)


def test_sector_validation_report_is_published_for_a_real_panel():
    """The other half of the contract: a market-like panel DOES publish a result."""
    import numpy as np

    days = 600
    rng = np.random.default_rng(11)
    returns = rng.normal(0.0004, 0.011, days)
    returns[200:260] -= 0.006
    closes = 100 * np.cumprod(1 + returns)
    dates = pd.date_range("2022-01-03", periods=days, freq="B")
    prices = normalize_price_frame(
        pd.DataFrame(
            {
                "ticker": ["SPY"] * days + ["XLE"] * days,
                "date": list(dates) * 2,
                "close": list(closes) + list(closes * 1.05),
            }
        ),
        source="stooq",
    )
    result = run_sector_validation(
        sector_scores=_sector_scores(),
        prices=prices,
        config=_validation_config(),
    )
    payload = build_sector_validation_report(
        returns=result.returns,
        summary=result.summary,
        prices=prices,
    )
    markdown = sector_validation_markdown(payload)
    assert payload["valid"] is True
    assert payload["reason"] is None
    assert "not an implementable performance test" in markdown


def test_sector_validation_cli_flow_with_mocked_csv(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    config_path = _write_validation_config(tmp_path)
    price_path = tmp_path / "prices.csv"
    _prices()[["ticker", "date", "close"]].to_csv(price_path, index=False)
    store = DuckDBStore(db_path)
    store.initialize()
    store.replace_sector_outputs(_sector_scores(), pd.DataFrame(), pd.DataFrame())

    ingest = runner.invoke(
        app,
        [
            "ingest-sector-proxy-prices",
            "--config",
            str(config_path),
            "--db-path",
            str(db_path),
        ],
    )
    assert ingest.exit_code == 0, ingest.output
    assert "price_rows" in ingest.output

    validate = runner.invoke(
        app,
        [
            "run-sector-validation",
            "--config",
            str(config_path),
            "--db-path",
            str(db_path),
        ],
    )
    assert validate.exit_code == 0, validate.output
    assert "valid_return_rows" in validate.output

    summary = runner.invoke(app, ["sector-validation-summary", "--db-path", str(db_path)])
    assert summary.exit_code == 0, summary.output
    summary_payload = json.loads(summary.output)
    assert summary_payload["valid"] is True

    report = runner.invoke(
        app,
        [
            "write-sector-validation-report",
            "--config",
            str(config_path),
            "--db-path",
            str(db_path),
        ],
    )
    assert report.exit_code == 0, report.output
    payload = json.loads((tmp_path / "outputs" / "sector_validation.json").read_text())
    markdown = (tmp_path / "outputs" / "sector_validation.md").read_text()
    # The CLI flow completes and writes both artifacts; the RESULT is withheld because the
    # mocked CSV panel cannot be market data (5 monthly points, `source='mock'`).
    assert payload["valid"] is False
    assert payload["reason"] == "unverified_price_provenance"
    assert "NOT PUBLISHED AS A RESULT" in markdown


def test_stooq_ticker_normalization():
    assert to_stooq_symbol("SPY") == "spy.us"
    assert to_stooq_symbol("spy.us") == "spy.us"
    assert to_stooq_symbol("SPY.US") == "spy.us"
    assert to_stooq_symbol("^SPX") == "^spx"


def test_stooq_csv_parsing():
    session = _FakeSession(
        "Date,Open,High,Low,Close,Volume\n2026-01-02,1,2,1,100.5,1000\n"
    )

    frame, diagnostic = _fetch_stooq_ticker(
        session,
        "SPY",
        start_date="2026-01-01",
        end_date="2026-01-31",
        api_key=None,
    )

    assert diagnostic["classification"] == "csv"
    assert diagnostic["stooq_symbol"] == "spy.us"
    assert frame.iloc[0]["ticker"] == "SPY"
    assert frame.iloc[0]["close"] == 100.5


def test_stooq_html_response_handling():
    session = _FakeSession("<html><body>captcha</body></html>", content_type="text/html")

    frame, diagnostic = _fetch_stooq_ticker(
        session,
        "SPY",
        start_date="2026-01-01",
        end_date="2026-01-31",
        api_key=None,
    )

    assert frame.empty
    assert diagnostic["classification"] == "html"
    assert "captcha" in diagnostic["preview"]


def test_stooq_empty_response_handling():
    session = _FakeSession("")

    frame, diagnostic = _fetch_stooq_ticker(
        session,
        "SPY",
        start_date="2026-01-01",
        end_date="2026-01-31",
        api_key=None,
    )

    assert frame.empty
    assert diagnostic["classification"] == "empty"


def test_stooq_apikey_instruction_response_handling():
    session = _FakeSession("Get your apikey:\nOpen https://stooq.com/q/d/?s=spy.us&get_apikey")

    frame, diagnostic = _fetch_stooq_ticker(
        session,
        "SPY",
        start_date="2026-01-01",
        end_date="2026-01-31",
        api_key=None,
    )

    assert frame.empty
    assert diagnostic["classification"] == "apikey_instruction"
    assert diagnostic["content_type"] == "text/plain; charset=UTF-8"


def _write_validation_config(tmp_path: Path) -> Path:
    path = tmp_path / "sector_validation.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "price_provider": {
                    "provider": "csv",
                    "csv_path": str(tmp_path / "prices.csv"),
                },
                "benchmark_ticker": "SPY",
                "horizons_months": [1, 3],
                "proxies": {
                    "energy": "XLE",
                    "utilities": "XLU",
                },
                "reports": {
                    "output_dir": str(tmp_path / "outputs"),
                },
            }
        ),
        encoding="utf-8",
    )
    return path


class _FakeSession:
    def __init__(self, text: str, content_type: str = "text/plain; charset=UTF-8") -> None:
        self._text = text
        self._content_type = content_type

    def get(self, url, params=None, timeout=30):
        return _FakeResponse(
            text=self._text,
            url=f"{url}?s={params['s']}",
            content_type=self._content_type,
        )


class _FakeResponse:
    def __init__(self, *, text: str, url: str, content_type: str) -> None:
        self.text = text
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None


# ── C1 (MRI_S1_APPROVAL.md §6): cross-section split, Newey-West t ──────────────────────


def test_newey_west_t_matches_naive_at_zero_lag():
    """lag=0 has no autocovariance terms, so it must reduce exactly to the naive
    (population-variance) t-statistic -- the 1-month-horizon case, where there is no
    month-to-month overlap to correct for. Newey-West's own convention normalizes the
    variance estimator by n (not n-1, the way `pandas.Series.std()` does), matching
    statsmodels' `cov_hac`, so the comparison here uses the same population formula."""
    from macro_engine.sectors.validation import newey_west_t

    values = [0.10, -0.05, 0.20, 0.00, 0.15, -0.02]
    arr = np.asarray(values, dtype=float)
    mean = arr.mean()
    population_variance = float(((arr - mean) ** 2).sum()) / len(arr)
    naive_t = mean / ((population_variance**0.5) / (len(values) ** 0.5))

    t_stat, _ = newey_west_t(values, lag=0)
    assert t_stat == pytest.approx(naive_t)


def test_newey_west_t_none_for_degenerate_series():
    from macro_engine.sectors.validation import newey_west_t

    assert newey_west_t([], lag=2) == (None, None)
    assert newey_west_t([0.1], lag=2) == (None, None)
    # zero variance: every value identical -> long-run variance is 0, not divide-by-zero.
    assert newey_west_t([0.1, 0.1, 0.1], lag=1) == (None, None)


def test_newey_west_t_positive_serial_correlation_widens_the_interval():
    """A positively autocorrelated series (the expected shape for overlapping-window
    returns) must produce a smaller |t| than the naive calculation -- the whole point of
    the correction. This is what made the review's sqrt(h) approximation the wrong
    direction of caution for `t_overlap_corrected` to skip."""
    from macro_engine.sectors.validation import newey_west_t

    # A trending, positively autocorrelated series.
    values = [0.05, 0.06, 0.07, 0.06, 0.08, 0.09, 0.07, 0.10, 0.08, 0.11]
    series = pd.Series(values, dtype=float)
    naive_t = series.mean() / (series.std() / (len(values) ** 0.5))

    t_stat, _ = newey_west_t(values, lag=2)
    assert t_stat is not None
    assert abs(t_stat) < abs(naive_t)


def _cross_section_returns() -> pd.DataFrame:
    # 4 "GICS" sectors + 1 sub-industry (a real one from measure_baseline's
    # SUB_INDUSTRY_SECTOR_IDS, so the equality test below can use the same set), over 6
    # dates. Scores are fixed per sector; returns track score plus noise large enough to
    # occasionally re-rank adjacent sectors, so the per-date IC series has genuine
    # cross-date variance (a fixture where every date's IC is identically 1.0 gives an
    # exactly-zero cross-date variance, which is a real degenerate case, not a bug, but
    # not what this fixture is for).
    rng = random.Random(20260923)
    sector_scores = {
        "energy": 2.0,
        "utilities": 1.0,
        "financials": -1.0,
        "healthcare": -2.0,
        "semiconductors": 1.5,
    }
    rows = []
    for date_index in range(6):
        score_date = f"2026-0{date_index + 1}-01"
        for sector_id, score in sector_scores.items():
            noise = rng.uniform(-3.0, 3.0)
            rows.append(
                {
                    "sector_id": sector_id,
                    "score_date": score_date,
                    "confidence_adjusted_score": score,
                    "relative_forward_1m_return": 0.01 * score + 0.008 * noise,
                    "relative_forward_3m_return": 0.03 * score + 0.015 * noise,
                    "valid": True,
                }
            )
    return pd.DataFrame(rows)


def test_summarize_validation_returns_splits_gics_and_subindustry_cross_sections():
    """C1: a sub-industry's rows must never leak into gics_11 -- the screener's gate reads
    that cross-section specifically, and S1.5 already ranks the two blocks separately for
    the same reason."""
    returns = _cross_section_returns()
    sub_industry_ids = {"semiconductors"}

    summary = summarize_validation_returns(returns, [1, 3], sub_industry_ids, run_id="run-x")

    gics_3m = summary[(summary["cross_section"] == "gics_11") & (summary["horizon"] == "3m")].iloc[0]
    sub_3m = summary[(summary["cross_section"] == "subindustry_6") & (summary["horizon"] == "3m")].iloc[0]
    pooled_3m = summary[(summary["cross_section"] == "pooled_17") & (summary["horizon"] == "3m")].iloc[0]

    assert gics_3m["observation_count"] == 4 * 6  # 4 GICS sectors x 6 dates
    assert sub_3m["observation_count"] == 1 * 6  # semiconductors only
    assert pooled_3m["observation_count"] == 5 * 6  # everything
    assert gics_3m["run_id"] == "run-x"
    assert gics_3m["n_dates"] == 6
    assert gics_3m["rank_ic_spearman"] > 0.5  # strong rank match by construction, not perfect
    assert gics_3m["t_overlap_corrected"] is not None
    assert gics_3m["score_end_date"] == "2026-06-01"


def test_gics_11_numbers_match_measure_baseline_script():
    """C1 rule 2: the gics_11 numbers published in `validation` must equal
    `scripts/measure_baseline.py`'s `11_row_cross_section` on the same data -- including
    the Newey-West t, since both now share the exact same `newey_west_t` implementation."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "measure_baseline_module",
        Path(__file__).resolve().parents[1] / "scripts" / "measure_baseline.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    returns = _cross_section_returns()
    sub_industry_ids = module.SUB_INDUSTRY_SECTOR_IDS
    assert "semiconductors" in sub_industry_ids  # the fixture's sub-industry stand-in

    summary = summarize_validation_returns(returns, [1, 3], sub_industry_ids)
    gics_3m = summary[(summary["cross_section"] == "gics_11") & (summary["horizon"] == "3m")].iloc[0]
    gics_1m = summary[(summary["cross_section"] == "gics_11") & (summary["horizon"] == "1m")].iloc[0]

    baseline = module._measure_sector_rank_ic(returns)
    baseline_3m = baseline["11_row_cross_section"]["3m"]
    baseline_1m = baseline["11_row_cross_section"]["1m"]

    # measure_baseline.py rounds every float to 6 digits before returning (its own
    # byte-identity reproducibility contract); production does not round, so the
    # comparison tolerance is set to that rounding, not float equality.
    for production_row, baseline_row in ((gics_3m, baseline_3m), (gics_1m, baseline_1m)):
        assert production_row["n_dates"] == baseline_row["n_dates"]
        assert production_row["rank_ic_spearman"] == pytest.approx(baseline_row["mean_per_date_ic"], abs=1e-6)
        assert production_row["sd_per_date_ic"] == pytest.approx(baseline_row["sd_per_date_ic"], abs=1e-6)
        assert production_row["t_naive"] == pytest.approx(baseline_row["naive_t"], abs=1e-6)
        assert production_row["t_overlap_corrected"] == pytest.approx(baseline_row["overlap_corrected_t"], abs=1e-6)
        assert production_row["positive_share"] == pytest.approx(baseline_row["positive_share_of_dates"], abs=1e-6)


def test_validation_missing_when_no_gics_11_rows():
    """No sector_validation_summary rows at all (validation never ran for this vintage) is
    `validation_missing`, per C1 rule 4."""
    from macro_engine.sectors.report import _build_validation_block

    block = _build_validation_block(pd.DataFrame(), "run-a")
    assert block["reasons"] == ["validation_missing"]
    assert block["horizon_3m"]["rank_ic"] is None
    assert block["horizon_3m"]["t_overlap_corrected"] is None


def test_validation_score_end_date_has_no_time_component_after_a_db_round_trip():
    """score_end_date round-trips through a DuckDB DATE column, which pandas reads back as
    a Timestamp -- str() on that carries a spurious "00:00:00" (the same defect class C3
    fixed on current_regime.json's own `date` field)."""
    from macro_engine.sectors.report import _build_validation_block

    summary = pd.DataFrame(
        [
            {
                "cross_section": "gics_11",
                "horizon": "3m",
                "observation_count": 100,
                "rank_ic_spearman": 0.05,
                "n_dates": 20,
                "sd_per_date_ic": 0.3,
                "t_naive": 0.7,
                "t_overlap_corrected": 0.4,
                "positive_share": 0.6,
                "score_end_date": pd.Timestamp("2026-08-01"),  # as DuckDB hands it back
                "run_id": "run-a",
            }
        ]
    )
    block = _build_validation_block(summary, "run-a")
    assert block["score_end_date"] == "2026-08-01"


def test_validation_stale_when_validated_run_id_differs_from_source_run_id():
    """C1 rule 4: a validation run against an OLDER sector-scoring run must not be
    presented as describing the current ranking -- every numeric leaf nulls out."""
    from macro_engine.sectors.report import _build_validation_block

    summary = pd.DataFrame(
        [
            {
                "cross_section": "gics_11",
                "horizon": "3m",
                "observation_count": 100,
                "rank_ic_spearman": 0.05,
                "n_dates": 20,
                "sd_per_date_ic": 0.3,
                "t_naive": 0.7,
                "t_overlap_corrected": 0.4,
                "positive_share": 0.6,
                "score_end_date": "2026-08-01",
                "run_id": "old-run",
            }
        ]
    )
    block = _build_validation_block(summary, "new-run")
    assert block["reasons"] == ["validation_stale:old-run"]
    assert block["horizon_3m"]["rank_ic"] is None
    assert block["validated_run_id"] == "old-run"
