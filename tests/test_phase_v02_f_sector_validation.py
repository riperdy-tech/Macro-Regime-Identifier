from __future__ import annotations

import json
from pathlib import Path

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

    assert payload["valid"] is True
    assert "not a trading backtest" in markdown
    assert "Proxy tickers are validation references only" in markdown
    forbidden = ["Buy ", "Sell ", "Overweight", "Underweight", "Avoid "]
    assert not any(term in markdown for term in forbidden)


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
    assert payload["valid"] is True
    assert "diagnostic validation" in markdown


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
