# LLM Confidence Calibration

Ledger rows: 8139  |  Directional calls: 5650

Diagnostic instrumentation only. No recalibration applied. Buckets are unreliable until directional calls are plentiful (target >= 200).

## Horizon 1m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 192 | 0.5677 | 0.0121 | 0.0221 |
| 0.3-0.6 | 1386 | 0.4113 | -0.0129 | 0.0213 |
| 0.6-0.8 | 528 | 0.4754 | -0.0015 | 0.0129 |
| 0.8-1.0 | 64 | 0.5625 | 0.0102 | -0.0073 |

## Horizon 3m

| Confidence Bucket | N | Hit Rate | Avg Signed Rel Return | Avg Raw Rel Return |
| --- | --- | --- | --- | --- |
| 0.0-0.3 | 18 | 0.5556 | 0.0022 | -0.0283 |
| 0.3-0.6 | 122 | 0.4426 | -0.0002 | -0.0091 |
| 0.6-0.8 | 91 | 0.5275 | 0.0110 | 0.0158 |
| 0.8-1.0 | 15 | 0.9333 | 0.0580 | 0.0350 |
