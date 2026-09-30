# LLM Confidence Calibration

Ledger rows: 4588  |  Directional calls: 3298

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 52 | 0.6538 | 0.0163 | -0.0258 |
| 0.3-0.6 | 345 | 0.5159 | 0.0031 | -0.0216 |
| 0.6-0.8 | 192 | 0.5833 | 0.0072 | -0.0118 |
| 0.8-1.0 | 29 | 0.8276 | 0.0297 | 0.0087 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
