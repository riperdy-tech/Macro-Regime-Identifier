# LLM Confidence Calibration

Ledger rows: 8513  |  Directional calls: 5898

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 219 | 0.5845 | 0.0142 | 0.0241 |
| 0.3-0.6 | 1577 | 0.4084 | -0.0134 | 0.0246 |
| 0.6-0.8 | 579 | 0.4594 | -0.0029 | 0.0135 |
| 0.8-1.0 | 66 | 0.5455 | 0.0082 | -0.0055 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
