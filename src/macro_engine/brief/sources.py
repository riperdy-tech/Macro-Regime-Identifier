"""Fetch the newest official release text for each source, once a day, and keep it on disk.

Each source is stored as data/brief_sources/<run date>/<source id>.json with its URL, fetch time, the release date
stated in the text and the text itself. A source that cannot be fetched is stored as missing, with the reason; the brief
runs on the rest. A source already stored for the run date is never fetched again (at most one fetch a day per source).
The client name in the User-Agent is generic on purpose: no personal contact details go into any request.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

USER_AGENT = "macro-brief-client/1.0"
TIMEOUT_SECONDS = 30
MAX_TEXT_CHARS = 60000  # a stored text is cut here; far above any one release page

_MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December")
_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b")

SOURCES: list[dict] = [
    {"id": "bls_cpi", "agency": "Bureau of Labor Statistics", "what": "consumer prices (CPI release)",
     "url": "https://www.bls.gov/news.release/cpi.nr0.htm", "date_hint": r"for release.{0,60}?"},
    {"id": "bls_jobs", "agency": "Bureau of Labor Statistics", "what": "jobs report (Employment Situation)",
     "url": "https://www.bls.gov/news.release/empsit.nr0.htm", "date_hint": r"for release.{0,60}?"},
    {"id": "bea_gdp", "agency": "Bureau of Economic Analysis", "what": "gross domestic product (latest release)",
     "url": "https://www.bea.gov/data/gdp/gross-domestic-product", "date_hint": r"current release:\s*"},
    {"id": "bea_income_outlays", "agency": "Bureau of Economic Analysis", "what": "personal income and outlays (latest release)",
     "url": "https://www.bea.gov/data/income-saving/personal-income", "date_hint": r"current release:\s*"},
    {"id": "fed_fomc", "agency": "Federal Reserve", "what": "latest FOMC statement and target range",
     "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "resolver": "fed_latest_statement"},
]

Getter = Callable[[str], tuple[int, bytes]]


def http_get(url: str) -> tuple[int, bytes]:
    import requests

    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, allow_redirects=True)
    return r.status_code, r.content


def _decode(content: bytes) -> str:
    return content.decode("utf-8", errors="replace").lstrip("﻿")


def html_to_text(content: bytes) -> str:
    from lxml import html

    tree = html.fromstring(_decode(content))
    for bad in tree.xpath("//script|//style|//noscript"):
        bad.getparent().remove(bad)
    return re.sub(r"\s+", " ", tree.text_content()).strip()


def parse_release_date(text: str, hint: str | None = None) -> str | None:
    """The release date stated in the text, as YYYY-MM-DD: the date right after the hint if given, else the first date."""
    candidates = []
    if hint:
        m = re.search(hint + rf"(?P<d>({_MONTHS})\s+\d{{1,2}},\s+\d{{4}})", text, flags=re.I)
        if m:
            candidates.append(m.group("d"))
    m = _DATE_RE.search(text)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            return datetime.strptime(re.sub(r"\s+", " ", c), "%B %d, %Y").date().isoformat()
        except ValueError:
            continue
    return None


def _fed_latest_statement_url(feed: bytes) -> str | None:
    """The link of the newest item titled 'Federal Reserve issues FOMC statement' in the Fed's monetary policy feed."""
    xml = _decode(feed)
    for item in re.findall(r"<item>(.*?)</item>", xml, flags=re.S):
        if "FOMC statement" in item:
            m = re.search(r"<link>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)", item)
            if m:
                return m.group(1)
    return None


def fetch_one(spec: dict, get: Getter, now: datetime) -> dict:
    """One source's record. Never raises: any failure becomes status 'missing' with a reason."""
    rec = {"id": spec["id"], "agency": spec["agency"], "what": spec["what"], "url": spec["url"],
           "fetched_at": now.isoformat(timespec="seconds"), "status": "missing", "reason": None,
           "release_date": None, "text": None}
    try:
        url = spec["url"]
        if spec.get("resolver") == "fed_latest_statement":
            code, body = get(url)
            if code != 200:
                rec["reason"] = f"HTTP {code} for the feed {url}"
                return rec
            url = _fed_latest_statement_url(body)
            if not url:
                rec["reason"] = "no FOMC statement item found in the feed"
                return rec
            rec["url"] = url
        code, body = get(url)
        if code != 200:
            snippet = re.sub(r"\s+", " ", _decode(body))[:120]
            rec["reason"] = f"HTTP {code} ({snippet})"
            return rec
        text = html_to_text(body)[:MAX_TEXT_CHARS]
        if not text:
            rec["reason"] = "empty page text"
            return rec
        rec["text"] = text
        rec["release_date"] = parse_release_date(text, spec.get("date_hint"))
        if rec["release_date"] is None:
            rec["status"] = "fetched_no_release_date"
            rec["reason"] = "no release date found in the text; the source is not offered to the model"
        else:
            rec["status"] = "fetched"
    except Exception as exc:  # noqa: BLE001  a failed source is recorded, never fatal
        rec["status"] = "missing"
        rec["reason"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return rec


def load_or_fetch(run_date: date, root: Path, get: Getter, specs: list[dict] | None = None,
                  now: datetime | None = None) -> list[dict]:
    """Every source's record for the run date; a record already on disk for that date is reused, not fetched again."""
    now = now or datetime.now(timezone.utc)
    folder = root / run_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for spec in specs if specs is not None else SOURCES:
        path = folder / f"{spec['id']}.json"
        if path.exists():
            out.append(json.loads(path.read_text(encoding="utf-8")))
            continue
        rec = fetch_one(spec, get, now)
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        out.append(rec)
    return out
