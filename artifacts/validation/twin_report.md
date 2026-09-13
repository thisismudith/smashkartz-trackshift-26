# CP-20 physics calibration

Version `20260912T223541Z`, commit `768a162fb554f94326145d847a4ebbeb32de9a66`.

Train 29981 rows, held out 10019 on 3 event(s).

| Rung | MAE (s) | RMSE (s) | Accepted | Reason |
|---|---|---|---|---|
| analytical | 0.3250 | 0.7043 | yes |  |
| global | 0.2738 | 0.7287 | no | parameter at a bound: cda_m2=0.6 at a bound. A fit that runs to a bound means the model is missing physics, not that the bound should move.; crr=0.005 at a bound. A fit that runs to a bound means the model is missing physics, not that the bound should move.; eta_drivetrain=0.98 at a bound. A fit that runs to a bound means the model is missing physics, not that the bound should move.; p_ice_max_kw=500 at a bound. A fit that runs to a bound means the model is missing physics, not that the bound should move.; held-out MAE 0.2738 s misses the 0.15 s target |
| team | 0.2739 | 0.7252 | no | held-out MAE 0.2739 s does not improve on the previous rung's 0.2738 s |
| event | 0.2738 | 0.7284 | no | held-out MAE 0.2738 s misses the 0.08 s target |
| physics_residual | 0.2288 | 0.5294 | no | held-out MAE 0.2288 s misses the 0.06 s target |
