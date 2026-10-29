# LLM Confidence Calibration

Ledger rows: 6755  |  Directional calls: 4689

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 110 | 0.5545 | 0.0088 | -0.0129 |
| 0.3-0.6 | 764 | 0.4503 | -0.0034 | -0.0039 |
| 0.6-0.8 | 347 | 0.5130 | 0.0063 | 0.0019 |
| 0.8-1.0 | 48 | 0.6250 | 0.0165 | 0.0011 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 10 | 0.6000 | 0.0068 | -0.0270 |
| 0.3-0.6 | 64 | 0.4062 | -0.0108 | -0.0182 |
| 0.6-0.8 | 46 | 0.4130 | -0.0089 | 0.0180 |
| 0.8-1.0 | 5 | 0.8000 | 0.0509 | 0.0002 |
