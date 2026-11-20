"""The daily read: `outputs/daily_read.json` (WO_MN1, MRI review step 4).

One cloud-model call a day that says WHY the market is where it is, from the day's classified news. The numbers
(market state, economy state, driver exposures) never depend on it and nothing here moves one: the read only explains.
Every quote the model gives is copied back to its news item by code; a quote not found there is thrown away, and a
read with fewer than two verified quotes from two different items is not published.

The invocation of the model (tag, effort, stream-json in and out, sandbox) is the one `rs2-local/tools/site/gemini_ondemand.py`
uses; it is written out here and nothing is imported from there. The model is never the "Argon" model. When the `agy`
call fails (missing, out of quota), the same prompt goes once to Claude Sonnet through the Claude Code command line, as
that file's fallback does (operator 2026-11-19 and 2026-11-21); the read records which model answered. Nothing else is
tried.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from macro_engine.market_state import _atomic_write_text

SCHEMA = "mri.daily_read.v1"
FILENAME = "daily_read.json"
HISTORY_FILENAME = "daily_read_history.jsonl"
ARCHIVE_SUBDIR = "daily_read"
PROMPT_VERSION = 1
MODEL = "gemini-4-hydrogen-high"  # the tag gemini_ondemand.py's "gemini" arm uses
FALLBACK_MODEL = "claude-sonnet-5-5"  # the tag gemini_ondemand.py's "sonnet" arm uses
EFFORT = "high"
CALL_TIMEOUT_SECONDS = 600
WINDOW_HOURS = 72
MAX_ITEMS = 200  # nearly every item of the window: ranking by severity alone crowds market news out behind war news (first real prompt, 2026-11-20)
MIN_ITEMS = 5
MIN_QUOTE_WORDS = 4
MIN_QUOTES = 2
MIN_QUOTE_ITEMS = 2
MAX_EXPOSED = 5
CARRY_DAYS = 5
CALM_WORDS = ("calm", "no stress", "not under stress", "markets are stable")
DRIVERS = ("rates", "growth", "credit", "inflation", "policy", "geopolitics", "earnings", "other", "unclear")
FAMILY = {"rates": "rates", "growth": "growth_credit", "credit": "growth_credit", "inflation": "inflation"}

Ask = Callable[[str], "tuple[str | None, str]"]


# --------------------------------------------------------------------------- the model call (copied from the brief branch)


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


def claude_exe() -> str:
    path = (os.environ.get("CLAUDE_EXE") or shutil.which("claude")
            or str(Path.home() / ".local" / "bin" / "claude.exe"))
    if not Path(path).exists():
        raise RuntimeError(f"claude not found (CLAUDE_EXE, PATH, ~/.local/bin): {path}")
    return path


def make_claude_ask(cwd: Path) -> Ask:
    """The same prompt to Claude Sonnet through the Claude Code command line: no tools, no settings, no session kept."""

    def ask(prompt: str) -> tuple[str | None, str]:
        try:
            cwd.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                [claude_exe(), "-p", "--model", FALLBACK_MODEL, "--effort", EFFORT, "--system-prompt",
                 "You are an analyst. Do exactly what the message asks, in one reply.", "--tools", "",
                 "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence",
                 "--output-format", "json"],
                input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(cwd),
                timeout=CALL_TIMEOUT_SECONDS)
            d = json.loads(r.stdout[r.stdout.index("{"):])
        except Exception as exc:  # noqa: BLE001  a failed call is recorded, never fatal
            return None, f"claude: {type(exc).__name__}: {str(exc)[:300]}"
        if not d.get("is_error") and d.get("subtype") == "success" and (d.get("result") or "").strip():
            return d["result"], "claude: SUCCESS"
        return None, f"claude: {d.get('api_error_status') or d.get('subtype')} {str(d.get('result'))[:200]}".strip()

    return ask


def with_fallback(first: Ask, second: Ask) -> Ask:
    """`first`, and when it gives no reply, `second` once. `ask.model` names the model whose reply (or last failure) is returned."""

    def ask(prompt: str) -> tuple[str | None, str]:
        ask.model = MODEL
        reply, detail = first(prompt)
        if reply is not None:
            return reply, detail
        ask.model = FALLBACK_MODEL
        reply, detail2 = second(prompt)
        return reply, f"{detail}; {detail2}"

    ask.model = MODEL
    return ask


# --------------------------------------------------------------------------- the news items


def _norm_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def select_items(db_path: str | Path, now: datetime) -> list[dict[str, Any]]:
    """News published in the WINDOW_HOURS before `now`, each with its newest successful (non-mock) classification,
    ranked by severity x confidence then published_at, one per normalised title, the top MAX_ITEMS, ids N1.."""
    import duckdb

    end = now.astimezone(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(hours=WINDOW_HOURS)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT i.news_id, i.source, i.source_url, i.title, i.body, i.published_at, c.summary, c.severity, c.confidence
            FROM news_items i
            JOIN (
                SELECT news_id, summary, severity, confidence,
                       row_number() OVER (PARTITION BY news_id ORDER BY classified_at DESC) AS rn
                FROM news_classifications
                WHERE classification_status = 'success' AND ai_provider IS DISTINCT FROM 'mock'
            ) c ON c.news_id = i.news_id AND c.rn = 1
            WHERE i.published_at > ? AND i.published_at <= ? AND i.title IS NOT NULL AND trim(i.title) <> ''
            """,
            [start, end],
        ).fetchall()
    finally:
        con.close()
    cols = ("news_id", "source", "source_url", "title", "body", "published_at", "summary", "severity", "confidence")
    items = [dict(zip(cols, r)) for r in rows]
    for it in items:
        it["body"] = it["body"] or ""
        # an item with no severity or confidence ranks below every item that has both (0.0 is a value, not a gap)
        has = it["severity"] is not None and it["confidence"] is not None
        it["_rank"] = float(it["severity"]) * float(it["confidence"]) if has else -1.0
    items.sort(key=lambda it: (it["_rank"], it["published_at"]), reverse=True)
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for it in items:
        key = _norm_title(it["title"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(it)
        if len(kept) == MAX_ITEMS:
            break
    out = []
    for n, it in enumerate(kept, 1):
        it.pop("_rank")
        out.append({"id": f"N{n}", **it, "published_at": it["published_at"].strftime("%Y-%m-%dT%H:%M:%SZ")})
    return out


# --------------------------------------------------------------------------- the prompt


def _f(x: Any, nd: int = 1, sign: bool = False) -> str:
    return "n/a" if x is None else f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def _series_line(name: str, e: dict[str, Any] | None) -> str:
    if not e:
        return f"{name}: not available"
    changes = ", ".join(f"{w} sessions {_f(e.get(f'change_{w}'), 0, True)}" for w in (5, 21, 63))
    return f"{name}: {_f(e.get('level'), 2)}% ; change in basis points: {changes}"


def _fund_line(rows: list[dict[str, Any]]) -> str:
    return ", ".join(f"{r['sector_id']} ({r['fund']}) {_f(r['relative_21'], 1, True)}" for r in rows) or "none"


def build_prompt(market: dict[str, Any], economy: str | None, exposures: str | None,
                 previous: dict[str, Any] | None, items: list[dict[str, Any]]) -> str:
    facts = market["facts"] or {}
    spy = facts.get("spy") or {}
    series = facts.get("series") or {}
    driver = market["driver"] or "none"
    if market.get("driver_window") is not None:
        driver += f" (window {market['driver_window']} sessions)"
    out = [
        f"DAILY READ, prompt version {PROMPT_VERSION}.", "",
        "You explain what is moving the U.S. stock market today, using only the news items listed below. Prices and "
        "the measured numbers were computed by code and are not yours to change.", "",
        "RULES",
        "1. The stress step and every number below are measured facts. Do not dispute or restate them differently.",
        "2. Say what is driving the market and why, from the news items only. If the items do not explain it, answer "
        "'unclear'. 'unclear' is an acceptable answer.",
        "3. Give two to five quotes, each copied WORD FOR WORD from the title or body of ONE named item (no ellipses, no "
        "paraphrase), from at least two different items. A quote not found in its item is thrown away.",
        "4. Do not forecast prices. Do not recommend buying or selling.",
        "", "MARKET TODAY",
        f"Summary: {market['summary']}",
        f"Stress step: {market['step']} since {market['since']}",
        f"Price driver: {driver}",
        f"Backdrop: {market['backdrop'] or 'none'}",
        f"Index (SPY) return in percent: 5 sessions {_f(spy.get('return_5'), 1, True)}, 21 sessions "
        f"{_f(spy.get('return_21'), 1, True)}, 63 sessions {_f(spy.get('return_63'), 1, True)}",
        f"VIX: {_f((facts.get('vix') or {}).get('level'), 1)}",
        _series_line("2-year yield", series.get("DGS2")),
        _series_line("10-year yield", series.get("DGS10")),
        f"Sector leaders against the index, 21 sessions, points: {_fund_line(market.get('leaders') or [])}",
        f"Sector laggards against the index, 21 sessions, points: {_fund_line(market.get('laggards') or [])}",
        "", "ECONOMY", economy or "not available",
        "", "MEASURED SENSITIVITIES", exposures or "not available",
        "", "PREVIOUS READ",
        f"{previous['date']} | driver: {previous['read']['driver']} | why: {previous['read']['why']}" if previous else "none",
        "", "NEWS ITEMS (id, date, source, title; then the body)",
    ]
    for it in items:
        out += [f"{it['id']} | {it['published_at'][:10]} | {it['source']} | {it['title']}", f"    {it['body']}"]
    out += [
        "", "REPLY with one JSON object and nothing else, in exactly this shape:",
        json.dumps({
            "driver": "rates | growth | credit | inflation | policy | geopolitics | earnings | sector:<name> | other | unclear",
            "why": "two plain sentences",
            "changed_since_previous": "one sentence, or 'nothing material'",
            "exposed": [{"what": "a sector or a kind of company", "direction": "hurt | helped", "reason": "one clause"}],
            "agrees_with_prices": "yes | partly | no",
            "quotes": [{"item": "N12", "text": "exact words from that item"}]}, indent=1),
        f"Give at most {MAX_EXPOSED} exposed entries.",
    ]
    return "\n".join(out)


# --------------------------------------------------------------------------- checks by code


def _text(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _valid_driver(d: Any) -> bool:
    return isinstance(d, str) and (d in DRIVERS or (d.startswith("sector:") and d[len("sector:"):].strip() != ""))


def parse_reply(text: str) -> tuple[dict[str, Any] | None, str]:
    """(reply, reason). The first JSON object in the text; every field present with an allowed value, else (None, why)."""
    decoder = json.JSONDecoder()
    obj = None
    for m in re.finditer(r"\{", text or ""):
        try:
            cand, _ = decoder.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(cand, dict):
            obj = cand
            break
    if obj is None:
        return None, "no JSON object in the reply"
    if not _valid_driver(obj.get("driver")):
        return None, f"driver not allowed: {obj.get('driver')!r}"
    for key in ("why", "changed_since_previous"):
        if not _text(obj.get(key)):
            return None, f"{key} missing or empty"
    if obj.get("agrees_with_prices") not in ("yes", "partly", "no"):
        return None, f"agrees_with_prices not allowed: {obj.get('agrees_with_prices')!r}"
    exposed = obj.get("exposed")
    if not isinstance(exposed, list) or len(exposed) > MAX_EXPOSED:
        return None, f"exposed is not a list of at most {MAX_EXPOSED}"
    for e in exposed:
        if not (isinstance(e, dict) and _text(e.get("what")) and e.get("direction") in ("hurt", "helped") and _text(e.get("reason"))):
            return None, "an exposed entry is malformed"
    quotes = obj.get("quotes")
    if not isinstance(quotes, list):
        return None, "quotes is not a list"
    for q in quotes:
        if not (isinstance(q, dict) and _text(q.get("item")) and _text(q.get("text"))):
            return None, "a quote is malformed"
    return {k: obj[k] for k in ("driver", "why", "changed_since_previous", "exposed", "agrees_with_prices", "quotes")}, ""


def _collapse(s: str) -> str:
    return " ".join(s.split())


def check_quote(quote: str, item: dict[str, Any] | None) -> bool:
    """True when the quote has at least MIN_QUOTE_WORDS words and is found, whitespace collapsed and case kept, in the
    item's title or body. An item that was not offered (None) fails."""
    if item is None or not isinstance(quote, str):
        return False
    q = _collapse(quote)
    if len(q.split()) < MIN_QUOTE_WORDS:
        return False
    return q in _collapse(item["title"]) or q in _collapse(item["body"])


def check_why(why: str, step: str | None) -> bool:
    """False when, under a stress or crisis step, the explanation calls the market calm."""
    if step not in ("stress", "crisis"):
        return True
    low = why.lower()
    return not any(w in low for w in CALM_WORDS)


def verify(reply: dict[str, Any], items: list[dict[str, Any]], step: str | None) -> tuple[bool, list[dict[str, Any]], int, str]:
    """(accepted, verified quotes with their item's fields, rejected count, reason when not accepted)."""
    by_id = {it["id"]: it for it in items}
    good: list[dict[str, Any]] = []
    rejected = 0
    seen: set[tuple[str, str]] = set()
    for q in reply["quotes"]:
        it = by_id.get(q["item"])
        if check_quote(q["text"], it):
            key = (q["item"], _collapse(q["text"]))
            if key not in seen:
                seen.add(key)
                good.append({"item": q["item"], "text": _collapse(q["text"]), "title": it["title"], "source": it["source"],
                             "source_url": it["source_url"], "published_at": it["published_at"]})
        else:
            rejected += 1
    if not check_why(reply["why"], step):
        return False, good, rejected, f"why contradicts the {step} step"
    if reply["driver"] == "unclear":
        return True, good, rejected, ""
    if len(good) < MIN_QUOTES or len({q["item"] for q in good}) < MIN_QUOTE_ITEMS:
        return False, good, rejected, f"{len(good)} verified quotes from {len({q['item'] for q in good})} items; need {MIN_QUOTES} from {MIN_QUOTE_ITEMS}"
    return True, good, rejected, ""


def agree(model_driver: str | None, price_driver: str | None) -> bool | None:
    """True when the model's driver family equals the price driver's family; None when either is unclear or none."""
    if model_driver in (None, "unclear") or price_driver in (None, "unclear", "none"):
        return None
    m = "sector_led" if model_driver.startswith("sector:") else FAMILY.get(model_driver, model_driver)
    p = "sector_led" if price_driver.startswith("sector_led") else price_driver
    return m == p


# --------------------------------------------------------------------------- the artifact


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _sentence(doc: dict[str, Any] | None, name: str, reasons: list[str]) -> str | None:
    if doc is None:
        reasons.append(f"{name}_unavailable: {name}.json is missing or unreadable")
        return None
    key = "summary"
    if doc.get("degraded") or not _text(doc.get(key)):
        reasons.append(f"{name}_unavailable: {name}.json is degraded or has no summary")
        return None
    return doc[key]


def _previous(outputs: Path) -> dict[str, Any] | None:
    """The last fresh read: {date, read, quotes}; None when there is none."""
    doc = _read_json(outputs / FILENAME)
    if not doc or not isinstance(doc.get("read"), dict):
        return None
    day = doc.get("as_of") if doc.get("status") == "fresh" else doc.get("carried_from") if doc.get("status") == "kept_previous" else None
    if not day:
        return None
    return {"date": day, "read": doc["read"], "quotes": doc.get("quotes") or []}


def _empty(now: datetime, as_of: str | None, status: str, detail: str, reasons: list[str], items_offered: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "as_of": as_of,
        "built_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "changes": "additive artifact; it explains and moves no number; no score, anchor, table or other output reads it or is changed by it",
        "summary": None,
        "read": None,
        "quotes": [],
        "quotes_rejected": 0,
        "carried_from": None,
        "prices": None,
        "agree": None,
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "items_offered": items_offered,
        "detail": detail,
        "reasons": reasons,
    }


def build_daily_read(db_path: str | Path, outputs_dir: str | Path = "outputs", now: datetime | None = None,
                     ask: Ask | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    outputs = Path(outputs_dir)
    reasons: list[str] = []
    market = _read_json(outputs / "market_state.json")
    if market is None or market.get("step") is None:
        reasons.append("market_state_unavailable: market_state.json is missing, unreadable or has no step")
        return _empty(now, (market or {}).get("asof"), "none", "no call made", reasons, 0)
    as_of = market.get("asof")
    economy = _sentence(_read_json(outputs / "economy_state.json"), "economy_state", reasons)
    exposures = _sentence(_read_json(outputs / "driver_exposures.json"), "driver_exposures", reasons)
    items = select_items(db_path, now)
    if len(items) < MIN_ITEMS:
        reasons.append("too_few_items")
        doc = _empty(now, as_of, "none", f"no call made: {len(items)} items, fewer than {MIN_ITEMS}", reasons, len(items))
        doc["prices"] = _prices(market)
        return doc

    previous = _previous(outputs)
    prompt = build_prompt(market, economy, exposures, previous, items)
    if ask is None:
        cwd = Path(tempfile.gettempdir()) / "mri_daily_read_agy"
        ask = with_fallback(make_agy_ask(cwd), make_claude_ask(cwd))
    reply_text, detail = ask(prompt)
    read, good, rejected, why_not = None, [], 0, ""
    if reply_text is None:
        why_not = "call failed"
    else:
        parsed, why_not = parse_reply(reply_text)
        if parsed is not None:
            ok, good, rejected, why_not = verify(parsed, items, market["step"])
            read = parsed if ok else None
    if why_not:
        detail = f"{detail}; rejected: {why_not}" if reply_text is not None else detail
        reasons.append(f"call_not_accepted: {why_not}")

    doc = _empty(now, as_of, "none", detail, reasons, len(items))
    doc["model"] = getattr(ask, "model", MODEL)  # the model that answered today's call
    doc["prices"] = _prices(market)
    doc["quotes_rejected"] = rejected
    if read is not None:
        doc["status"] = "fresh"
        doc["read"] = {k: read[k] for k in ("driver", "why", "changed_since_previous", "exposed", "agrees_with_prices")}
        doc["quotes"] = good
        read_date = as_of
    else:
        carried = previous
        if carried is not None:
            age = (date.fromisoformat(as_of) - date.fromisoformat(carried["date"])).days
            if age > CARRY_DAYS:
                reasons.append(f"carry_expired: the last fresh read is {age} days old, more than {CARRY_DAYS}")
                carried = None
        if carried is None:
            return doc
        doc["status"] = "kept_previous"
        doc["read"], doc["quotes"], doc["carried_from"] = carried["read"], carried["quotes"], carried["date"]
        read_date = carried["date"]
    doc["agree"] = agree(doc["read"]["driver"], market["driver"])
    doc["summary"] = f"News read ({read_date}): {doc['read']['why']} Prices say: {market['driver']}."
    return doc


def _prices(market: dict[str, Any]) -> dict[str, Any]:
    return {"stress_step": market["step"], "driver": market["driver"], "backdrop": market["backdrop"]}


def write_daily_read(db_path: str | Path, outputs_dir: str | Path = "outputs", now: datetime | None = None,
                     ask: Ask | None = None) -> tuple[Path, Path]:
    """Write `<outputs>/daily_read.json`, its archive copy under `archive/<build day>/daily_read/`, and append one line to
    `<outputs>/daily_read_history.jsonl`."""
    doc = build_daily_read(db_path, outputs_dir, now, ask)
    text = json.dumps(doc, indent=2, allow_nan=False) + "\n"  # a NaN is a bug here, never a value
    out = Path(outputs_dir)
    main_path = out / FILENAME
    archive_path = out / "archive" / doc["built_at"][:10] / ARCHIVE_SUBDIR / FILENAME
    _atomic_write_text(main_path, text)
    _atomic_write_text(archive_path, text)
    line = {
        "as_of": doc["as_of"],
        "built_at": doc["built_at"],
        "status": doc["status"],
        "driver": (doc["read"] or {}).get("driver"),
        "agree": doc["agree"],
        "quotes_passed": len(doc["quotes"]) if doc["status"] == "fresh" else 0,  # today's call only
        "quotes_rejected": doc["quotes_rejected"],
        "detail": doc["detail"],
    }
    with (out / HISTORY_FILENAME).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, allow_nan=False) + "\n")
    return main_path, archive_path
