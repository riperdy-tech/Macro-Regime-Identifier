"""Tests for Ken French factor data parsing."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import fetch_ff_factors  # noqa: E402
from fetch_ff_factors import (  # noqa: E402
    parse_ff_factors_daily,
    parse_ff_factors_monthly,
    parse_ff_monthly_columns,
)

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


# ---------------------------------------------------------------- momentum and five-factor files (no network)

FIXTURE_MOM = """This file was created by using the 202609 CRSP database.

 ,Mom   
192701,   0.46
192702,  -99.99
192703,   0.00
192704,  -999
192705,

 Annual Factors: January-December 
 ,Mom   
  1927,   9.99
  1928,  11.11

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""

FIXTURE_5F = """This file was created by using the 202609 CRSP database.

,Mkt-RF,SMB,HML,RMW,CMA,RF
196307,  -0.39,  -0.47,  -0.83,   0.66,  -1.15,   0.27
196308,   5.08,  -0.80,   1.65,   0.40,  -0.38,   0.25
196309,  -1.57,  -0.43,   0.00,  -0.80,   0.15,  -99.99

 Annual Factors: January-December 
,Mkt-RF,SMB,HML,RMW,CMA,RF
  1963,  12.00,  -1.00,   2.00,   3.00,   4.00,   2.90

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""


def _zip_bytes(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


def test_parse_momentum_monthly_percent_to_decimal_and_no_annual_block() -> None:
    df = parse_ff_monthly_columns(FIXTURE_MOM, ("Mom",))
    assert list(df.columns) == ["date", "year_month", "mom"]
    assert list(df["year_month"]) == ["1927-01", "1927-02", "1927-03", "1927-04", "1927-05"]  # no annual rows
    assert df.iloc[0]["date"] == "1927-01-31"
    assert df.iloc[0]["mom"] == pytest.approx(0.0046)


def test_missing_codes_are_absent_never_a_number_and_zero_is_a_value() -> None:
    df = parse_ff_monthly_columns(FIXTURE_MOM, ("Mom",))
    assert df.loc[1, "mom"] != df.loc[1, "mom"]  # -99.99 -> NaN
    assert df.loc[3, "mom"] != df.loc[3, "mom"]  # -999 -> NaN
    assert df.loc[4, "mom"] != df.loc[4, "mom"]  # empty cell -> NaN
    assert df.loc[2, "mom"] == 0.0  # a true zero stays a value
    assert not (df["mom"].dropna().abs() > 1).any()


def test_parse_five_factor_monthly_columns() -> None:
    cols = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF")
    df = parse_ff_monthly_columns(FIXTURE_5F, cols)
    assert list(df.columns) == ["date", "year_month", "mkt_rf", "smb", "hml", "rmw", "cma", "rf"]
    assert len(df) == 3  # the annual 1963 row is not read
    assert df.iloc[0]["rmw"] == pytest.approx(0.0066)
    assert df.iloc[0]["mkt_rf"] == pytest.approx(-0.0039)
    assert df.iloc[2]["hml"] == 0.0
    assert df.iloc[2]["rf"] != df.iloc[2]["rf"]  # -99.99 -> NaN


def test_parse_monthly_columns_missing_header_raises() -> None:
    with pytest.raises(ValueError):
        parse_ff_monthly_columns(FIXTURE_MONTHLY, ("Mom",))


def test_fetch_extra_writes_files_and_provenance(tmp_path, monkeypatch) -> None:
    zips = {
        fetch_ff_factors.FF_MOMENTUM_URL: _zip_bytes("F-F_Momentum_Factor.csv", FIXTURE_MOM),
        fetch_ff_factors.FF_FIVE_FACTOR_URL: _zip_bytes("F-F_Research_Data_5_Factors_2x3.csv", FIXTURE_5F),
    }

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    monkeypatch.setattr(
        fetch_ff_factors.urllib.request, "urlopen", lambda req, timeout=0: _Resp(zips[req.full_url])
    )
    prov = fetch_ff_factors.fetch_and_cache_extra_factors(tmp_path)
    assert prov["momentum"]["sha256"] == hashlib.sha256(zips[fetch_ff_factors.FF_MOMENTUM_URL]).hexdigest()
    assert prov["momentum"]["row_count"] == 5
    assert prov["momentum"]["missing_by_column"] == {"mom": 3}
    assert prov["five_factor"]["row_count"] == 3
    assert (tmp_path / "ff_momentum_monthly.csv").exists()
    assert (tmp_path / "ff_5factors_monthly.csv").exists()
    on_disk = json.loads((tmp_path / "ff_extra_provenance.json").read_text(encoding="utf-8"))
    assert on_disk["five_factor"]["url"] == fetch_ff_factors.FF_FIVE_FACTOR_URL
    assert not (tmp_path / "ff_factors_monthly.csv").exists()  # the extra run writes none of the existing files
