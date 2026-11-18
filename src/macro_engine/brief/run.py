"""Builds the daily brief: as measured, the sources, the one model call, the checks, the bound, as of today, the output."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from macro_engine.brief import PROMPT_VERSION, checks, inputs, model, sources as src

OUTPUT_NAME = "macro_brief.json"
HISTORY_NAME = "macro_brief_history.jsonl"


def _events_for_prompt(register_path: Path, after: str) -> list[dict]:
    """Register candidates last reported after `after`, most publishers first then newest; capped for prompt size only."""
    if not register_path.exists():
        return []
    reg = json.loads(register_path.read_text(encoding="utf-8"))
    rows = [c for c in reg.get("candidates", []) if str(c.get("last_report_at", ""))[:10] > after]
    rows.sort(key=lambda c: (c.get("publisher_count") or 0, c.get("last_report_at") or ""), reverse=True)
    return [{"date": str(c["last_report_at"])[:10], "title": c.get("title", "")} for c in rows[:model.EVENT_PROMPT_LIMIT]]


def _reading_pair(scores: dict[str, float], report_date: str, current: dict, config: Path) -> dict:
    r = inputs.regime_reading(scores, report_date, current, config)
    return {"probabilities": r["probabilities"], "leader": r["leader"]}


def run_brief(*, store: Path, config: Path, inputs_dir: Path, out_dir: Path, sources_dir: Path, run_date: date,
              get: src.Getter, ask: model.Ask, now: datetime | None = None, force: bool = False,
              source_specs: list[dict] | None = None) -> dict:
    """One run. The fetches (`get`) and the model call (`ask`) are injected. Returns the output dict (also written)."""
    now = now or datetime.now(timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OUTPUT_NAME
    if out_path.exists() and not force:
        prior = json.loads(out_path.read_text(encoding="utf-8"))
        if prior.get("date") == run_date.isoformat() and prior.get("call", {}).get("status") == "ok":
            prior["already_ran_today"] = True
            return prior

    current = inputs.read_current_regime(inputs_dir / "current_regime.json")
    report_date = str(current["date"])[:10]
    dims = inputs.regime_dimensions(config)
    measured = inputs.measured_scores(current, dims)
    dates = inputs.data_dates(current, inputs.dimension_series(config, dims))
    hist = inputs.score_history(store, dims)
    month = pd.Timestamp(report_date)
    bounds = {d: (inputs.change_percentiles(hist[d], month) if d in hist else None) for d in dims}

    records = src.load_or_fetch(run_date, sources_dir, get, source_specs, now)
    by_id = {r["id"]: r for r in records}
    allowed = {d: [r["id"] for r in records if r["status"] == "fetched" and r["release_date"] > (dates[d]["data_date"] or "")]
               for d in dims}
    offered = [by_id[i] for i in dict.fromkeys(i for ids in allowed.values() for i in ids)]
    earliest = min((dates[d]["data_date"] for d in dims if dates[d]["data_date"]), default=report_date)
    events_in = _events_for_prompt(inputs_dir / "event_register.json", earliest)

    call = {"status": "not_made", "detail": "no fetched source was published after any dimension's data date"}
    reply: dict | None = None
    if offered:
        prompt_dims = {d: {"score": measured[d]["score"], "data_date": dates[d]["data_date"],
                           "allowed": allowed[d]}
                       for d in dims if measured[d]["valid"]}
        text, detail = ask(model.build_prompt(prompt_dims, offered, events_in))
        call = {"status": "failed", "detail": detail}
        if text is not None:
            try:
                reply = checks.parse_reply(text)
                call = {"status": "ok", "detail": detail}
            except ValueError as exc:  # json.JSONDecodeError is a ValueError
                call = {"status": "failed", "detail": f"reply could not be read: {exc}"}

    dim_out: dict[str, dict] = {}
    applied: dict[str, float] = {}
    for d in dims:
        rec = {"score": measured[d]["score"], "data_date": dates[d]["data_date"], "known_by": dates[d]["known_by"],
               "series": dates[d]["series"], "sources_offered": allowed[d], "bound_percentiles": bounds[d],
               "change": "unchanged", "size": None, "score_change_applied": 0.0, "quotes": [], "reason": None,
               "status": "as_measured", "check": {"passed": None, "failures": []}}
        if not measured[d]["valid"]:
            rec["check"] = {"passed": False, "failures": [f"the measured score is not valid ({measured[d]['reason']})"]}
            rec["status"] = "refused"
        elif reply is not None:
            res = checks.check_dimension(reply["dimensions"].get(d), by_id, allowed[d], dates[d]["data_date"] or "")
            rec["reason"], rec["quotes"] = res["reason"], res["quotes"]
            rec["check"] = {"passed": res["passed"], "failures": res["failures"]}
            if not res["passed"]:
                rec["status"] = "refused"
                rec["claimed"] = {"change": res["change"], "size": res["size"]}
            elif res["change"] == "unchanged":
                rec["status"] = "unchanged"
            elif bounds[d] is None:
                rec["status"] = "refused"
                rec["check"] = {"passed": False, "failures": ["no score history to bound the change"]}
                rec["claimed"] = {"change": res["change"], "size": res["size"]}
            else:
                rec["change"], rec["size"] = res["change"], res["size"]
                rec["score_change_applied"] = checks.score_change(res["change"], res["size"], bounds[d])
                rec["status"] = "accepted"
                applied[d] = rec["score_change_applied"]
        dim_out[d] = rec

    base = {d: measured[d]["score"] for d in dims}
    as_measured = _reading_pair({}, report_date, current, config)
    stored = current.get("regime_probabilities") or {}
    as_measured["max_abs_difference_from_current_regime_json"] = max(
        (abs(as_measured["probabilities"][k] - v) for k, v in stored.items() if k in as_measured["probabilities"]),
        default=None)
    as_measured["scores"] = base

    def at(factor: float) -> dict:
        adj = {d: base[d] + factor * c for d, c in applied.items()}
        r = _reading_pair(adj, report_date, current, config)
        r["scores"] = {d: adj.get(d, base[d]) for d in dims}
        return r

    today, half, double = at(1.0), at(0.5), at(2.0)
    identical = not applied
    events, refused_events, summary, summary_words = [], [], None, []
    if reply is not None:
        events, refused_events = checks.check_events(reply.get("events"))
        summary = reply.get("summary") if isinstance(reply.get("summary"), str) else None
        summary_words = checks.regime_words(summary, *[x.get("reason") for x in dim_out.values()],
                                            *[e["event"] for e in events])

    out = {
        "artifact": "macro_brief", "schema_version": 1, "date": run_date.isoformat(), "report_month": report_date,
        "generated_at": now.isoformat(timespec="seconds"), "prompt_version": PROMPT_VERSION, "model": model.MODEL,
        "effort": model.EFFORT, "call": call,
        "judgement": {"size_percentiles": inputs.SIZE_PERCENTILES,
                      "note": ("These three percentiles of each dimension's own one-month absolute score changes "
                               "(months before the report month) are the judgement in this design. Nothing is fitted.")},
        "as_measured": as_measured, "as_of_today": today,
        "readings_identical": identical,
        "identical_note": ("No dimension change was accepted, so as of today is identical to as measured." if identical
                           else "Accepted changes: " + ", ".join(f"{d} {c:+.3f}" for d, c in applied.items())),
        "at_half_the_bound": half, "at_double_the_bound": double,
        "dimensions": dim_out,
        "events": events, "refused_events": refused_events, "summary": summary,
        "regime_words_in_model_text": summary_words,
        "sources": [{"id": r["id"], "agency": r["agency"], "url": r["url"], "release_date": r["release_date"],
                     "status": r["status"], "reason": r["reason"], "fetched_at": r["fetched_at"]} for r in records],
    }
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    line = {"date": out["date"], "generated_at": out["generated_at"], "report_month": report_date,
            "prompt_version": PROMPT_VERSION, "model": model.MODEL, "call_status": call["status"],
            "as_measured": {"probabilities": as_measured["probabilities"], "leader": as_measured["leader"]},
            "as_of_today": {"probabilities": today["probabilities"], "leader": today["leader"]},
            "at_half_the_bound": {"leader": half["leader"], "probabilities": half["probabilities"]},
            "at_double_the_bound": {"leader": double["leader"], "probabilities": double["probabilities"]},
            "scores_measured": base, "scores_as_of_today": today["scores"],
            "changes": {d: {"status": r["status"], "change": r["change"], "size": r["size"],
                            "score_change_applied": r["score_change_applied"], "data_date": r["data_date"],
                            "quotes": r["quotes"], "failures": r["check"]["failures"]} for d, r in dim_out.items()},
            "sources": [{"id": s["id"], "release_date": s["release_date"], "status": s["status"]} for s in out["sources"]],
            "events": events, "summary": summary}
    with (out_dir / HISTORY_NAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    return out
