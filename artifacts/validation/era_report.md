# Regulation-era comparison (M13, CP-17)

Generated 2026-09-13T02:38:36.316821+00:00 at commit `4676aeb70eb5a55cc32c4ed67686e3ba3a576017`.

> **5 of 6 strategies could not be run.** Section 41's comparison needs two eras; the reasons are below and no substitute was fitted in their place.

| Strategy | Runnable | Blockers |
|---|---|---|
| `modern_only` | yes | — |
| `era_feature` | **no** | needs 2022-2025 rows; the table holds none. Years present: ['2026'] |
| `domain_weighting` | **no** | needs 2022-2025 rows; the table holds none. Years present: ['2026'] |
| `separate_models` | **no** | needs 2022-2025 rows; the table holds none. Years present: ['2026'] |
| `recalibration` | **no** | needs 2022-2025 rows; the table holds none. Years present: ['2026'] |
| `historical_pretraining` | **no** | needs 2022-2025 rows; the table holds none. Years present: ['2026'] |

## Results

Scored on the same 2026 test rows whatever each strategy trained on. Brier is primary and lower wins (section 26). **N is not comparable across rows** -- a strategy trained on four extra seasons is not competing on equal footing, and the training counts say so.

| Rank | Strategy | Brier | Brier sd | Log loss | ROC-AUC | Folds | N train (2026 / hist) | Δ vs 2026-only |
|---:|---|---:|---:|---:|---:|---:|---|---:|
| 1 | `modern_only` | 0.11527 | 0.03097 | 0.37872 | 0.7384 | 6 | 2808 / 0 | +0.00000 |

Only one strategy (`modern_only`) could be run, so this is a single measurement rather than a comparison. Section 41's question -- whether historical data helps -- is not answered here and must not be reported as answered.

## Limitations

- `historical_pretraining` is a continued fit on the 2026 rows rather than a true warm start; the tree families do not all expose one. Read it as an upper bound on what sequential training would give, not as the strategy itself.
- `domain_weighting` is unavailable for the `mlp` family, which takes no sample weights. It is omitted there rather than silently run unweighted.
