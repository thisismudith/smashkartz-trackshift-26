# Feature-group ablation (M28, CP-23)

Generated 2026-09-13T03:24:36.027616+00:00 at commit `68846aee31cbefc3dfb31fe126ea51a427c740a5`.

> **Evidence grade: INTERIM.** The split is leakage-safe and these deltas are real, but the run does not meet CP-14's acceptance conditions, so the decisions below are provisional:
>
> - design is 'leave_one_event_out', not CP-14's documented 'year_table'; the table holds no ['2022', '2023', '2024', '2025']
> - the benchmark is leakage-safe and its numbers are real, but it does not measure generalisation across regulation eras, which is what CP-14's year table exists to measure

| Setting | Value |
|---|---|
| Primary metric | `brier` (lower is better) |
| Seeds | 42, 43, 44 |
| Families | lightgbm |
| Folds | 6 |
| Cells fitted | 1404 of 1404 |

A **positive** leave-one-out delta means removing the group made the model worse, so the group was earning its place. Deltas inside the seed-to-seed noise floor are not distinguishable from rerunning the same configuration.

## DETECTION

Seed-to-seed noise floor: `0.00602` brier.

| Group | n feat | Leave-one-out Δ | 95% interval | Add-one-in Δ | Verdict |
|---|---:|---:|---|---:|---|
| `attacker` | 3 | -0.00016 | [-0.00193, +0.00161] | +0.00101 | **UNINFORMATIVE** |
| `canonical_gap` | 1 | +0.00420 | [+0.00118, +0.00723] | +0.00000 | **UNINFORMATIVE** |
| `compound` | 1 | +0.00102 | [-0.00122, +0.00325] | -0.00198 | **UNINFORMATIVE** |
| `defender` | 3 | -0.00062 | [-0.00238, +0.00114] | -0.00031 | **UNINFORMATIVE** |
| `geometry` | 2 | +0.00054 | [-0.00184, +0.00292] | -0.00059 | **UNINFORMATIVE** |
| `identity` ⚠️ | 4 | -0.00197 | [-0.00487, +0.00093] | -0.00350 | **UNINFORMATIVE** |
| `overtake_eligibility` | 3 | +0.01855 | [+0.01380, +0.02330] | +0.00561 | **KEEP** |
| `relative` | 1 | +0.00120 | [-0.00141, +0.00382] | +0.00115 | **UNINFORMATIVE** |
| `team_identity` ⚠️ | 2 | -0.00089 | [-0.00267, +0.00088] | -0.00026 | **UNINFORMATIVE** |
| `track_relative_wind` | 2 | -0.00110 | [-0.00318, +0.00097] | -0.00479 | **UNINFORMATIVE** |
| `tyre` | 6 | +0.00323 | [+0.00034, +0.00611] | +0.00093 | **UNINFORMATIVE** |
| `weather` | 4 | -0.00231 | [-0.00487, +0.00024] | -0.00340 | **UNINFORMATIVE** |

- `attacker` — leave-one-out delta -0.00016 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `canonical_gap` — leave-one-out delta +0.00420 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `compound` — leave-one-out delta +0.00102 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `defender` — leave-one-out delta -0.00062 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `geometry` — leave-one-out delta +0.00054 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `identity` — leave-one-out delta -0.00197 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `overtake_eligibility` — removing it costs +0.01855 brier, above the 0.00602 seed-to-seed noise floor and with an interval clear of zero
- `relative` — leave-one-out delta +0.00120 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `team_identity` — leave-one-out delta -0.00089 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `track_relative_wind` — leave-one-out delta -0.00110 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `tyre` — leave-one-out delta +0.00323 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own
- `weather` — leave-one-out delta -0.00231 is inside the 0.00602 noise floor and the group does not beat the minimal baseline on its own

## ACTIVATION

Seed-to-seed noise floor: `0.00239` brier.

| Group | n feat | Leave-one-out Δ | 95% interval | Add-one-in Δ | Verdict |
|---|---:|---:|---|---:|---|
| `attacker` | 3 | -0.00087 | [-0.00217, +0.00043] | -0.00248 | **UNINFORMATIVE** |
| `canonical_gap` | 1 | +0.01374 | [+0.01107, +0.01642] | +0.00000 | **KEEP** |
| `compound` | 1 | -0.00027 | [-0.00123, +0.00070] | -0.00417 | **UNINFORMATIVE** |
| `defender` | 3 | -0.00064 | [-0.00204, +0.00076] | -0.00434 | **UNINFORMATIVE** |
| `geometry` | 2 | +0.00061 | [-0.00038, +0.00161] | -0.00335 | **UNINFORMATIVE** |
| `identity` ⚠️ | 4 | -0.00307 | [-0.00560, -0.00053] | -0.00585 | **DROP** |
| `overtake_eligibility` | 4 | +0.01960 | [+0.01654, +0.02266] | +0.00204 | **KEEP** |
| `relative` | 1 | -0.00043 | [-0.00144, +0.00058] | +0.00033 | **UNINFORMATIVE** |
| `team_identity` ⚠️ | 2 | -0.00028 | [-0.00132, +0.00075] | -0.00189 | **UNINFORMATIVE** |
| `track_relative_wind` | 2 | -0.00013 | [-0.00122, +0.00096] | -0.00612 | **UNINFORMATIVE** |
| `tyre` | 6 | -0.00016 | [-0.00192, +0.00160] | -0.00552 | **UNINFORMATIVE** |
| `weather` | 4 | +0.00375 | [+0.00151, +0.00599] | -0.00295 | **KEEP** |

- `attacker` — leave-one-out delta -0.00087 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `canonical_gap` — removing it costs +0.01374 brier, above the 0.00239 seed-to-seed noise floor and with an interval clear of zero
- `compound` — leave-one-out delta -0.00027 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `defender` — leave-one-out delta -0.00064 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `geometry` — leave-one-out delta +0.00061 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `identity` — removing it *improves* brier by 0.00307; the group is costing accuracy, not adding it
- `overtake_eligibility` — removing it costs +0.01960 brier, above the 0.00239 seed-to-seed noise floor and with an interval clear of zero
- `relative` — leave-one-out delta -0.00043 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `team_identity` — leave-one-out delta -0.00028 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `track_relative_wind` — leave-one-out delta -0.00013 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `tyre` — leave-one-out delta -0.00016 is inside the 0.00239 noise floor and the group does not beat the minimal baseline on its own
- `weather` — removing it costs +0.00375 brier, above the 0.00239 seed-to-seed noise floor and with an interval clear of zero

## BRAKING

Seed-to-seed noise floor: `0.00255` brier.

| Group | n feat | Leave-one-out Δ | 95% interval | Add-one-in Δ | Verdict |
|---|---:|---:|---|---:|---|
| `attacker` | 3 | -0.00177 | [-0.00323, -0.00032] | -0.00351 | **UNINFORMATIVE** |
| `canonical_gap` | 1 | +0.01278 | [+0.01080, +0.01475] | +0.00000 | **KEEP** |
| `compound` | 1 | -0.00105 | [-0.00215, +0.00006] | -0.00506 | **UNINFORMATIVE** |
| `defender` | 3 | -0.00044 | [-0.00170, +0.00082] | -0.00474 | **UNINFORMATIVE** |
| `geometry` | 2 | -0.00085 | [-0.00198, +0.00028] | -0.00312 | **UNINFORMATIVE** |
| `identity` ⚠️ | 4 | -0.00392 | [-0.00793, +0.00008] | -0.00647 | **DROP** |
| `overtake_eligibility` | 5 | +0.02119 | [+0.01828, +0.02409] | -0.00401 | **KEEP** |
| `relative` | 1 | -0.00118 | [-0.00249, +0.00014] | +0.00047 | **UNINFORMATIVE** |
| `team_identity` ⚠️ | 2 | -0.00089 | [-0.00179, +0.00001] | -0.00327 | **UNINFORMATIVE** |
| `track_relative_wind` | 2 | -0.00107 | [-0.00239, +0.00025] | -0.00676 | **UNINFORMATIVE** |
| `tyre` | 6 | -0.00098 | [-0.00213, +0.00016] | -0.00684 | **UNINFORMATIVE** |
| `weather` | 4 | +0.00297 | [+0.00059, +0.00536] | -0.00350 | **KEEP** |

- `attacker` — leave-one-out delta -0.00177 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `canonical_gap` — removing it costs +0.01278 brier, above the 0.00255 seed-to-seed noise floor and with an interval clear of zero
- `compound` — leave-one-out delta -0.00105 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `defender` — leave-one-out delta -0.00044 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `geometry` — leave-one-out delta -0.00085 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `identity` — removing it *improves* brier by 0.00392; the group is costing accuracy, not adding it
- `overtake_eligibility` — removing it costs +0.02119 brier, above the 0.00255 seed-to-seed noise floor and with an interval clear of zero
- `relative` — leave-one-out delta -0.00118 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `team_identity` — leave-one-out delta -0.00089 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `track_relative_wind` — leave-one-out delta -0.00107 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `tyre` — leave-one-out delta -0.00098 is inside the 0.00255 noise floor and the group does not beat the minimal baseline on its own
- `weather` — removing it costs +0.00297 brier, above the 0.00255 seed-to-seed noise floor and with an interval clear of zero

## Reading the verdicts

- **KEEP** — removing it costs more than the noise floor, interval clear of zero.
- **DROP** — removing it *improves* the metric; the group is costing accuracy.
- **REDUNDANT** — no leave-one-out effect, but it beats the minimal baseline alone. The signal is real and also carried elsewhere, so it is safe to drop only while that other group stays.
- **UNINFORMATIVE** — no effect either way; the signal was never there.

⚠️ marks identity groups. Section 17: a large gain there is evidence of memorising drivers and teams rather than learning racecraft, and should be read against the racecraft groups before being kept.
