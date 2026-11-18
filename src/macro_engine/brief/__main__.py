"""python -m macro_engine.brief run [--store ...] [--inputs-dir ...] [--out-dir ...] [--sources-dir ...] [--date ...]"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from macro_engine.brief import model, run as runner, sources

REPO_ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m macro_engine.brief")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="build today's macro brief (one model call)")
    p.add_argument("--store", default=str(REPO_ROOT / "data" / "macro_engine.duckdb"), help="DuckDB store, opened read-only")
    p.add_argument("--config", default=str(REPO_ROOT / "config" / "phase_b_sources.yaml"))
    p.add_argument("--inputs-dir", default=str(REPO_ROOT / "outputs"), help="where current_regime.json and event_register.json are")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "outputs"), help="where macro_brief.json and the history line go")
    p.add_argument("--sources-dir", default=str(REPO_ROOT / "data" / "brief_sources"))
    p.add_argument("--date", default=None, help="run date YYYY-MM-DD (default: today)")
    p.add_argument("--force", action="store_true", help="run again even though today's call already succeeded")
    a = ap.parse_args(argv)
    run_date = date.fromisoformat(a.date) if a.date else date.today()
    out = runner.run_brief(
        store=Path(a.store), config=Path(a.config), inputs_dir=Path(a.inputs_dir), out_dir=Path(a.out_dir),
        sources_dir=Path(a.sources_dir), run_date=run_date, get=sources.http_get,
        ask=model.make_agy_ask(REPO_ROOT / "data" / "brief_agy_cwd"), force=a.force)
    if out.get("already_ran_today"):
        print(f"already ran today ({out['date']}); not calling again (use --force)")
        return 0
    print(f"call {out['call']['status']}: {out['call']['detail']}")
    print(f"as measured : {out['as_measured']['leader']}  {out['as_measured']['probabilities']}")
    print(f"as of today : {out['as_of_today']['leader']}  {out['as_of_today']['probabilities']}")
    print(out["identical_note"])
    for d, r in out["dimensions"].items():
        print(f"  {d}: {r['status']} {r['change']} {r['size']} {r['score_change_applied']:+.3f} {r['check']['failures']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
