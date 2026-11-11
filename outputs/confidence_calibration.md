# LLM Confidence Calibration

Ledger rows: 8037  |  Directional calls: 5584

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 188 | 0.5798 | 0.0140 | 0.0211 |
| 0.3-0.6 | 1351 | 0.4145 | -0.0124 | 0.0207 |
| 0.6-0.8 | 521 | 0.4760 | -0.0017 | 0.0125 |
| 0.8-1.0 | 63 | 0.5714 | 0.0117 | -0.0060 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
