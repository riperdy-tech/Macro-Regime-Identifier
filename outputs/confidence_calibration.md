# LLM Confidence Calibration

Ledger rows: 6627  |  Directional calls: 4610

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 69 | 0.5797 | 0.0085 | -0.0295 |
| 0.3-0.6 | 473 | 0.5074 | 0.0020 | -0.0237 |
| 0.6-0.8 | 256 | 0.5664 | 0.0088 | -0.0107 |
| 0.8-1.0 | 39 | 0.7179 | 0.0201 | -0.0038 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
