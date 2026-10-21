"""Events firewall: sha256 baseline of the protected artifacts (MRI_EVENTS_PLAN v2 section 9.1).

The events job must leave these unchanged:

  - current_regime.json, current_sector_ranking.json
  - cost_of_capital_anchor.json, long_run_growth_anchor.json, sector_multiple_bands.json
  - the numeric fields of shock_register.json
  - the raw_observations rows for DGS10, DFII10 and T10YIE (v4's risk-free inputs)

Run metadata is canonicalised away before hashing, so a rebuild of identical numbers hashes the same.

    python scripts/events_firewall_hash.py --write docs/events_build/firewall_baseline.json
    python scripts/events_firewall_hash.py --check docs/events_build/firewall_baseline.json

`--check` exits 0 when every hash matches, 1 on any difference or missing input. A daily FRED ingest
legitimately moves the raw_observations hashes; run the check immediately before and after the events
job, not across days.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

JSON_ARTIFACTS = (
    "current_regime.json",
    "current_sector_ranking.json",
    "cost_of_capital_anchor.json",
    "long_run_growth_anchor.json",
    "sector_multiple_bands.json",
)
NUMERIC_ONLY_ARTIFACTS = ("shock_register.json",)
RAW_SERIES = ("DGS10", "DFII10", "T10YIE")

# Run metadata: exact keys plus any key ending in _at or _run_id.
_VOLATILE_KEYS = {"built_at", "source_run_id", "validated_run_id", "generated_at", "fetched_at"}
_VOLATILE_SUFFIXES = ("_at", "_run_id")


def _is_volatile(key: str) -> bool:
    return key in _VOLATILE_KEYS or key.endswith(_VOLATILE_SUFFIXES)


def canonicalise(obj: Any) -> Any:
    """Drop run-metadata keys recursively; everything else is kept as is."""
    if isinstance(obj, dict):
        return {k: canonicalise(v) for k, v in obj.items() if not _is_volatile(str(k))}
    if isinstance(obj, list):
        return [canonicalise(v) for v in obj]
    return obj


def numeric_leaves(obj: Any, path: str = "") -> dict[str, float]:
    """Path -> value for every int/float leaf (bools excluded), run metadata skipped."""
    out: dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _is_volatile(str(k)):
                continue
            out.update(numeric_leaves(v, f"{path}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(numeric_leaves(v, f"{path}[{i}]"))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out[path] = obj
    return out


def _digest(obj: Any) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_json_artifact(path: Path, *, numeric_only: bool = False) -> str:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return _digest(numeric_leaves(doc) if numeric_only else canonicalise(doc))


def hash_raw_observations(db_path: Path, series_ids: tuple[str, ...] = RAW_SERIES) -> dict[str, dict[str, Any]]:
    """Per series: row count, newest date and a hash of (date, value, realtime_start, realtime_end).
    `fetched_at` is excluded: a re-fetch of identical values is not a change."""
    import duckdb

    out: dict[str, dict[str, Any]] = {}
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for sid in series_ids:
            rows = con.execute(
                "SELECT CAST(date AS VARCHAR), value, CAST(realtime_start AS VARCHAR), CAST(realtime_end AS VARCHAR) "
                "FROM raw_observations WHERE series_id = ? ORDER BY date, realtime_start, realtime_end",
                [sid],
            ).fetchall()
            out[sid] = {
                "rows": len(rows),
                "max_date": rows[-1][0] if rows else None,
                "sha256": _digest([list(r) for r in rows]),
            }
    finally:
        con.close()
    return out


def snapshot(outputs_dir: Path, db_path: Path) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for name in JSON_ARTIFACTS:
        artifacts[name] = {"sha256": hash_json_artifact(outputs_dir / name)}
    for name in NUMERIC_ONLY_ARTIFACTS:
        artifacts[name] = {"sha256": hash_json_artifact(outputs_dir / name, numeric_only=True), "scope": "numeric_fields"}
    return {"schema_version": 1, "artifacts": artifacts, "raw_observations": hash_raw_observations(db_path)}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    diffs: list[str] = []
    for name, entry in baseline.get("artifacts", {}).items():
        now = current["artifacts"].get(name)
        if now is None or now["sha256"] != entry["sha256"]:
            diffs.append(f"artifact changed: {name}")
    for sid, entry in baseline.get("raw_observations", {}).items():
        now = current["raw_observations"].get(sid)
        if now is None or now["sha256"] != entry["sha256"]:
            diffs.append(f"raw_observations changed: {sid} (rows {entry['rows']} -> {None if now is None else now['rows']})")
    return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", metavar="BASELINE_JSON")
    group.add_argument("--check", metavar="BASELINE_JSON")
    parser.add_argument("--outputs-dir", default=str(REPO_ROOT / "outputs"))
    parser.add_argument("--db-path", default=str(REPO_ROOT / "data" / "macro_engine.duckdb"))
    args = parser.parse_args(argv)

    try:
        current = snapshot(Path(args.outputs_dir), Path(args.db_path))
    except (OSError, ValueError) as exc:
        print(f"firewall: cannot read protected inputs: {exc}", file=sys.stderr)
        return 1
    if args.write:
        Path(args.write).write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"firewall baseline written: {args.write}")
        return 0
    baseline = json.loads(Path(args.check).read_text(encoding="utf-8"))
    diffs = compare(baseline, current)
    for d in diffs:
        print(f"firewall: {d}")
    print("firewall: OK" if not diffs else f"firewall: {len(diffs)} difference(s)")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
