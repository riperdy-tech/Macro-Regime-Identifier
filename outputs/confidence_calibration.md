# LLM Confidence Calibration

Ledger rows: 8245  |  Directional calls: 5726

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 198 | 0.5707 | 0.0122 | 0.0219 |
| 0.3-0.6 | 1415 | 0.4163 | -0.0125 | 0.0219 |
| 0.6-0.8 | 533 | 0.4765 | -0.0015 | 0.0132 |
| 0.8-1.0 | 64 | 0.5625 | 0.0102 | -0.0073 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
