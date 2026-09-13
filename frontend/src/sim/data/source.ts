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
import type { FittedParams, Leaf } from "../engine/params";

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
  /** Absent from artifacts built before the rivalry exporter landed. Same treatment as
   * `rules` above: a consumer must then say it has no measured conversion rate, never
   * fall back to zero -- a circuit at 0% and a circuit that was never measured are
   * different claims, and only one of them is in the data. */
  rivalries?: string;
  /** Absent from artifacts built before scripts/features/export_rivalry_showcase.py.
   * Same treatment again: with no artifact the sim offers no showcase rather than
   * assembling one in the browser, which would be the UI scoring the model against
   * the telemetry -- exactly the model logic AGENTS.md keeps out of here. */
  rivalryShowcase?: string;
  /** The season these artifacts were built from, e.g. "2026". Written by
   * scripts/build_sim_data.py --year. Artifact filenames carry the circuit slug but no
   * year, so one build directory holds exactly one season; this states which.
   * Absent from artifacts built before the build became year-selectable. */
  year?: string;
  tracks: Record<string, string>;
  sessions: Record<string, Record<string, { manifest: string; bin: string }>>;
}

/** One approach the pass model called correctly, as the exporter writes it. */
export interface ShowcaseRivalry {
  opportunityId: string;
  /** Race or Sprint. A weekend produces both, and lap 3 of one is a different
   * moment from lap 3 of the other, so the reel is filtered to the session on
   * screen rather than seeking blindly by lap number. */
  session: string | null;
  attacker: string;
  defender: string;
  lap: number | null;
  zone: number | null;
  gapS: number | null;
  closingRateSPerS: number | null;
  pEligible: number | null;
  /** What the model said at the Detection Line. */
  pPass: number;
  /** What the cars actually did. */
  outcome: boolean;
  call: "TRUE_POSITIVE" | "TRUE_NEGATIVE";
  detectionDistanceM: number | null;
  /** Session seconds at which the attacker crossed the Detection Line. The stage
   * seeks here so the replay plays the moment the model was asked about, rather
   * than the top of the lap. */
  detectionSessionTimeS: number | null;
  /** Measured telemetry either side of that crossing. Null when the session's
   * telemetry could not supply it -- an absent window is absent, not empty. */
  window: ShowcaseWindow | null;
}

/** One 20 m telemetry sample inside the window. Every channel is OBSERVED. */
export interface ShowcaseSample {
  /** Seconds from the centre of the window; negative is before the pass. */
  t: number;
  speedKph: number | null;
  throttlePct: number | null;
  /** Distance to THIS PAIR's other car. Positive means the other car is still
   * ahead, so the trace crosses zero exactly at the overtake. Distinct from
   * `gapAheadM`, which is the gap to whoever happens to be ahead and therefore
   * jumps to a different car the instant the pass completes. */
  gapToRivalM: number | null;
  gapAheadM: number | null;
  distanceM: number | null;
  /** SIMULATED by scripts/simdata/twin.py: the power that must have been at the
   * wheels to produce this speed trace. The public feed carries no such channel,
   * so this is a model's estimate and is never labelled measured. */
  wheelPowerKw: number | null;
  /** SIMULATED: the electrical share of it. Deploy and harvest are exclusive. */
  ersDeployKw: number | null;
  ersHarvestKw: number | null;
}

export interface ShowcaseWindow {
  beforeS: number;
  afterS: number;
  provenance: string;
  /** Session seconds the window is centred on. The stage seeks to
   * `centreSessionTimeS - beforeS` and stops at `+ afterS`, so the replay plays
   * exactly this span and cannot run past it. */
  centreSessionTimeS: number;
  /** What centred it: the position swap for a completed pass, the closest
   * approach for one that did not convert. */
  centredOn: "POSITION_SWAP" | "CLOSEST_APPROACH";
  /** Where the Detection Line sits relative to the centre. Usually well before
   * the window, because the zone can run most of a lap. */
  detectionOffsetS: number;
  attacker: ShowcaseSample[];
  defender: ShowcaseSample[];
}

/**
 * One circuit's reel, and the population it was drawn from.
 *
 * `population` is not optional detail. Showing only the calls the model got right
 * IS cherry-picking unless the number it was picked from is on screen beside it,
 * so the artifact keeps the two in one object and the UI renders them together.
 */
export interface ShowcaseEvent {
  event: string;
  slug: string;
  checkpoint: string;
  model: {
    artifact: string; family: string; evidenceGrade: string | null;
    featuresSupplied: number; featuresTotal: number; featuresMissing: string[];
  };
  population: {
    opportunities: number; passes: number; baseRate: number;
    rocAuc: number | null; unlabelledExcluded: number;
    selected: number; repeatsSkipped: number; note: string;
  };
  thresholds: { highDecile: number; lowDecile: number; tailQuantile: number };
  rivalries: ShowcaseRivalry[];
}

export interface RivalryShowcaseSet {
  schemaVersion: number;
  checkpoint: string;
  events: Record<string, ShowcaseEvent>;
  provenance: string;
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

/**
 * The raw tally behind one directed rivalry, as the exporter counts it.
 *
 * The three numbers are not interchangeable and the difference is the whole reason they are
 * carried separately. `opportunities` is every time this attacker was recorded in a following
 * position on this defender; `unlabelled` is how many of those the label rule could not decide;
 * `passes` is how many of the REMAINDER ended with the attacker ahead. So the denominator behind
 * the rate is the labelled count, not `opportunities` -- an opportunity nobody could label is
 * missing evidence, not a failed pass, and rolling it into the denominator would quietly
 * understate every driver on the chart.
 */
export interface PairCounts {
  opportunities: number;
  passes: number;
  unlabelled: number;
}

/**
 * MEASURED head-to-head conversion: one leaf per DIRECTED pair of drivers.
 *
 * Directed means "ANT → RUS" and "RUS → ANT" are two different rivalries and both appear in
 * `pairs`. How often Antonelli got past Russell is a different question from how often Russell
 * got past Antonelli -- they have different denominators, and a symmetric "rivalry score" that
 * averaged them would answer neither. This is also why the key is an ordered string rather than
 * a set: the order IS the claim.
 *
 * `pairs` values are the same `Leaf` the fitted parameters and `passConversionRate` use, with a
 * Wilson score interval on the proportion, so a consumer that can already draw an estimate with
 * its interval needs no new code path here. `counts` is the arithmetic underneath -- not a
 * duplicate of `n`, because `n` is only the labelled denominator and says nothing about how much
 * was set aside to get it.
 */
export interface HeadToHead {
  /** The exporter's own sentence on what a directed pair is and what converting means. Shown
   * rather than paraphrased: change the label rule and this changes with the numbers. */
  note: string;
  /** Keyed "<ATTACKER> → <DEFENDER>" -- three-letter codes either side of U+2192 with a space
   * each side. `splitPairKey` below is the only thing that should take that apart. */
  pairs: Record<string, Leaf>;
  /** Same keys as `pairs`. A key present in one and not the other is an exporter bug, so a
   * consumer reads counts as optional per key rather than assuming the pairing. */
  counts: Record<string, PairCounts>;
}

/**
 * The separator inside a head-to-head key: U+2192 RIGHTWARDS ARROW with a space either side.
 *
 * Spelled as an escape rather than pasted, because the difference between this and the ASCII
 * "->" some other tool might write is invisible in a diff and would silently split nothing.
 */
export const PAIR_SEPARATOR = " → ";

/**
 * "ANT → RUS" -> { attacker: "ANT", defender: "RUS" }; null for anything that is not a pair key.
 *
 * Null rather than a guess: a caller that cannot identify both sides cannot colour the row by
 * the attacker's team or match it against a driver filter, and half-applying either would be
 * worse than declining. Callers fall back to showing the key verbatim.
 */
export function splitPairKey(key: string): { attacker: string; defender: string } | null {
  const at = key.indexOf(PAIR_SEPARATOR);
  if (at < 0) return null;
  const attacker = key.slice(0, at).trim();
  const defender = key.slice(at + PAIR_SEPARATOR.length).trim();
  if (!attacker || !defender) return null;
  return { attacker, defender };
}

/**
 * MEASURED overtaking outcomes for one season, built from
 * data/processed/overtake_opportunities by the rivalry exporter.
 *
 * This is the counterpart to the FITTED dirty-air index in params.json, and the two must not
 * be confused. `dirtyAirLossPerSecondOfProximity` is a coefficient a regression chose to
 * explain lap times; `passConversionRate` is a tally of what actually happened -- how often a
 * car that got into a following position ended up ahead. They agree at the extremes (Monaco
 * hardest to follow AND lowest conversion; Monza easiest AND highest) and disagree in the
 * middle, which is the interesting part and the reason both are shown.
 *
 * Every value under `passConversionRate` is deliberately the same `Leaf` shape the fitted
 * parameters use -- {value, ci95, n, provenance, note} -- so a consumer that already knows how
 * to draw an estimate with its interval needs no new code path to draw this one. The interval
 * is a Wilson score interval on a proportion, not a bootstrap or a standard-error box.
 */
export interface RivalrySet {
  schemaVersion: number;
  /** The season these outcomes were counted from, e.g. "2026". */
  season: string;
  /** Which rule decided that an opportunity "converted", e.g. "zone_exit_v1". Named rather
   * than assumed: change the rule and every rate below changes with it. */
  labelDefinition: string;
  provenance: string;
  /** The processed table and the module that wrote it, so a number can be traced back. */
  source: string;
  note: string;
  /**
   * Season-wide counts behind the per-event rates.
   *
   * `opportunities - labelled = unlabelled`, and the unlabelled ones are EXCLUDED from every
   * rate rather than counted as failures to pass: an opportunity whose outcome could not be
   * determined is missing evidence, not evidence of no pass. Each leaf's `n` counts only the
   * labelled opportunities at that event, so these totals are what lets a reader see how much
   * was set aside.
   */
  totals: {
    opportunities: number;
    labelled: number;
    unlabelled: number;
    passes: number;
    events: number;
    sessions: string[];
  };
  /** Keyed by FULL event display name ("British Grand Prix"), matching params.json rather
   * than the circuit slug, so the two artifacts line up without a slugify() in between. */
  passConversionRate: Record<string, Leaf>;
  /**
   * Per-rivalry conversion. OPTIONAL, and treated exactly like `rivalries` is on SimIndex:
   * an artifact written before the head-to-head exporter simply has no key here, and a consumer
   * must then say it has no head-to-head data rather than drawing one. In particular it must
   * never render the absence as 0% -- "these two never met on track" and "this attacker never
   * got past" are different claims and only one of them would be in the data.
   */
  headToHead?: HeadToHead;
  /**
   * Per-attacker conversion over ALL that driver's pairs: how often this driver converts the
   * chances they get, whoever is in front. Same `Leaf` shape and same optionality as above.
   *
   * Not derivable in the browser from `headToHead.pairs` -- summing rates would weight a
   * two-opportunity pair the same as a hundred-opportunity one, and re-deriving the Wilson
   * interval here would put the statistics in the UI, which is exactly where they do not belong.
   * The exporter counts it from the labelled opportunities directly.
   */
  attackerTotals?: Record<string, Leaf>;
  /**
   * The mirror image: how often this driver is passed when attacked, over all pairs where they
   * were the one in front. A LOW value is a good defender, so a consumer that ranks this must
   * say which end of its own chart is the good end.
   */
  defenderTotals?: Record<string, Leaf>;
}

export interface SimSource {
  /** What sessions exist and where their pieces live. */
  index(): Promise<SimIndex>;
  catalogue<T>(): Promise<T>;
  /** Regulation limits, owned by scripts/simdata/rules.py. null when the built
   * artifacts predate the rule engine -- the UI then shows no thresholds at all rather
   * than inventing them. */
  rules(): Promise<RuleSet | null>;
  /** Measured 2026 overtake outcomes, owned by the rivalry exporter. null when the built
   * artifacts predate it -- the UI then says it has no measured conversion rate rather than
   * drawing one, and in particular never renders the absence as 0%. */
  rivalries(): Promise<RivalrySet | null>;
  /** The reel of opportunities where the pass model agreed with the telemetry, with
   * the full population it was drawn from. null when the artifacts predate the
   * exporter; the sim then simply offers no showcase. */
  rivalryShowcase(): Promise<RivalryShowcaseSet | null>;
  /** Fitted lap-model parameters from scripts/simdata/fit_params.py. Every leaf carries
   * se / ci95 / n, and the UI is expected to SHOW them: these are estimates with spread,
   * not settings. */
  params(): Promise<FittedParams>;
  track(slug: string): Promise<RawTrackModel>;
  sessionManifest(ref: SessionRef): Promise<RawSessionManifest>;
  /** URLs handed to the worker, which streams the binary itself. */
  sessionUrls(ref: SessionRef): Promise<{ trackUrl: string; manifestUrl: string; binUrl: string }>;
}

/** One place where an artifact request can fail, so it fails legibly.
 *
 * Every artifact is gitignored BUILD OUTPUT, not repo content, so the ordinary state of a
 * fresh clone is that none of them exist. Without a status check that showed up as
 * `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`: the server answers a missing
 * file with its HTML 404 page and r.json() tries to parse the markup. The panel then
 * reported a parse error, which points at the data being corrupt when in truth it was
 * never built. Measured against a running `next start`: status 404, body `<!DOCTYPE html>`.
 *
 * Anything the browser can be told here it must be told, because this is the only signal
 * the person gets. */
async function getJson<T>(url: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch (cause) {
    throw new Error(`could not reach ${url} (${String(cause)})`, { cause });
  }
  if (!response.ok) {
    throw new Error(
      `${url} responded ${response.status}. The sim artifacts are gitignored build ` +
        `output, so a fresh clone has none: run \`python scripts/build_sim_data.py ` +
        `--year 2026 --all --jobs 0 --fresh\` from the repo root (it needs the raw ` +
        `mirror under data/raw/tracinginsights/), or point NEXT_PUBLIC_SIM_BASE at ` +
        `wherever they are hosted.`,
    );
  }
  try {
    return (await response.json()) as T;
  } catch (cause) {
    throw new Error(`${url} did not contain JSON (${String(cause)})`, { cause });
  }
}

/** Reads the artifacts under /sim that the Python build wrote. */
export class StaticSimSource implements SimSource {
  private indexPromise: Promise<SimIndex> | null = null;

  constructor(private base = "/sim") {}

  index(): Promise<SimIndex> {
    if (!this.indexPromise) {
      this.indexPromise = (async () => {
        const pointer = await getJson<{ latest: string }>(`${this.base}/index.json`);
        return getJson<SimIndex>(`${this.base}/${pointer.latest}`);
      })();
    }
    return this.indexPromise;
  }

  async catalogue<T>(): Promise<T> {
    const idx = await this.index();
    return getJson<T>(`${this.base}/${idx.catalogue}`);
  }

  async rules(): Promise<RuleSet | null> {
    const idx = await this.index();
    if (!idx.rules) return null;
    return getJson<RuleSet>(`${this.base}/${idx.rules}`);
  }

  async rivalries(): Promise<RivalrySet | null> {
    const idx = await this.index();
    if (!idx.rivalries) return null;
    return getJson<RivalrySet>(`${this.base}/${idx.rivalries}`);
  }

  async rivalryShowcase(): Promise<RivalryShowcaseSet | null> {
    const idx = await this.index();
    if (!idx.rivalryShowcase) return null;
    return getJson<RivalryShowcaseSet>(`${this.base}/${idx.rivalryShowcase}`);
  }

  async params(): Promise<FittedParams> {
    const idx = await this.index();
    return getJson<FittedParams>(`${this.base}/${idx.params}`);
  }

  async track(slug: string): Promise<RawTrackModel> {
    const idx = await this.index();
    const file = idx.tracks[slug];
    if (!file) throw new Error(`no track artifact for ${slug}`);
    return getJson<RawTrackModel>(`${this.base}/${file}`);
  }

  async sessionManifest(ref: SessionRef): Promise<RawSessionManifest> {
    const { manifestUrl } = await this.sessionUrls(ref);
    return getJson<RawSessionManifest>(manifestUrl);
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

/** The artifacts are gitignored build output, not repo content, so a deployed build has
 * no /sim to serve. Point NEXT_PUBLIC_SIM_BASE at wherever they are hosted (a bucket, a
 * release asset) and the whole app follows; unset, it reads the local build as before. */
export const defaultSimSource: SimSource = new StaticSimSource(
  process.env.NEXT_PUBLIC_SIM_BASE || "/sim");
