# v0.4-M2 AI Classification Reliability Review

## Verdict

v0.4-M2 passes.

The M1 failure modes were directly addressed with prompt hardening, safe schema repair, conservative retry support, richer source-quality checks, and tests. No news scoring formulas, macro formulas, sector assumptions, or combined diagnostic formulas were changed.

## M1 Failure Analysis

M1 live pilot result:

```text
items: 40
successful classifications: 32
failed classifications: 8
success rate: 80.0%
```

Failure categories from the M1 pilot database:

```text
invalid theme direction enum: 3
invalid entity_type enum: 5
numeric scores outside bounds: 1
malformed JSON: 0
missing required fields: 0
invalid sector impact enum: 0
```

Main examples:

- `uncertainty` or `uncertain` used where the schema expected `unclear`
- `region` and `person` used as entity types when the schema allowed only broader categories
- integer-style scores such as `5.0` or `3.0` instead of bounded `0.0` to `1.0` values

## Prompt Changes

The classification prompt now explicitly lists:

- allowed theme direction values
- allowed sector impact direction values
- allowed entity types
- allowed time horizons
- numeric score bounds

It also instructs the model to use `unclear` or `neutral` when uncertain, avoid unsupported sector impacts, return JSON only, and avoid market-action language.

## Schema Changes

Entity types now include:

```text
region
person
```

This preserves useful real-news entity fidelity without affecting downstream scoring, because entity rows are not used as numeric sector or macro score inputs.

## Safe Repair Policy

An optional safe repair layer now runs before final validation.

Allowed repairs:

- enum alias normalization, such as `uncertainty` -> `unclear`
- sector alias normalization, such as `positive_for_sector` -> `tailwind`
- numeric clamping to configured schema bounds
- integer numeric values converted to floats
- whitespace trimming and lowercase enum normalization
- empty optional arrays normalized to empty lists

Disallowed repairs:

- inventing missing required fields
- fabricating severity or confidence
- converting totally invalid structures into valid classifications
- hiding final validation errors

Repair metadata is stored in `raw_ai_response`, including:

- `was_repaired`
- `repair_notes`
- `validation_errors`
- `retry_count`

## Retry Policy

Live classifiers can now retry on schema validation failure when configured.

The retry prompt includes:

- validation error text
- prior raw response
- reminder of enum and numeric constraints
- request for corrected JSON only

The default production config remains conservative:

```text
max_retries: 0
enable_schema_repair: true
```

Pilot configs may enable one retry.

## Source-Quality Improvements

`validate-news-input` now reports:

- short body count
- missing source URL count
- duplicate title count
- duplicate content hash count
- item count by source
- item count by day
- future-dated items
- very old items
- likely non-news page markers
- per-source quality status

The validation remains non-destructive and does not block minor warnings by default.

## Tests

New or updated tests cover:

- profile-based source loading
- pilot input validation CLI
- duplicate detection
- duplicate title warning
- source quality status
- enum alias repair
- numeric clamping
- invalid structure still failing
- retry prompt construction
- retry success metadata
- retry stopping after max attempts
- report guardrails with failed classifications

Validation commands:

```text
python -m pytest tests/test_phase_v03_m1_news.py
python -m ruff check .
python -m macro_engine.cli validate-config
```

Results:

```text
tests: passed
ruff: clean
config validation: passed
```

## Remaining Risks

- Repair is deliberately narrow; some invalid responses will still fail.
- Live AI may still produce schema drift when headlines are ambiguous or source text is thin.
- Retry behavior increases API usage during pilot runs.
- The classifier still depends on prompt compliance and provider behavior.

## M3 Readiness

M3 can proceed.

The specific M1 failure modes are addressed, and the expanded pilot can test whether live classification success improves meaningfully on a larger real-news set.
