/**
 * TypeScript mirror of API.md — the TrackShift model backend's public contract.
 * Not implemented anywhere yet (no src/trackshift/serve/, no /api/v1 service).
 * This module only types the shapes so a future fetch layer can be written
 * against something concrete; it is not wired into any page.
 */

export type Provenance = "OBSERVED" | "DERIVED" | "INFERRED" | "SIMULATED" | "RULE";

export interface Quantity {
  value: number | null;
  provenance: Provenance;
  unit?: string;
  reason?: string;
}

export interface Uncertain {
  mean: number;
  low: number;
  high: number;
  draws?: number[];
  provenance: Provenance;
  unit?: string;
}

export interface StateRef {
  year: number;
  event: string;
  session: string;
  lap: number;
  segment_id: number;
  distance_m: number;
  lap_fraction: number;
}

export interface Action {
  deploy_level: 0 | 0.25 | 0.5 | 0.75 | 1.0;
  lift_amount: number;
  label?: string;
}

export interface Versions {
  api_version: string;
  git_commit: string;
  models: Record<string, string>;
}

export type DecisionCheckpoint = "DETECTION" | "ACTIVATION" | "BRAKING";

export type PitState = "ON_TRACK" | "PIT_IN" | "PIT_LANE" | "PIT_OUT" | "UNKNOWN";
export type RaceControlState = "GREEN" | "YELLOW" | "DOUBLE_YELLOW" | "VSC" | "SC" | "RED" | "UNKNOWN";

export interface RaceControl {
  pit_state: PitState;
  race_control_state: RaceControlState;
  safety_car_active: boolean;
  virtual_safety_car_active: boolean;
  race_control_transition_flag: boolean;
  pit_transition_flag: boolean;
  green_flag_elapsed_s: number;
  normal_race_model_eligible: boolean;
  provenance: Provenance;
}

export interface PowerEnvelopeCurve {
  breakpoints_kmh: number[];
  max_power_kw: number[];
}

export interface PowerEnvelope {
  normal: PowerEnvelopeCurve;
  override: PowerEnvelopeCurve;
  separation_speed_kmh: number;
  provenance: Provenance;
  verified: boolean;
  source: string;
}

export interface ErsState {
  ers_soc_est_mj: Uncertain;
  ers_store_capacity_mj: Quantity;
  ers_deploy_budget_remaining_est_mj: Uncertain;
  ers_harvest_budget_remaining_est_mj: Uncertain;
  ers_energy_used_est_mj: Uncertain;
  ers_energy_harvested_est_mj: Uncertain;
  ers_deploy_power_est_kw: Uncertain;
  ers_harvest_power_est_kw: Uncertain;
  cap_kw: Quantity;
  applicable_mode: "NORMAL" | "OVERRIDE";
  headroom_kw: Quantity;
  ers_mode_inferred: "NORMAL" | "OVERRIDE" | "UNKNOWN";
  override_active_inferred: Quantity | null;
  discriminable: boolean;
  envelope_violation: boolean;
}

export interface RegulationEra {
  era: "2022" | "2023" | "2024" | "2025" | "2026";
  historical_drs_eligible: boolean | null;
  historical_drs_open: boolean | null;
  overtake_eligible: boolean | null;
  overtake_state: "NOT_ARMED" | "ARMED" | "ACTIVE" | "DISABLED" | null;
}

export interface ApiErrorBody {
  error: { code: string; message: string; detail?: Record<string, unknown> };
}

// ---- GET /track/{event} ----

export interface TrackCentrelinePoint {
  distance_m: number;
  x_m: number;
  y_m: number;
}

export interface TrackSegment {
  segment_id: number;
  start_distance_m: number;
  end_distance_m: number;
  segment_length_m: number;
  kind: "STRAIGHT" | "BRAKING" | "CORNER" | "EXIT";
  sector: number;
  zone: number | null;
  corner_id: number | null;
  corner_type: string | null;
  corner_phase: string | null;
  track_heading_deg: number;
  brake_onset_m: number | null;
  mean_gradient: number;
  geometry_version: string;
  provenance: Provenance;
}

export interface TrackLine {
  kind: "DETECTION" | "ACTIVATION";
  zone: number;
  distance_m: number;
  provenance: Provenance;
  source: string;
}

export interface TrackZone {
  zone: number;
  name: string;
  start_distance_m: number;
  end_distance_m: number;
}

export interface TrackResponse {
  event: string;
  track: string;
  lap_length_m: number;
  centreline: TrackCentrelinePoint[];
  segments: TrackSegment[];
  lines: TrackLine[];
  zones: TrackZone[];
  versions: Versions;
}

// ---- GET /rules/{event} ----

export interface RulesResponse {
  event: string;
  year: number;
  config_version: string;
  regulation_snapshot: {
    section_issues: string[];
    effective_for_event: string;
    source_documents: string[];
    retrieved_at: string;
  };
  overtake: {
    enabled: boolean;
    detection_gap_s: Quantity & { source: string };
    zones: { zone: number; detection_line_m: number; activation_line_m: number }[];
  };
  power_envelope: PowerEnvelope & { competition_adjustments: unknown[] };
  energy: {
    deploy_limit_per_lap_mj: Quantity & { source: string };
    harvest_limit_per_lap_mj: Quantity & { source: string };
    store_capacity_mj: Quantity & { source: string };
    accounting_window: "lap";
  };
  race_control: { overtake_disabled: boolean };
  unverified_keys: string[];
  versions: Versions;
}

// ---- GET /battles ----

export interface BattleSummary {
  battle_id: string;
  year: number;
  event: string;
  session: string;
  attacker: string;
  defender: string;
  attacker_team: string;
  defender_team: string;
  start_lap: number;
  end_lap: number;
  duration_segments: number;
  duration_s: number;
  minimum_distance_gap_m: number;
  minimum_time_gap_s: number | null;
  maximum_closing_rate_mps: number;
  detection_opportunities: number;
  pass_attempted: boolean;
  pass_completed: boolean;
  bounded_by: "PASS" | "PAIR_SWITCH" | "RACE_CONTROL_TRANSITION" | "PIT_TRANSITION" | "SESSION_END";
  normal_race_only: boolean;
  provenance: Provenance;
}

export interface BattlesResponse {
  battles: BattleSummary[];
  versions: Versions;
}

// ---- GET /battles/{battle_id}/timeline ----

export interface TimelineCarBlock {
  speed_kmh: Quantity;
  throttle_pct: Quantity;
  brake_on: boolean;
  braking_intensity_proxy: Quantity;
  tyre: { compound: string; life_laps: number; stint: number; degradation_proxy: Quantity };
  ers: ErsState;
  fuel_kg: Uncertain;
}

export interface BaselineResidualBlock {
  segment_time_vs_driver_s: Quantity & { n: number };
  segment_time_vs_team_s: Quantity & { n: number };
  segment_time_vs_field_s: Quantity & { n: number };
  exit_speed_vs_driver_kmh: Quantity & { n: number };
}

export interface EligibilityBlock {
  zone: number;
  armed: boolean;
  p_eligible: Quantity;
  eligibility_margin_s: Quantity;
  projected_gap_at_detection_s: Uncertain;
  energy_required_to_unlock_kj: Uncertain;
  eligibility_fragility_per_kj: Quantity;
}

export interface PassCheckpointResult {
  reached: boolean;
  feature_cutoff_distance_m: number;
  p_pass_by_outcome_horizon: number | null;
  ensemble_spread: number | null;
  model_version: string;
}

export interface PassBlock {
  is_opportunity: boolean;
  opportunity_id: string;
  outcome_horizon: string;
  checkpoints: Record<DecisionCheckpoint, PassCheckpointResult>;
  provenance: Provenance;
}

export interface RivalStateBlock {
  p: Partial<Record<"CONSERVING" | "BALANCED" | "DEPLOYING" | "DERATING", number>>;
  merged: string[];
  provenance: Provenance;
  model_version: string;
}

export interface TimelineStep {
  ref: StateRef;
  session_time_s: number;
  race_context: { label: string; provenance: Provenance };
  race_control: RaceControl;
  era: RegulationEra;
  geometry: {
    sector: number;
    zone: number | null;
    corner_id: number | null;
    corner_type: string | null;
    corner_phase: string | null;
    track_heading_deg: number;
  };
  weather: {
    wind_head_component_mps: Quantity;
    wind_cross_component_mps: Quantity;
    air_density_proxy: Quantity;
    track_temperature: Quantity;
    wet_track_flag: boolean;
  };
  time_gap_s: Quantity;
  distance_gap_m: Quantity;
  closing_rate_mps: Quantity;
  relative_speed_to_ahead_mps: Quantity;
  relative_acceleration_to_ahead_mps2: Quantity;
  gap_rate_ahead_s_per_s: Quantity;
  delta_speed_kmh: Quantity;
  delta_segment_time_s: Quantity;
  tyre_age_delta_laps: Quantity;
  compound_pair: string;
  baseline_residuals: {
    attacker: BaselineResidualBlock;
    defender: BaselineResidualBlock;
    baseline_valid: boolean;
  } | null;
  attacker: TimelineCarBlock | null;
  defender: TimelineCarBlock | null;
  rival_state: RivalStateBlock | null;
  eligibility: EligibilityBlock | null;
  pass: PassBlock | null;
  shadow_price_s_per_kj: Quantity | null;
  recommended_action: Action | null;
  legal_actions: Action[] | null;
  live_safe: boolean;
}

export interface TimelineResponse {
  battle_id: string;
  attacker: string;
  defender: string;
  steps: TimelineStep[];
  versions: Versions;
}

// ---- GET /value/{event}/shadow_price ----

export interface ShadowPriceResponse {
  event: string;
  energy_kj: number;
  gap_s: number;
  eligibility: "NOT_ARMED" | "ARMED";
  profile: { segment_id: number; lambda_s_per_kj: number; note?: string }[];
  spikes: { segment_id: number; cause: string; zone: number }[];
  provenance: Provenance;
  model_version: string;
  grid: { energy_kj: number[]; gap_s: number[] };
  versions: Versions;
}

// ---- POST /plan ----

export interface PlanStep {
  segment_id: number;
  action: Action;
  expected_gap_s: number;
  expected_energy_kj: number;
  lambda_s_per_kj: number;
}

export interface PlanBaseline {
  name: string;
  p_ahead_at_horizon: number;
  final_energy_kj: number;
  rule_violations: number;
}

export interface PlanResponse {
  plan: PlanStep[];
  p_ahead_at_horizon: Uncertain;
  p_pass_now: Quantity;
  p_repass_within_horizon: Quantity;
  final_energy_kj: Uncertain;
  cvar_p_ahead: number;
  rule_violations: number;
  latency_ms: number;
  decision: {
    dominant_mechanism: string;
    primary_constraint: string;
    decision_stability: number;
    alternatives: { action: Action; expected_value: number; regret: number }[];
  };
  baselines?: PlanBaseline[];
  model_version: string;
  versions: Versions;
}

// ---- POST /simulate ----

export interface SimulateEpisode {
  episode: number;
  outcome: "AHEAD" | "BEHIND";
  pass_lap: number | null;
  pass_segment_id: number | null;
  repassed: boolean;
  trace: { segment_id: number; gap_s: number; energy_kj: number; action: Action }[];
}

export interface SimulateResponse {
  summary: {
    p_ahead_at_horizon: number;
    mean_final_energy_kj: number;
    rule_violations: number;
    cvar_p_ahead: number;
    n_episodes: number;
    seed: number;
  };
  episodes: SimulateEpisode[];
  assumptions: string[];
  provenance: Provenance;
  model_version: string;
  versions: Versions;
}

export interface SimulatePolicy {
  name: string;
  description: string;
  parameters: Record<string, number | string>;
}

export interface SimulatePoliciesResponse {
  policies: SimulatePolicy[];
  provenance: Provenance;
  model_version: string;
  versions: Versions;
}

// ---- Request bodies the UI can already assemble, ahead of the service existing ----

export interface ShadowPriceQuery {
  energy_kj: number;
  time_gap_s: number;
  eligibility: "NOT_ARMED" | "ARMED";
}

export interface PlanRequestBody {
  state: {
    ref: StateRef;
    energy: { energy_kj: number; deployed_kj: number; harvested_kj: number };
    tyre: { compound: string; life_laps: number };
    gap: {
      time_gap_s: number;
      distance_gap_m: number | null;
      relative_speed_mps: number | null;
      relative_acceleration_mps2: number | null;
      gap_rate_s_per_s: number | null;
    };
    overtake_state: "NOT_ARMED" | "ARMED" | "ACTIVE" | "DISABLED";
    race_control: { overtake_disabled: boolean };
    power_envelope: { regime: "NORMAL" | "OVERRIDE" };
  };
  rival_state?: { p: Partial<Record<"CONSERVING" | "BALANCED" | "DEPLOYING" | "DERATING", number>> };
  horizon_laps: number;
  include_baselines?: boolean;
  risk?: { cvar_alpha: number };
}

export interface ApiCallResult<T> {
  ok: boolean;
  data?: T;
  error?: string;
  status?: number;
  request: { method: "GET" | "POST"; url: string; body?: unknown };
}
