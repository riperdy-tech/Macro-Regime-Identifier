#!/usr/bin/env python3
"""Fetch and cache Ken French F-F Research Data Factors (monthly and daily).

Downloads the public CSV zip files from Kenneth French's Data Library:
  - Monthly: F-F_Research_Data_Factors_CSV.zip
  - Daily:   F-F_Research_Data_Factors_daily_CSV.zip

Parses factors (Mkt-RF, SMB, HML, RF), converts percentage values to decimals,
computes total market return = (Mkt-RF + RF) / 100.0 (stated in decimal),
and writes cached files under data/external/ along with a provenance JSON.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from typing import Any
import urllib.request
import zipfile

import pandas as pd

FF_MONTHLY_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip"
)
FF_DAILY_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_daily_CSV.zip"
)


def parse_ff_factors_monthly(csv_text: str) -> pd.DataFrame:
    """Parse Ken French monthly factor CSV text.

    Finds the monthly section, parses YYYYMM dates up to the 'Annual Factors' section,
    converts percentages to decimals, and computes decimal total market return:
    market return = (Mkt-RF + RF) / 100.0.
    """
    lines = csv_text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        clean = line.strip().replace(" ", "")
        if "Mkt-RF" in clean and "SMB" in clean and "RF" in clean:
            header_idx = i
            break

    if header_idx == -1:
        raise ValueError("Could not find factors header line in monthly CSV text")

    records: list[dict[str, Any]] = []
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if "annual factors" in stripped.lower() or "copyright" in stripped.lower():
            break

        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) < 5:
            continue

        raw_date = parts[0]
        # Monthly date must be 6 digits YYYYMM
        if not (raw_date.isdigit() and len(raw_date) == 6):
            break

        year = int(raw_date[:4])
        month = int(raw_date[4:6])
        period = pd.Period(f"{year:04d}-{month:02d}", freq="M")
        # Month-end timestamp
        date_str = str(period.to_timestamp(how="end").date())
        ym_str = f"{year:04d}-{month:02d}"

        try:
            mkt_rf = float(parts[1]) / 100.0
            smb = float(parts[2]) / 100.0
            hml = float(parts[3]) / 100.0
            rf = float(parts[4]) / 100.0
        except ValueError:
            continue

        # Market return = Mkt-RF + RF (percent -> decimal)
        mkt_return = mkt_rf + rf

        records.append(
            {
                "date": date_str,
                "year_month": ym_str,
                "mkt_rf": mkt_rf,
                "smb": smb,
                "hml": hml,
                "rf": rf,
                "mkt_return": mkt_return,
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("No monthly factor rows parsed")
    return df.sort_values("date").reset_index(drop=True)


def parse_ff_factors_daily(csv_text: str) -> pd.DataFrame:
    """Parse Ken French daily factor CSV text.

    Finds the daily section, parses YYYYMMDD dates, converts percentages to decimals,
    and computes decimal total market return: market return = (Mkt-RF + RF) / 100.0.
    """
    lines = csv_text.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        clean = line.strip().replace(" ", "")
        if "Mkt-RF" in clean and "SMB" in clean and "RF" in clean:
            header_idx = i
            break

    if header_idx == -1:
        raise ValueError("Could not find factors header line in daily CSV text")

    records: list[dict[str, Any]] = []
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if "copyright" in stripped.lower():
            break

        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) < 5:
            continue

        raw_date = parts[0]
        # Daily date must be 8 digits YYYYMMDD
        if not (raw_date.isdigit() and len(raw_date) == 8):
            continue

        year = int(raw_date[:4])
        month = int(raw_date[4:6])
        day = int(raw_date[6:8])
        date_str = f"{year:04d}-{month:02d}-{day:02d}"

        try:
            mkt_rf = float(parts[1]) / 100.0
            smb = float(parts[2]) / 100.0
            hml = float(parts[3]) / 100.0
            rf = float(parts[4]) / 100.0
        except ValueError:
            continue

        # Market return = Mkt-RF + RF (percent -> decimal)
        mkt_return = mkt_rf + rf

        records.append(
            {
                "date": date_str,
                "mkt_rf": mkt_rf,
                "smb": smb,
                "hml": hml,
                "rf": rf,
                "mkt_return": mkt_return,
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("No daily factor rows parsed")
    return df.sort_values("date").reset_index(drop=True)


def download_and_extract_csv(url: str) -> tuple[bytes, str]:
    """Download ZIP from URL, return raw zip bytes and extracted CSV text."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        zip_bytes = resp.read()

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    csv_filename = zf.namelist()[0]
    csv_bytes = zf.read(csv_filename)
    csv_text = csv_bytes.decode("utf-8", errors="replace")
    return zip_bytes, csv_text


def fetch_and_cache_factors(
    out_dir: str | Path = "data/external",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch monthly and daily FF factors, cache CSVs/parquets, and save provenance JSON."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    fetched_at = datetime.now(timezone.utc).isoformat()

    print(f"Fetching monthly FF factors from {FF_MONTHLY_URL}...")
    zip_bytes_m, csv_text_m = download_and_extract_csv(FF_MONTHLY_URL)
    sha256_m = hashlib.sha256(zip_bytes_m).hexdigest()
    df_m = parse_ff_factors_monthly(csv_text_m)

    print(f"Fetching daily FF factors from {FF_DAILY_URL}...")
    zip_bytes_d, csv_text_d = download_and_extract_csv(FF_DAILY_URL)
    sha256_d = hashlib.sha256(zip_bytes_d).hexdigest()
    df_d = parse_ff_factors_daily(csv_text_d)

    provenance = {
        "monthly": {
            "url": FF_MONTHLY_URL,
            "fetched_at": fetched_at,
            "sha256": sha256_m,
            "row_count": len(df_m),
            "first_date": str(df_m["date"].min()),
            "last_date": str(df_m["date"].max()),
            "market_return_formula": "mkt_return = (Mkt-RF + RF) / 100.0 (percent converted to decimal)",
        },
        "daily": {
            "url": FF_DAILY_URL,
            "fetched_at": fetched_at,
            "sha256": sha256_d,
            "row_count": len(df_d),
            "first_date": str(df_d["date"].min()),
            "last_date": str(df_d["date"].max()),
            "market_return_formula": "mkt_return = (Mkt-RF + RF) / 100.0 (percent converted to decimal)",
        },
    }

    if not dry_run:
        monthly_csv = out_path / "ff_factors_monthly.csv"
        monthly_pq = out_path / "ff_factors_monthly.parquet"
        daily_csv = out_path / "ff_factors_daily.csv"
        daily_pq = out_path / "ff_factors_daily.parquet"
        prov_json = out_path / "ff_factors_provenance.json"

        df_m.to_csv(monthly_csv, index=False)
        df_m.to_parquet(monthly_pq, index=False)
        df_d.to_csv(daily_csv, index=False)
        df_d.to_parquet(daily_pq, index=False)

        with prov_json.open("w", encoding="utf-8") as f:
            json.dump(provenance, f, indent=2)

        print(f"Saved monthly factors to {monthly_csv} and {monthly_pq} ({len(df_m)} rows)")
        print(f"Saved daily factors to {daily_csv} and {daily_pq} ({len(df_d)} rows)")
        print(f"Saved provenance to {prov_json}")
    else:
        print("Dry run complete; files not written.")

    return provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        default="data/external",
        help="cache directory under which to write external factor files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="download and parse without writing to disk",
    )
    args = parser.parse_args(argv)

    fetch_and_cache_factors(out_dir=args.out_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
