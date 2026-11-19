# LLM Confidence Calibration

Ledger rows: 8724  |  Directional calls: 6047

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 230 | 0.5913 | 0.0146 | 0.0246 |
| 0.3-0.6 | 1661 | 0.4064 | -0.0135 | 0.0250 |
| 0.6-0.8 | 609 | 0.4532 | -0.0036 | 0.0130 |
| 0.8-1.0 | 73 | 0.5342 | 0.0060 | -0.0078 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 27 | 0.5556 | 0.0021 | -0.0149 |
| 0.3-0.6 | 190 | 0.4737 | 0.0000 | 0.0004 |
| 0.6-0.8 | 119 | 0.4874 | -0.0002 | 0.0105 |
| 0.8-1.0 | 18 | 0.8333 | 0.0419 | 0.0228 |
