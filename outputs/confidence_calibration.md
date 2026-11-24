# LLM Confidence Calibration

Ledger rows: 9105  |  Directional calls: 6298

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
| 0.0-0.3 | 43 | 0.4651 | -0.0053 | -0.0048 |
| 0.3-0.6 | 258 | 0.4729 | -0.0002 | 0.0028 |
| 0.6-0.8 | 152 | 0.5132 | 0.0003 | 0.0114 |
| 0.8-1.0 | 22 | 0.7727 | 0.0353 | 0.0198 |
