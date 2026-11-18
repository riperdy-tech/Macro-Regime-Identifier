"""The checks, in code, on the model's reply, and the bound that turns a size word into a score change.

Nothing is clamped silently. A dimension whose reply fails any check stays as measured and the reasons are recorded.
"""
from __future__ import annotations

import json
import re
from datetime import date

CHANGES = ("unchanged", "higher", "lower")
SIZES = ("small", "typical", "large")
MAX_QUOTES = 3
MAX_EVENTS = 5
REGIME_WORDS = ("goldilocks", "reflation", "stagflation", "recession", "tightening")


def normalise(text: str) -> str:
    """Whitespace-normalised: every run of whitespace becomes one space."""
    return " ".join(str(text).split())


def parse_reply(text: str) -> dict:
    """The one JSON object in the reply. Raises ValueError when there is none."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict) or not isinstance(obj.get("dimensions"), dict):
        raise ValueError("the reply has no 'dimensions' object")
    return obj


def quote_check(quote: dict, sources: dict[str, dict], allowed: list[str], data_date: str) -> list[str]:
    """Failures for one quote: the source is named and offered for the dimension, the quote is found word for word
    (whitespace-normalised) in that source's text, and the source's release date is after the dimension's data."""
    fails = []
    if not isinstance(quote, dict) or not isinstance(quote.get("source"), str) or not isinstance(quote.get("text"), str):
        return ["quote is not {source, text}"]
    sid, text = quote["source"], normalise(quote["text"])
    if not text:
        return [f"empty quote from {sid}"]
    src = sources.get(sid)
    if src is None or src.get("status") != "fetched":
        return [f"quote names source '{sid}', which was not fetched"]
    if sid not in allowed:
        fails.append(f"source '{sid}' was not offered for this dimension")
    if text not in normalise(src["text"]):
        fails.append(f"quote not found word for word in {sid}: \"{text[:80]}\"")
    if not src.get("release_date") or src["release_date"] <= data_date:
        fails.append(f"source '{sid}' release date {src.get('release_date')} is not after {data_date}")
    return fails


def check_dimension(reply: object, sources: dict[str, dict], allowed: list[str], data_date: str) -> dict:
    """Checks one dimension's reply. Returns {passed, failures, change, size, quotes, reason}. `unchanged` always passes
    (nothing is updated, so there is nothing to check); a move needs a size, one to three quotes that pass, a reason."""
    res = {"passed": False, "failures": [], "change": None, "size": None, "quotes": [], "reason": None}
    if not isinstance(reply, dict):
        res["failures"].append("dimension reply is not an object")
        return res
    change, size = reply.get("change"), reply.get("size")
    res["change"] = change if change in CHANGES else None
    res["reason"] = reply.get("reason") if isinstance(reply.get("reason"), str) else None
    if change not in CHANGES:
        res["failures"].append(f"change '{change}' is not one of {CHANGES}")
        return res
    if change == "unchanged":
        res["passed"] = True
        return res
    res["size"] = size if size in SIZES else None
    if size not in SIZES:
        res["failures"].append(f"size '{size}' is not one of {SIZES}")
    quotes = reply.get("quotes")
    if not isinstance(quotes, list) or not 1 <= len(quotes) <= MAX_QUOTES:
        res["failures"].append(f"needs one to {MAX_QUOTES} quotes")
        quotes = []
    if not res["reason"] or not res["reason"].strip():
        res["failures"].append("no reason sentence")
    for q in quotes:
        fails = quote_check(q, sources, allowed, data_date)
        res["failures"] += fails
        if isinstance(q, dict):
            src = sources.get(q.get("source")) if isinstance(q.get("source"), str) else None
            res["quotes"].append({"source": q.get("source"), "text": q.get("text"),
                                  "release_date": (src or {}).get("release_date"), "found": not fails})
    res["passed"] = not res["failures"]
    return res


def check_events(events: object) -> tuple[list[dict], list[str]]:
    """(kept events, refusals). An event needs an ISO date and a description; more than five are cut and listed."""
    kept, refused = [], []
    if not isinstance(events, list):
        return [], ["events is not a list"] if events is not None else []
    for i, e in enumerate(events):
        try:
            date.fromisoformat(str(e["date"]))
            if not isinstance(e["event"], str) or not e["event"].strip():
                raise ValueError("no description")
        except (KeyError, TypeError, ValueError) as exc:
            refused.append(f"event {i}: {exc}")
            continue
        if len(kept) >= MAX_EVENTS:
            refused.append(f"event {i}: beyond the {MAX_EVENTS} allowed")
            continue
        kept.append({"date": str(e["date"]), "event": e["event"], "source": e.get("source")})
    return kept, refused


def regime_words(*texts: str | None) -> list[str]:
    """Regime names found in the model's free text. Annotated in the output, never a gate."""
    blob = " ".join(t for t in texts if t).lower()
    return sorted(w for w in REGIME_WORDS if re.search(rf"\b{w}\b", blob))


def score_change(change: str, size: str, percentiles: dict) -> float:
    """The signed score change a size word maps to: the dimension's own 25th (small), 50th (typical) or 90th (large)
    percentile of its one-month absolute changes, negative for 'lower'."""
    magnitude = float(percentiles[size])
    return magnitude if change == "higher" else -magnitude
