"""Builds items.csv for the E1 reference set (plan section 9 step 2). Run once; the output is committed.

No clustering, Jaccard or classifier code is used or imported. Selection is: every item whose title matches the
example keyword rules below (the three plan examples plus the Aden palace story and obviously adjacent coverage),
plus a seeded random sample of the remaining items, up to TARGET_TOTAL.
"""
from __future__ import annotations

import csv
import random
import re
import sys
from urllib.parse import urlparse

import duckdb

SEED = 20261018
TARGET_TOTAL = 300
FIRST_SEEN_FROM = "2026-10-08"
FIRST_SEEN_TO = "2026-10-17"  # exclusive
EXAMPLE_RE = re.compile(
    r"coreweave|houthi|aramco|saudi|\baden\b|yemen|riyadh|dhahran|sanaa|airport|inflation|consumer prices|"
    r"wholesale prices|\bcpi\b|\bppi\b|rate hike|\bfed\b|federal reserve|fomc|powell",
    re.I,
)


def publisher_displayed(title: str, url: str) -> str:
    m = re.search(r"\s[-–—]\s([^-–—]{2,40})$", title or "")
    if urlparse(url or "").netloc == "news.google.com" and m:
        return m.group(1).strip()
    return urlparse(url or "").netloc


def main(db: str, out: str) -> None:
    con = duckdb.connect(db, read_only=True)
    rows = con.execute(
        "select news_id, published_at, first_seen_at, title, source_url from news_items "
        "where first_seen_at >= ? and first_seen_at < ? order by news_id",
        [FIRST_SEEN_FROM, FIRST_SEEN_TO],
    ).fetchall()
    forced = [r for r in rows if EXAMPLE_RE.search(r[3] or "")]
    rest = [r for r in rows if not EXAMPLE_RE.search(r[3] or "")]
    k = max(TARGET_TOTAL - len(forced), 0)
    sample = random.Random(SEED).sample(rest, k)
    chosen = sorted(forced + sample, key=lambda r: (r[2], r[0]))
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["news_id", "published_at", "first_seen_at", "title", "publisher_displayed", "source_url_domain"])
        for nid, pub, seen, title, url in chosen:
            w.writerow([nid, (pub.isoformat() if pub else ""), seen.isoformat(), title, publisher_displayed(title, url), urlparse(url or "").netloc])
    print(f"window items {len(rows)}; forced {len(forced)}; random {len(sample)}; total {len(chosen)}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
