# Pass-model fine-tuning (M13, CP-17)

Generated 2026-09-13T02:44:50.542216+00:00 at commit `4676aeb70eb5a55cc32c4ed67686e3ba3a576017`.

> **Evidence grade: INTERIM.** The split is leakage-safe and these numbers are real, but the run does not meet CP-14's acceptance conditions, so any adopted configuration is provisional:
>
> - design is 'leave_one_event_out', not CP-14's documented 'year_table'; the table holds no ['2022', '2023', '2024', '2025']
> - the benchmark is leakage-safe and its numbers are real, but it does not measure generalisation across regulation eras, which is what CP-14's year table exists to measure

| Setting | Value |
|---|---|
| Objective | `roc_auc` |
| Family | `lightgbm` (CP-14 ranked lightgbm first at DETECTION on Brier (0.11527) in benchmark.json) |
| Trials | 12 (trial 0 is CP-14's untuned block) |
| Folds | 6 |
| Selection split | validation (the test split is never tuned on) |

## DETECTION

**KEEP_BASELINE** — the best configuration improves roc_auc by +0.01101, which is inside the baseline's own fold-to-fold spread of 0.04097. A search always produces a leader; this one is not distinguishable from the untuned model, so tuning has not earned the added complexity.. WARNING: brier worsened from 0.13266 to 0.14156. Section 26 selects on calibration because the DP consumes probabilities, not rankings -- a model that orders pairs better while stating their probabilities worse is not an improvement for the planner.

| | Trial | Params | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---|---:|---:|---:|---:|---:|
| baseline | 0 | — | 0.72716 | 0.36686 | 0.13266 | 0.62747 | 0.10655 |
| best | 10 | learning_rate=0.03, min_child_samples=20, num_leaves=31, reg_lambda=0.0 | 0.73818 | 0.39273 | 0.14156 | 0.98993 | 0.11932 |

⚠️ Calibration regressed: `brier` moved +0.00890. Section 26 selects on calibration because the DP consumes probabilities, not rankings.

Scored once on the held-out test split, after selection:

| Config | `roc_auc` | `pr_auc` | `brier` | `log_loss` | `ece` |
|---|---:|---:|---:|---:|---:|
| baseline | 0.72053 | 0.36523 | 0.13333 | 0.62160 | 0.11159 |
| best | 0.72878 | 0.37686 | 0.14212 | 0.97478 | 0.12110 |

## How to read this

- Configurations are selected on each fold's **validation** split. The test split is scored once, afterwards, for the baseline and the winner only.
- `KEEP_BASELINE` means the search found no gain distinguishable from the baseline's own fold-to-fold spread. A search always produces a leader; that is not the same as finding an improvement.
- A ROC-AUC gain paid for in Brier is flagged, not silently adopted.
