"""MRI health watch (WO_MA1): reads what the scheduled jobs already leave behind and says what is wrong.

Pure reading, no repair and no network. The jobs record their result (`outputs/job_status.json`), the macro job
exports five files for the screener, the cost-of-capital anchor carries its own as-of date and `degraded` flag, the
store holds the latest reading's inputs. This module turns those into a list of findings
`{code, severity, subject, detail}` and a summary, written as strict JSON, plus a small state file so the caller can
tell a new finding from a repeat (`changed`).

Alert texts carry job names, input ids, dates and counts only: the repository is public.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from macro_engine.dashboard_export import build_data_health

# --------------------------------------------------------------------------- thresholds
JOB_MAX_AGE_HOURS = 26  # a job's last_success older than this is a job that did not run
EXPORT_MAX_AGE_HOURS = 26
ANCHOR_MAX_AGE_DAYS = 1  # as-of today or yesterday, the same bound run_local_daily.ps1's anchor check uses
FALLBACK_MONTHS_WARN = 3
CARRIED_FORWARD_MAX_AGE_DAYS = 60
REGIME_PROBABILITY_TOLERANCE = 0.10

EXPECTED_JOBS = ("macro_daily", "events_daily")
# The files run_local_daily.ps1 exports to the folder the screener runner reads (its required list).
EXPORTED_FILES = (
    "current_sector_ranking.json",
    "current_regime.json",
    "cost_of_capital_anchor.json",
    "long_run_growth_anchor.json",
    "sector_multiple_bands.json",
)
# Intentionally switched off in config: not a failing input.
NOT_A_FAILURE_REASONS = ("disabled_feature",)

CRITICAL = "critical"
WARNING = "warning"
SUMMARY_FILENAME = "health_watch.json"
STATE_FILENAME = "health_watch_state.json"
SCHEMA = "mri.health_watch.v1"


def _finding(code: str, severity: str, subject: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "subject": subject, "detail": detail}


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """(document, None) or (None, why). A missing file, bad JSON and a non-object are all 'why'."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as exc:
        return None, f"unreadable ({type(exc).__name__})"
    if not isinstance(doc, dict):
        return None, "unreadable (not a JSON object)"
    return doc, None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- the rules


def _check_jobs(outputs_dir: Path, now: datetime, findings: list[dict[str, str]]) -> None:
    doc, why = _read_json(outputs_dir / "job_status.json")
    if doc is None:
        findings.append(_finding("source_unreadable", CRITICAL, "job_status.json", why or "unreadable"))
        return
    jobs = doc.get("jobs")
    jobs = jobs if isinstance(jobs, dict) else {}
    for name in EXPECTED_JOBS:
        record = jobs.get(name)
        if not isinstance(record, dict):
            findings.append(_finding("job_not_run", CRITICAL, name, "no record of this job"))
            continue
        success = _parse_time(record.get("last_success"))
        if success is None:
            findings.append(_finding("job_not_run", CRITICAL, name, "no success recorded"))
        else:
            age_hours = (now - success).total_seconds() / 3600.0
            if age_hours > JOB_MAX_AGE_HOURS:
                findings.append(
                    _finding(
                        "job_not_run",
                        CRITICAL,
                        name,
                        f"last success {success.isoformat()} is {age_hours:.1f} h old (limit {JOB_MAX_AGE_HOURS} h)",
                    )
                )
        exit_code = record.get("last_exit_code")
        if exit_code is not None and exit_code != 0:
            findings.append(_finding("job_failed", CRITICAL, name, f"last exit code {exit_code}"))
        failures = record.get("non_fatal_failures")
        if isinstance(failures, list) and failures:
            findings.append(
                _finding("job_nonfatal", WARNING, name, "non-fatal failures: " + "; ".join(str(f) for f in failures))
            )


def _check_anchor(outputs_dir: Path, now: datetime, findings: list[dict[str, str]]) -> None:
    doc, why = _read_json(outputs_dir / "cost_of_capital_anchor.json")
    if doc is None:
        findings.append(_finding("source_unreadable", CRITICAL, "cost_of_capital_anchor.json", why or "unreadable"))
        return
    problems: list[str] = []
    asof = doc.get("asof")
    try:
        age = (now.astimezone(UTC).date() - date.fromisoformat(str(asof))).days
    except ValueError:
        age = None
        problems.append(f"as-of date {asof!r} is not a date")
    if age is not None and not 0 <= age <= ANCHOR_MAX_AGE_DAYS:
        problems.append(f"as-of {asof} is {age} days old (allowed 0 to {ANCHOR_MAX_AGE_DAYS})")
    if doc.get("degraded") is not False:
        reasons = (doc.get("provenance") or {}).get("degradation_reasons") if isinstance(doc.get("provenance"), dict) else None
        reasons = [str(r) for r in reasons] if isinstance(reasons, list) else []
        problems.append(f"degraded={doc.get('degraded')!r}" + (f" ({'; '.join(reasons)})" if reasons else ""))
    if problems:
        findings.append(_finding("anchor_stale_or_degraded", CRITICAL, "cost_of_capital_anchor", "; ".join(problems)))


def _check_exports(export_dir: Path, now: datetime, findings: list[dict[str, str]]) -> None:
    for name in EXPORTED_FILES:
        path = export_dir / name
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            findings.append(_finding("export_stale", CRITICAL, name, "missing from the export folder"))
            continue
        age_hours = (now - mtime).total_seconds() / 3600.0
        if age_hours > EXPORT_MAX_AGE_HOURS:
            findings.append(
                _finding("export_stale", CRITICAL, name, f"{age_hours:.1f} h old (limit {EXPORT_MAX_AGE_HOURS} h)")
            )


def _required_dimensions(config_path: Path) -> tuple[dict[str, bool] | None, str | None]:
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return {
            str(item["dimension_id"]): bool(item.get("required_for_regime", True)) for item in data.get("dimensions", [])
        }, None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return None, f"unreadable ({type(exc).__name__})"


def _fallback_months(db_path: Path, reading_date: str, feature_ids: list[str]) -> dict[str, int | None]:
    """Months each feature was extended by its fallback series at the reading date. A store older than WO_MF3 has no
    such column: the months stay unknown (None), they are not invented."""
    months: dict[str, int | None] = {feature_id: None for feature_id in feature_ids}
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            rows = con.execute(
                "SELECT feature_id, fallback_months FROM asof_feature_values WHERE evaluation_date = CAST(? AS DATE)",
                [reading_date],
            ).fetchall()
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - the column or the store may be missing; months then stay unknown
        return months
    for feature_id, value in rows:
        if feature_id in months and value is not None:
            months[feature_id] = int(value)
    return months


def _check_inputs(
    outputs_dir: Path,
    db_path: Path,
    dimension_config: Path,
    now: datetime,
    findings: list[dict[str, str]],
) -> None:
    health = build_data_health(outputs_dir, db_path=db_path, today=now.astimezone(UTC).date())
    groups = health.get("input_groups")
    if groups is None:
        findings.append(
            _finding("source_unreadable", CRITICAL, "store", str(health.get("input_groups_reason") or "unreadable"))
        )
        return
    required, why = _required_dimensions(dimension_config)
    if required is None:
        findings.append(_finding("source_unreadable", CRITICAL, "dimension_config", why or "unreadable"))
    fallback_inputs: list[tuple[str, dict[str, Any]]] = []
    for group in groups:
        dimension = str(group["group"])
        # A dimension the config does not name is treated as required: the stricter reading, never a silent pass.
        is_required = True if required is None else required.get(dimension, True)
        for entry in group["inputs"]:
            subject = f"{dimension}/{entry['feature_id']}" + (f" ({entry['series_id']})" if entry.get("series_id") else "")
            if entry["used"] is False and entry["reason"] not in NOT_A_FAILURE_REASONS:
                findings.append(
                    _finding(
                        "input_invalid",
                        CRITICAL if is_required else WARNING,
                        subject,
                        f"not valid at {health['reading_date']}: {entry['reason']}"
                        + ("" if is_required else " (dimension not required for the regime)"),
                    )
                )
            if (
                entry["reason"] == "carried_forward"
                and entry["age_days"] is not None
                and entry["age_days"] > CARRIED_FORWARD_MAX_AGE_DAYS
            ):
                findings.append(
                    _finding(
                        "input_carried_forward",
                        WARNING,
                        subject,
                        f"carried forward, observation {entry['observation_date']} is {entry['age_days']} days old",
                    )
                )
            if entry["reason"] == "extended_by_fallback":
                fallback_inputs.append((subject, entry))
    if fallback_inputs:
        months = _fallback_months(db_path, str(health["reading_date"]), [e["feature_id"] for _, e in fallback_inputs])
        for subject, entry in fallback_inputs:
            value = months[entry["feature_id"]]
            if value is None:
                findings.append(
                    _finding("input_on_fallback", WARNING, subject, "extended by its fallback series (months not recorded)")
                )
            elif value >= FALLBACK_MONTHS_WARN:
                findings.append(
                    _finding("input_on_fallback", WARNING, subject, f"extended by its fallback series for {value} months")
                )


def _regime_view(doc: dict[str, Any]) -> tuple[str | None, dict[str, float]]:
    """(top regime, probabilities) from current_regime.json or the published regime_status.json (top probability only)."""
    top = doc.get("reported_regime") or doc.get("dominant_regime")
    probs: dict[str, float] = {}
    table = doc.get("regime_probabilities")
    if isinstance(table, dict):
        for name, value in table.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                probs[str(name)] = float(value)
    else:
        top_prob = doc.get("reported_regime_probability")
        if top_prob is None:
            top_prob = doc.get("regime_probability")
        if top is not None and isinstance(top_prob, (int, float)) and not isinstance(top_prob, bool):
            probs[str(top)] = float(top_prob)
    return (str(top) if top is not None else None), probs


def _check_regime_agreement(outputs_dir: Path, other: Path | None, findings: list[dict[str, str]]) -> None:
    if other is None or not other.exists():
        findings.append(
            _finding("regime_compare_unavailable", WARNING, "current_regime", "no second regime file to compare with")
        )
        return
    local_doc, why = _read_json(outputs_dir / "current_regime.json")
    if local_doc is None:
        findings.append(_finding("source_unreadable", CRITICAL, "current_regime.json", why or "unreadable"))
        return
    other_doc, why = _read_json(other)
    if other_doc is None:
        findings.append(_finding("source_unreadable", CRITICAL, "second regime file", why or "unreadable"))
        return
    local_top, local_probs = _regime_view(local_doc)
    other_top, other_probs = _regime_view(other_doc)
    problems: list[str] = []
    if local_top is None or other_top is None:
        problems.append(f"top regime missing (PC {local_top}, other {other_top})")
    elif local_top != other_top:
        problems.append(f"top regime differs (PC {local_top}, other {other_top})")
    for name in sorted(set(local_probs) & set(other_probs)):
        gap = abs(local_probs[name] - other_probs[name])
        if gap > REGIME_PROBABILITY_TOLERANCE:
            problems.append(f"{name} probability differs by {gap:.2f}")
    if problems:
        findings.append(_finding("regime_disagrees", CRITICAL, "current_regime", "; ".join(problems)))


def _check_risk_state(outputs_dir: Path, findings: list[dict[str, str]]) -> None:
    doc, why = _read_json(outputs_dir / "risk_state.json")
    if doc is None:
        if why == "missing":
            findings.append(_finding("risk_state_degraded", WARNING, "risk_state.json", "absent"))
        else:
            findings.append(_finding("source_unreadable", CRITICAL, "risk_state.json", why or "unreadable"))
        return
    if doc.get("degraded") is not False:
        reasons = doc.get("reasons")
        reasons = [str(r) for r in reasons] if isinstance(reasons, list) else []
        findings.append(
            _finding(
                "risk_state_degraded",
                WARNING,
                "risk_state.json",
                f"degraded={doc.get('degraded')!r}" + (f": {'; '.join(reasons)}" if reasons else ""),
            )
        )


# --------------------------------------------------------------------------- the whole watch


def run_health_watch(
    outputs_dir: str | Path,
    *,
    export_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    compare_regime: str | Path | None = None,
    dimension_config: str | Path = "config/phase_b_sources.yaml",
    out_dir: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run every rule, write `health_watch.json` and `health_watch_state.json` into `out_dir` (default `outputs_dir`),
    and return the summary. `export_dir` and `db_path` are optional: a caller that passes None skips those rules."""
    outputs = Path(outputs_dir)
    out = Path(out_dir) if out_dir is not None else outputs
    now = (now or datetime.now(UTC))
    now = now if now.tzinfo else now.replace(tzinfo=UTC)

    findings: list[dict[str, str]] = []
    _check_jobs(outputs, now, findings)
    _check_anchor(outputs, now, findings)
    if export_dir is not None:
        _check_exports(Path(export_dir), now, findings)
    if db_path is not None:
        _check_inputs(outputs, Path(db_path), Path(dimension_config), now, findings)
    _check_regime_agreement(outputs, Path(compare_regime) if compare_regime is not None else None, findings)
    _check_risk_state(outputs, findings)

    pairs = sorted({(f["code"], f["subject"]) for f in findings})
    previous = _previous_pairs(out / STATE_FILENAME)
    critical = sum(1 for f in findings if f["severity"] == CRITICAL)
    warning = sum(1 for f in findings if f["severity"] == WARNING)
    summary = {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "status": "critical" if critical else "warning" if warning else "ok",
        "counts": {"critical": critical, "warning": warning, "total": len(findings)},
        "changed": set(pairs) != previous,
        "findings": findings,
    }
    _write_json(out / SUMMARY_FILENAME, summary)
    _write_json(out / STATE_FILENAME, {"schema": SCHEMA, "generated_at": now.isoformat(), "pairs": [list(p) for p in pairs]})
    return summary


def _previous_pairs(path: Path) -> set[tuple[str, str]]:
    doc, _ = _read_json(path)
    if doc is None or not isinstance(doc.get("pairs"), list):
        return set()
    return {(str(p[0]), str(p[1])) for p in doc["pairs"] if isinstance(p, list) and len(p) == 2}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
