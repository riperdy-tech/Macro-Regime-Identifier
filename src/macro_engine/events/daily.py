"""Daily candidate-register job (`events-daily`; docs/events_build/STAGE1_RUNBOOK.md).

Steps: freshness check of the local news store -> build the register for the as-of day -> firewall assertion (the
protected artifacts and the v4 raw series hash the same before and after) -> atomic write of
`event_register.json`, a dated archive copy and `event_register_run.json`.

On ANY failure the previous `event_register.json` is left exactly as it was and only the run record says `failed`: a
reader never sees a half-written file, and never a register that looks current when it is not. The register document is a
pure function of (store, as-of day), so the same day twice writes the same bytes.

Grouping (`config/events_register.yaml`): `syndication` (default) makes every syndication-collapsed report its own
candidate and never links across reports; `arm_a` / `arm_b` are selectable and labelled experimental in the file.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from typing import Any

import yaml

from macro_engine import peer_paths
from macro_engine.events.config import load_e1_config, load_generic_entities
from macro_engine.events.entities import load_resolver, normalize_name
from macro_engine.events.linker import ArmALinker, ArmBLinker, load_arm_a_config, load_arm_b_config
from macro_engine.events.register import (
    REGISTER_FILENAME,
    RegisterStore,
    apply_pool,
    background_for,
    build_register,
    collapse_pool,
    stabilize_pool,
    write_register,
)
from macro_engine.events.state import EventState, NeverLinkStub
from macro_engine.events.store_io import load_all_items
from macro_engine.news.history import news_store_freshness

RUN_FILENAME = "event_register_run.json"
ARCHIVE_SUBDIR = "events_daily"
DEFAULT_CONFIG_PATH = peer_paths.REPO_ROOT / "config" / "events_register.yaml"
DEFAULT_MAX_AGE_HOURS = 48.0
GROUPING_MODES = ("syndication", "arm_a", "arm_b")
MODE_META_KEY = "register_grouping_mode"

NOTICE = (
    "These are candidates, not confirmed events. Each entry is one news report (copies of the same wire story are "
    "collapsed into it); reports are not linked into stories in this mode, so two entries can describe the same "
    "event. A single-source candidate is shown as such. Nothing here changes any number, score or verdict."
)
EXPERIMENTAL_NOTICE = (
    "EXPERIMENTAL grouping: a linker proposed these groups. The linker arms have not passed their pre-registered "
    "evaluation, so a group is a proposal, not a claim that its reports are the same story."
)


class _Stop(Exception):
    """A checked failure whose reason is already in the run record."""


def load_grouping(path: str | Path = DEFAULT_CONFIG_PATH) -> str:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    mode = (doc.get("register") or {}).get("grouping", "syndication")
    if mode not in GROUPING_MODES:
        raise ValueError(f"{path}: register.grouping must be one of {GROUPING_MODES}, got {mode!r}")
    return str(mode)


def as_of_instant(day: date) -> datetime:
    """The last instant of the UTC day: the register for a day sees everything first seen up to its end."""
    return datetime.combine(day, dtime.max)


def _load_firewall() -> Any:
    path = peer_paths.REPO_ROOT / "scripts" / "events_firewall_hash.py"
    spec = importlib.util.spec_from_file_location("events_firewall_hash", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ----------------------------------------------------------------------------- store guards


def _guard_grouping_mode(con: Any, mode: str) -> None:
    """One store, one grouping mode: candidates written under another mode must not be silently reinterpreted."""
    row = con.execute("SELECT v FROM event_state_meta WHERE k = ?", [MODE_META_KEY]).fetchone()
    if row is not None:
        if row[0] != mode:
            raise RuntimeError(f"this store holds candidates written under grouping '{row[0]}', refusing '{mode}'")
        return
    if mode == "syndication":
        multi = con.execute("SELECT count(*) FROM (SELECT event_id FROM event_state_reports GROUP BY event_id "
                            "HAVING count(*) > 1)").fetchone()[0]
        if multi:
            raise RuntimeError(f"the store already holds {multi} multi-report candidates from a linker; "
                               "syndication mode would misdescribe them")
    con.execute("INSERT INTO event_state_meta VALUES (?, ?)", [MODE_META_KEY, mode])


def refresh_report_details(con: Any, pool: list[Any], state: EventState) -> int:
    """`RegisterStore.record` keeps the details first seen. A report that was unclassified then and is classified now
    must show the classification (a candidate would otherwise stay 'unclassified' for good), so replace the stored detail
    when the stored status is not 'classified' and the new one differs. Returns the number of reports refreshed."""
    n = 0
    for pr in pool:
        if state.event_of_report(pr.report.report_id) is None:
            continue
        row = con.execute("SELECT detail_json FROM event_register_reports WHERE report_id = ?",
                          [pr.report.report_id]).fetchone()
        new = json.dumps(pr.detail, sort_keys=True, default=str)
        if row is None or row[0] == new or json.loads(row[0]).get("classification_status") == "classified":
            continue
        con.execute("UPDATE event_register_reports SET detail_json = ? WHERE report_id = ?", [new, pr.report.report_id])
        n += 1
    return n


# ----------------------------------------------------------------------------- the document


def finalize_document(doc: dict[str, Any], mode: str, as_of_day: date, warnings: list[str]) -> dict[str, Any]:
    """Add the fields this job owns to the register document: grouping mode, notice, experimental label."""
    experimental = mode != "syndication"
    out = dict(doc)
    out["as_of_date"] = as_of_day.isoformat()
    out["grouping_mode"] = mode
    out["experimental"] = experimental
    out["notice"] = f"{NOTICE} {EXPERIMENTAL_NOTICE}" if experimental else NOTICE
    out["warnings"] = sorted(warnings)
    if not experimental:
        out["linker"] = {"arm": "none", "note": "syndication collapse only; reports are never linked"}
    cands = []
    for c in doc["candidates"]:
        c = dict(c)
        c["publisher_count"] = len(c["distinct_publishers"])
        c["official_source"] = c["corroboration"]["category"] == "officially_sourced"
        c["syndicated_copy_count"] = len(c["item_ids"]) - c["deduped_report_count"]
        if not experimental:
            if c["related_candidates"] or c["deduped_report_count"] != 1:
                raise RuntimeError(f"{c['candidate_id']}: syndication mode must hold exactly one report per candidate")
        cands.append(c)
    out["candidates"] = cands
    return out


def summarize(doc: dict[str, Any]) -> dict[str, Any]:
    cands = doc["candidates"]
    reports = sum(c["classification_status"]["total_reports"] for c in cands)
    classified = sum(c["classification_status"]["classified_reports"] for c in cands)
    single = sum(1 for c in cands if c["corroboration"]["category"] == "single_source")
    categories = Counter(c["corroboration"]["category"] for c in cands)
    return {
        "candidates": len(cands), "reports": reports, "items": sum(len(c["item_ids"]) for c in cands),
        "classified_reports": classified,
        "unclassified_share": ((reports - classified) / reports) if reports else None,
        "single_source": single, "single_source_share": (single / len(cands)) if cands else None,
        "corroboration": dict(sorted(categories.items())),
        "open_candidates": sum(1 for c in cands if c["status_window"]["status"] == "open"),
    }


# ----------------------------------------------------------------------------- the job


def _build(db_path: Path, mode: str, as_of_day: date, warnings: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    import duckdb

    when = as_of_instant(as_of_day)
    cfg = load_e1_config()
    try:
        resolver = load_resolver()
    except Exception as exc:  # noqa: BLE001 - entities are annotations; say so, do not stop the register
        resolver = None
        warnings.append(f"entity_resolver_unavailable:{type(exc).__name__}")
    suffixes = resolver.legal_suffixes if resolver is not None else ()
    generic = frozenset(normalize_name(g, suffixes) for g in load_generic_entities(cfg.generic_entities_ref))
    kw: dict[str, Any] = {"config": cfg, "resolver": resolver, "generic": generic, "suffixes": suffixes}

    con = duckdb.connect(str(db_path))
    try:
        state = EventState(con)
        store = RegisterStore(con)
        _guard_grouping_mode(con, mode)
        items = load_all_items(con)
        arm = None
        linker: Any = NeverLinkStub()
        if mode != "syndication":
            arm = load_arm_b_config() if mode == "arm_b" else load_arm_a_config()
            bg = background_for(items, when - timedelta(days=cfg.pool_window_days), arm, **kw)
            if mode == "arm_b":
                from macro_engine.events.embeddings import EmbeddingStore, load_model_pin

                pin = load_model_pin()
                vectors = EmbeddingStore(con, create=False).load_vectors(None, pin.name, pin.model_sha256)
                linker = ArmBLinker(arm, bg, vectors, state.cfg.open_days)
            else:
                linker = ArmALinker(arm, bg, state.cfg.open_days)
        pool = stabilize_pool(collapse_pool(items, when, **kw), store.known_items())
        result = apply_pool(state, None, linker, pool, when)
        # apply_day closes stale events at the START of a day, so candidates created by this very run from old reports
        # would be reported 'closed' with no closed_on until tomorrow's run: close them now so the same day twice is one file.
        result.closed += state.close_stale(when.date())
        store.record(pool, state)
        refreshed = refresh_report_details(con, pool, state)
        doc = finalize_document(build_register(con, when, config=cfg, arm=arm, open_days=state.cfg.open_days),
                                mode, as_of_day, warnings)
        counts = {"pool_reports": len(pool), "pool_items": sum(len(p.item_ids) for p in pool),
                  "created": len(result.created), "joined": len(result.joined),
                  "already_assigned": len(result.skipped_existing), "closed": len(result.closed),
                  "details_refreshed": refreshed}
    finally:
        con.close()
    return doc, counts


def run_events_daily(
    *, db_path: str | Path, outputs_dir: str | Path, as_of_day: date, grouping: str,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS, protected_dir: str | Path | None = None, now: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run the job. Returns (exit code, run record); the record is also written to `<outputs>/event_register_run.json`.
    `protected_dir` is where the protected artifacts are read from (default: `outputs_dir`)."""
    db, out_dir = Path(db_path), Path(outputs_dir)
    protected = Path(protected_dir) if protected_dir else out_dir
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    run: dict[str, Any] = {
        "artifact": "event_register_run", "schema_version": 1, "status": "failed", "as_of_date": as_of_day.isoformat(),
        "grouping_mode": grouping, "started_at": started.isoformat(), "register_written": False,
        "max_age_hours": max_age_hours, "freshness": None, "firewall": None, "counts": None, "summary": None,
        "timings_s": {}, "errors": [], "warnings": [],
    }
    step = {"name": "start", "t": t0}

    def mark(name: str) -> None:
        run["timings_s"][step["name"]] = round(time.monotonic() - step["t"], 3)
        step["name"], step["t"] = name, time.monotonic()

    try:
        if grouping not in GROUPING_MODES:
            raise ValueError(f"grouping must be one of {GROUPING_MODES}, got {grouping!r}")
        mark("freshness")
        fresh = news_store_freshness(db, now=now)
        run["freshness"] = fresh
        if fresh.get("error"):
            run["errors"].append(str(fresh["error"]))
        elif fresh.get("age_hours") is None:
            run["errors"].append("store_has_no_first_seen_at")
        elif fresh["age_hours"] > max_age_hours:
            run["errors"].append(f"store_stale:{fresh['age_hours']}h>{max_age_hours}h")
        if run["errors"]:
            raise _Stop()

        fw = _load_firewall()
        mark("firewall_before")
        before = fw.snapshot(protected, db)
        mark("build")
        doc, run["counts"] = _build(db, grouping, as_of_day, run["warnings"])
        run["summary"] = summarize(doc)
        mark("firewall_after")
        diffs = fw.compare(before, fw.snapshot(protected, db))
        run["firewall"] = {"ok": not diffs, "differences": diffs}
        if diffs:
            run["errors"].append("firewall_violation:" + "; ".join(diffs))
            raise _Stop()

        mark("write")
        changed = write_register(out_dir / REGISTER_FILENAME, doc)
        text = (out_dir / REGISTER_FILENAME).read_text(encoding="utf-8")
        archive = out_dir / "archive" / as_of_day.isoformat() / ARCHIVE_SUBDIR / REGISTER_FILENAME
        write_register(archive, doc)
        run.update(status="ok", register_written=True, register_changed=changed, archive_path=str(archive),
                   register_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    except _Stop:
        pass
    except Exception as exc:  # noqa: BLE001 - the run record must say what failed, whatever it was
        run["errors"].append(f"{type(exc).__name__}: {exc}")
        run["traceback"] = traceback.format_exc()
    finally:
        mark("end")
        run["finished_at"] = datetime.now(timezone.utc).isoformat()
        run["duration_s"] = round(time.monotonic() - t0, 3)
        run["previous_register_kept"] = run["status"] != "ok" and (out_dir / REGISTER_FILENAME).exists()
        _atomic_write_text(out_dir / RUN_FILENAME, json.dumps(run, indent=2, sort_keys=True, default=str) + "\n")
    return (0 if run["status"] == "ok" else 1), run
