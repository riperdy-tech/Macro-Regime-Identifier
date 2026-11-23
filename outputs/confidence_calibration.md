# LLM Confidence Calibration

Ledger rows: 9027  |  Directional calls: 6241

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 234 | 0.5897 | 0.0147 | 0.0240 |
| 0.3-0.6 | 1710 | 0.4064 | -0.0135 | 0.0253 |
| 0.6-0.8 | 646 | 0.4551 | -0.0031 | 0.0134 |
| 0.8-1.0 | 77 | 0.5195 | 0.0039 | -0.0074 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 34 | 0.5000 | -0.0030 | -0.0125 |
| 0.3-0.6 | 224 | 0.4777 | 0.0002 | 0.0029 |
| 0.6-0.8 | 133 | 0.4962 | -0.0005 | 0.0135 |
| 0.8-1.0 | 21 | 0.8095 | 0.0371 | 0.0209 |
