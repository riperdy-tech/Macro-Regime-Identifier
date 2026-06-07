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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

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
    decode_candidate_payload,
    evaluate_promotion,
    parse_candidate_response,
    promote_candidates,
    select_discovery_candidates,
)
from macro_engine.storage.duckdb_store import DuckDBStore  # noqa: E402

DEFAULT_DB_PATH = "data/macro_engine.duckdb"
DEFAULT_THEMES_CONFIG = "config/news_themes.yaml"
DEFAULT_OUTPUT = "outputs/candidate_themes.json"
DEFAULT_ERROR_OUTPUT = "outputs/theme_discovery_error.json"
DEFAULT_MAX_ITEMS = 200


@dataclass
class DiscoveryParseFailure:
    attempt: int
    error_type: str
    message: str
    response_length: int
    error_position: int | None
    sanitized_excerpt: str


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


def _call_deepseek(system: str, user: str, ai_config, *, max_tokens: int | None = None) -> str:
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
            "max_tokens": max_tokens or ai_config.max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        },
        timeout=ai_config.request_timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _sanitized_excerpt(text: str, pos: int | None, *, radius: int = 240) -> str:
    if pos is None:
        pos = min(len(text), radius)
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    excerpt = text[start:end]
    return "".join(ch if ch.isprintable() or ch in "\n\t" else "?" for ch in excerpt)


def _parse_failure_record(attempt: int, exc: Exception, raw: str) -> DiscoveryParseFailure:
    pos = exc.pos if isinstance(exc, JSONDecodeError) else None
    return DiscoveryParseFailure(
        attempt=attempt,
        error_type=type(exc).__name__,
        message=str(exc),
        response_length=len(raw),
        error_position=pos,
        sanitized_excerpt=_sanitized_excerpt(raw, pos),
    )


def _write_error_artifact(
    path: str,
    *,
    model: str,
    failures: list[DiscoveryParseFailure],
    candidate_count: int,
    max_calls: int,
) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "status": "failed",
        "failure_type": "invalid_model_response",
        "model": model,
        "candidate_count": candidate_count,
        "max_calls": max_calls,
        "failed_at": datetime.now(UTC).isoformat(),
        "failures": [asdict(failure) for failure in failures],
        "note": (
            "Sanitized excerpts are bounded snippets around the parse error. "
            "No candidate was promoted from invalid model output."
        ),
    }
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _discover_candidates_with_retries(
    *,
    system: str,
    user: str,
    ai_config,
    candidates_df,
    existing_theme_ids: set[str],
    max_calls: int,
    error_output: str,
):
    max_attempts = min(max_calls, ai_config.max_retries + 1)
    failures: list[DiscoveryParseFailure] = []
    for attempt in range(1, max_attempts + 1):
        retrying = attempt > 1
        attempt_system = system
        if retrying:
            attempt_system = (
                f"{system}\n\n"
                "Your previous response was rejected because it was malformed JSON. "
                "Return one complete valid JSON object only. Do not truncate strings. "
                "If uncertain, return {\"candidates\": []}."
            )
        max_tokens = ai_config.max_tokens
        if retrying:
            max_tokens = int(ai_config.max_tokens * ai_config.truncation_retry_multiplier)
        print(
            f"Calling DeepSeek ({ai_config.model}) attempt "
            f"{attempt}/{max_attempts} max_tokens={max_tokens}..."
        )
        raw = _call_deepseek(attempt_system, user, ai_config, max_tokens=max_tokens)
        try:
            payload = decode_candidate_payload(raw)
            return parse_candidate_response(
                payload, candidates_df, existing_theme_ids=existing_theme_ids
            )
        except (JSONDecodeError, ValueError) as exc:
            failure = _parse_failure_record(attempt, exc, raw)
            failures.append(failure)
            print(
                "Rejected malformed DeepSeek response "
                f"(attempt {attempt}/{max_attempts}): {failure.error_type}: {failure.message}",
                file=sys.stderr,
            )
            if attempt < max_attempts:
                print("Retrying once with stricter JSON instruction and larger token ceiling...")
                continue

    _write_error_artifact(
        error_output,
        model=ai_config.model,
        failures=failures,
        candidate_count=len(candidates_df),
        max_calls=max_calls,
    )
    raise RuntimeError(
        "DeepSeek theme-discovery response remained invalid after "
        f"{max_attempts} attempt(s). Wrote sanitized diagnostics to {error_output}."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="WS-C dynamic secular theme discovery")
    parser.add_argument("--apply", action="store_true", help="call DeepSeek (network)")
    parser.add_argument("--promote", action="store_true", help="run gate + write news_themes.yaml")
    parser.add_argument("--max-calls", type=int, default=1, help="hard cap on DeepSeek calls")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--themes-config", default=DEFAULT_THEMES_CONFIG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--error-output", default=DEFAULT_ERROR_OUTPUT)
    parser.add_argument("--ai-config", default="config/news_ai_live.yaml")
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
    ai_config = load_news_ai_config(args.ai_config)
    candidates = _discover_candidates_with_retries(
        system=system,
        user=user,
        ai_config=ai_config,
        candidates_df=candidates_df,
        existing_theme_ids=existing,
        max_calls=args.max_calls,
        error_output=args.error_output,
    )
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
