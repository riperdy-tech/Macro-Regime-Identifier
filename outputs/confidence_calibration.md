# LLM Confidence Calibration

Ledger rows: 9469  |  Directional calls: 6546

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 237 | 0.5907 | 0.0148 | 0.0239 |
| 0.3-0.6 | 1739 | 0.4048 | -0.0138 | 0.0256 |
| 0.6-0.8 | 669 | 0.4514 | -0.0038 | 0.0142 |
| 0.8-1.0 | 83 | 0.4940 | -0.0007 | -0.0030 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 47 | 0.4468 | -0.0060 | -0.0081 |
| 0.3-0.6 | 290 | 0.4655 | -0.0035 | 0.0055 |
| 0.6-0.8 | 162 | 0.5000 | -0.0016 | 0.0131 |
| 0.8-1.0 | 25 | 0.7200 | 0.0252 | 0.0116 |
