# LLM Confidence Calibration

Ledger rows: 4477  |  Directional calls: 3231

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 51 | 0.6471 | 0.0166 | -0.0264 |
| 0.3-0.6 | 325 | 0.5169 | 0.0031 | -0.0210 |
| 0.6-0.8 | 181 | 0.5912 | 0.0072 | -0.0096 |
| 0.8-1.0 | 28 | 0.8571 | 0.0319 | 0.0102 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
