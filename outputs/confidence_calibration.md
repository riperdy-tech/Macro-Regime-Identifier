# LLM Confidence Calibration

Ledger rows: 7061  |  Directional calls: 4898

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 119 | 0.5462 | 0.0071 | -0.0095 |
| 0.3-0.6 | 872 | 0.4358 | -0.0049 | 0.0008 |
| 0.6-0.8 | 376 | 0.5106 | 0.0067 | 0.0060 |
| 0.8-1.0 | 49 | 0.6122 | 0.0155 | 0.0004 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
