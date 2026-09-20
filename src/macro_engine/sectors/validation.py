from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field
import requests
import yaml

from macro_engine.storage.duckdb_store import DuckDBStore

MAX_PRICE_LOOKAHEAD_DAYS = 7

# ── Price-panel provenance gate ───────────────────────────────────────────────
# Validation statistics computed on a panel that is not market data are not weak evidence,
# they are fabricated evidence, and the report has no way to know unless it checks. The
# `source` column cannot decide this on its own -- a legitimate CSV load and a generated
# sample both arrive as "csv" -- so the test is FALSIFICATION on the data itself: numbers
# that fail these bounds cannot be a broad equity index.
#
# Measured basis for the bounds (SPY, daily, 1998-2026): realised annualised volatility runs
# ~12-30% depending on window; the 2008 and 2020 drawdowns exceed -30%; and no real multi-year
# equity benchmark window is monotone. The synthetic panel this gate was written for showed
# 5.1% volatility, SPY RISING through March 2020, and an XLE/SPY correlation of -0.12.
PANEL_MIN_ANNUAL_VOL = 0.08
PANEL_MAX_ANNUAL_VOL = 0.45
PANEL_MIN_DRAWDOWN = -0.15
PANEL_MIN_OBSERVATIONS = 250


@dataclass(frozen=True)
class PanelAssessment:
    market_observed: bool
    checks: dict[str, Any]
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_observed": self.market_observed,
            "checks": self.checks,
            "reasons": self.reasons,
        }


def assess_price_panel(
    prices: pd.DataFrame,
    *,
    benchmark_ticker: str = "SPY",
) -> PanelAssessment:
    """Decide whether a price panel can be market data, by trying to falsify it.

    Returns an assessment carrying the measured checks, so a `False` verdict is auditable
    rather than an opaque refusal.
    """
    checks: dict[str, Any] = {
        "benchmark_ticker": benchmark_ticker,
        "sources": [],
        "observations": 0,
    }
    reasons: list[str] = []
    if prices.empty:
        return PanelAssessment(False, checks, ["no price rows"])

    frame = prices.copy()
    if "source" in frame.columns:
        checks["sources"] = sorted({str(v) for v in frame["source"].dropna().unique()})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "date", "close"])
    checks["observations"] = int(len(frame))

    benchmark = frame[frame["ticker"] == benchmark_ticker].sort_values("date")
    if benchmark.empty:
        return PanelAssessment(False, checks, [f"benchmark {benchmark_ticker} absent from the panel"])
    if len(benchmark) < PANEL_MIN_OBSERVATIONS:
        reasons.append(
            f"benchmark has {len(benchmark)} observations; {PANEL_MIN_OBSERVATIONS} required"
        )

    closes = benchmark["close"].astype(float)
    returns = closes.pct_change().dropna()
    checks["annual_vol"] = None if returns.empty else round(float(returns.std() * (252 ** 0.5)), 4)
    running_max = closes.cummax()
    checks["max_drawdown"] = round(float((closes / running_max - 1.0).min()), 4)
    checks["distinct_returns"] = int(returns.round(6).nunique())
    checks["start"] = str(benchmark["date"].min().date())
    checks["end"] = str(benchmark["date"].max().date())

    vol = checks["annual_vol"]
    if vol is None:
        reasons.append("benchmark has no returns")
    elif not PANEL_MIN_ANNUAL_VOL <= vol <= PANEL_MAX_ANNUAL_VOL:
        reasons.append(
            f"benchmark annualised volatility {vol:.1%} is outside "
            f"[{PANEL_MIN_ANNUAL_VOL:.0%}, {PANEL_MAX_ANNUAL_VOL:.0%}] -- not a broad equity index"
        )
    drawdown = checks["max_drawdown"]
    if drawdown is not None and drawdown > PANEL_MIN_DRAWDOWN:
        reasons.append(
            f"benchmark worst drawdown {drawdown:.1%} is shallower than "
            f"{PANEL_MIN_DRAWDOWN:.0%} -- no real multi-year equity window is that smooth"
        )
    if checks["distinct_returns"] < 20:
        reasons.append(
            f"benchmark has only {checks['distinct_returns']} distinct returns -- degenerate series"
        )

    return PanelAssessment(not reasons, checks, reasons)


class PriceProviderConfig(BaseModel):
    # `stooq` remains the shipped default. It is currently JavaScript-bot-challenged on every
    # endpoint (2026-09-20), so `ingest-sector-proxy-prices` fails loudly rather than degrading
    # silently — correct, but it leaves CI without a working source until an operator picks one.
    # `yahoo` is a tested, key-free alternative; switching is a one-line config change.
    provider: Literal["csv", "stooq", "yahoo"] = "csv"
    csv_path: str = "data/validation/sector_proxy_prices.csv"
    start_date: str = "1998-12-22"
    end_date: str | None = None
    api_key_env: str | None = None


class SectorValidationConfig(BaseModel):
    price_provider: PriceProviderConfig = PriceProviderConfig()
    benchmark_ticker: str = "SPY"
    horizons_months: list[int] = Field(default_factory=lambda: [1, 3])
    proxies: dict[str, str]
    output_dir: str = "outputs"


@dataclass(frozen=True)
class SectorValidationResult:
    returns: pd.DataFrame
    summary: pd.DataFrame


def load_sector_validation_config(path: str | Path = "config/sector_validation.yaml") -> SectorValidationConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    payload = dict(data)
    payload["output_dir"] = data.get("reports", {}).get("output_dir", "outputs")
    return SectorValidationConfig.model_validate(payload)


def load_proxy_prices(config: SectorValidationConfig) -> pd.DataFrame:
    if config.price_provider.provider == "stooq":
        return load_stooq_prices(config)
    if config.price_provider.provider == "yahoo":
        return load_yahoo_prices(config)
    if config.price_provider.provider != "csv":
        raise ValueError(f"unsupported price provider {config.price_provider.provider}")
    path = Path(config.price_provider.csv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"sector proxy price CSV not found: {path}. "
            "Provide a CSV with ticker,date,close columns or use mocked prices in tests."
        )
    frame = pd.read_csv(path)
    return normalize_price_frame(frame, source="csv")


def load_yahoo_prices(config: SectorValidationConfig) -> pd.DataFrame:
    """Sector ETF history from Yahoo's public chart endpoint (no API key).

    A first-class provider rather than a script-only workaround so `provider: yahoo` is a
    config switch and the daily/CI path exercises the same code the operator would. Raises when
    no ticker returns data, exactly like the stooq path: a provider that cannot fetch must fail
    loudly, because a stale panel on disk reads as measured market data.
    """
    frames: list[pd.DataFrame] = []
    failed: list[tuple[str, str]] = []
    session = requests.Session()
    session.headers.update({"User-Agent": YAHOO_USER_AGENT, "Accept": "application/json"})
    tickers = sorted(set(config.proxies.values()) | {config.benchmark_ticker})
    for ticker in tickers:
        try:
            frame = _fetch_yahoo_ticker(session, ticker, start=config.price_provider.start_date)
        except requests.RequestException as exc:
            failed.append((ticker, str(exc)[:80]))
            continue
        if frame.empty:
            failed.append((ticker, "no rows returned"))
            continue
        frames.append(frame)
    if not frames:
        raise ValueError(
            "Yahoo provider returned no price data for any ticker. "
            f"Failures: {failed[:5]}"
        )
    return normalize_price_frame(pd.concat(frames, ignore_index=True), source="yahoo")


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125 Safari/537.36"
)


def _fetch_yahoo_ticker(
    session: requests.Session,
    ticker: str,
    *,
    start: str,
    end: str | None = None,
) -> pd.DataFrame:
    """Daily closes for one ticker. Empty frame (not an exception) when unavailable."""
    period1 = int(pd.Timestamp(start).timestamp())
    period2 = int(pd.Timestamp(end).timestamp()) if end else int(pd.Timestamp.now("UTC").timestamp())
    response = session.get(
        YAHOO_CHART_URL.format(symbol=ticker),
        params={"period1": period1, "period2": period2, "interval": "1d"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    results = ((payload.get("chart") or {}).get("result")) or []
    if not results:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    result = results[0]
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    if not timestamps or not closes:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    frame = pd.DataFrame(
        {
            "ticker": ticker,
            "date": pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None).normalize(),
            "close": closes,
        }
    )
    return frame.dropna(subset=["close"])


def load_stooq_prices(config: SectorValidationConfig) -> pd.DataFrame:
    tickers = sorted(set(config.proxies.values()) | {config.benchmark_ticker})
    frames = []
    diagnostics = []
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/125 Safari/537.36"
            ),
            "Accept": "text/csv,text/plain,*/*",
            "Referer": "https://stooq.com/",
        }
    )
    try:
        session.get("https://stooq.com/", timeout=30)
    except requests.RequestException:
        pass
    for ticker in tickers:
        frame, diagnostic = _fetch_stooq_ticker(
            session,
            ticker,
            start_date=config.price_provider.start_date,
            end_date=config.price_provider.end_date,
            api_key=os.getenv(config.price_provider.api_key_env)
            if config.price_provider.api_key_env
            else None,
        )
        diagnostics.append(diagnostic)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise ValueError(
            "Stooq provider returned no CSV-shaped price data. "
            f"First response diagnostic: {diagnostics[0] if diagnostics else 'no responses'}"
        )
    return normalize_price_frame(pd.concat(frames, ignore_index=True), source="stooq")


def to_stooq_symbol(ticker: str) -> str:
    symbol = ticker.strip().lower()
    if not symbol:
        return symbol
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return f"{symbol}.us"


def normalize_price_frame(prices: pd.DataFrame, *, source: str = "mock") -> pd.DataFrame:
    required = {"ticker", "date", "close"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"price data missing required columns: {sorted(missing)}")
    frame = prices[["ticker", "date", "close"]].copy()
    frame["ticker"] = frame["ticker"].astype(str)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["ticker", "date", "close"])
    frame["source"] = source
    frame["fetched_at"] = pd.Timestamp.now(tz="UTC")
    return frame.sort_values(["ticker", "date"]).reset_index(drop=True)


def _fetch_stooq_ticker(
    session: requests.Session,
    ticker: str,
    *,
    start_date: str,
    end_date: str | None,
    api_key: str | None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    start = pd.Timestamp(start_date).strftime("%Y%m%d")
    end = pd.Timestamp(end_date or pd.Timestamp.today()).strftime("%Y%m%d")
    stooq_symbol = to_stooq_symbol(ticker)
    url = "https://stooq.com/q/d/l/"
    params = {"s": stooq_symbol, "i": "d", "d1": start, "d2": end}
    if api_key:
        params["apikey"] = api_key
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    diagnostic = _stooq_response_diagnostic(response, ticker=ticker, symbol=stooq_symbol)
    text = response.text.strip()
    if not _looks_like_stooq_csv(text):
        return pd.DataFrame(columns=["ticker", "date", "close"]), diagnostic
    from io import StringIO

    try:
        frame = pd.read_csv(StringIO(text))
    except pd.errors.ParserError:
        diagnostic["classification"] = "csv_parse_error"
        return pd.DataFrame(columns=["ticker", "date", "close"]), diagnostic
    if frame.empty or "Close" not in frame.columns or "Date" not in frame.columns:
        diagnostic["classification"] = "missing_csv_columns"
        return pd.DataFrame(columns=["ticker", "date", "close"]), diagnostic
    return (
        pd.DataFrame(
            {
                "ticker": ticker,
                "date": frame["Date"],
                "close": frame["Close"],
            }
        ),
        diagnostic,
    )


def _looks_like_stooq_csv(text: str) -> bool:
    if not text:
        return False
    first_line = text.splitlines()[0].strip().lower()
    return first_line.startswith("date,") and "close" in first_line


def _stooq_response_diagnostic(
    response: requests.Response,
    *,
    ticker: str,
    symbol: str,
) -> dict[str, str]:
    preview = response.text[:200].replace("\n", " ").replace("\r", " ")
    text_lower = response.text[:500].lower()
    if _looks_like_stooq_csv(response.text.strip()):
        classification = "csv"
    elif "get your apikey" in text_lower:
        classification = "apikey_instruction"
    elif "<html" in text_lower or "<!doctype" in text_lower:
        classification = "html"
    elif not response.text.strip():
        classification = "empty"
    else:
        classification = "non_csv"
    return {
        "ticker": ticker,
        "stooq_symbol": symbol,
        "url": response.url,
        "status_code": str(response.status_code),
        "content_type": response.headers.get("content-type", ""),
        "classification": classification,
        "preview": preview,
    }


def ingest_sector_proxy_prices(
    *,
    config_path: str | Path = "config/sector_validation.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> pd.DataFrame:
    config = load_sector_validation_config(config_path)
    prices = load_proxy_prices(config)
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_sector_proxy_prices(prices)
    return prices


def run_stored_sector_validation(
    *,
    config_path: str | Path = "config/sector_validation.yaml",
    db_path: str | Path = "data/macro_engine.duckdb",
) -> SectorValidationResult:
    config = load_sector_validation_config(config_path)
    store = DuckDBStore(db_path)
    store.initialize()
    result = run_sector_validation(
        sector_scores=store.read_table("sector_scores"),
        prices=store.read_sector_proxy_prices(),
        config=config,
    )
    store.replace_sector_validation_outputs(result.returns, result.summary)
    return result


def run_sector_validation(
    *,
    sector_scores: pd.DataFrame,
    prices: pd.DataFrame,
    config: SectorValidationConfig,
) -> SectorValidationResult:
    returns = calculate_validation_returns(
        sector_scores=sector_scores,
        prices=prices,
        config=config,
    )
    summary = summarize_validation_returns(returns, config.horizons_months)
    return SectorValidationResult(returns=returns, summary=summary)


def calculate_validation_returns(
    *,
    sector_scores: pd.DataFrame,
    prices: pd.DataFrame,
    config: SectorValidationConfig,
) -> pd.DataFrame:
    if sector_scores.empty:
        return pd.DataFrame(columns=_return_columns())
    price_lookup = _prepared_price_lookup(prices)
    scores = sector_scores.copy()
    scores["date"] = pd.to_datetime(scores["date"], errors="coerce")
    scores = scores[scores["valid"]].sort_values(["date", "rank"])
    rows: list[dict[str, Any]] = []
    for score in scores.to_dict(orient="records"):
        sector_id = score["sector_id"]
        proxy_ticker = config.proxies.get(sector_id)
        row: dict[str, Any] = {
            "sector_id": sector_id,
            "proxy_ticker": proxy_ticker,
            "score_date": pd.Timestamp(score["date"]).date(),
            "sector_score": _optional_float(score.get("raw_sector_score")),
            "confidence_adjusted_score": _optional_float(
                score.get("confidence_adjusted_score")
            ),
            "forward_1m_return": None,
            "forward_3m_return": None,
            "relative_forward_1m_return": None,
            "relative_forward_3m_return": None,
            "valid": True,
            "reason": "ok",
        }
        if proxy_ticker is None:
            row["valid"] = False
            row["reason"] = "missing_proxy_ticker"
            rows.append(row)
            continue
        for horizon in config.horizons_months:
            sector_return = _forward_return(
                price_lookup,
                proxy_ticker,
                pd.Timestamp(score["date"]),
                horizon,
            )
            benchmark_return = _forward_return(
                price_lookup,
                config.benchmark_ticker,
                pd.Timestamp(score["date"]),
                horizon,
            )
            forward_column = f"forward_{horizon}m_return"
            relative_column = f"relative_forward_{horizon}m_return"
            if forward_column in row:
                row[forward_column] = sector_return
            if relative_column in row:
                row[relative_column] = (
                    None
                    if sector_return is None or benchmark_return is None
                    else sector_return - benchmark_return
                )
        if all(
            row.get(f"relative_forward_{horizon}m_return") is None
            for horizon in config.horizons_months
        ):
            row["valid"] = False
            row["reason"] = "missing_forward_prices"
        rows.append(row)
    return pd.DataFrame(rows, columns=_return_columns())


def summarize_validation_returns(
    returns: pd.DataFrame,
    horizons: list[int],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if returns.empty:
        return pd.DataFrame(rows, columns=_summary_columns())
    for horizon in horizons:
        relative_column = f"relative_forward_{horizon}m_return"
        valid = returns[returns["valid"] & returns[relative_column].notna()].copy()
        if valid.empty:
            rows.append(_summary_row(f"{horizon}m", 0, None, None, None, None, None, "no_valid_returns"))
            continue
        ic_values = []
        top_returns = []
        bottom_returns = []
        for _, group in valid.groupby("score_date", dropna=False):
            if len(group) < 2:
                continue
            ic = _spearman(group["confidence_adjusted_score"], group[relative_column])
            if ic is not None:
                ic_values.append(ic)
            ordered = group.sort_values("confidence_adjusted_score", ascending=False)
            bucket_size = max(1, int(round(len(ordered) * 0.2)))
            top_returns.extend(ordered.head(bucket_size)[relative_column].tolist())
            bottom_returns.extend(ordered.tail(bucket_size)[relative_column].tolist())
        rank_ic = None if not ic_values else float(pd.Series(ic_values).mean())
        top_avg = None if not top_returns else float(pd.Series(top_returns).mean())
        bottom_avg = None if not bottom_returns else float(pd.Series(bottom_returns).mean())
        spread = None if top_avg is None or bottom_avg is None else top_avg - bottom_avg
        hit_rate = None if not top_returns else float((pd.Series(top_returns) > 0).mean())
        rows.append(
            _summary_row(
                f"{horizon}m",
                int(len(valid)),
                rank_ic,
                top_avg,
                bottom_avg,
                spread,
                hit_rate,
                "diagnostic_validation_not_trading_backtest",
            )
        )
    return pd.DataFrame(rows, columns=_summary_columns())


def _prepared_prices(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.dropna(subset=["ticker", "date", "close"]).sort_values(["ticker", "date"])


def _prepared_price_lookup(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    frame = _prepared_prices(prices)
    return {
        ticker: group.sort_values("date").reset_index(drop=True)
        for ticker, group in frame.groupby("ticker")
    }


def _forward_return(
    prices: dict[str, pd.DataFrame],
    ticker: str,
    score_date: pd.Timestamp,
    horizon_months: int,
) -> float | None:
    ticker_prices = prices.get(ticker)
    if ticker_prices is None:
        return None
    if ticker_prices.empty:
        return None
    start = _price_on_or_after(ticker_prices, score_date)
    end = _price_on_or_after(
        ticker_prices, score_date + pd.DateOffset(months=horizon_months)
    )
    if start is None or end is None or start <= 0:
        return None
    return (end / start) - 1.0


def _price_on_or_after(prices: pd.DataFrame, date: pd.Timestamp) -> float | None:
    index = prices["date"].searchsorted(date, side="left")
    if index >= len(prices):
        return None
    first = prices.iloc[int(index)]
    lag_days = (pd.Timestamp(first["date"]) - date).days
    if lag_days > MAX_PRICE_LOOKAHEAD_DAYS:
        return None
    return float(first["close"])


def _spearman(left: pd.Series, right: pd.Series) -> float | None:
    frame = pd.DataFrame({"left": left, "right": right}).dropna()
    if len(frame) < 2:
        return None
    left_rank = frame["left"].rank()
    right_rank = frame["right"].rank()
    if left_rank.nunique() < 2 or right_rank.nunique() < 2:
        return None
    value = left_rank.corr(right_rank)
    return None if pd.isna(value) else float(value)


def _summary_row(
    horizon: str,
    observation_count: int,
    rank_ic_spearman: float | None,
    top_avg: float | None,
    bottom_avg: float | None,
    spread: float | None,
    hit_rate: float | None,
    notes: str,
) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "observation_count": observation_count,
        "rank_ic_spearman": rank_ic_spearman,
        "top_quintile_avg_relative_return": top_avg,
        "bottom_quintile_avg_relative_return": bottom_avg,
        "top_minus_bottom_spread": spread,
        "hit_rate_top_positive": hit_rate,
        "notes": notes,
    }


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _return_columns() -> list[str]:
    return [
        "sector_id",
        "proxy_ticker",
        "score_date",
        "sector_score",
        "confidence_adjusted_score",
        "forward_1m_return",
        "forward_3m_return",
        "relative_forward_1m_return",
        "relative_forward_3m_return",
        "valid",
        "reason",
    ]


def _summary_columns() -> list[str]:
    return [
        "horizon",
        "observation_count",
        "rank_ic_spearman",
        "top_quintile_avg_relative_return",
        "bottom_quintile_avg_relative_return",
        "top_minus_bottom_spread",
        "hit_rate_top_positive",
        "notes",
    ]
