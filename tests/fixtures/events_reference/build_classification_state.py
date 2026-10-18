"""Writes classification_state.csv: the classification state of the 300 reference items as stored at freeze.

Reads only news_id, classification_status and classified_at from news_classifications. It never reads themes,
entities, summaries or any other classifier output, so it cannot influence the labels. Run once; the output is
committed. Usage: python build_classification_state.py <db> <items.csv> <out.csv>
"""
from __future__ import annotations

import csv
import sys

import duckdb


def main(db: str, items: str, out: str) -> None:
    ids = [r["news_id"] for r in csv.DictReader(open(items, encoding="utf-8"))]
    con = duckdb.connect(db, read_only=True)
    rows = con.execute(
        "select news_id, classification_status, classified_at from news_classifications"
    ).fetchall()
    best: dict[str, tuple[str, str]] = {}
    for nid, status, at in rows:
        cur = best.get(nid)
        if cur is None or (status == "success" and cur[0] != "success") or (status == cur[0] and at.isoformat() > cur[1]):
            best[nid] = (status, at.isoformat())
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["news_id", "state", "classified_at"])
        for nid in ids:
            status, at = best.get(nid, ("none", ""))
            w.writerow([nid, "classified" if status == "success" else ("failed" if status == "error" else "unclassified"), at])


if __name__ == "__main__":
    main(*sys.argv[1:4])
