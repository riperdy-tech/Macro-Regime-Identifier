# v0.6-M2 Scheduled Daily Run Support

## Verdict

v0.6-M2 passes as scheduled-run support.

This milestone added scripts, a runbook, and a daily health-check command so the
existing daily operating pipeline is easier to run repeatedly.

## What Changed

Added scripts:

```text
scripts/run_daily_diagnostic.ps1
scripts/run_daily_diagnostic.sh
```

Added runbook:

```text
docs/operations/daily_runbook.md
```

Added CLI command:

```text
python -m macro_engine.cli daily-health-check --config config/daily_pipeline.yaml
```

Added log hygiene:

```text
logs/
```

is ignored by git.

## Health Check Result

Latest health check:

```text
valid: true
status: warning
```

The warning is expected in mock-safe release mode because the synthetic sample
news profile contains short example bodies. Blocking checks passed:

```text
daily_pipeline_config: ok
macro_config: ok
sector_config: ok
news_sources_config: ok
news_ai_config: ok
news_scoring_config: ok
combined_config: ok
monitoring_config: ok
database: ok
archive_root: ok
ai_key: ok
gitignore_generated_outputs: ok
```

## Manual Run

Mock-safe manual command:

```text
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
```

Latest mock/archive run:

```text
run_id: 20260519T135600Z-e9f3adf5
run_date: 2026-05-19
status: success
archive_path: outputs\archive\2026-05-19\20260519T135600Z-e9f3adf5
warning_count: 0
error_count: 0
```

Windows script:

```text
.\scripts\run_daily_diagnostic.ps1
```

Live AI is opt-in through local environment configuration:

```text
MACRO_ENGINE_LIVE_AI=1
MACRO_ENGINE_SOURCE_PROFILE=daily_local_csv
```

## Logs

Scripts write logs under:

```text
logs/daily/
```

Logs are local generated artifacts and are ignored by git.

## Tests

Focused v0.6 tests passed:

```text
python -m pytest tests/test_phase_v06_m1_m2_operations.py
5 passed
```

Full suite passed:

```text
python -m pytest
158 passed, 2 skipped
```

Ruff passed:

```text
python -m ruff check .
All checks passed
```

Config validation passed:

```text
python -m macro_engine.cli validate-config
Config valid: 13 sources, 11 dimensions, 6 regimes
```

## Known Limitations

The scripts do not install dependencies or create a system scheduler entry by
themselves. Windows Task Scheduler or cron setup remains local user operation.

The health check verifies prerequisites and local file availability; it does
not validate predictive value.

## Next Step

After repeated daily runs accumulate enough balanced real-news history, v0.6-M3
should review validation readiness.

No scoring formulas were changed. No trading, allocation, execution, portfolio
sizing, or market-action logic was introduced.
