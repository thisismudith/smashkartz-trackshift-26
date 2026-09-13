/**
 * Types for what `src/trackshift/serve/app.py` ACTUALLY returns today.
 *
 * This is deliberately a second, separate module from `../api-contract/types.ts`.
 * That one mirrors API.md — the contract the backend is aiming at. This one mirrors
 * the running synthetic development service, and the two do not agree yet:
 *
 *   API.md 5.5 timeline      real service
 *   -------------------      ------------
 *   `steps: [...]`           `segments: [...]`
 *   per-step `race_control`  absent
 *   per-step `era`           absent
 *   per-step `pass`          absent
 *   per-step `eligibility`   absent
 *   (n/a)                    top-level `causal_cutoff`
 *
 * Typing the aspiration and then rendering the reality is how a UI ends up printing a
 * zero where a field does not exist. So the rendering path is typed against the wire,
 * every optional block is `| undefined`, and anything the service omits is rendered by
 * `unavailable()` with the reason "not returned by this API version" — never as 0.
 *
 * Values are widened on purpose (`unknown` at the boundaries, `number | null` inside
 * quantities): the service is synthetic and under active development, so a field can
 * change shape between commits. `guards.ts` narrows at runtime and reports a visible
 * error rather than letting a shape change reach the DOM as `NaN` or `[object Object]`.
 */

/**
 * API.md 3.1 names five tags. The rule engine additionally emits its own `value_source`
 * vocabulary for where a *rule number* came from (`RULE_FIA`, `DERIVED_TELEMETRY`,
 * `UNVERIFIED`), and `app.py` puts those straight into the `provenance` field of
 * `/track` lines. Both vocabularies therefore arrive on the same key, so both are
 * modelled here and `provenance.ts` decides how each is badged.
 */
export type CoreProvenance = "OBSERVED" | "DERIVED" | "INFERRED" | "SIMULATED" | "RULE";
export type RuleValueSource = "RULE_FIA" | "DERIVED_TELEMETRY" | "UNVERIFIED" | "UNRESOLVED";
export type Provenance = CoreProvenance | RuleValueSource;

/** A number with provenance. `value: null` means missing and must carry a `reason`. */
export interface Quantity {
  value: number | null;
  provenance?: Provenance;
  unit?: string;
  reason?: string;
}

export interface Versions {
  api_version: string;
  git_commit: string;
  models: Record<string, string>;
}

/** GET /meta */
export interface MetaResponse extends Versions {
  mode: string;
  stubs: string[];
  /** Present (and non-empty) when the backend is serving the synthetic fixture. */
  synthetic_fixture?: string;
  /** False until official rules and accepted C4/C5 callbacks exist. */
  release_ready?: boolean;
}

/** GET /battles — one entry per battle. `battle_id` is the ONLY source of a battle id. */
export interface BattleSummary {
  battle_id: string;
  year?: number;
  event?: string;
  session?: string;
  attacker?: string;
  defender?: string;
  provenance?: Provenance;
}

export interface BattlesResponse {
  battles: BattleSummary[];
  versions?: Versions;
  /** Set when the list is empty because no episode data is built, not because none exist. */
  reason?: string;
}

/** The rival-state distribution (C10). `p` keys vary — states can be merged per API.md 25. */
export interface RivalState {
  p?: Record<string, number>;
  merged?: string[];
  merged_states?: string[];
  model_version?: string;
  provenance?: Provenance;
  split_version?: string;
  causal_cutoff?: number;
  uncertainty?: { entropy?: number; provenance?: Provenance };
}

/** GET /battles/{id}/timeline — note `segments`, not API.md's `steps`. */
export interface TimelineSegment {
  segment_id: number;
  gap_s?: Quantity;
  energy_mj?: Quantity;
  rival_state?: RivalState;
  provenance?: Provenance;
}

export interface TimelineResponse {
  battle_id: string;
  segments: TimelineSegment[];
  causal_cutoff?: { segment_index?: number; provenance?: Provenance };
  versions?: Versions;
}

/** A rule number carries its own citation. `value_source` is the rule-engine vocabulary. */
export interface RuleValue {
  value: number | null;
  value_source?: RuleValueSource;
  source?: string;
  note?: string;
  landmark?: string;
}

export interface OvertakeZone {
  zone: string | number;
  name?: string;
  detection_line_m?: RuleValue;
  activation_line_m?: RuleValue;
  zone_end_m?: RuleValue;
}

export interface EnvelopeCurveSpec {
  breakpoints_kmh: number[];
  max_power_kw: number[];
  source?: string;
  note?: string;
  value_source?: RuleValueSource;
}

export interface RulesResponse {
  event: string;
  year?: number;
  config_version?: string;
  overtake?: { enabled?: boolean; detection_gap_s?: RuleValue; zones?: OvertakeZone[] };
  power_envelope?: {
    normal?: EnvelopeCurveSpec;
    override?: EnvelopeCurveSpec;
    separation_speed_kmh?: RuleValue;
    competition_adjustments?: unknown[];
  };
  energy?: Record<string, RuleValue | unknown>;
  regulation_snapshot?: {
    effective_date?: string;
    retrieved_at?: string;
    encoded_configuration_version?: string;
    section_issues?: Record<string, number>;
    source_documents?: Array<Record<string, unknown>>;
    note?: string;
  };
  strict_mode?: boolean;
  versions?: Versions;
}

/** GET /track/{event}. `lines` carry the rule-engine `value_source` in `provenance`. */
export interface TrackLine {
  kind: "DETECTION" | "ACTIVATION" | string;
  zone?: string | number;
  distance_m: number | null;
  provenance?: Provenance;
  source?: string;
}

export interface TrackResponse {
  event: string;
  track?: string;
  lap_length_m?: Quantity;
  centreline?: Array<{ distance_m: number; x_m: number; y_m: number; provenance?: Provenance }>;
  segments?: Array<Record<string, unknown>>;
  lines?: TrackLine[];
  provenance?: Provenance;
  versions?: Versions;
}

/** GET /rules/{event}/power_envelope — sampled by the one C3 evaluator, not by us. */
export interface PowerEnvelopeResponse {
  event: string;
  curves: Record<string, Array<{ speed_kmh: number; max_power_kw: number; provenance?: Provenance }>>;
  provenance?: Provenance;
  rule_configuration_version?: string;
  versions?: Versions;
}

/**
 * An action as the rule engine returns it. `cap_kw` is the speed-dependent regulatory
 * maximum at this state, not a constant — see API.md 3.10 / 20.1.
 */
export interface PlanAction {
  deploy_level: number;
  lift_amount: number;
  cap_kw?: number | null;
  applicable_mode?: string;
  delivered_power_kw?: number | null;
  delta_e_mj?: number | null;
  label?: string;
  /** Only `oracle_rival_state` sets these: an upper bound, never a deployable policy. */
  deployable?: boolean;
  upper_bound_only?: boolean;
}

export interface PlanAlternative {
  action: PlanAction;
  expected_value: number | null;
  regret: number | null;
}

/** An action the rule engine removed, with the rule that removed it. */
export interface ExcludedAction {
  action?: PlanAction;
  rule?: string;
  source?: string;
  reason?: string;
}

export interface PlanResponse {
  status?: string;
  reason?: string | null;
  recommended_action?: PlanAction;
  legal_actions?: PlanAction[];
  excluded_actions?: ExcludedAction[];
  alternatives?: PlanAlternative[];
  decision?: {
    dominant_mechanism?: string;
    primary_constraint?: string;
    decision_stability?: number | null;
    alternatives?: PlanAlternative[];
  };
  expected_value?: number | null;
  /** API.md 5.13: must always be 0 for /plan; reported so the UI can assert it. */
  rule_violations?: number;
  rule_configuration_version?: string;
  input_provenance?: Record<string, Provenance>;
  model_versions?: Record<string, string>;
  schema_version?: string;
  provenance?: Provenance;
  risk?: { criterion?: string; cvar_alpha?: number };
  baselines?: {
    names?: string[];
    baselines?: Record<string, PlanAction[]>;
    rule_violations?: number;
    status?: string;
    oracle_note?: string;
    provenance?: Provenance;
  };
  versions?: Versions;
}

/** GET /simulate/policies — `description` is shown verbatim (API.md 56). */
export interface SimPolicy {
  name: string;
  description?: string;
  parameters?: Record<string, unknown>;
}

export interface PoliciesResponse {
  policies: SimPolicy[];
  provenance?: Provenance;
  model_version?: string;
  schema_version?: string;
  versions?: Versions;
}

export interface SimulateResponse {
  status?: string;
  summary?: {
    p_ahead_at_horizon?: number;
    mean_final_energy_mj?: number;
    rule_violations?: number;
    n_episodes?: number;
    seed?: number;
    cvar_p_ahead?: number;
  };
  episodes?: Array<Record<string, unknown>>;
  /** Mandatory on screen (API.md 8 / 56). */
  assumptions?: string[];
  provenance?: Provenance;
  schema_version?: string;
  model_versions?: Record<string, string>;
  versions?: Versions;
}

/** POST /pass/predict */
export interface PassPredictResponse {
  p_pass_by_outcome_horizon?: Quantity;
  checkpoint?: string;
  /** "synthetic-development" here means exactly that, and must be shown. */
  calibration?: string;
  outcome_horizon?: string;
  versions?: Versions;
}

/** GET /validation */
export interface ValidationResponse extends Partial<Versions> {
  rival?: Record<string, unknown>;
  value?: Record<string, unknown>;
  planner?: Record<string, unknown>;
  simulator?: Record<string, unknown>;
  release_ready?: boolean;
  reason?: string;
  era_evaluation?: EraEvaluation;
}

/**
 * The era report. These three flags are the ones the UI is obliged to surface —
 * they are the difference between "a benchmark ran" and "the checkpoint passed".
 */
export interface EraEvaluation {
  status?: string;
  selected?: string;
  strategies?: string[];
  metrics?: Record<string, EraStrategyMetrics>;
  split_version?: string;
  rule_configuration_version?: string;
  schema_version?: string;
  provenance?: Provenance;
  reason?: string | null;
  synthetic_fixture?: string;
  /** False while no real 2022-25 lake is joined: historical validation is NOT complete. */
  real_historical_evidence?: boolean;
  /** Must stay false — British GP is held out from training/calibration. */
  british_gp_used_for_training_or_calibration?: boolean;
  historical_drs_is_not_2026_overtake?: boolean;
  held_out_2026_n?: number;
  historical_n?: number;
  cp08_next_blocker?: string | null;
}

export interface EraStrategyMetrics {
  n?: number;
  held_out_evidence_n?: number;
  status?: string;
  calibration?: string;
  reason?: string | null;
  mean_log_likelihood?: number;
  mean_nll?: number;
  year_coverage?: number[];
  event_coverage?: string[];
  fold_ids?: string[];
  model_versions?: string[];
  rule_configuration_versions?: string[];
  stability?: Record<string, number>;
}

/** The error body FastAPI produces for our HTTPException detail dicts. */
export interface ApiErrorDetail {
  detail?: { code?: string; message?: string } | string;
}
