# LLM Confidence Calibration

Ledger rows: 7930  |  Directional calls: 5514

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 173 | 0.5780 | 0.0135 | 0.0156 |
| 0.3-0.6 | 1317 | 0.4138 | -0.0125 | 0.0199 |
| 0.6-0.8 | 503 | 0.4871 | -0.0002 | 0.0120 |
| 0.8-1.0 | 61 | 0.5738 | 0.0105 | -0.0046 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
