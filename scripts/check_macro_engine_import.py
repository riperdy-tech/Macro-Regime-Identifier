#!/usr/bin/env python3
"""Fail fast if `macro_engine` is not importable from THIS repository.

The venv's editable install (`.pth`) can point at a stale path after the repo moves -- a
reorg did exactly this: `python -m macro_engine.cli` raised `ModuleNotFoundError`, and the
daily diagnostic silently stopped running for four months because nothing checked. This runs
before any real work so a broken environment fails loudly, at the top of the log, instead of
looking like a quiet no-op.

Usage: python scripts/check_macro_engine_import.py
Exit code: 0 if `macro_engine` imports and resolves under this repo root, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        import macro_engine
    except ModuleNotFoundError as exc:
        print(
            f"ERROR: `import macro_engine` failed ({exc}).\n"
            f"  expected repo root : {REPO_ROOT}\n"
            "  fix: activate this repo's venv and run `pip install -e .` from the repo root.",
            file=sys.stderr,
        )
        return 1

    resolved = Path(macro_engine.__file__).resolve()
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        print(
            "ERROR: `macro_engine` resolved OUTSIDE this repository -- the editable install "
            "points at a stale path (this happens after a reorg or a repo move).\n"
            f"  resolved macro_engine.__file__ : {resolved}\n"
            f"  expected repo root             : {REPO_ROOT}\n"
            "  fix: run `pip install -e .` from the repo root to repair the editable install.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
