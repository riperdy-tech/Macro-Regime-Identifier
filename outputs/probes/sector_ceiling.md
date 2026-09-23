# Probe Report: Sector Ceiling (Fitted-Exposure Upper Bound and OOS)

## 1. Executive Summary & Verdict

**Verdict:** `NOT DEMONSTRATED OOS`

The screener requires a 3-month rank IC > 0 with Newey-West $t \ge 2$, which at historical cross-sectional dispersion (sd ~ 0.441 over 310 dates) requires a mean per-date IC of **~0.087**. Today's hand-set exposures have no measurable skill (-0.0189 at 3m). This probe measures the maximum skill achievable if sector exposures were fitted directly from historical market data.

- **In-Sample Ceiling (3m):** Mean IC = **0.2080** (t = 6.58, n = 310). The ceiling comfortably clears the 0.087 bar in-sample.
- **Out-of-Sample Best (3m):** Mean IC = **0.0696** (t = 1.96, n = 248). Fails to reach the 0.087 bar or t >= 2.
- **Conclusion:** The sector channel's lack of predictive skill is not merely a flaw of hand-set priors; even when fitted on 27 years of data, linear macro factor exposures cannot demonstrate the edge required to open the screener's quota tilt out-of-sample.

---

## 2. Results by Specification

### Table A: In-Sample Ceiling (Upper Bound)

Full-sample OLS fit per sector. Fitted on the data it is scored on.

| Horizon | Mean IC | SD IC | Newey-West t | Positive Share | Dates |
| :--- | :---: | :---: | :---: | :---: | :---: |
| 1m | 0.1335 | 0.3883 | 6.09 | 64.1% | 312 |
| 3m | 0.2080 | 0.4011 | 6.58 | 67.4% | 310 |

### Table B: Out-of-Sample Expanding Window (Unregularized OLS)

Expanding window refit at each date using only data whose return had fully realised before the evaluation date. Minimum 60 months of training.

| Horizon | Mean IC | SD IC | Newey-West t | Positive Share | Dates |
| :--- | :---: | :---: | :---: | :---: | :---: |
| 1m | 0.0138 | 0.4125 | 0.53 | 53.2% | 252 |
| 3m | 0.0696 | 0.4092 | 1.96 | 64.5% | 248 |

### Table C: Ridge-Style Shrinkage (OOS by Penalty Strength)

Loadings shrunk toward zero with penalty $\lambda \in \{0, 1, 5, 20\}$.

| Horizon | Shrinkage $\lambda$ | Mean IC | SD IC | Newey-West t | Positive Share | Dates |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| 1m | 0 | 0.0138 | 0.4125 | 0.53 | 53.2% | 252 |
| 1m | 1 | 0.0151 | 0.4079 | 0.59 | 52.8% | 252 |
| 1m | 5 | 0.0216 | 0.4084 | 0.84 | 52.4% | 252 |
| 1m | 20 | 0.0260 | 0.4109 | 1.01 | 52.4% | 252 |
| 3m | 0 | 0.0696 | 0.4092 | 1.96 | 64.5% | 248 |
| 3m | 1 | 0.0676 | 0.4145 | 1.86 | 62.9% | 248 |
| 3m | 5 | 0.0629 | 0.4107 | 1.75 | 60.9% | 248 |
| 3m | 20 | 0.0582 | 0.4141 | 1.60 | 58.9% | 248 |

### Table D: Baseline Hand-Set Scores (Comparison)

Today's hand-set exposures evaluated over the full history (310 dates) and over the identical OOS evaluation dates (248 dates) for direct comparison.

| Sample Basis | Horizon | Mean IC | SD IC | Newey-West t | Positive Share | Dates |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Full History | 1m | 0.0021 | 0.4318 | 0.08 | 51.6% | 312 |
| Full History | 3m | -0.0189 | 0.4400 | -0.54 | 46.8% | 310 |
| OOS Window | 1m | 0.0020 | 0.4469 | 0.07 | 51.6% | 252 |
| OOS Window | 3m | -0.0216 | 0.4546 | -0.53 | 47.6% | 248 |

---

## 3. Leakage and Lookahead Prevention

The probe strictly eliminates both sources of leakage common in forward-return models:

1. **No Lookahead in Features:** Feature vectors $X_T$ only use dimension scores dated at or before evaluation date $T$. No future macro revisions or forward-looking information enters the feature matrix.
2. **Training-Window Overlap Exclusion:** For a forward return horizon of $h$ months, a training observation dated $d$ is only fully realised at $d + h$. Any training date $d$ where $d + h > T$ overlaps the evaluation window $[T, T+h]$ and is strictly excluded. Specifically, the training cutoff satisfies $d \le T - h$ months.
3. **Newly Launched Tickers:** Newly listed sector ETFs (e.g. XLRE in Oct 2015, XLC in Jul 2018) require at least 10 realised observations before a sector-specific regression is estimated. Prior to that threshold, their tilt score falls back to neutral 0.0.

---

## 4. Key Observations & Surprises

- **In-Sample vs. OOS Degradation:** In-sample fitting generates an apparent 3m IC of 0.2080 (t = 6.58). However, in an honest expanding window, the signal degrades to 0.0696 (t = 1.96). This 66% drop highlights the severity of cross-sectional overfitting when fitting 11 independent sector regressions on correlated macro series.
- **Shrinkage Effect:** At 3 months, mild or zero shrinkage ($\lambda = 0$) achieves the highest IC (0.0696). Increasing shrinkage toward zero monotonically reduces the IC (0.0582 at $\lambda = 20$).
- **7 Dimensions vs 5 Dimensions:** Expanding the feature set from the 5 core macro dimensions to all 7 dimensions increases in-sample 3m IC from 0.2080 to 0.2319, but degrades OOS 3m IC from 0.0696 down to 0.0468. Adding more correlated factors accelerates out-of-sample parameter estimation error.

