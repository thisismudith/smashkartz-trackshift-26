# Pass-model ensemble spread (M12, CP-16)

Generated 2026-09-13T03:16:28.093165+00:00 at commit `006bbb240d5dbc08fae9bd3e30df36f11430b065`.

| Run | Value |
|---|---|
| Members per ensemble | 21 |
| Base seed | 42 |
| Bagged families | catboost, lightgbm, logistic, xgboost |
| Event jackknife | False |
| Split design | leave_one_event_out (6 folds) |
| Spread relative standard error | 0.158 |

## What this number is, and is not

`ensemble_spread` is the standard deviation across members, which is CP-16's
definition and what the API contract expects (MODELS.md C4). It measures
**disagreement among members that share a training distribution**, so it cannot
see error common to all of them. It goes to the UI beside `p_pass`; section 33
does not list it among the planner's uncertainty inputs.

The scale check is `gap/band`: the RMS calibration gap against the mean spread.
It is deliberately NOT measured against raw RMSE -- a probability scored on a 0/1
label carries irreducible Bernoulli error of sqrt(b(1-b)) (the `Irreducible`
column), which no band should be asked to cover and which no model removes.

Members are bootstrapped as well as seeded. `LogisticRegression(solver="lbfgs")`
ignores `random_state`, so seeds alone give a spread of exactly 0.0 for the
family CP-14 selected at DETECTION -- measured on all 21 checkpoint-by-fold cells.
MLP is deliberately not bagged: its seed already drives weight initialisation.

## Results

| Checkpoint | Family | Fold | Mean spread | p95 | Zero-spread | Sharpness kept | RMS calib gap | gap/band | Irreducible | Jackknife x | Out-of-support |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ACTIVATION | catboost | australian_grand_prix | 0.0450 | 0.0892 | 0.0% | 0.920 | 0.0721 | 1.60x | 0.358 | -- | 19.2% |
| ACTIVATION | catboost | barcelona_grand_prix | 0.0512 | 0.1112 | 0.0% | 0.905 | 0.1213 | 2.37x | 0.308 | -- | 0.0% |
| ACTIVATION | catboost | canadian_grand_prix | 0.0337 | 0.0486 | 0.0% | 0.824 | 0.1563 | 4.64x | 0.333 | -- | 47.5% |
| ACTIVATION | catboost | italian_grand_prix | 0.0344 | 0.0928 | 0.0% | 0.892 | 0.1343 | 3.91x | 0.445 | -- | 25.2% |
| ACTIVATION | catboost | japanese_grand_prix | 0.0326 | 0.0898 | 0.0% | 0.878 | 0.0795 | 2.44x | 0.366 | -- | 0.3% |
| ACTIVATION | catboost | miami_grand_prix | 0.0518 | 0.1232 | 0.0% | 0.789 | 0.0568 | 1.10x | 0.325 | -- | 0.0% |
| ACTIVATION | lightgbm | australian_grand_prix | 0.0492 | 0.1130 | 0.0% | 0.879 | 0.0631 | 1.28x | 0.358 | -- | 19.2% |
| ACTIVATION | lightgbm | barcelona_grand_prix | 0.0436 | 0.1008 | 0.0% | 0.870 | 0.0472 | 1.08x | 0.308 | -- | 0.0% |
| ACTIVATION | lightgbm | canadian_grand_prix | 0.0472 | 0.0984 | 0.0% | 0.809 | 0.0511 | 1.08x | 0.333 | -- | 47.5% |
| ACTIVATION | lightgbm | italian_grand_prix | 0.0400 | 0.0855 | 0.0% | 0.850 | 0.1306 | 3.26x | 0.445 | -- | 25.2% |
| ACTIVATION | lightgbm | japanese_grand_prix | 0.0372 | 0.0928 | 0.0% | 0.889 | 0.0567 | 1.52x | 0.366 | -- | 0.3% |
| ACTIVATION | lightgbm | miami_grand_prix | 0.0399 | 0.0892 | 0.0% | 0.793 | 0.0714 | 1.79x | 0.325 | -- | 0.0% |
| ACTIVATION | logistic | australian_grand_prix | 0.0451 | 0.1147 | 0.0% | 0.876 | 0.0632 | 1.40x | 0.358 | -- | 19.2% |
| ACTIVATION | logistic | barcelona_grand_prix | 0.0527 | 0.1008 | 0.0% | 0.893 | 0.1757 | 3.33x | 0.308 | -- | 0.0% |
| ACTIVATION | logistic | canadian_grand_prix | 0.0567 | 0.1166 | 0.0% | 0.850 | 0.0780 | 1.37x | 0.333 | -- | 47.5% |
| ACTIVATION | logistic | italian_grand_prix | 0.0361 | 0.0707 | 0.0% | 0.884 | 0.1916 | 5.31x | 0.445 | -- | 25.2% |
| ACTIVATION | logistic | japanese_grand_prix | 0.0310 | 0.0509 | 0.0% | 0.883 | 0.1028 | 3.31x | 0.366 | -- | 0.3% |
| ACTIVATION | logistic | miami_grand_prix | 0.0455 | 0.0820 | 0.0% | 0.812 | 0.0857 | 1.88x | 0.325 | -- | 0.0% |
| ACTIVATION | mlp | australian_grand_prix | 0.1308 | 0.2547 | 0.0% | 0.727 | 0.1132 | 0.87x | 0.358 | -- | 19.2% |
| ACTIVATION | mlp | barcelona_grand_prix | 0.1050 | 0.1761 | 0.0% | 0.709 | 0.1473 | 1.40x | 0.308 | -- | 0.0% |
| ACTIVATION | mlp | canadian_grand_prix | 0.1029 | 0.2138 | 0.0% | 0.571 | 0.0662 | 0.64x | 0.333 | -- | 47.5% |
| ACTIVATION | mlp | italian_grand_prix | 0.0621 | 0.2166 | 0.0% | 0.653 | 0.2010 | 3.24x | 0.445 | -- | 25.2% |
| ACTIVATION | mlp | japanese_grand_prix | 0.1243 | 0.2235 | 0.0% | 0.639 | 0.0959 | 0.77x | 0.366 | -- | 0.3% |
| ACTIVATION | mlp | miami_grand_prix | 0.0971 | 0.2012 | 0.0% | 0.729 | 0.1231 | 1.27x | 0.325 | -- | 0.0% |
| ACTIVATION | xgboost | australian_grand_prix | 0.0555 | 0.1295 | 0.0% | 0.845 | 0.0936 | 1.69x | 0.358 | -- | 19.2% |
| ACTIVATION | xgboost | barcelona_grand_prix | 0.0513 | 0.1420 | 0.0% | 0.836 | 0.0600 | 1.17x | 0.308 | -- | 0.0% |
| ACTIVATION | xgboost | canadian_grand_prix | 0.0428 | 0.1000 | 0.0% | 0.803 | 0.0565 | 1.32x | 0.333 | -- | 47.5% |
| ACTIVATION | xgboost | italian_grand_prix | 0.0400 | 0.0996 | 0.0% | 0.846 | 0.1390 | 3.47x | 0.445 | -- | 25.2% |
| ACTIVATION | xgboost | japanese_grand_prix | 0.0379 | 0.0978 | 0.0% | 0.895 | 0.0630 | 1.66x | 0.366 | -- | 0.3% |
| ACTIVATION | xgboost | miami_grand_prix | 0.0366 | 0.0828 | 0.0% | 0.815 | 0.0748 | 2.04x | 0.325 | -- | 0.0% |
| BRAKING | catboost | australian_grand_prix | 0.0414 | 0.0845 | 0.0% | 0.920 | 0.0636 | 1.54x | 0.358 | -- | 19.2% |
| BRAKING | catboost | barcelona_grand_prix | 0.0513 | 0.1115 | 0.0% | 0.901 | 0.1192 | 2.32x | 0.308 | -- | 0.0% |
| BRAKING | catboost | canadian_grand_prix | 0.0300 | 0.0513 | 0.0% | 0.821 | 0.1555 | 5.19x | 0.333 | -- | 47.4% |
| BRAKING | catboost | italian_grand_prix | 0.0430 | 0.1206 | 0.0% | 0.913 | 0.1432 | 3.33x | 0.445 | -- | 25.2% |
| BRAKING | catboost | japanese_grand_prix | 0.0347 | 0.0925 | 0.0% | 0.913 | 0.0723 | 2.09x | 0.366 | -- | 0.3% |
| BRAKING | catboost | miami_grand_prix | 0.0408 | 0.0894 | 0.0% | 0.857 | 0.0590 | 1.45x | 0.325 | -- | 0.0% |
| BRAKING | lightgbm | australian_grand_prix | 0.0392 | 0.1027 | 0.0% | 0.887 | 0.0360 | 0.92x | 0.358 | -- | 19.2% |
| BRAKING | lightgbm | barcelona_grand_prix | 0.0484 | 0.1218 | 0.0% | 0.861 | 0.0608 | 1.26x | 0.308 | -- | 0.0% |
| BRAKING | lightgbm | canadian_grand_prix | 0.0431 | 0.0993 | 0.0% | 0.814 | 0.0405 | 0.94x | 0.333 | -- | 47.4% |
| BRAKING | lightgbm | italian_grand_prix | 0.0455 | 0.0997 | 0.0% | 0.901 | 0.1111 | 2.44x | 0.445 | -- | 25.2% |
| BRAKING | lightgbm | japanese_grand_prix | 0.0386 | 0.0915 | 0.0% | 0.906 | 0.0612 | 1.59x | 0.366 | -- | 0.3% |
| BRAKING | lightgbm | miami_grand_prix | 0.0423 | 0.0896 | 0.0% | 0.826 | 0.0413 | 0.98x | 0.325 | -- | 0.0% |
| BRAKING | logistic | australian_grand_prix | 0.0406 | 0.1041 | 0.0% | 0.808 | 0.0585 | 1.44x | 0.358 | -- | 19.2% |
| BRAKING | logistic | barcelona_grand_prix | 0.0534 | 0.0931 | 0.0% | 0.882 | 0.1940 | 3.63x | 0.308 | -- | 0.0% |
| BRAKING | logistic | canadian_grand_prix | 0.0549 | 0.1241 | 0.0% | 0.809 | 0.0729 | 1.33x | 0.333 | -- | 47.4% |
| BRAKING | logistic | italian_grand_prix | 0.0359 | 0.0720 | 0.0% | 0.849 | 0.1972 | 5.49x | 0.445 | -- | 25.2% |
| BRAKING | logistic | japanese_grand_prix | 0.0337 | 0.0508 | 0.0% | 0.852 | 0.0856 | 2.54x | 0.366 | -- | 0.3% |
| BRAKING | logistic | miami_grand_prix | 0.0412 | 0.0691 | 0.0% | 0.821 | 0.0803 | 1.95x | 0.325 | -- | 0.0% |
| BRAKING | mlp | australian_grand_prix | 0.0812 | 0.2248 | 0.0% | 0.782 | 0.0855 | 1.05x | 0.358 | -- | 19.2% |
| BRAKING | mlp | barcelona_grand_prix | 0.1184 | 0.2193 | 0.0% | 0.716 | 0.1710 | 1.44x | 0.308 | -- | 0.0% |
| BRAKING | mlp | canadian_grand_prix | 0.1224 | 0.2344 | 0.0% | 0.600 | 0.0885 | 0.72x | 0.333 | -- | 47.4% |
| BRAKING | mlp | italian_grand_prix | 0.0773 | 0.2380 | 0.0% | 0.620 | 0.1928 | 2.49x | 0.445 | -- | 25.2% |
| BRAKING | mlp | japanese_grand_prix | 0.1229 | 0.2288 | 0.0% | 0.676 | 0.1052 | 0.86x | 0.366 | -- | 0.3% |
| BRAKING | mlp | miami_grand_prix | 0.0906 | 0.2012 | 0.0% | 0.724 | 0.1020 | 1.13x | 0.325 | -- | 0.0% |
| BRAKING | xgboost | australian_grand_prix | 0.0504 | 0.1204 | 0.0% | 0.847 | 0.0626 | 1.24x | 0.358 | -- | 19.2% |
| BRAKING | xgboost | barcelona_grand_prix | 0.0469 | 0.1243 | 0.0% | 0.869 | 0.0635 | 1.36x | 0.308 | -- | 0.0% |
| BRAKING | xgboost | canadian_grand_prix | 0.0367 | 0.0905 | 0.0% | 0.798 | 0.0545 | 1.49x | 0.333 | -- | 47.4% |
| BRAKING | xgboost | italian_grand_prix | 0.0439 | 0.1130 | 0.0% | 0.898 | 0.1329 | 3.02x | 0.445 | -- | 25.2% |
| BRAKING | xgboost | japanese_grand_prix | 0.0399 | 0.0961 | 0.0% | 0.908 | 0.0562 | 1.41x | 0.366 | -- | 0.3% |
| BRAKING | xgboost | miami_grand_prix | 0.0372 | 0.0764 | 0.0% | 0.831 | 0.0546 | 1.47x | 0.325 | -- | 0.0% |
| DETECTION | catboost | australian_grand_prix | 0.0507 | 0.1012 | 0.0% | 0.895 | 0.0656 | 1.29x | 0.358 | -- | 19.2% |
| DETECTION | catboost | barcelona_grand_prix | 0.0378 | 0.0917 | 0.0% | 0.944 | 0.0613 | 1.62x | 0.308 | -- | 0.0% |
| DETECTION | catboost | canadian_grand_prix | 0.0413 | 0.0566 | 0.0% | 0.879 | 0.1183 | 2.86x | 0.333 | -- | 47.4% |
| DETECTION | catboost | italian_grand_prix | 0.0310 | 0.0724 | 0.0% | 0.897 | 0.1663 | 5.37x | 0.445 | -- | 25.2% |
| DETECTION | catboost | japanese_grand_prix | 0.0298 | 0.0727 | 0.0% | 0.956 | 0.0802 | 2.69x | 0.366 | -- | 0.0% |
| DETECTION | catboost | miami_grand_prix | 0.0519 | 0.1209 | 0.0% | 0.812 | 0.0815 | 1.57x | 0.325 | -- | 0.0% |
| DETECTION | lightgbm | australian_grand_prix | 0.0396 | 0.0867 | 0.0% | 0.863 | 0.0774 | 1.95x | 0.358 | -- | 19.2% |
| DETECTION | lightgbm | barcelona_grand_prix | 0.0385 | 0.1155 | 0.0% | 0.911 | 0.0553 | 1.44x | 0.308 | -- | 0.0% |
| DETECTION | lightgbm | canadian_grand_prix | 0.0521 | 0.1061 | 0.0% | 0.759 | 0.0565 | 1.08x | 0.333 | -- | 47.4% |
| DETECTION | lightgbm | italian_grand_prix | 0.0382 | 0.0843 | 0.0% | 0.813 | 0.1963 | 5.13x | 0.445 | -- | 25.2% |
| DETECTION | lightgbm | japanese_grand_prix | 0.0286 | 0.0602 | 0.0% | 0.931 | 0.0605 | 2.12x | 0.366 | -- | 0.0% |
| DETECTION | lightgbm | miami_grand_prix | 0.0478 | 0.1059 | 0.0% | 0.822 | 0.0508 | 1.06x | 0.325 | -- | 0.0% |
| DETECTION | logistic | australian_grand_prix | 0.0329 | 0.0923 | 0.0% | 0.905 | 0.0717 | 2.18x | 0.358 | -- | 19.2% |
| DETECTION | logistic | barcelona_grand_prix | 0.0381 | 0.0822 | 0.0% | 0.953 | 0.1343 | 3.53x | 0.308 | -- | 0.0% |
| DETECTION | logistic | canadian_grand_prix | 0.0486 | 0.1229 | 0.0% | 0.879 | 0.0810 | 1.67x | 0.333 | -- | 47.4% |
| DETECTION | logistic | italian_grand_prix | 0.0394 | 0.0825 | 0.0% | 0.921 | 0.1576 | 4.00x | 0.445 | -- | 25.2% |
| DETECTION | logistic | japanese_grand_prix | 0.0297 | 0.0591 | 0.0% | 0.965 | 0.0936 | 3.15x | 0.366 | -- | 0.0% |
| DETECTION | logistic | miami_grand_prix | 0.0339 | 0.0751 | 0.0% | 0.899 | 0.0372 | 1.10x | 0.325 | -- | 0.0% |
| DETECTION | mlp | australian_grand_prix | 0.1005 | 0.2456 | 0.0% | 0.733 | 0.0598 | 0.60x | 0.358 | -- | 19.2% |
| DETECTION | mlp | barcelona_grand_prix | 0.1046 | 0.2569 | 0.0% | 0.702 | 0.1202 | 1.15x | 0.308 | -- | 0.0% |
| DETECTION | mlp | canadian_grand_prix | 0.1035 | 0.2342 | 0.0% | 0.650 | 0.0698 | 0.67x | 0.333 | -- | 47.4% |
| DETECTION | mlp | italian_grand_prix | 0.1464 | 0.3212 | 0.0% | 0.553 | 0.1292 | 0.88x | 0.445 | -- | 25.2% |
| DETECTION | mlp | japanese_grand_prix | 0.1375 | 0.2790 | 0.0% | 0.757 | 0.1605 | 1.17x | 0.366 | -- | 0.0% |
| DETECTION | mlp | miami_grand_prix | 0.1266 | 0.2574 | 0.0% | 0.675 | 0.1601 | 1.27x | 0.325 | -- | 0.0% |
| DETECTION | xgboost | australian_grand_prix | 0.0497 | 0.1040 | 0.0% | 0.877 | 0.0785 | 1.58x | 0.358 | -- | 19.2% |
| DETECTION | xgboost | barcelona_grand_prix | 0.0360 | 0.1007 | 0.0% | 0.932 | 0.0434 | 1.21x | 0.308 | -- | 0.0% |
| DETECTION | xgboost | canadian_grand_prix | 0.0516 | 0.1163 | 0.0% | 0.769 | 0.0574 | 1.11x | 0.333 | -- | 47.4% |
| DETECTION | xgboost | italian_grand_prix | 0.0341 | 0.0763 | 0.0% | 0.856 | 0.1875 | 5.50x | 0.445 | -- | 25.2% |
| DETECTION | xgboost | japanese_grand_prix | 0.0298 | 0.0654 | 0.0% | 0.922 | 0.0574 | 1.93x | 0.366 | -- | 0.0% |
| DETECTION | xgboost | miami_grand_prix | 0.0442 | 0.0964 | 0.0% | 0.808 | 0.0647 | 1.46x | 0.325 | -- | 0.0% |

## CP-16 acceptance gates

CP-16's own thresholds are restated here so they can fire. "Spread not ~0"
and "spread not > 0.25" are not on a meaningful scale at a 15% base rate, and
"the ensemble mean is at least as well calibrated as any member" reduces to a
Jensen identity when tested against the member mean.

| Gate | Failing cells |
|---|---|
| Ensemble keeps at least 90% of member sharpness | 71: ACTIVATION/catboost/canadian_grand_prix, ACTIVATION/catboost/italian_grand_prix, ACTIVATION/catboost/japanese_grand_prix, ACTIVATION/catboost/miami_grand_prix |
| Members are genuinely different (spread is not structurally zero) | none |
| Per-row spread is estimated from enough members to report | none |
| RMS calibration gap fits inside 1.0 band(s) | 79: ACTIVATION/catboost/australian_grand_prix, ACTIVATION/catboost/barcelona_grand_prix, ACTIVATION/catboost/canadian_grand_prix, ACTIVATION/catboost/italian_grand_prix |
| Realised error rises with spread across quintiles | 62: ACTIVATION/catboost/barcelona_grand_prix, ACTIVATION/catboost/japanese_grand_prix, ACTIVATION/catboost/miami_grand_prix, ACTIVATION/lightgbm/barcelona_grand_prix |

## Known limitation: spread shrinks outside the training support

CP-16 asks that spread widen where data is thin. For the tree families it does
the opposite: beyond the training range every member returns its boundary leaf,
so they agree and the spread falls. Extrapolation is therefore reported as its
own `out_of_support` fraction rather than inferred from spread, which would read
as confidence exactly where the model is least supported.

## Hardware

3 concurrent cell(s), 2 thread(s) each; process pool: True.

- 16 cores detected; 15 budgeted for fits
- 9252 training rows is below the 50000-row threshold, so 2 threads per fit and the remaining cores spent on concurrent fits
- 7 concurrent fits fit in the core budget; 90 cells to run
- capped to 3 by 2.6 GB free memory at about 0.75 GB per spawned worker
- process pool of 3 workers, 2 threads each

