"""The one model call a day: the prompt, and a small caller for the `agy` command-line tool.

The invocation (model tag, effort, stream-json in and out, sandbox) is the one rs2-local/tools/site/gemini_ondemand.py
uses; it is written out here and nothing is imported from there. The model is never the "Argon" model, and if `agy` is
missing or out of quota the call fails and the brief says so: no other model or tool is tried.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from macro_engine.brief import PROMPT_VERSION
from macro_engine.brief.inputs import DIMENSION_TEXT

MODEL = "gemini-4-hydrogen-high"  # the tag gemini_ondemand.py's "gemini" arm uses
EFFORT = "high"
CALL_TIMEOUT_SECONDS = 1200
EVENT_PROMPT_LIMIT = 40  # how many register events are shown to the model (prompt size only; the register is not trimmed on disk)

Ask = Callable[[str], "tuple[str | None, str]"]


def agy_exe() -> str:
    path = (os.environ.get("AGY_EXE") or shutil.which("agy")
            or str(Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"))
    if not Path(path).exists():
        raise RuntimeError(f"agy not found (AGY_EXE, PATH, LOCALAPPDATA/agy/bin): {path}")
    return path


def make_agy_ask(cwd: Path) -> Ask:
    """A function that sends one prompt through agy and returns (reply text or None, detail)."""

    def ask(prompt: str) -> tuple[str | None, str]:
        try:
            cwd.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"
            r = subprocess.run(
                [agy_exe(), "--model", MODEL, "--effort", EFFORT, "--sandbox", "--input-format", "stream-json",
                 "--output-format", "stream-json", "-p", ""],
                input=line, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(cwd),
                timeout=CALL_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001  a failed call is recorded, never fatal
            return None, f"{type(exc).__name__}: {str(exc)[:300]}"
        res = None
        for ln in (r.stdout or "").splitlines():
            if '"event":"result"' in ln:
                try:
                    res = json.loads(ln)["result"]
                except ValueError:
                    pass
        if res and res.get("status") == "SUCCESS" and (res.get("response") or "").strip():
            return res["response"], "agy: SUCCESS"
        return None, f"agy: {(res or {}).get('status')} {(res or {}).get('error') or (r.stderr or '')[-300:]}".strip()

    return ask


def build_prompt(dims: dict[str, dict], sources: list[dict], events: list[dict]) -> str:
    """`dims`: {dimension: {score, data_date, allowed: [source ids]}}; `sources`: the records offered;
    `events`: [{date, title}] from the daily event register."""
    out = [f"MACRO BRIEF, prompt version {PROMPT_VERSION}.", "",
           "You are helping update five readings of the U.S. economy. Each reading (a dimension) is a score computed from "
           "monthly and daily statistics. Those statistics publish weeks late, so newer official releases may already say "
           "which way a dimension has moved since its data date.", "",
           "RULES",
           "1. Do NOT name or guess an overall economic regime or label of the economy. You only say which way each "
           "dimension's score has moved.",
           "2. Use ONLY the source texts below. For each dimension, only the sources listed for it count: they were "
           "published after the dimension's data date.",
           "3. If no listed source bears on a dimension, answer 'unchanged' for it. 'unchanged' is the normal answer. The score "
           "already includes everything that was measured up to its data date: for a monthly statistic the whole month "
           "shown. A release that only reports a period the score already covers (for example last month's figure that "
           "the score was built from) is not new: answer 'unchanged' unless the release says something that happened "
           "after the data date.",
           "4. 'higher' means the dimension's SCORE should rise; 'lower' means it should fall. Read each dimension's "
           "description for what moves its score.",
           "5. For 'higher' or 'lower', give a size ('small', 'typical' or 'large': how big a move this is against a usual "
           "month for that dimension) and one to three quotes copied WORD FOR WORD from one named source (the exact "
           "characters of the text, no ellipses, no paraphrase). A quote that is not found in the named source is thrown "
           "away and the dimension stays unchanged.",
           "", "DIMENSIONS"]
    for name, d in dims.items():
        listed = ", ".join(d["allowed"]) if d["allowed"] else "none (answer unchanged)"
        out.append(f"- {name}: {DIMENSION_TEXT[name]} Current score {d['score']:.3f}. Data date {d['data_date']}. "
                   f"Sources that count: {listed}.")
    out += ["", "SOURCE TEXTS (each begins with its id, agency, release date)"]
    for s in sources:
        out += [f"=== SOURCE {s['id']} | {s['agency']} | {s['what']} | released {s['release_date']} ===", s["text"], ""]
    out += ["EVENTS COLLECTED BY THE DAILY NEWS REGISTER (date, headline; for the events list only, not for quotes)"]
    out += [f"- {e['date']} {e['title']}" for e in events] or ["- none"]
    out += ["",
            "REPLY with one JSON object and nothing else, in exactly this shape:",
            json.dumps({
                "dimensions": {n: {"change": "unchanged | higher | lower", "size": "small | typical | large | none",
                                   "quotes": [{"source": "<source id>", "text": "<exact words from that source>"}],
                                   "reason": "one sentence"} for n in dims},
                "events": [{"date": "YYYY-MM-DD", "event": "short description", "source": "<source id or event register>"}],
                "summary": "three plain sentences on what the newest releases say, without naming a regime"},
                indent=1),
            "Give at most five events, each with the date it happened or was published."]
    return "\n".join(out)
