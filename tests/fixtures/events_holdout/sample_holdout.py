"""Selects the holdout items for the v2.3 pre-registration: EVERY item first seen in one contiguous window.

No keyword selection, no sampling, no filtering: the window is the sample, so real distractors stay in. Deterministic:
the same store gives the same file. Do NOT run this on data from after the freeze commit until the freeze commit is
recorded in V23_PREREGISTRATION.md; the guards below refuse a window that starts before the freeze date, or that has
not fully elapsed in the store (the newest first_seen_at must lie beyond the window end), so a partial window cannot be
labelled by mistake.

    python sample_holdout.py <db> <out.csv> --start 2026-10-20 --freeze-date 2026-10-19 [--days 5]

The output has the reference-set columns (news_id, published_at, first_seen_at, title, publisher_displayed,
source_url_domain), ordered by (first_seen_at, news_id). It contains no classifier or builder output.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

AGGREGATOR_HOSTS = ("news.google.com",)  # plan 5.2; the suffix names a publisher only on these hosts
_SUFFIX = re.compile(r"\s[-–—]\s([^-–—]{2,40})$")


def publisher_displayed(title: str | None, url: str | None) -> str:
    host = urlparse(url or "").netloc
    m = _SUFFIX.search(title or "")
    return m.group(1).strip() if host in AGGREGATOR_HOSTS and m else host


def window(start: date, days: int) -> tuple[datetime, datetime]:
    lo = datetime.combine(start, datetime.min.time())
    return lo, lo + timedelta(days=days)


def select(con, start: date, days: int, freeze_date: date) -> list[tuple]:
    if start < freeze_date:
        raise SystemExit(f"window start {start} is before the freeze date {freeze_date}")
    lo, hi = window(start, days)
    newest = con.execute("select max(first_seen_at) from news_items").fetchone()[0]
    if newest is None or newest < hi:
        raise SystemExit(f"window {lo}..{hi} has not fully elapsed in the store (newest first_seen_at {newest})")
    return con.execute(
        "select news_id, published_at, first_seen_at, title, source_url from news_items "
        "where first_seen_at >= ? and first_seen_at < ? order by first_seen_at, news_id", [lo, hi]).fetchall()


def write(rows: list[tuple], out: str) -> None:
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["news_id", "published_at", "first_seen_at", "title", "publisher_displayed", "source_url_domain"])
        for nid, pub, seen, title, url in rows:
            w.writerow([nid, pub.isoformat() if pub else "", seen.isoformat(), title,
                        publisher_displayed(title, url), urlparse(url or "").netloc])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("out")
    ap.add_argument("--start", required=True, type=date.fromisoformat)
    ap.add_argument("--freeze-date", required=True, type=date.fromisoformat)
    ap.add_argument("--days", type=int, default=5)
    args = ap.parse_args(argv)
    import duckdb

    con = duckdb.connect(args.db, read_only=True)
    rows = select(con, args.start, args.days, args.freeze_date)
    write(rows, args.out)
    print(f"window {args.start} + {args.days} days: {len(rows)} items written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
