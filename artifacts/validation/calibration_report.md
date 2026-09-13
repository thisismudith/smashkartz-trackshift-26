# Pass-model calibration (M11, CP-15)

Generated 2026-09-13T02:47:24.812004+00:00 at commit `4676aeb70eb5a55cc32c4ed67686e3ba3a576017`.

| Run | Value |
|---|---|
| Seed | 42 |
| Checkpoints | DETECTION, ACTIVATION, BRAKING |
| Families | logistic, lightgbm, xgboost, catboost, mlp |
| Methods | uncalibrated, platt, isotonic |
| Calibration data | cross-fitted over the training events |
| Split design | leave_one_event_out (6 folds) |

## Why calibration data is cross-fitted

CP-15 specifies fitting the calibrator on the validation split. In CP-14 that
split is already consumed by early stopping for LightGBM, XGBoost and CatBoost,
so a calibrator fitted there measures calibration on rows the model was tuned
against. Spending a whole event instead was measured and is unusable at this
table size: the seven candidate blocks hold 61-1,228 rows at base rates from
1.6% to 27.1%, and one fold would calibrate on 61 rows containing one positive.
Cross-fitting over the training events keeps every calibration row out-of-sample
for the model that scored it, spends no training data, and leaves CP-14's
train/validation/test indices byte-identical.

## Calibration sets

| Fold | n | +ve | Base rate | Inner fits | Licensed method |
|---|---|---|---|---|---|
| australian_grand_prix | 3041 | 483 | 0.1588 | 5 | isotonic |
| barcelona_grand_prix | 2578 | 441 | 0.1711 | 5 | isotonic |
| canadian_grand_prix | 2205 | 289 | 0.1311 | 5 | isotonic |
| italian_grand_prix | 3084 | 391 | 0.1268 | 5 | isotonic |
| japanese_grand_prix | 2926 | 475 | 0.1623 | 5 | isotonic |
| miami_grand_prix | 2510 | 415 | 0.1653 | 5 | isotonic |

## Variant comparison

Weighted by n across folds. ECE is a count-weighted mean within a fold, so
an unweighted mean across folds of unequal size is a mean of means -- the
same ratio trap CP-14 hit with Brier skill.

### DETECTION

| Family | Method | N | Brier | ECE | Log loss | ROC-AUC | PR-AUC | Sharpness | Min bins | Rejected folds |
|---|---|---|---|---|---|---|---|---|---|---|
| catboost | isotonic | 4005 | 0.1219 | 0.0891 | 0.4136 | 0.7294 | 0.3487 | 0.01946 | 4 | 0 |
| catboost | platt | 4005 | 0.1192 | 0.0873 | 0.3943 | 0.7395 | 0.4242 | 0.01417 | 10 | 0 |
| catboost | uncalibrated | 4005 | 0.1213 | 0.0993 | 0.4009 | 0.7395 | 0.4242 | 0.01483 | 10 | 0 |
| lightgbm | isotonic | 4005 | 0.1165 | 0.0602 | 0.4130 | 0.7269 | 0.3584 | 0.01555 | 4 | 0 |
| lightgbm | platt | 4005 | 0.1164 | 0.0613 | 0.3817 | 0.7321 | 0.3948 | 0.01282 | 10 | 0 |
| lightgbm | uncalibrated | 4005 | 0.1157 | 0.0571 | 0.3806 | 0.7321 | 0.3948 | 0.01436 | 10 | 0 |
| logistic | isotonic | 4005 | 0.1240 | 0.0678 | 0.4069 | 0.6896 | 0.2804 | 0.00542 | 4 | 0 |
| logistic | platt | 4005 | 0.1251 | 0.0743 | 0.4095 | 0.6945 | 0.2959 | 0.00353 | 10 | 0 |
| logistic | uncalibrated | 4005 | 0.1251 | 0.0650 | 0.4112 | 0.6945 | 0.2959 | 0.01686 | 10 | 0 |
| mlp | isotonic | 4005 | 0.1293 | 0.0553 | 0.4300 | 0.6410 | 0.2425 | 0.00588 | 1 | 0 |
| mlp | platt | 4005 | 0.1288 | 0.0794 | 0.4225 | 0.6711 | 0.2817 | 0.00260 | 10 | 0 |
| mlp | uncalibrated | 4005 | 0.1427 | 0.1044 | 0.4788 | 0.6711 | 0.2817 | 0.03611 | 10 | 0 |
| xgboost | isotonic | 4005 | 0.1207 | 0.0518 | 0.4189 | 0.7309 | 0.3198 | 0.02053 | 5 | 0 |
| xgboost | platt | 4005 | 0.1187 | 0.0660 | 0.3867 | 0.7387 | 0.3602 | 0.01507 | 10 | 0 |
| xgboost | uncalibrated | 4005 | 0.1182 | 0.0619 | 0.3858 | 0.7387 | 0.3602 | 0.01694 | 10 | 0 |

**Selected: lightgbm / uncalibrated** (Brier 0.1157, ECE 0.0571, sharpness 0.01436).

### ACTIVATION

| Family | Method | N | Brier | ECE | Log loss | ROC-AUC | PR-AUC | Sharpness | Min bins | Rejected folds |
|---|---|---|---|---|---|---|---|---|---|---|
| catboost | isotonic | 4005 | 0.1183 | 0.0797 | 0.4803 | 0.7419 | 0.4022 | 0.02080 | 6 | 0 |
| catboost | platt | 4005 | 0.1168 | 0.0766 | 0.3980 | 0.7486 | 0.4353 | 0.01829 | 10 | 0 |
| catboost | uncalibrated | 4005 | 0.1201 | 0.0952 | 0.4039 | 0.7486 | 0.4353 | 0.01534 | 10 | 0 |
| lightgbm | isotonic | 4005 | 0.1126 | 0.0450 | 0.3808 | 0.7392 | 0.4011 | 0.01862 | 5 | 0 |
| lightgbm | platt | 4005 | 0.1115 | 0.0496 | 0.3728 | 0.7452 | 0.4423 | 0.01596 | 10 | 0 |
| lightgbm | uncalibrated | 4005 | 0.1116 | 0.0482 | 0.3718 | 0.7452 | 0.4423 | 0.01642 | 10 | 0 |
| logistic | isotonic | 4005 | 0.1323 | 0.0564 | 0.4448 | 0.5632 | 0.2049 | 0.00374 | 2 | 0 |
| logistic | platt | 4005 | 0.1327 | 0.0702 | 0.4382 | 0.5641 | 0.2194 | 0.00080 | 10 | 1 |
| logistic | uncalibrated | 4005 | 0.1356 | 0.0818 | 0.4556 | 0.5918 | 0.2424 | 0.01224 | 10 | 0 |
| mlp | isotonic | 4005 | 0.1277 | 0.0776 | 0.4423 | 0.6402 | 0.2819 | 0.00494 | 4 | 0 |
| mlp | platt | 4005 | 0.1317 | 0.0914 | 0.4457 | 0.6512 | 0.3313 | 0.00187 | 10 | 0 |
| mlp | uncalibrated | 4005 | 0.1528 | 0.1412 | 0.7215 | 0.6512 | 0.3312 | 0.04223 | 10 | 0 |
| xgboost | isotonic | 4005 | 0.1200 | 0.0648 | 0.4370 | 0.7280 | 0.3846 | 0.02172 | 3 | 0 |
| xgboost | platt | 4005 | 0.1164 | 0.0690 | 0.3886 | 0.7345 | 0.4305 | 0.01832 | 10 | 0 |
| xgboost | uncalibrated | 4005 | 0.1159 | 0.0655 | 0.3848 | 0.7345 | 0.4305 | 0.01754 | 10 | 0 |

**Selected: lightgbm / platt** (Brier 0.1115, ECE 0.0496, sharpness 0.01596).

### BRAKING

| Family | Method | N | Brier | ECE | Log loss | ROC-AUC | PR-AUC | Sharpness | Min bins | Rejected folds |
|---|---|---|---|---|---|---|---|---|---|---|
| catboost | isotonic | 4005 | 0.1158 | 0.0666 | 0.4003 | 0.7213 | 0.3936 | 0.01848 | 4 | 0 |
| catboost | platt | 4005 | 0.1144 | 0.0655 | 0.3846 | 0.7279 | 0.4266 | 0.01466 | 10 | 0 |
| catboost | uncalibrated | 4005 | 0.1202 | 0.0910 | 0.4075 | 0.7279 | 0.4266 | 0.01644 | 10 | 0 |
| lightgbm | isotonic | 4005 | 0.1130 | 0.0551 | 0.4009 | 0.7324 | 0.3958 | 0.02767 | 3 | 0 |
| lightgbm | platt | 4005 | 0.1125 | 0.0549 | 0.3769 | 0.7386 | 0.4338 | 0.01784 | 10 | 0 |
| lightgbm | uncalibrated | 4005 | 0.1108 | 0.0497 | 0.3730 | 0.7386 | 0.4338 | 0.01757 | 10 | 0 |
| logistic | isotonic | 4005 | 0.1333 | 0.0551 | 0.4451 | 0.5513 | 0.1770 | 0.00132 | 2 | 0 |
| logistic | platt | 4005 | 0.1332 | 0.0729 | 0.4398 | 0.4923 | 0.1787 | 0.00030 | 10 | 2 |
| logistic | uncalibrated | 4005 | 0.1383 | 0.0869 | 0.4653 | 0.5442 | 0.2042 | 0.00857 | 10 | 0 |
| mlp | isotonic | 4005 | 0.1299 | 0.0808 | 0.5402 | 0.6471 | 0.2791 | 0.00675 | 3 | 0 |
| mlp | platt | 4005 | 0.1305 | 0.0855 | 0.4357 | 0.6602 | 0.3178 | 0.00240 | 10 | 0 |
| mlp | uncalibrated | 4005 | 0.1437 | 0.1242 | 0.5745 | 0.6602 | 0.3178 | 0.03797 | 10 | 0 |
| xgboost | isotonic | 4005 | 0.1167 | 0.0580 | 0.3940 | 0.7154 | 0.3781 | 0.02368 | 2 | 0 |
| xgboost | platt | 4005 | 0.1160 | 0.0612 | 0.3876 | 0.7250 | 0.4206 | 0.01655 | 10 | 0 |
| xgboost | uncalibrated | 4005 | 0.1150 | 0.0579 | 0.3858 | 0.7250 | 0.4206 | 0.01522 | 10 | 0 |

**Selected: lightgbm / uncalibrated** (Brier 0.1108, ECE 0.0497, sharpness 0.01757).

## Rejected calibrators

A calibrator that collapses toward a constant scores well on ECE and Brier
while discarding the discrimination the planner needs, and a Platt fit whose
slope takes the wrong sign inverts the model's ranking while improving ECE.
Both are refused rather than selected.

| Checkpoint | Family | Fold | Method | Reason |
|---|---|---|---|---|
| ACTIVATION | logistic | barcelona_grand_prix | platt | mapping is not monotone non-decreasing; it reorders the model's ranking. For Platt this means the fitted slope has the wrong sign, which happens on low-signal calibration sets and improves ECE while doing it |
| BRAKING | logistic | australian_grand_prix | platt | mapping is not monotone non-decreasing; it reorders the model's ranking. For Platt this means the fitted slope has the wrong sign, which happens on low-signal calibration sets and improves ECE while doing it |
| BRAKING | logistic | barcelona_grand_prix | platt | mapping is not monotone non-decreasing; it reorders the model's ranking. For Platt this means the fitted slope has the wrong sign, which happens on low-signal calibration sets and improves ECE while doing it |

## Example reliability bins

DETECTION / logistic / uncalibrated, fold australian_grand_prix.

| Bin | Range | n | Mean predicted | Observed | Wilson band | In band |
|---|---|---|---|---|---|---|
| 1 | 0.002-0.012 | 72 | 0.0096 | 0.0000 | 0.000-0.051 | yes |
| 2 | 0.012-0.024 | 72 | 0.0173 | 0.0417 | 0.014-0.115 | yes |
| 3 | 0.024-0.039 | 72 | 0.0314 | 0.0417 | 0.014-0.115 | yes |
| 4 | 0.039-0.057 | 72 | 0.0482 | 0.1250 | 0.067-0.221 | **no** |
| 5 | 0.057-0.073 | 72 | 0.0663 | 0.1250 | 0.067-0.221 | **no** |
| 6 | 0.073-0.095 | 72 | 0.0847 | 0.0833 | 0.039-0.170 | yes |
| 7 | 0.095-0.113 | 72 | 0.1050 | 0.0833 | 0.039-0.170 | yes |
| 8 | 0.113-0.163 | 69 | 0.1390 | 0.1304 | 0.070-0.230 | yes |
| 9 | 0.163-0.286 | 72 | 0.2206 | 0.3750 | 0.272-0.490 | **no** |
| 10 | 0.286-0.785 | 72 | 0.4197 | 0.5000 | 0.387-0.613 | yes |

## Reliability diagrams

45 figure(s) written to `artifacts\validation\reliability`, each beside a
`.json` sidecar carrying the bin numbers it was drawn from.

## Reading this report

- A constant predictor is perfectly calibrated. ECE is not a proper scoring rule
  and is minimised at the base rate, so sharpness is reported beside it and a
  collapsed calibrator is refused rather than selected.
- In-sample isotonic ECE is 0.0 by algebraic identity, so every number here is
  measured on a held-out event the calibrator never saw.
- ECE below n=200 is dominated by its own positive bias. Those folds
  are marked not measurable rather than reported as small numbers.
- Platt preserves ranking exactly when its slope is negative; isotonic may lose a
  little ROC-AUC to tie-flattening, which is expected and not a bug.

