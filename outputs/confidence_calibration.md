# LLM Confidence Calibration

Ledger rows: 5244  |  Directional calls: 3723

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 68 | 0.5882 | 0.0093 | -0.0293 |
| 0.3-0.6 | 437 | 0.4989 | 0.0016 | -0.0249 |
| 0.6-0.8 | 244 | 0.5656 | 0.0084 | -0.0114 |
| 0.8-1.0 | 35 | 0.7143 | 0.0208 | -0.0007 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
