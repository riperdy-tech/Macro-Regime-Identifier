# LLM Confidence Calibration

Ledger rows: 6954  |  Directional calls: 4830

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 116 | 0.5431 | 0.0060 | -0.0110 |
| 0.3-0.6 | 823 | 0.4447 | -0.0036 | -0.0013 |
| 0.6-0.8 | 361 | 0.5125 | 0.0063 | 0.0040 |
| 0.8-1.0 | 49 | 0.6122 | 0.0155 | 0.0004 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 14 | 0.6429 | 0.0088 | -0.0307 |
| 0.3-0.6 | 94 | 0.4362 | -0.0045 | -0.0133 |
| 0.6-0.8 | 70 | 0.5143 | 0.0055 | 0.0140 |
| 0.8-1.0 | 10 | 0.9000 | 0.0691 | 0.0346 |
