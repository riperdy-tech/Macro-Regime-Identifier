# LLM Confidence Calibration

Ledger rows: 7266  |  Directional calls: 5050

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 125 | 0.5280 | 0.0031 | -0.0051 |
| 0.3-0.6 | 986 | 0.4331 | -0.0061 | 0.0079 |
| 0.6-0.8 | 405 | 0.5037 | 0.0052 | 0.0090 |
| 0.8-1.0 | 51 | 0.6078 | 0.0153 | 0.0008 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
