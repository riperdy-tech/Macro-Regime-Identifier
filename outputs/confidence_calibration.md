# LLM Confidence Calibration

Ledger rows: 4113  |  Directional calls: 2996

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 43 | 0.6279 | 0.0136 | -0.0297 |
| 0.3-0.6 | 263 | 0.4981 | 0.0022 | -0.0188 |
| 0.6-0.8 | 158 | 0.5570 | 0.0024 | -0.0083 |
| 0.8-1.0 | 22 | 0.8636 | 0.0324 | 0.0048 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
