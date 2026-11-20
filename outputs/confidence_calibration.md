# LLM Confidence Calibration

Ledger rows: 8825  |  Directional calls: 6113

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 232 | 0.5948 | 0.0149 | 0.0242 |
| 0.3-0.6 | 1679 | 0.4044 | -0.0137 | 0.0251 |
| 0.6-0.8 | 624 | 0.4567 | -0.0029 | 0.0128 |
| 0.8-1.0 | 76 | 0.5263 | 0.0053 | -0.0089 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 31 | 0.5484 | 0.0003 | -0.0173 |
| 0.3-0.6 | 202 | 0.4703 | -0.0003 | 0.0008 |
| 0.6-0.8 | 128 | 0.4922 | -0.0008 | 0.0120 |
| 0.8-1.0 | 21 | 0.8095 | 0.0371 | 0.0209 |
