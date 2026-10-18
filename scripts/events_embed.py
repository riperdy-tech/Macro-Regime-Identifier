"""Compute and persist dense title embeddings for events arm B (BRIEF_P4C.md). Idempotent: stored vectors are skipped.

    python scripts/events_embed.py --from 2026-10-08 --through 2026-10-18      embed items first seen in [from, through]
    python scripts/events_embed.py --reference                                 also embed the 300 reference items
    python scripts/events_embed.py --determinism                               measure run-to-run / batch-order drift

The build path reads the stored vectors (table event_embeddings) and never calls the model. The model is the pinned
one of config/events_v23.yaml (`dense:`); the run refuses on a sha256 mismatch. Needs the optional extra `embeddings`.
Writing touches only the additive table `event_embeddings` of the store; back the store up first (--backup).
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "macro_engine.duckdb"
REFERENCE_ITEMS = REPO_ROOT / "tests" / "fixtures" / "events_reference" / "items.csv"
BACKUP_DIR = REPO_ROOT / "data" / "backups"

sys.path.insert(0, str(REPO_ROOT / "src"))

from macro_engine.events.config import load_e1_config  # noqa: E402
from macro_engine.events.embeddings import (  # noqa: E402
    EmbeddingModel,
    EmbeddingStore,
    embed_items,
    item_text,
    load_model_pin,
)
from macro_engine.events.store_io import load_all_items  # noqa: E402


def reference_ids() -> list[str]:
    with open(REFERENCE_ITEMS, encoding="utf-8") as fh:
        return [r["news_id"] for r in csv.DictReader(fh)]


def determinism(model: EmbeddingModel, texts: list[str], seed: int = 20261018) -> dict:
    """Embed `texts` twice in the same order (batch 32), then in a shuffled order with another batch size (7), and
    report max abs differences. Not a claim of bitwise determinism: only what was measured."""
    def run(order: list[int], bs: int) -> dict[int, list[float]]:
        out = {}
        for k in range(0, len(order), bs):
            idx = order[k:k + bs]
            for i, v in zip(idx, model.embed([texts[i] for i in idx]), strict=True):
                out[i] = v
        return out

    import numpy as np

    base = list(range(len(texts)))
    a, b = run(base, 32), run(base, 32)
    shuffled = base[:]
    random.Random(seed).shuffle(shuffled)
    c = run(shuffled, 7)
    d = run(base, 1)

    def diff(x, y):
        return float(max(np.max(np.abs(x[i] - y[i])) for i in base))

    return {"titles": len(texts), "same_order_same_batch_max_abs": diff(a, b),
            "shuffled_batch7_vs_base_max_abs": diff(a, c), "batch1_vs_base_max_abs": diff(a, d),
            "bitwise_identical_same_run": all(np.array_equal(a[i], b[i]) for i in base)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--from", dest="start", help="first day (UTC), inclusive")
    ap.add_argument("--through", help="last day (UTC), inclusive")
    ap.add_argument("--reference", action="store_true", help="also embed the 300 reference items")
    ap.add_argument("--backup", action="store_true", help="copy the store to data/backups/ before writing")
    ap.add_argument("--determinism", action="store_true", help="measure drift on the reference titles; writes nothing")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    import duckdb

    model = EmbeddingModel.load(load_model_pin(args.config))
    cfg = load_e1_config()
    con = duckdb.connect(str(args.db), read_only=args.determinism)
    try:
        items = load_all_items(con)
        by_id = {i.news_id: i for i in items}
        if args.determinism:
            texts = [item_text(by_id[i], cfg) for i in reference_ids()]
            print(json.dumps(determinism(model, texts), indent=2))
            return 0
        wanted: dict[str, object] = {}
        if args.start or args.through:
            lo = datetime.combine(date.fromisoformat(args.start), datetime.min.time()) if args.start else datetime.min
            hi = (datetime.combine(date.fromisoformat(args.through), datetime.min.time()) + timedelta(days=1)
                  if args.through else datetime.max)
            for i in items:
                if i.first_seen_at is not None and lo <= i.first_seen_at < hi:
                    wanted[i.news_id] = i
        if args.reference:
            for nid in reference_ids():
                wanted[nid] = by_id[nid]
        if not wanted:
            raise SystemExit("nothing selected: give --from/--through and/or --reference")
        if args.backup:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            dest = BACKUP_DIR / f"macro_engine.pre_embeddings.{datetime.now().strftime('%Y%m%d_%H%M%S')}.duckdb"
            con.close()
            shutil.copy2(args.db, dest)
            print(f"backup: {dest}")
            con = duckdb.connect(str(args.db))
        store = EmbeddingStore(con)
        t0 = time.perf_counter()
        counts = embed_items(store, model, list(wanted.values()), batch_size=args.batch, config=cfg)  # type: ignore[arg-type]
        counts["seconds"] = round(time.perf_counter() - t0, 2)
        counts["model"] = model.name
        counts["model_sha256"] = model.sha256[:12]
        print(json.dumps(counts, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
