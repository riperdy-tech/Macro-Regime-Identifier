# LLM Confidence Calibration

Ledger rows: 5017  |  Directional calls: 3586

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 66 | 0.6061 | 0.0117 | -0.0281 |
| 0.3-0.6 | 420 | 0.4952 | 0.0014 | -0.0243 |
| 0.6-0.8 | 231 | 0.5584 | 0.0066 | -0.0124 |
| 0.8-1.0 | 33 | 0.7576 | 0.0233 | 0.0004 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
