# macro-regime-indicator

The Macro Regime Intelligence Engine: a local-first engine that turns FRED data into transparent
U.S. macro regime diagnostics, and the source of the capital-market anchors that `rs2-local`
consumes. Currently `v1.0-rc1`.

It is a diagnostic platform. It is not investment advice, a trading system, an allocation system,
a security-selection engine, or a performance-validated forecasting model. Historical outputs use
revised FRED data, not ALFRED point-in-time vintages — say so whenever an output is presented as
history.

Workspace-wide rules — path resolution between repositories, the ten non-negotiables, working
style — are in [../AGENTS.md](../AGENTS.md) and are not repeated here. This file carries only what
is specific to this repository.

## Layout

`src/macro_engine/` holds the engine, as a chain of phases: `ingest/` (FRED), `normalize/`,
`features/`, `dimensions/`, `regimes/`, `diagnostics/`, `sectors/`, `news/`, `reports/`,
`outputs/`, `storage/` (DuckDB at `data/macro_engine.duckdb`). `cli.py` is the Typer entry point
for every phase; `pipeline_runner.py` and `daily.py` drive the daily workflow; `replay.py` runs
historical news replay; `dashboard_export.py` emits the read-only dashboard JSON in `dashboard/`.

`scripts/run_daily_diagnostic.ps1` / `.sh` is the repeatable daily run. `outputs/` holds generated
artifacts and `outputs/archive/` the dated copies — they are products, not source.

## The anchors are a contract

`src/macro_engine/anchors/` (cost of capital, growth, multiples, PIT calendar) is the one part of
this repository another system depends on: `rs2-local` reads the published anchors through its own
resolver. Anchors are additive artifacts written beside the other outputs, which is why `rs2-local`
looks at `MRI_ANCHORS_DIR` first and falls back to `MRI_OUTPUTS_DIR`.

Changing an anchor's schema, units, or meaning changes the discount rates in live underwriting.
Treat it as a versioned decision: state it, do not slip it into an unrelated change, and check
`tests/test_anchor_*.py` covers the new shape.

`src/macro_engine/peer_paths.py` resolves everything outside this repository. Never hardcode a
sibling path, and never walk up the directory tree to find one.

Do not touch Stock Screener reverse-engine files from this repository.

## Layer maturity — say which layer you are in

Not every layer carries the same weight, and claims should match:

- **v0.1 macro scoring and the anchors** — the stable core.
- **v0.2 sector macro mapping and v0.3 news overlay** — experimental. Sector and news scores are
  diagnostics, and the combined output is deliberately kept separate from macro scoring.
- **v0.9 replay** — an operating check, not a predictive backtest. Never describe it as one.

Accumulated real-news history is still too short for predictive validation. Do not build anything
that presumes otherwise.

## Testing

This repository has its own virtual environment and the suite needs it:

```bash
.venv/Scripts/python.exe -m pytest -q
```

Tests are phase-shaped (`test_phase_b_ingestion.py` through `test_phase_f_diagnostics.py`) plus the
anchor contracts and `test_peer_paths.py`. When you change a phase, run its test and the anchor
tests; when you change an output schema the dashboard reads, run
`test_dashboard_export_optional.py`.

`python -m macro_engine.cli` failing with `ModuleNotFoundError` means the venv's editable install
(`.pth`) points at a stale path — this happens after a reorg or a repo move, and it fails
silently for anything that only checks the exit code. `scripts/run_daily_diagnostic.ps1` / `.sh`
now check this first (`scripts/check_macro_engine_import.py`) and abort with both paths named
before doing any real work. Fix: run `pip install -e .` from the repo root.

## Agent policy

One agent works in this repository at a time. The orchestrating Claude session may delegate coding
to Gemini through the Antigravity CLI (`agy`), by operator decision of 2026-09-23; the delegated
agent follows every rule in this file and the workspace `AGENTS.md`, and the orchestrator reviews
each change before it is merged. Do not dispatch work to DeepSeek agents, `.ds-codex` workers, or
worker-grid workers; do not create DeepSeek task files or run
`python .ds-codex/scripts/ds_codex.py dispatch`; do not ask any other external agent to edit files,
generate patches or review diffs. The `.ds-codex/` directory and `instructions.md` are inert local
scaffold — leave them alone unless the operator explicitly asks for them.
