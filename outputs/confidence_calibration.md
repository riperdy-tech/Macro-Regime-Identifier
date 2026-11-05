# LLM Confidence Calibration

Ledger rows: 7497  |  Directional calls: 5209

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 138 | 0.5362 | 0.0034 | -0.0005 |
| 0.3-0.6 | 1088 | 0.4283 | -0.0082 | 0.0123 |
| 0.6-0.8 | 431 | 0.5081 | 0.0054 | 0.0106 |
| 0.8-1.0 | 57 | 0.6140 | 0.0172 | 0.0009 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
