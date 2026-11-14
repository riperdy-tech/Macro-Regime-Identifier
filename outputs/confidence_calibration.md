# LLM Confidence Calibration

Ledger rows: 8330  |  Directional calls: 5788

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 204 | 0.5686 | 0.0118 | 0.0224 |
| 0.3-0.6 | 1467 | 0.4124 | -0.0130 | 0.0232 |
| 0.6-0.8 | 548 | 0.4672 | -0.0024 | 0.0133 |
| 0.8-1.0 | 65 | 0.5538 | 0.0100 | -0.0073 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
