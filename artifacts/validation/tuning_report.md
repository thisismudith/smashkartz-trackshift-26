# Pass-model fine-tuning (M13, CP-17)

Generated 2026-09-13T03:21:18.369079+00:00 at commit `68846aee31cbefc3dfb31fe126ea51a427c740a5`.

> **Evidence grade: INTERIM.** The split is leakage-safe and these numbers are real, but the run does not meet CP-14's acceptance conditions, so any adopted configuration is provisional:
>
> - design is 'leave_one_event_out', not CP-14's documented 'year_table'; the table holds no ['2022', '2023', '2024', '2025']
> - the benchmark is leakage-safe and its numbers are real, but it does not measure generalisation across regulation eras, which is what CP-14's year table exists to measure

| Setting | Value |
|---|---|
| Objective | `roc_auc` |
| Family | `lightgbm` (CP-14 ranked lightgbm first at ACTIVATION on Brier (0.11223) in benchmark.json; CP-14 ranked lightgbm first at BRAKING on Brier (0.10950) in benchmark.json; CP-14 ranked lightgbm first at DETECTION on Brier (0.11527) in benchmark.json) |
| Trials | 24 (trial 0 is CP-14's untuned block) |
| Folds | 6 |
| Selection split | validation (the test split is never tuned on) |

## DETECTION

**KEEP_BASELINE** — the best configuration improves roc_auc by +0.01133, which is inside the baseline's own fold-to-fold spread of 0.04097. A search always produces a leader; this one is not distinguishable from the untuned model, so tuning has not earned the added complexity. WARNING: brier worsened from 0.13266 to 0.14095. Section 26 selects on calibration because the DP consumes probabilities, not rankings -- a model that orders pairs better while stating their probabilities worse is not an improvement for the planner.

| | Trial | Params | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---|---:|---:|---:|---:|---:|
| baseline | 0 | — | 0.72716 | 0.36686 | 0.13266 | 0.62747 | 0.10655 |
| best | 20 | learning_rate=0.03, min_child_samples=20, num_leaves=63, reg_lambda=0.0 | 0.73849 | 0.39274 | 0.14095 | 1.03491 | 0.12111 |

⚠️ Calibration regressed: `brier` moved +0.00829. Section 26 selects on calibration because the DP consumes probabilities, not rankings.

Scored once on the held-out test split, after selection:

| Config | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---:|---:|---:|---:|
| baseline | 0.72053 | 0.36523 | 0.13333 | 0.62160 | 0.11159 |
| best | 0.73089 | 0.38848 | 0.13893 | 0.99109 | 0.12013 |

## ACTIVATION

**ADOPT_TUNED** — improves roc_auc by +0.02121, beyond the baseline's 0.01457 fold-to-fold spread

| | Trial | Params | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---|---:|---:|---:|---:|---:|
| baseline | 0 | — | 0.71035 | 0.38603 | 0.12991 | 0.61788 | 0.10726 |
| best | 8 | learning_rate=0.02, min_child_samples=20, num_leaves=63, reg_lambda=5.0 | 0.73156 | 0.41210 | 0.12165 | 0.47916 | 0.08810 |

Scored once on the held-out test split, after selection:

| Config | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---:|---:|---:|---:|
| baseline | 0.71663 | 0.40281 | 0.12742 | 0.58754 | 0.10279 |
| best | 0.73829 | 0.42182 | 0.12039 | 0.46198 | 0.08347 |

## BRAKING

**KEEP_BASELINE** — the best configuration improves roc_auc by +0.01255, which is inside the baseline's own fold-to-fold spread of 0.04423. A search always produces a leader; this one is not distinguishable from the untuned model, so tuning has not earned the added complexity

| | Trial | Params | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---|---:|---:|---:|---:|---:|
| baseline | 0 | — | 0.71069 | 0.40402 | 0.12793 | 0.65585 | 0.11028 |
| best | 8 | learning_rate=0.02, min_child_samples=20, num_leaves=63, reg_lambda=5.0 | 0.72324 | 0.43208 | 0.11917 | 0.49802 | 0.08610 |

Scored once on the held-out test split, after selection:

| Config | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---:|---:|---:|---:|
| baseline | 0.70917 | 0.41426 | 0.12499 | 0.62897 | 0.09971 |
| best | 0.71748 | 0.42844 | 0.11960 | 0.49207 | 0.08143 |

## How to read this

- Configurations are selected on each fold's **validation** split. The test split is scored once, afterwards, for the baseline and the winner only.
- `KEEP_BASELINE` means the search found no gain distinguishable from the baseline's own fold-to-fold spread. A search always produces a leader; that is not the same as finding an improvement.
- A ROC-AUC gain paid for in Brier is flagged, not silently adopted.
