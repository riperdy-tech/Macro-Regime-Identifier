# LLM Confidence Calibration

Ledger rows: 4798  |  Directional calls: 3449

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 62 | 0.6129 | 0.0115 | -0.0260 |
| 0.3-0.6 | 404 | 0.4901 | 0.0009 | -0.0236 |
| 0.6-0.8 | 220 | 0.5455 | 0.0046 | -0.0134 |
| 0.8-1.0 | 29 | 0.8276 | 0.0297 | 0.0087 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 0 | n/a | n/a | n/a |
| 0.3-0.6 | 0 | n/a | n/a | n/a |
| 0.6-0.8 | 0 | n/a | n/a | n/a |
| 0.8-1.0 | 0 | n/a | n/a | n/a |
