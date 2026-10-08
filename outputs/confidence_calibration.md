# LLM Confidence Calibration

Ledger rows: 5474  |  Directional calls: 3875

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 69 | 0.5797 | 0.0085 | -0.0295 |
| 0.3-0.6 | 449 | 0.4989 | 0.0017 | -0.0252 |
| 0.6-0.8 | 246 | 0.5650 | 0.0086 | -0.0117 |
| 0.8-1.0 | 36 | 0.6944 | 0.0193 | -0.0016 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
