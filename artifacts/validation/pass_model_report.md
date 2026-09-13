# Pass-model benchmark (M10, CP-14)

Generated 2026-09-13T02:31:05.007508+00:00 at commit `4676aeb70eb5a55cc32c4ed67686e3ba3a576017`.

> **Evidence grade: INTERIM — this is NOT a CP-14 pass.**
>
> The measurements below are real and the split is leakage-safe, but the run does not meet CP-14's acceptance conditions:
>
> - design is 'leave_one_event_out', not CP-14's documented 'year_table'; the table holds no ['2022', '2023', '2024', '2025']
> - the benchmark is leakage-safe and its numbers are real, but it does not measure generalisation across regulation eras, which is what CP-14's year table exists to measure
>
> Split unit `battle_id`, design `leave_one_event_out`, years present ['2026']. CP-14 requires train ['2022', '2023', '2024'], validation `2025`, test `2026` excluding the British Grand Prix.

| Run | Value |
|---|---|
| Seed | 42 |
| Checkpoints | DETECTION, ACTIVATION, BRAKING |
| Families requested | logistic, lightgbm, xgboost, catboost, mlp |
| Families unavailable | none |
| Driver identity in features | False |
| Deterministic fits | True |
| Label definition | zone_exit_v1 |

## Dataset

| Metric | Value |
|---|---|
| Rows | 13794 |
| Opportunities | 4598 |
| Events | 8 |
| Years | 2026 |
| Labelled opportunities | 4467 |
| Unlabelled (retirement or truncated session) | 131 |
| Base rate | 0.1489 |
| British GP rows (frozen holdout) | None |

## Splits

Design **leave_one_event_out**, split unit **battle_id**, seed 42, 6 fold(s). C9 assignment `c9_split_assignments_v1:6a1d0320dee7`.

- One outer fold per event. A battle cannot span two events, so the no-battle-across-folds property CP-14 wanted from battle_id holds here too.
- Per-fold n varies widely; read every metric against its own n.
- events kept in training but never scored as a fold, having fewer than 10 positive labels: monaco_grand_prix (1). Scoring a fold against so few positives would put a meaningless ROC-AUC in the report and let one unmeasurable fold drive the cross-fold variance gate.
- split unit: battle_id populated on every row (CP-14 preferred unit)
- demo holdout scope: track -- every British Grand Prix, all seasons; C9's stricter assertion applied
- C9 assignment c9_split_assignments_v1:6a1d0320dee7 over 'battle_id', read from disk rather than re-derived, so this run is comparable with any other that cites it

| Fold | Train rows | Train +ve | Val rows | Test rows | Test +ve | Notes |
|---|---|---|---|---|---|---|
| australian_grand_prix | 9123 | 1449 | 903 | 2151 | 324 | validation event: barcelona_grand_prix |
| barcelona_grand_prix | 7734 | 1323 | 3540 | 903 | 96 | validation event: canadian_grand_prix |
| canadian_grand_prix | 6615 | 867 | 2022 | 3540 | 450 | validation event: italian_grand_prix |
| italian_grand_prix | 9252 | 1173 | 903 | 2022 | 552 | validation event: japanese_grand_prix |
| japanese_grand_prix | 8778 | 1425 | 2496 | 903 | 144 | validation event: miami_grand_prix |
| miami_grand_prix | 7530 | 1245 | 2151 | 2496 | 300 | validation event: australian_grand_prix |

## Results

Brier is primary. Brier ascending, then ECE ascending, then log loss ascending; ROC-AUC breaks remaining ties only. Section 26: calibration outranks ranking quality because the DP consumes probabilities.

### DETECTION

| Rank | Family | N | +ve | Brier | Brier std | Skill | Log loss | ECE | ROC-AUC | PR-AUC | Folds | s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lightgbm | 4005 | 622 | 0.1153 | 0.0310 | 0.1353 | 0.3787 | 0.0575 | 0.7384 | 0.4027 | 6/6 | 2.9 |
| 2 | xgboost | 4005 | 622 | 0.1184 | 0.0352 | 0.1122 | 0.3867 | 0.0633 | 0.7410 | 0.3697 | 6/6 | 12.9 |
| 3 | catboost | 4005 | 622 | 0.1189 | 0.0297 | 0.1084 | 0.3937 | 0.0882 | 0.7415 | 0.4252 | 6/6 | 73.9 |
| 4 | logistic | 4005 | 622 | 0.1271 | 0.0373 | 0.0467 | 0.4178 | 0.0710 | 0.6943 | 0.2964 | 6/6 | 5.8 |
| 5 | mlp | 4005 | 622 | 0.1405 | 0.0438 | -0.0537 | 0.4699 | 0.0984 | 0.6717 | 0.2821 | 6/6 | 10.6 |

**Selected: lightgbm** (Brier 0.1153, ECE 0.0575, ROC-AUC 0.7384).

### ACTIVATION

| Rank | Family | N | +ve | Brier | Brier std | Skill | Log loss | ECE | ROC-AUC | PR-AUC | Folds | s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lightgbm | 4005 | 622 | 0.1122 | 0.0383 | 0.1582 | 0.3737 | 0.0518 | 0.7416 | 0.4472 | 6/6 | 2.8 |
| 2 | xgboost | 4005 | 622 | 0.1166 | 0.0411 | 0.1254 | 0.3878 | 0.0680 | 0.7278 | 0.4385 | 6/6 | 3.4 |
| 3 | catboost | 4005 | 622 | 0.1207 | 0.0360 | 0.0948 | 0.4037 | 0.0878 | 0.7363 | 0.4296 | 6/6 | 89.5 |
| 4 | logistic | 4005 | 622 | 0.1378 | 0.0448 | -0.0336 | 0.4602 | 0.0944 | 0.5869 | 0.2528 | 6/6 | 1.5 |
| 5 | mlp | 4005 | 622 | 0.1510 | 0.0560 | -0.1329 | 0.7153 | 0.1420 | 0.6666 | 0.3537 | 6/6 | 7.7 |

**Selected: lightgbm** (Brier 0.1122, ECE 0.0518, ROC-AUC 0.7416).

### BRAKING

| Rank | Family | N | +ve | Brier | Brier std | Skill | Log loss | ECE | ROC-AUC | PR-AUC | Folds | s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lightgbm | 4005 | 622 | 0.1095 | 0.0386 | 0.1786 | 0.3704 | 0.0522 | 0.7378 | 0.4547 | 6/6 | 2.6 |
| 2 | xgboost | 4005 | 622 | 0.1145 | 0.0402 | 0.1413 | 0.3855 | 0.0617 | 0.7209 | 0.4420 | 6/6 | 3.4 |
| 3 | catboost | 4005 | 622 | 0.1195 | 0.0358 | 0.1033 | 0.4035 | 0.0862 | 0.7221 | 0.4384 | 6/6 | 79.0 |
| 4 | logistic | 4005 | 622 | 0.1405 | 0.0436 | -0.0537 | 0.4694 | 0.0968 | 0.5533 | 0.2154 | 6/6 | 1.6 |
| 5 | mlp | 4005 | 622 | 0.1489 | 0.0390 | -0.1172 | 0.5822 | 0.1453 | 0.6770 | 0.3556 | 6/6 | 8.3 |

**Selected: lightgbm** (Brier 0.1095, ECE 0.0522, ROC-AUC 0.7378).

## CP-14 acceptance gates

| Gate | Result | Detail |
|---|---|---|
| Run uses CP-14's documented split (train 2022-2024 / validate 2025 / test 2026 excluding British GP, grouped by battle_id) | **FAIL** | evidence grade INTERIM; the gates below are real measurements on a leakage-safe split, but this run does NOT satisfy CP-14. design is 'leave_one_event_out', not CP-14's documented 'year_table'; the table holds no ['2022', '2023', '2024', '2025'] the benchmark is leakage-safe and its numbers are real, but it does not measure generalisation across regulation eras, which is what CP-14's year table exists to measure |
| All benchmark cells produce an artifact with a locked feature schema | PASS | 15 of 15 (checkpoint x family) cells scored a test split |
| Every model beats a constant-base-rate predictor on Brier | **FAIL** | ACTIVATION/logistic skill=-0.0336; ACTIVATION/mlp skill=-0.1329; BRAKING/logistic skill=-0.0537; BRAKING/mlp skill=-0.1172; DETECTION/mlp skill=-0.0537 |
| LightGBM / XGBoost / CatBoost beat logistic regression on log loss | PASS | ACTIVATION: every tree family beat logistic 0.4602 | BRAKING: every tree family beat logistic 0.4694 | DETECTION: every tree family beat logistic 0.4178. If not, the features are weak, not the models. |
| ACTIVATION and BRAKING outperform DETECTION (they see more) | PASS | best Brier per checkpoint: ACTIVATION=0.1122, BRAKING=0.1095, DETECTION=0.1153 |
| Leave-one-track-out Brier std below 0.05 | **FAIL** | ACTIVATION/mlp std=0.0560. High variance means the model is memorising circuits. |

## Driver identity (section 17)

Negative delta means identity improved Brier. A large gain is not good news:
it means the model is learning who tends to overtake whom rather than when an
overtake is on, and it will not transfer to an unseen line-up.

| Checkpoint | Family | Brier without | Brier with | Delta | ROC-AUC without | ROC-AUC with |
|---|---|---|---|---|---|---|
| ACTIVATION | catboost | 0.1207 | 0.1228 | 0.0022 | 0.7363 | 0.7321 |
| ACTIVATION | lightgbm | 0.1122 | 0.1156 | 0.0033 | 0.7416 | 0.7264 |
| ACTIVATION | logistic | 0.1378 | 0.1502 | 0.0125 | 0.5869 | 0.6090 |
| ACTIVATION | mlp | 0.1510 | 0.1687 | 0.0177 | 0.6666 | 0.6528 |
| ACTIVATION | xgboost | 0.1166 | 0.1239 | 0.0073 | 0.7278 | 0.6935 |
| BRAKING | catboost | 0.1195 | 0.1195 | -0.0000 | 0.7221 | 0.7285 |
| BRAKING | lightgbm | 0.1095 | 0.1137 | 0.0042 | 0.7378 | 0.7223 |
| BRAKING | logistic | 0.1405 | 0.1478 | 0.0073 | 0.5533 | 0.6003 |
| BRAKING | mlp | 0.1489 | 0.1900 | 0.0411 | 0.6770 | 0.6246 |
| BRAKING | xgboost | 0.1145 | 0.1203 | 0.0058 | 0.7209 | 0.6887 |
| DETECTION | catboost | 0.1189 | 0.1162 | -0.0027 | 0.7415 | 0.7422 |
| DETECTION | lightgbm | 0.1153 | 0.1160 | 0.0007 | 0.7384 | 0.7292 |
| DETECTION | logistic | 0.1271 | 0.1371 | 0.0100 | 0.6943 | 0.6848 |
| DETECTION | mlp | 0.1405 | 0.1966 | 0.0561 | 0.6717 | 0.6392 |
| DETECTION | xgboost | 0.1184 | 0.1170 | -0.0014 | 0.7410 | 0.7172 |

## Hardware

10 concurrent fit(s), 2 thread(s) each; process pool: True.

- 16 cores detected; 15 budgeted for fits
- 9252 training rows is below the 50000-row threshold, so 2 threads per fit and the remaining cores spent on concurrent fits
- workers pinned to 10 by the caller
- process pool of 10 workers, 2 threads each

## Reading this report

- Calibration decides selection, not ROC-AUC. The DP consumes probabilities, so the
  difference between 20% and 80% matters even where the ranking is unchanged.
- Tree probabilities are expected to be miscalibrated here. That is CP-15's job,
  and tuning it away at this stage would hide what CP-15 needs to measure.
- Every metric is reported against its own N. Folds differ in size by more than an
  order of magnitude, so a mean across folds is not a mean across opportunities.

