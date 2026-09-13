# Feature-group ablation (M28, CP-23)

Generated 2026-09-13T02:35:25.709815+00:00 at commit `4676aeb70eb5a55cc32c4ed67686e3ba3a576017`.

> **Evidence grade: INTERIM.** The split is leakage-safe and these deltas are real, but the run does not meet CP-14's acceptance conditions, so the decisions below are provisional:
>
> - design is 'leave_one_event_out', not CP-14's documented 'year_table'; the table holds no ['2022', '2023', '2024', '2025']
> - the benchmark is leakage-safe and its numbers are real, but it does not measure generalisation across regulation eras, which is what CP-14's year table exists to measure

| Setting | Value |
|---|---|
| Primary metric | `brier` (lower is better) |
| Seeds | 42, 43 |
| Families | lightgbm |
| Folds | 6 |
| Cells fitted | 96 of 96 |

A **positive** leave-one-out delta means removing the group made the model worse, so the group was earning its place. Deltas inside the seed-to-seed noise floor are not distinguishable from rerunning the same configuration.

## DETECTION

Seed-to-seed noise floor: `0.00525` brier.

| Group | n feat | Leave-one-out Δ | 95% interval | Add-one-in Δ | Verdict |
|---|---:|---:|---|---:|---|
| `geometry` | 2 | +0.00011 | [-0.00334, +0.00357] | -0.00063 | **UNINFORMATIVE** |
| `tyre` | 6 | +0.00217 | [-0.00070, +0.00503] | +0.00106 | **UNINFORMATIVE** |
| `weather` | 4 | -0.00295 | [-0.00628, +0.00038] | -0.00324 | **UNINFORMATIVE** |

- `geometry` — leave-one-out delta +0.00011 is inside the 0.00525 noise floor and the group does not beat the minimal baseline on its own
- `tyre` — leave-one-out delta +0.00217 is inside the 0.00525 noise floor and the group does not beat the minimal baseline on its own
- `weather` — leave-one-out delta -0.00295 is inside the 0.00525 noise floor and the group does not beat the minimal baseline on its own

## Reading the verdicts

- **KEEP** — removing it costs more than the noise floor, interval clear of zero.
- **DROP** — removing it *improves* the metric; the group is costing accuracy.
- **REDUNDANT** — no leave-one-out effect, but it beats the minimal baseline alone. The signal is real and also carried elsewhere, so it is safe to drop only while that other group stays.
- **UNINFORMATIVE** — no effect either way; the signal was never there.

⚠️ marks identity groups. Section 17: a large gain there is evidence of memorising drivers and teams rather than learning racecraft, and should be read against the racecraft groups before being kept.
