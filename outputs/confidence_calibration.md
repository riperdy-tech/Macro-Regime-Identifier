# LLM Confidence Calibration

Ledger rows: 7363  |  Directional calls: 5119

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 134 | 0.5299 | 0.0028 | -0.0031 |
| 0.3-0.6 | 1028 | 0.4319 | -0.0065 | 0.0101 |
| 0.6-0.8 | 419 | 0.5060 | 0.0055 | 0.0101 |
| 0.8-1.0 | 55 | 0.6364 | 0.0219 | 0.0050 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
