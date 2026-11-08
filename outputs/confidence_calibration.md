# LLM Confidence Calibration

Ledger rows: 7765  |  Directional calls: 5399

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 163 | 0.5828 | 0.0148 | 0.0130 |
| 0.3-0.6 | 1169 | 0.4328 | -0.0088 | 0.0155 |
| 0.6-0.8 | 452 | 0.5111 | 0.0049 | 0.0098 |
| 0.8-1.0 | 57 | 0.6140 | 0.0172 | 0.0009 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
