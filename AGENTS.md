<!-- ds-codex:start -->
This project has the Codex + DeepSeek orchestration scaffold installed.

Read `instructions.md` or `instructions.ds-codex.md` before running DS orchestration.

Core rules:
- Codex is master planner, reviewer, and integrator.
- DeepSeek workers are external API-backed helpers.
- DeepSeek returns patch proposals only.
- Never store real API keys in repo files or logs.
- Use `.ds-codex/.env` for local secrets.
- Review DS patches with `.ds-codex/scripts/ds_codex.py review` before applying.
- Apply approved patches one at a time.
<!-- ds-codex:end -->

# AI Worker Grid

Codex is the manager/reviewer. DeepSeek is implementation labor only.

Before large, repetitive, or multi-hour work:

1. Plan the work first.
2. Explicitly split:
   - Codex-owned brain work: architecture, tradeoffs, risk, security, integration, final review.
   - DeepSeek-owned labor: bounded code edits, repetitive tests, narrow report/doc updates, mechanical refactors.
3. Prefer DS for cheap bounded work whenever it can run without blocking Codex's immediate critical path.

## Task Files

For worker-grid tasks, break work into small task files under `tasks/todo/`.
For ds-codex tasks, use `.ds-codex/tasks/*.json`.

Each task must include:

- Goal
- Files to inspect
- Allowed files/paths
- Constraints
- Definition of done
- Tests to run
- What not to touch
- Risk level
- Maximum autonomy

Do not ask DeepSeek to make broad architecture decisions.
Do not ask DeepSeek to choose product direction, integration pattern, security policy, deployment policy, or data-loss-sensitive behavior.

## Launch

Parent worker-grid launcher, when using workspace-level workers:

```powershell
python scripts/launch_workers.py 3
```

Local ds-codex launcher, when using patch-proposal workers:

```powershell
python .ds-codex/scripts/ds_codex.py dispatch --task .ds-codex/tasks/<task-id>.json
python .ds-codex/scripts/ds_codex.py collect --run .ds-codex/runs/<run-id>
python .ds-codex/scripts/ds_codex.py review --run .ds-codex/runs/<run-id>
```

## Review Gate

After workers finish:

1. Review the patch or git diff.
2. Reject malformed, broad, risky, or stale patches.
3. Run required focused tests.
4. Run broader tests when blast radius touches shared behavior.
5. Move bad worker outputs back to `tasks/todo/` or reroll the ds-codex task.
6. Keep/apply only good outputs.
7. Apply approved patches one at a time.
8. Codex owns final integration, commit, and push.

## Hard Stops

Never allow workers to:

- Delete files.
- Edit secrets or `.env*`.
- Print, log, store, or commit real API keys.
- Change CI/deployment config unless explicitly asked.
- Deploy automatically.
- Touch `.git/`, `node_modules/`, lockfiles, generated databases, or raw local outputs unless task explicitly allows it.
- Modify Stock Screener reverse-engine files.

Never deploy automatically. Human/operator must intentionally trigger deploy-sensitive actions unless the user explicitly asked Codex to do so.
