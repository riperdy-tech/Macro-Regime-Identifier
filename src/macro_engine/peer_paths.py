"""peer_paths.py - the single owner of where MRI's PEER repositories live.

MRI both reads from and is read by its peers, and until 2026-09-22 it named them with absolute
Windows paths in three places:

    scripts/build_equity_aggregate.py   DEFAULT_DATA_DIR = C:\\...\\Stock Screener\\Stock Screener\\public\\data
    scripts/build_valuation_panel.py    DEFAULT_DATA_DIR = the same path again
    src/macro_engine/daily_health.py    RS2_CONFIG_PATH default = C:\\...\\RS2 Local\\config.json

Three copies of two facts, on one machine's layout. This module holds them once.

Resolution order per key:

  1. the key's own environment variable, if set - used VERBATIM, never probed. An operator who
     names a path means it; a typo must surface where the file is opened, not be quietly
     substituted.
  2. a probe across the candidate roots and folder spellings below, current layout first.

The screener is a sibling of this repo in BOTH layouts. RS2 is not: today it lives one level
further up, outside the `Stock Screener` wrapper, and after the planned move it becomes a sibling.
So both the parent and the grandparent are searched, and the folder move needs no edit here.

Callers decide whether absence is fatal. It is not fatal for either current caller: the anchor
builders take an explicit --data-dir, and `daily_health` already falls back to its own default
when RS2's config is unreachable, deliberately, so that MRI can run with no RS2 checkout present.

Mirrors rs2-local/paths.py and stock-screener/scripts/peer_paths.py. See
docs/superpowers/specs/2026-09-22-stocks-workspace-reorg-design.md (stock-screener repo).
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]      # the macro-regime-indicator repo root

# Folder spellings, current layout first, so that a half-finished move resolves to the repo that
# actually holds the data rather than to a freshly created empty sibling.
_SCREENER_NAMES = ("Stock Screener", "stock-screener")
_RS2_NAMES = ("RS2 Local", "rs2-local")


def _candidate_roots() -> list[Path]:
    """Parents that may hold the peer repos.

    STOCKS_ROOT replaces the search when set. Otherwise both the parent (where the screener sits
    today, and where everything sits after the move) and the grandparent (where RS2 sits today,
    outside the wrapper) are candidates.
    """
    env = (os.environ.get("STOCKS_ROOT") or "").strip()
    if env:
        return [Path(env).expanduser()]
    return [REPO_ROOT.parent, REPO_ROOT.parent.parent]


def _probe(names: tuple[str, ...], suffix: str | None = None) -> Path | None:
    for root in _candidate_roots():
        for name in names:
            candidate = root / name
            if suffix:
                candidate = candidate / suffix
            if candidate.exists():
                return candidate
    return None


def _tried(names: tuple[str, ...], suffix: str | None = None) -> str:
    out = []
    for root in _candidate_roots():
        for name in names:
            p = root / name
            out.append(str(p / suffix if suffix else p))
    return ", ".join(out)


def screener_data_dir(required: bool = False) -> Path | None:
    """The screener's `public/data`, the corpus the anchor builders read."""
    env = (os.environ.get("SCREENER_DATA_DIR") or "").strip()
    if env:
        return Path(env).expanduser()

    hit = _probe(_SCREENER_NAMES, "public/data")
    if hit is not None:
        return hit

    if required:
        raise FileNotFoundError(
            f"cannot locate the screener data directory. Tried SCREENER_DATA_DIR, then: "
            f"{_tried(_SCREENER_NAMES, 'public/data')}. Set SCREENER_DATA_DIR or STOCKS_ROOT, "
            f"or pass --data-dir - do not guess."
        )
    return None


def rs2_config_path(required: bool = False) -> Path | None:
    """RS2's config.json, read only to borrow `anchor_max_age_days`.

    MRI must not invent its own staleness limit: the consumer already decides how old is too old,
    and two limits would eventually disagree.
    """
    env = (os.environ.get("RS2_CONFIG_PATH") or "").strip()
    if env:
        return Path(env).expanduser()

    hit = _probe(_RS2_NAMES, "config.json")
    if hit is not None:
        return hit

    if required:
        raise FileNotFoundError(
            f"cannot locate RS2's config.json. Tried RS2_CONFIG_PATH, then: "
            f"{_tried(_RS2_NAMES, 'config.json')}. Set RS2_CONFIG_PATH or STOCKS_ROOT - do not "
            f"guess."
        )
    return None


if __name__ == "__main__":
    print(f"roots              = {', '.join(str(r) for r in _candidate_roots())}")
    for label, value in (("screener_data_dir", screener_data_dir()),
                         ("rs2_config_path", rs2_config_path())):
        state = "" if value is None else ("(exists)" if value.exists() else "(MISSING)")
        print(f"{label:18s} = {value}  {state}")
