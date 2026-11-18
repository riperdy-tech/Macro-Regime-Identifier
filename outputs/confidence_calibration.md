# LLM Confidence Calibration

Ledger rows: 8616  |  Directional calls: 5968

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 228 | 0.5921 | 0.0148 | 0.0241 |
| 0.3-0.6 | 1628 | 0.4091 | -0.0131 | 0.0249 |
| 0.6-0.8 | 593 | 0.4553 | -0.0034 | 0.0128 |
| 0.8-1.0 | 66 | 0.5455 | 0.0082 | -0.0055 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 21 | 0.4762 | -0.0049 | -0.0175 |
| 0.3-0.6 | 149 | 0.4899 | 0.0034 | -0.0046 |
| 0.6-0.8 | 108 | 0.4815 | 0.0020 | 0.0106 |
| 0.8-1.0 | 18 | 0.8333 | 0.0419 | 0.0228 |
