/**
 * THE data-source seam. Everything the simulator needs from the Python side comes
 * through here and nowhere else.
 *
 * Today every method reads a static artifact that `scripts/build_sim_data.py` wrote
 * ahead of time. The Python functions behind those artifacts (see scripts/simdata/,
 * especially twin.py) are pure input->output, so when the HTTP API lands it calls the
 * SAME functions per request and only this file changes: swap StaticSimSource for
 * ApiSimSource and the renderer, the worker and every panel carry on unchanged.
 *
 * The rule this enforces: the browser never computes simulation or physics. It asks
 * for results and draws them. Any new derived quantity (shadow price, pass
 * probability, rival belief) arrives as another field on these responses.
 */

import type { RawSessionManifest, RawTrackModel } from "./manifest";
import type { FittedParams } from "../engine/params";

/** Per-lap energy estimate produced by the Python twin. Every field is INFERRED or
 * SIMULATED -- the public feed carries no battery, MGU-K power or fuel mass. */
export interface LapEnergy {
  peakWheelPowerKw: number;
  peakBrakingKw: number;
  rawMaxWheelPowerKw: number;
  ersEnergyUsedMj: number;
  ersEnergyHarvestedMj: number;
  energyBalanceMj: number;
  socEndMj: number;
  socUncertaintyMj: number;
  envelopeCapViolations: number;
  /** Populated when the twin cannot satisfy a basic energy check. Shown, not hidden. */
  warnings: string[];
}

export interface SessionRef {
  trackSlug: string;
  session: string;
}

export interface SimIndex {
  catalogue: string;
  params: string;
  /** Absent from artifacts built before the rule engine landed. */
  rules?: string;
  tracks: Record<string, string>;
  sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
}

/** One regulation quantity as scripts/simdata/rules.py holds it. `verified` is the
 * important field: every 2026 energy figure is currently an unsourced placeholder, and
 * the UI must say so rather than presenting a threshold as regulation fact. */
export interface RuleQuantity {
  value_mj: number;
  accounting_window: string;
  source: string;
  citation: string;
  note: string;
  verified: boolean;
}

/** One mode's piecewise-linear cap. The breakpoints ARE the polyline vertices -- a chart
 * draws straight segments between them and reproduces the curve exactly. Evaluating the cap
 * at an arbitrary speed is a different thing and belongs to rules.py's
 * max_electrical_power_kw, never to the browser (AGENTS.md section 32). */
export interface RuleEnvelope {
  breakpoints_kmh: number[];
  max_power_kw: number[];
  source: string;
  citation: string;
  derivation: string;
  verified: boolean;
}

/** One row of the sampled envelope table: the cap at one speed, in one mode. */
export interface RuleCurveSample {
  speed_kmh: number;
  max_power_kw: number;
}

/** `max_electrical_power_kw` evaluated on a declared speed grid, both modes, by the Python
 * that owns the curve (AGENTS.md section 32, API.md section 5.3a). This is how the UI reads a
 * cap at a speed: BY LOOKUP. Interpolating between these rows in TypeScript would be the second
 * implementation of the curve that section 32 forbids, and the number on screen would no longer
 * be provably the number the optimiser saw.
 *
 * The grid is every multiple of `step_kmh` from 0 to the highest breakpoint PLUS every
 * breakpoint of every mode, and it is shared by both modes -- so the two caps at one speed are
 * comparable by index, and no sampled segment hides the cliff. */
export interface SampledCurves {
  step_kmh: number;
  /** Above this speed the two modes differ; at or below it override confers nothing
   * (section 20.2). null means the curves never differ -- not zero. */
  separation_speed_kmh: number | null;
  curves: Record<string, RuleCurveSample[]>;
  provenance: string;
  verified: boolean;
  note: string;
}

export interface RuleSet {
  schema_version: number;
  provenance: string;
  regulation_snapshot: {
    season: number;
    source_documents: string[];
    retrieved_at: string | null;
    verified: boolean;
    note: string;
  };
  competition: {
    season: number;
    event: string;
    session_type: string;
    configuration_version: string;
  };
  power_envelope: Record<string, RuleEnvelope>;
  /** Absent from artifacts built before the sampled table landed; a consumer must then say
   * it has no table rather than interpolating the breakpoints itself. */
  sampled_curves?: SampledCurves;
  energy_budget: {
    ers_store_capacity: RuleQuantity;
    deploy_budget: RuleQuantity;
    harvest_budget: RuleQuantity;
  };
  compliance: {
    provenance: string;
    models_speed_dependent_envelope: boolean;
    legal_by_construction: boolean;
    all_values_verified: boolean;
    unverified_keys: string[];
    statement: string;
    configuration_version: string;
    schema_version: number;
  };
}

export interface SimSource {
  /** What sessions exist and where their pieces live. */
  index(): Promise<SimIndex>;
  catalogue<T>(): Promise<T>;
  /** Regulation limits, owned by scripts/simdata/rules.py. null when the built
   * artifacts predate the rule engine -- the UI then shows no thresholds at all rather
   * than inventing them. */
  rules(): Promise<RuleSet | null>;
  /** Fitted lap-model parameters from scripts/simdata/fit_params.py. Every leaf carries
   * se / ci95 / n, and the UI is expected to SHOW them: these are estimates with spread,
   * not settings. */
  params(): Promise<FittedParams>;
  track(slug: string): Promise<RawTrackModel>;
  sessionManifest(ref: SessionRef): Promise<RawSessionManifest>;
  /** URLs handed to the worker, which streams the binary itself. */
  sessionUrls(ref: SessionRef): Promise<{ trackUrl: string; manifestUrl: string; binUrl: string }>;
}

/** Reads the artifacts under /sim that the Python build wrote. */
export class StaticSimSource implements SimSource {
  private indexPromise: Promise<SimIndex> | null = null;

  constructor(private base = "/sim") {}

  index(): Promise<SimIndex> {
    if (!this.indexPromise) {
      this.indexPromise = (async () => {
        const pointer = await fetch(`${this.base}/index.json`).then((r) => r.json());
        return fetch(`${this.base}/${pointer.latest}`).then((r) => r.json());
      })();
    }
    return this.indexPromise;
  }

  async catalogue<T>(): Promise<T> {
    const idx = await this.index();
    return fetch(`${this.base}/${idx.catalogue}`).then((r) => r.json());
  }

  async rules(): Promise<RuleSet | null> {
    const idx = await this.index();
    if (!idx.rules) return null;
    return fetch(`${this.base}/${idx.rules}`).then((r) => r.json());
  }

  async params(): Promise<FittedParams> {
    const idx = await this.index();
    return fetch(`${this.base}/${idx.params}`).then((r) => r.json());
  }

  async track(slug: string): Promise<RawTrackModel> {
    const idx = await this.index();
    const file = idx.tracks[slug];
    if (!file) throw new Error(`no track artifact for ${slug}`);
    return fetch(`${this.base}/${file}`).then((r) => r.json());
  }

  async sessionManifest(ref: SessionRef): Promise<RawSessionManifest> {
    const { manifestUrl } = await this.sessionUrls(ref);
    return fetch(manifestUrl).then((r) => r.json());
  }

  async sessionUrls(ref: SessionRef) {
    const idx = await this.index();
    const files = idx.sessions[ref.trackSlug]?.[ref.session];
    const trackFile = idx.tracks[ref.trackSlug];
    if (!files || !trackFile) {
      throw new Error(`no built data for ${ref.trackSlug}/${ref.session}`);
    }
    return {
      trackUrl: `${this.base}/${trackFile}`,
      manifestUrl: `${this.base}/${files.manifest}`,
      binUrl: `${this.base}/${files.bin}`,
    };
  }
}

/**
 * Planned replacement once the service exists. Left unimplemented on purpose rather
 * than stubbed with fake data: a stub that silently returns something plausible is
 * exactly the hidden fallback the project contract forbids.
 *
 *   export class ApiSimSource implements SimSource { ... fetch(`${base}/api/...`) }
 *
 * Because the Python is already pure functions, the service is a thin layer over
 * scripts/simdata/*; nothing in the maths moves.
 */

export const defaultSimSource: SimSource = new StaticSimSource();
