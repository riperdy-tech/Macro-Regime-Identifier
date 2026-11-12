#!/usr/bin/env python3
"""Write `outputs/job_status.json`: one small record per scheduled job, readable by a monitor.

Standard library only, on purpose: the job scripts call this even when the editable install of
`macro_engine` is broken (the failure the import check exists for), and that is exactly when a status
file matters most.

Each job's record:
  job, last_start, last_finish, last_success, last_exit_code, warnings, non_fatal_failures

`last_success` moves only on exit code 0, so a monitor compares it with the schedule: a job that has not
succeeded for more than a day (or two for the events job) is down, whatever its latest exit code says.
`warnings` is the count the job's own record carries (None when the job did not get far enough to say);
`non_fatal_failures` lists steps that failed without stopping the job.

Usage:
  python scripts/job_status.py --job macro_daily start
  python scripts/job_status.py --job macro_daily finish --exit-code 0 --warnings-from outputs/daily_diagnostic_summary.json \
      --non-fatal "sector price panel (ingest-sector-proxy-prices)"

A failure to write the status is printed and returns 0: this must never be the reason a job fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUS_FILENAME = "job_status.json"
SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def warning_count_from(path: str | Path) -> int | None:
    """The warning count a job's own record carries: `warning_count`, else the length of `warnings`."""
    doc = _read(Path(path))
    if isinstance(doc.get("warning_count"), int):
        return doc["warning_count"]
    if isinstance(doc.get("warnings"), list):
        return len(doc["warnings"])
    return None


def record_job_status(
    outputs_dir: str | Path,
    job: str,
    event: str,
    *,
    exit_code: int | None = None,
    warnings: int | None = None,
    non_fatal_failures: list[str] | None = None,
    now: str | None = None,
) -> Path:
    """Merge one event ("start" or "finish") for `job` into the status file, atomically."""
    if event not in ("start", "finish"):
        raise ValueError(f"event must be 'start' or 'finish', got {event!r}")
    stamp = now or _now()
    path = Path(outputs_dir) / STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = _read(path)
    jobs = doc.get("jobs") if isinstance(doc.get("jobs"), dict) else {}
    record = dict(jobs.get(job) or {})
    record["job"] = job
    for key in ("last_start", "last_finish", "last_success", "last_exit_code", "warnings"):
        record.setdefault(key, None)
    record.setdefault("non_fatal_failures", [])
    if event == "start":
        record["last_start"] = stamp
    else:
        if exit_code is None:
            raise ValueError("a finish event needs an exit code")
        record["last_finish"] = stamp
        record["last_exit_code"] = int(exit_code)
        if int(exit_code) == 0:
            record["last_success"] = stamp
        record["warnings"] = warnings
        record["non_fatal_failures"] = list(non_fatal_failures or [])
    jobs[job] = record
    out = {"schema_version": SCHEMA_VERSION, "updated_at": stamp, "jobs": jobs}
    text = json.dumps(out, indent=2, sort_keys=True, allow_nan=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", required=True)
    parser.add_argument("--outputs-dir", default=str(Path(__file__).resolve().parent.parent / "outputs"))
    parser.add_argument("event", choices=["start", "finish"])
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--warnings", type=int)
    parser.add_argument("--warnings-from", help="a job record JSON carrying warning_count or a warnings list")
    parser.add_argument("--non-fatal", action="append", default=[], help="a step that failed without stopping the job")
    args = parser.parse_args(argv)
    try:
        warnings = args.warnings
        if warnings is None and args.warnings_from:
            warnings = warning_count_from(args.warnings_from)
        record_job_status(
            args.outputs_dir,
            args.job,
            args.event,
            exit_code=args.exit_code,
            warnings=warnings,
            non_fatal_failures=args.non_fatal,
        )
    except Exception as exc:  # noqa: BLE001 - never the reason a job fails
        print(f"job_status: not written ({type(exc).__name__}: {exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
