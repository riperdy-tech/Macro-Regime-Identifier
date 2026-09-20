#!/usr/bin/env python3
"""
Fetch the documented, versioned historical equity-risk-premium series.

Rationale: the cost-of-capital anchor's implied ERP needs an equity aggregate MRI does not
hold. Its documented DEGRADED mode is to publish the risk-free curve plus a
regime-conditional percentile band from a citable ERP history, and to mark the implied value
unavailable. That band needs a real history, and inventing one from memory is exactly the
fabrication the anchor rules forbid.

Source: Aswath Damodaran (NYU Stern), "Historical Implied Equity Risk Premiums" workbook,
`histimpl.xlsx`, sheet `Historical Impl Premiums`, column `Implied ERP (FCFE)` (the modern
measure; `Implied Premium (DDM)` is the pre-1961-era legacy and is not used). Annual,
1961-present. Freely published for research use.

The output is a plain `date,erp,measure,source` CSV so the anchor layer reads a stable local
artifact rather than a live scrape, and so the vintage of the history is pinned in the repo.

Usage:
    python scripts/fetch_erp_history.py
    python scripts/fetch_erp_history.py --out data/anchors/erp_history.csv

Exit code: 0 on success, 1 if the workbook is unreachable or unparseable.
"""

from __future__ import annotations

import argparse
import csv
import io
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent

WORKBOOK_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xlsx"
SHEET = "Historical Impl Premiums"
ERP_COLUMN = "Implied ERP (FCFE)"
SOURCE_LABEL = "Damodaran implied ERP (FCFE), annual"
DEFAULT_OUT = "data/anchors/erp_history.csv"

# Below this the series cannot support a percentile band, so a truncated download is refused
# rather than silently yielding a thinner history than the anchor is configured to expect.
MIN_OBSERVATIONS = 40


def fetch_workbook(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(WORKBOOK_URL, timeout=60) as response:  # noqa: S310 - fixed, published URL
        payload = response.read()
    if len(payload) < 10_000:
        raise ValueError(f"workbook download looks truncated ({len(payload)} bytes)")
    destination.write_bytes(payload)
    return destination


def parse_rows(workbook_path: Path) -> tuple[list[tuple[int, float]], str]:
    """(year, erp) pairs plus the workbook's own 'Date updated' stamp."""
    import openpyxl

    workbook = openpyxl.load_workbook(workbook_path, data_only=True)
    if SHEET not in workbook.sheetnames:
        raise ValueError(f"sheet {SHEET!r} not found; sheets are {workbook.sheetnames}")
    sheet = workbook[SHEET]

    updated = ""
    for row in sheet.iter_rows(min_row=1, max_row=6, values_only=True):
        if row and str(row[0] or "").strip().lower().startswith("date updated"):
            updated = str(row[1] or "")

    header_row = None
    for index, row in enumerate(sheet.iter_rows(min_row=1, max_row=12, values_only=True), start=1):
        if row and str(row[0] or "").strip() == "Year":
            header_row = index
            break
    if header_row is None:
        raise ValueError("could not locate the 'Year' header row")

    headers = {
        column: str(sheet.cell(row=header_row, column=column).value or "").strip()
        for column in range(1, sheet.max_column + 1)
    }
    erp_column = next((c for c, name in headers.items() if name == ERP_COLUMN), None)
    if erp_column is None:
        raise ValueError(f"column {ERP_COLUMN!r} not found; headers were {sorted(headers.values())}")

    rows: list[tuple[int, float]] = []
    for row_index in range(header_row + 1, sheet.max_row + 1):
        year = sheet.cell(row=row_index, column=1).value
        if year is None:
            break
        value = sheet.cell(row=row_index, column=erp_column).value
        if isinstance(value, int | float):
            rows.append((int(year), float(value)))
    return rows, updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--workbook", default=None, help="use an existing local workbook")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    workbook_path = (
        Path(args.workbook) if args.workbook else Path(tempfile.mkdtemp(prefix="erp_")) / "histimpl.xlsx"
    )
    try:
        if not args.workbook:
            fetch_workbook(workbook_path)
        rows, updated = parse_rows(workbook_path)
    except Exception as exc:  # noqa: BLE001 - report and fail loudly, never write a partial history
        print(f"ERP history fetch failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.workbook:
            # The workbook is a transport artifact; only the parsed CSV is a repo input.
            shutil.rmtree(workbook_path.parent, ignore_errors=True)

    if len(rows) < MIN_OBSERVATIONS:
        print(
            f"refusing to write: only {len(rows)} observations parsed, "
            f"{MIN_OBSERVATIONS} required for a percentile band",
            file=sys.stderr,
        )
        return 1

    lines = ["date,erp,measure,source"]
    for year, value in sorted(rows):
        # csv.writer, not an f-string: the source label contains a comma, and an unquoted one
        # silently shifts every field after it (the reader then sees erp as text and drops
        # every row as NaN).
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="").writerow(
            [f"{year}-12-31", f"{value:.6f}", ERP_COLUMN, SOURCE_LABEL]
        )
        lines.append(buffer.getvalue())
    body = "\n".join(lines) + "\n"

    print(f"parsed {len(rows)} annual observations, {rows[0][0]}..{rows[-1][0]}")
    print(f"workbook 'Date updated': {updated or 'unknown'}")
    if args.dry_run:
        print("dry-run: nothing written")
        return 0

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    print(f"wrote {out_path} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
