# LLM Confidence Calibration

Ledger rows: 6866  |  Directional calls: 4770

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 116 | 0.5431 | 0.0060 | -0.0111 |
| 0.3-0.6 | 823 | 0.4447 | -0.0037 | -0.0014 |
| 0.6-0.8 | 361 | 0.5125 | 0.0062 | 0.0038 |
| 0.8-1.0 | 49 | 0.6122 | 0.0155 | 0.0004 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 14 | 0.6429 | 0.0089 | -0.0303 |
| 0.3-0.6 | 94 | 0.4362 | -0.0046 | -0.0138 |
| 0.6-0.8 | 70 | 0.5143 | 0.0040 | 0.0126 |
| 0.8-1.0 | 10 | 0.9000 | 0.0674 | 0.0315 |
