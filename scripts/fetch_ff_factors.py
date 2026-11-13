#!/usr/bin/env python3
"""Fetch and cache Ken French F-F Research Data Factors (monthly and daily).

Downloads the public CSV zip files from Kenneth French's Data Library:
  - Monthly: F-F_Research_Data_Factors_CSV.zip
  - Daily:   F-F_Research_Data_Factors_daily_CSV.zip
With --extra, instead fetch the two files the factor-tilt test (PREREG_factor_tilt_v1) needs:
  - Monthly momentum:    F-F_Momentum_Factor_CSV.zip (column Mom)
  - Monthly five-factor: F-F_Research_Data_5_Factors_2x3_CSV.zip (Mkt-RF, SMB, HML, RMW, CMA, RF)
Their missing-value codes (-99.99, -999) become absent (NaN, an empty cell in the CSV), never a number.

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
FF_MOMENTUM_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_CSV.zip"
)
FF_FIVE_FACTOR_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_CSV.zip"
)
FF_MISSING_CODES = (-99.99, -999.0)


def parse_ff_monthly_columns(csv_text: str, columns: tuple[str, ...]) -> pd.DataFrame:
    """Parse the monthly block of a Ken French CSV for the named columns.

    The header is the first line whose cells (after the empty date cell) include every name in `columns`.
    Rows are read while the first cell is a 6-digit YYYYMM; the annual block (4-digit years) is never read.
    Percent -> decimal. A missing-value code (-99.99, -999), an empty or non-numeric cell becomes NaN.
    Output columns: date (month end), year_month, then each column lower-cased with '-' as '_'.
    """
    lines = csv_text.splitlines()
    header_idx = -1
    header: list[str] = []
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.split(",")]
        if all(c in cells[1:] for c in columns):
            header_idx = i
            header = cells
            break
    if header_idx == -1:
        raise ValueError(f"Could not find header with columns {columns} in monthly CSV text")

    records: list[dict[str, Any]] = []
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = [p.strip() for p in stripped.split(",")]
        raw_date = parts[0]
        if not (raw_date.isdigit() and len(raw_date) == 6):
            break  # the annual block or the copyright line
        year = int(raw_date[:4])
        month = int(raw_date[4:6])
        period = pd.Period(f"{year:04d}-{month:02d}", freq="M")
        rec: dict[str, Any] = {
            "date": str(period.to_timestamp(how="end").date()),
            "year_month": f"{year:04d}-{month:02d}",
        }
        for name in columns:
            idx = header.index(name)
            value = float("nan")
            if idx < len(parts):
                try:
                    raw = float(parts[idx])
                except ValueError:
                    raw = float("nan")
                if raw == raw and raw not in FF_MISSING_CODES:
                    value = raw / 100.0
            rec[name.lower().replace("-", "_")] = value
        records.append(rec)

    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError("No monthly rows parsed")
    return df.sort_values("date").reset_index(drop=True)


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


def fetch_and_cache_extra_factors(
    out_dir: str | Path = "data/external",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch the monthly momentum and five-factor files (PREREG_factor_tilt_v1), cache them, write provenance."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()

    specs = {
        "momentum": (FF_MOMENTUM_URL, ("Mom",), "ff_momentum_monthly"),
        "five_factor": (
            FF_FIVE_FACTOR_URL,
            ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"),
            "ff_5factors_monthly",
        ),
    }
    provenance: dict[str, Any] = {}
    frames: dict[str, pd.DataFrame] = {}
    for key, (url, columns, _stem) in specs.items():
        print(f"Fetching {key} from {url}...")
        zip_bytes, csv_text = download_and_extract_csv(url)
        df = parse_ff_monthly_columns(csv_text, columns)
        frames[key] = df
        names = [c.lower().replace("-", "_") for c in columns]
        provenance[key] = {
            "url": url,
            "fetched_at": fetched_at,
            "sha256": hashlib.sha256(zip_bytes).hexdigest(),
            "row_count": len(df),
            "first_date": str(df["date"].min()),
            "last_date": str(df["date"].max()),
            "missing_by_column": {n: int(df[n].isna().sum()) for n in names},
            "units": "percent converted to decimal; missing-value codes -99.99 and -999 are absent (NaN)",
        }

    if not dry_run:
        for key, (_url, _cols, stem) in specs.items():
            frames[key].to_csv(out_path / f"{stem}.csv", index=False)
            frames[key].to_parquet(out_path / f"{stem}.parquet", index=False)
            print(f"Saved {key} to {out_path / (stem + '.csv')} ({len(frames[key])} rows)")
        prov_json = out_path / "ff_extra_provenance.json"
        with prov_json.open("w", encoding="utf-8") as f:
            json.dump(provenance, f, indent=2)
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
    parser.add_argument(
        "--extra",
        action="store_true",
        help="fetch only the monthly momentum and five-factor files (PREREG_factor_tilt_v1)",
    )
    args = parser.parse_args(argv)

    if args.extra:
        fetch_and_cache_extra_factors(out_dir=args.out_dir, dry_run=args.dry_run)
    else:
        fetch_and_cache_factors(out_dir=args.out_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
