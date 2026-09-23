"""Tests for Ken French factor data parsing."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from fetch_ff_factors import parse_ff_factors_daily, parse_ff_factors_monthly  # noqa: E402

FIXTURE_MONTHLY = """This file was created using the CRSP database.
The 1-month TBill rate data.

,Mkt-RF,SMB,HML,RF
192607,   2.89,  -2.42,  -2.75,   0.22
192608,   2.64,  -1.44,   4.13,   0.25
202003, -13.38,  -4.89,  -4.96,   0.12

 Annual Factors: January-December 
,Mkt-RF,SMB,HML,RF
  1927,  29.44,  -3.05,  -3.36,   3.12
  1928,  35.56,   3.73,  -5.26,   3.56

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""

FIXTURE_DAILY = """This file was created by using the CRSP database.
Daily factors.

,Mkt-RF,SMB,HML,RF
19260701,    0.09,   -0.23,   -0.28,    0.01
19260702,    0.45,   -0.34,   -0.03,    0.01
20200316,  -11.98,    4.55,    3.12,    0.01

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""


def test_parse_ff_factors_monthly() -> None:
    df = parse_ff_factors_monthly(FIXTURE_MONTHLY)
    assert len(df) == 3
    assert list(df.columns) == [
        "date",
        "year_month",
        "mkt_rf",
        "smb",
        "hml",
        "rf",
        "mkt_return",
    ]

    # Row 0: 192607
    row0 = df.iloc[0]
    assert row0["date"] == "1926-07-31"
    assert row0["year_month"] == "1926-07"
    assert pytest.approx(row0["mkt_rf"], 1e-6) == 0.0289
    assert pytest.approx(row0["rf"], 1e-6) == 0.0022
    # Market return = Mkt-RF + RF (in decimal)
    assert pytest.approx(row0["mkt_return"], 1e-6) == 0.0311

    # Row 2: 202003 COVID crash month
    row2 = df.iloc[2]
    assert row2["date"] == "2020-03-31"
    assert row2["year_month"] == "2020-03"
    assert pytest.approx(row2["mkt_rf"], 1e-6) == -0.1338
    assert pytest.approx(row2["rf"], 1e-6) == 0.0012
    assert pytest.approx(row2["mkt_return"], 1e-6) == -0.1326


def test_parse_ff_factors_daily() -> None:
    df = parse_ff_factors_daily(FIXTURE_DAILY)
    assert len(df) == 3
    assert list(df.columns) == ["date", "mkt_rf", "smb", "hml", "rf", "mkt_return"]

    row0 = df.iloc[0]
    assert row0["date"] == "1926-07-01"
    assert pytest.approx(row0["mkt_rf"], 1e-6) == 0.0009
    assert pytest.approx(row0["rf"], 1e-6) == 0.0001
    assert pytest.approx(row0["mkt_return"], 1e-6) == 0.0010

    # COVID circuit-breaker day 2020-03-16
    row2 = df.iloc[2]
    assert row2["date"] == "2020-03-16"
    assert pytest.approx(row2["mkt_rf"], 1e-6) == -0.1198
    assert pytest.approx(row2["rf"], 1e-6) == 0.0001
    assert pytest.approx(row2["mkt_return"], 1e-6) == -0.1197
