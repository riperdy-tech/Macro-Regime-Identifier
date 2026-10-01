# LLM Confidence Calibration

Ledger rows: 4703  |  Directional calls: 3385

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 57 | 0.6491 | 0.0153 | -0.0255 |
| 0.3-0.6 | 372 | 0.4973 | 0.0013 | -0.0221 |
| 0.6-0.8 | 206 | 0.5583 | 0.0054 | -0.0128 |
| 0.8-1.0 | 29 | 0.8276 | 0.0297 | 0.0087 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
