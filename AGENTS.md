# Agent Policy

Codex works directly in this repo.

Do not use DeepSeek agents, ds-codex workers, workspace worker-grid workers, or any other external coding agents unless the operator explicitly re-enables them in a new instruction.

## Operating Rules

- Codex owns planning, implementation, review, testing, integration, commits, and pushes.
- Do not dispatch `.ds-codex` tasks.
- Do not create DeepSeek task files.
- Do not run `python .ds-codex/scripts/ds_codex.py dispatch`.
- Do not run `python scripts/launch_workers.py`.
- Do not ask external agents to edit files, generate patches, review diffs, or perform implementation work.
- Existing `.ds-codex/`, `instructions.md`, and worker scaffold files are inert local scaffold unless the operator explicitly asks to use or modify them.

## Safety

- Never store real API keys in repo files or logs.
- Do not edit secrets or `.env*`.
- Do not delete files unless explicitly asked.
- Do not change CI/deployment config unless explicitly asked.
- Never deploy automatically.
- Do not touch Stock Screener reverse-engine files from this repo.

## Verification

Before claiming work is done:

- Review the git diff.
- Run focused tests for touched code.
- Run broader tests when shared behavior changes.
- Report any tests that could not be run.
