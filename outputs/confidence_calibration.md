# LLM Confidence Calibration

Ledger rows: 7621  |  Directional calls: 5307

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 153 | 0.5686 | 0.0109 | 0.0074 |
| 0.3-0.6 | 1127 | 0.4321 | -0.0082 | 0.0139 |
| 0.6-0.8 | 444 | 0.5113 | 0.0053 | 0.0098 |
| 0.8-1.0 | 57 | 0.6140 | 0.0172 | 0.0009 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
