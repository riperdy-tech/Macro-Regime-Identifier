#!/usr/bin/env python3
"""
WS-C - Dynamic Secular Theme Discovery

Propose EMERGING secular themes from recent news the fixed taxonomy could not
place, gate them with a pure-Python anti-noise test, and (optionally) register
survivors into config/news_themes.yaml for human git-diff review before deploy.

Cost discipline (mirrors scripts/backfill_fred_1980.py):
    # Dry-run (default): read DB, print the candidate plan, NO network, NO writes
    python scripts/discover_themes.py

    # Call DeepSeek (network) and write outputs/candidate_themes.json
    python scripts/discover_themes.py --apply --max-calls 1

    # Also run the promotion gate and register survivors into news_themes.yaml
    python scripts/discover_themes.py --apply --max-calls 1 --promote

DeepSeek is only contacted with --apply, and never more than --max-calls times.
This is intended for the existing WEEKLY cadence, not daily. No auto-commit.

Exit code: 0 on success, non-zero on error (suitable for CI detection).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    from dotenv import load_dotenv  # noqa: E402
except ImportError:  # pragma: no cover
    def load_dotenv() -> None:  # type: ignore
        return None

from macro_engine.news.config import (  # noqa: E402
    load_news_ai_config,
    load_news_themes_config,
)
from macro_engine.news.theme_discovery import (  # noqa: E402
    PromotionThresholds,
    build_discovery_prompt,
    evaluate_promotion,
    parse_candidate_response,
    promote_candidates,
    select_discovery_candidates,
)
from macro_engine.storage.duckdb_store import DuckDBStore  # noqa: E402

DEFAULT_DB_PATH = "data/macro_engine.duckdb"
DEFAULT_THEMES_CONFIG = "config/news_themes.yaml"
DEFAULT_OUTPUT = "outputs/candidate_themes.json"
DEFAULT_MAX_ITEMS = 200


def _load_news_frames(db_path: str):
    store = DuckDBStore(db_path)
    news = store.read_news_items()
    with store._connect() as con:  # noqa: SLF001 - read-only helper
        try:
            cls = con.execute("SELECT * FROM news_classifications").fetchdf()
        except Exception:
            import pandas as pd

            cls = pd.DataFrame()
    return news, cls


def _call_deepseek(system: str, user: str, ai_config) -> str:
    import requests

    api_key = os.getenv(ai_config.api_key_env)
    if not api_key:
        raise RuntimeError(
            f"{ai_config.api_key_env} is required for --apply (live DeepSeek call)"
        )
    resp = requests.post(
        f"{ai_config.base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": ai_config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": ai_config.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        timeout=ai_config.request_timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def main() -> int:
    parser = argparse.ArgumentParser(description="WS-C dynamic secular theme discovery")
    parser.add_argument("--apply", action="store_true", help="call DeepSeek (network)")
    parser.add_argument("--promote", action="store_true", help="run gate + write news_themes.yaml")
    parser.add_argument("--max-calls", type=int, default=1, help="hard cap on DeepSeek calls")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--themes-config", default=DEFAULT_THEMES_CONFIG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--max-confidence", type=float, default=0.35)
    args = parser.parse_args()

    try:  # Windows console is cp949; news headlines may carry non-ASCII chars
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    load_dotenv()
    themes = load_news_themes_config(args.themes_config)
    existing = set(themes.secular_theme_ids)

    news, cls = _load_news_frames(args.db_path)
    candidates_df = select_discovery_candidates(
        news, cls, max_confidence=args.max_confidence, max_items=args.max_items
    )

    print("=" * 60)
    print("WS-C Dynamic Secular Theme Discovery")
    print("=" * 60)
    print(f"Existing secular themes: {len(existing)}")
    print(f"Unplaced/low-confidence news selected: {len(candidates_df)}")
    print(f"Max DeepSeek calls: {args.max_calls}  apply={args.apply}  promote={args.promote}")

    if candidates_df.empty:
        print("No discovery candidates. Nothing to do.")
        return 0

    if not args.apply:
        print()
        print("DRY RUN - no network, no writes. Would send these articles to DeepSeek:")
        for _, row in candidates_df.head(15).iterrows():
            print(f"  - {str(row.get('title', ''))[:90]}")
        if len(candidates_df) > 15:
            print(f"  ... and {len(candidates_df) - 15} more")
        return 0

    if args.max_calls < 1:
        print("ERROR: --apply requires --max-calls >= 1")
        return 2

    system, user = build_discovery_prompt(candidates_df, existing_theme_ids=existing)
    ai_config = load_news_ai_config()
    print(f"Calling DeepSeek ({ai_config.model}) once...")
    raw = _call_deepseek(system, user, ai_config)
    candidates = parse_candidate_response(raw, candidates_df, existing_theme_ids=existing)
    print(f"Model proposed {len(candidates)} candidate theme(s).")

    thresholds = PromotionThresholds()
    enriched = []
    for cand in candidates:
        ok, reason = evaluate_promotion(cand, existing_theme_ids=existing, thresholds=thresholds)
        record = cand.to_dict()
        record["passes_gate"] = ok
        record["gate_reason"] = reason
        enriched.append(record)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "existing_secular_themes": sorted(existing),
                "thresholds": thresholds.__dict__,
                "candidates": enriched,
                "disclaimer": "Promotion thresholds are hand-set anti-noise priors, not measured.",
            },
            handle,
            indent=2,
        )
    print(f"Wrote {out_path}")

    if args.promote:
        summary = promote_candidates(args.themes_config, candidates, thresholds=thresholds)
        print(f"Promoted {summary['new_count']} theme(s): {summary['promoted']}")
        if summary["rejected"]:
            print(f"Rejected: {summary['rejected']}")
        print("Review the news_themes.yaml diff before committing/deploying.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
