# TrackShift closure loop v2

This file is the authoritative, append-only closure log for branch
`rishabh/closure-loop-v2`. Generated data, models, plots, reports, artifacts,
and `data/processed` remain local evidence and must not be committed.

## Run identity

- Exact base commit: `03609f56691bd81005be654269bf18fb02ab4ad1`
- Base ref: fetched `origin/main`
- Base commit date: `2026-09-13T04:22:08+05:30`
- Branch: `rishabh/closure-loop-v2`
- Initial worktree: clean (`git status --short --branch` returned only the branch header)
- British Grand Prix policy: excluded from every training and calibration path; permitted only for final evaluation, replay, and demo.

## Initial evidence state

1. Historical C1/C7 spine is merged and smoke-tested for 2022 Australian GP and 2025 Japanese GP.
2. It supports only C1/C7 so far. It does not itself create historical C8, M06, C9, M08, or M07.
3. Historical availability is partial. Missing geometry and unusable source partitions remain explicit skips.
4. Historical DRS is an observed historical covariate only. It must never populate 2026 Overtake fields.
5. M07 v2 feature repair is merged, but trainable M07 rows still need causal C8 `battle_id` propagation and battle-level C9 assignments.
6. Tanveer CP-14 requires a valid battle-level split and the documented 2022-2024 train, 2025 validation, and non-British 2026 test policy.
7. C5 storage and calibration handling were repaired, but no calibrated C5 transition is accepted for final mode.
8. Current C5 blockers include no accepted physics rung, bound-pinning or worse-than-baseline fit if reproduced, unsafe segment sensitivity behavior, inherited uncertainty, and no public decision-time callback.
9. Rishabh CP-10 through CP-16 remain final-mode blocked until real C4 and C5 exist.
10. British GP must never enter training or calibration.

## Dependency graph

```text
Track A
A1 historical audit and smoke evidence
  -> historical C1/C7 scale only if both smoke runs pass
  -> inspect/materialise C8 -> M06 -> C9(battle_id) -> M08/M07
  -> A2 causal C8 battle_id join + persistent C9 repair
  -> A3 CP-14 benchmark (2022-2024 train / 2025 validation /
       non-British 2026 test)
  -> A4 CP-15 calibration and public C4 artifact

Track B
B1 full-scale CP-20 on year-partitioned store
  -> B2 CP-21 + CP-22 + CP-18b only if CP-20 is usable
  -> B3 public decision-time C5 callback only if every C5 gate passes

Final Chain V
A4 public calibrated C4 + B3 public calibrated C5 + non-stubbed C3/C10
  -> Rishabh CP-10 -> CP-11 -> CP-12 -> CP-13 -> CP-14 -> CP-15 -> CP-16
  -> RELEASE_READY
```

## Status table

Only the status values in this table are valid for this run.

| Item | Status | Evidence / condition |
|---|---|---|
| Run | IN_PROGRESS | Branch and exact fetched base established. |
| A1 historical spine validation and scale | IN_PROGRESS | Audit and both smoke reruns passed. The requested full-scale run was interrupted during 2025 for account handoff; 2022-2024 lake stages completed, but aggregate C1/C7 and the terminal manifest did not. |
| A2 M07-to-C8 `battle_id` and C9 repair | NOT_STARTED | Requires inspection/materialisation evidence from A1. |
| A3 Tanveer CP-14 benchmark | BLOCKED_EVIDENCE | Requires historical M07 for 2022-2024 and 2025, non-British 2026 M07, exact C8 `battle_id`, persistent battle-level C9, M07 v2 audit, and British exclusion evidence. |
| A4 Tanveer CP-15 calibration/public C4 | BLOCKED_EVIDENCE | Requires a genuine CP-14 pass. |
| B1 CP-20 full-scale revalidation | READY | Current year-partitioned store and 2026 rules must be used; absent controls remain absent. |
| B2 CP-21/CP-22/CP-18b downstream validation | BLOCKED_EVIDENCE | Runs only if CP-20 produces a usable fit. |
| B3 public C5 callback | BLOCKED_EVIDENCE | Runs only after all C5 gates pass. |
| Rishabh CP-10 through CP-16 final validation | BLOCKED_EVIDENCE | Development cores exist; real public C4 and C5 are required for final mode. |
| Terminal | IN_PROGRESS | Stop only at RELEASE_READY or HUMAN_BLOCKED under the terminal rules. |

## Current blocker and next unblocked action

- Track A blocker: the full historical C1/C7 scale run was interrupted before its aggregate manifest and C1/C7 stages completed; downstream C8/M06/C9/M08/M07 availability and causal join coverage are not yet measured.
- Track B blocker: the repaired full-scale CP-20 result has not yet been measured.
- Next unblocked action: rerun the same full-scale A1 command. Its run root has no completed aggregate manifest, so the freshness guard permits a deterministic restart. Do not treat the partial lake as an A1 pass.

## Evidence commands

```bash
git status --short --branch
git fetch origin main
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main
git log -1 --format='%H%n%ad%n%s' --date=iso-strict origin/main
git switch -c rishabh/closure-loop-v2 origin/main

.venv/bin/python scripts/data/materialize_historical_spine.py --audit-only
.venv/bin/python -m pytest -q
```

Exact outputs and local artifact paths are appended per iteration below.

## Append-only iteration log

### INIT — 2026-09-13

- Decision: start a finite two-track closure run from the exact fetched base; do not commit or push.
- `git status --short --branch` before branch creation: `## main...origin/main`; no changed paths.
- `git fetch origin main`: succeeded.
- `git rev-parse origin/main`: `03609f56691bd81005be654269bf18fb02ab4ad1`.
- `git rev-list --left-right --count HEAD...origin/main`: `0 0`.
- `git switch -c rishabh/closure-loop-v2 origin/main`: succeeded and set tracking to `origin/main`.
- Source changes: none.
- Next: A1 historical audit.

### A1 — audit, smoke validation, and interrupted scale run — 2026-09-13

- Base for every command: `03609f56691bd81005be654269bf18fb02ab4ad1`.
- Audit command: `.venv/bin/python scripts/data/materialize_historical_spine.py --audit-only`.
- Audit exit: `0`, status `AUDIT_ONLY`.
- Audit result: 55 planned/buildable units, 58 explicit skips, 55,994 telemetry laps discovered.
- Audit skips: `NO_GEOMETRY_CONFIG=54`, `EXCLUDED_EVENT=4`; no declared-season mismatch override was used.
- Audit coverage by year: 2022 = 12 units / 12,579 laps; 2023 = 13 / 13,577; 2024 = 15 / 15,084; 2025 = 15 / 14,754.
- Audit artifact: `artifacts/schema_audit/historical_spine_audit.json` (generated local artifact; do not stage).
- 2022 smoke command: `.venv/bin/python scripts/data/materialize_historical_spine.py --years 2022 --events "Australian Grand Prix" --sessions Race --drivers VER LEC PER --max-laps-per-driver 30 --run-root /tmp/trackshift-a1-2022-vLTgaF/run --lake-root /tmp/trackshift-a1-2022-vLTgaF/telemetry_20m --segments-root /tmp/trackshift-a1-2022-vLTgaF/segments`.
- 2022 smoke exit/result: `0`, `COMPLETE`; 84 accepted / 6 rejected laps; 21,896 lake rows; 2,604 C1 rows; 2,604 C7 rows; 1,116 eligible rows.
- 2022 smoke manifest: `/tmp/trackshift-a1-2022-vLTgaF/run/run_manifest.json`.
- 2025 smoke command: `.venv/bin/python scripts/data/materialize_historical_spine.py --years 2025 --events "Japanese Grand Prix" --sessions Race --drivers VER NOR PIA --max-laps-per-driver 30 --run-root /tmp/trackshift-a1-2025-NwvpSv/run --lake-root /tmp/trackshift-a1-2025-NwvpSv/telemetry_20m --segments-root /tmp/trackshift-a1-2025-NwvpSv/segments`.
- 2025 smoke exit/result: `0`, `COMPLETE`; 84 accepted / 6 rejected laps; 24,202 lake rows; 2,688 C1 rows; 2,688 C7 rows; 2,304 eligible rows.
- 2025 smoke manifest: `/tmp/trackshift-a1-2025-NwvpSv/run/run_manifest.json`.
- Scale command: `.venv/bin/python scripts/data/materialize_historical_spine.py --years 2022,2023,2024,2025 --run-root data/processed/historical_spine --jobs 8`.
- Scale decision: the command was interrupted with `Ctrl-C` after the user requested an account handoff. It did not emit a terminal aggregate manifest and must not be recorded as PASS.
- Partial local scale state at interruption: telemetry lake partitions 2022 = 12, 2023 = 13, 2024 = 15, 2025 = 1. Per-year lake snapshots exist for 2022-2024 under `data/processed/historical_spine/lake/`; aggregate C1/C7 evidence is incomplete.
- Focused tests: not run after the scale command because the user interrupted for handoff.
- Full test suite: not run in this iteration for the same reason. The prior report's 14 simdata failures remain baseline history, not a current result.
- Source inspection decision: current M07 constructs `OpportunityContext(..., battle_id=None)` and the current pass trainer does not consume a persistent C9 assignment artifact. A2 must implement an exact causal C8 join and fail-closed battle-level C9 consumption before CP-14 can run.
- Tracked changes: `runbooks/CLOSURE_LOOP.md` only.
- Generated local outputs are ignored and must not be staged.
- Next: restart the exact scale command, wait for the aggregate manifest, then run focused historical tests and `.venv/bin/python -m pytest -q` before moving to A2 or B1.

## Terminal status

`IN_PROGRESS`

Release readiness has not been claimed. Human blocking has not yet been proven on both tracks.
