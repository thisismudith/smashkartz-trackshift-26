/**
 * The provenance vocabulary, and what each tag is allowed to claim.
 *
 * API.md 43 requires every displayed number to carry its tag, and API.md 8 requires the
 * tags to be told apart at a glance. Colour alone will not do that (three of the five
 * would have to share a hue on a three-colour palette, and colour-blind readers lose it
 * anyway), so each tag gets a SHORT WORD plus a distinct shape treatment, and the long
 * form lives in the `title` tooltip.
 *
 * Two vocabularies arrive on the same `provenance` key and are both handled here:
 *
 *   API.md 3.1 core     OBSERVED DERIVED INFERRED SIMULATED RULE
 *   rule-engine source  RULE_FIA DERIVED_TELEMETRY UNVERIFIED UNRESOLVED
 *
 * `app.py` copies a rule's `value_source` straight into the `provenance` field of
 * `/track` lines, so a Detection Line really does arrive tagged `DERIVED_TELEMETRY`. That
 * is more informative than flattening it to `RULE`, so it is kept and badged distinctly —
 * a landmark measured off our own telemetry is not the same claim as a number quoted from
 * an FIA article, and an UNVERIFIED one is weaker than both.
 */
import type { Provenance } from "./types";

export type ProvenanceTone =
  /** Measured. The only tone that may say a thing was seen. */
  | "observed"
  /** Computed from measurements by a documented transform. */
  | "derived"
  /** A model's belief about a hidden quantity. */
  | "inferred"
  /** Produced by a model of the world, not by the world. */
  | "simulated"
  /** Quoted from a regulation or configuration. */
  | "rule"
  /** A rule value with no resolved citation yet. Weakest claim on screen. */
  | "unverified"
  /** The tag was absent or is not one we know. */
  | "unknown";

export interface ProvenanceMeaning {
  tone: ProvenanceTone;
  /** Rendered in the badge. Kept to one short word so it fits inline beside a number. */
  short: string;
  /** Tooltip. States what the tag permits and forbids, because that is the whole point. */
  title: string;
}

const MEANINGS: Record<string, ProvenanceMeaning> = {
  OBSERVED: {
    tone: "observed",
    short: "Observed",
    title: "Measured from published telemetry. The only tag that means the value was seen rather than computed or modelled.",
  },
  DERIVED: {
    tone: "derived",
    short: "Derived",
    title: "Computed from observed telemetry by a documented transform. Not measured directly, but not a model's belief either.",
  },
  INFERRED: {
    tone: "inferred",
    short: "Inferred",
    title: "A model's estimate of something not directly visible. Carries uncertainty and may be wrong; never state it as fact.",
  },
  SIMULATED: {
    tone: "simulated",
    short: "Simulated",
    title: "Produced by a model of the car or race, not by the race. Never present this as measured — in particular, estimated electrical energy is not a battery reading.",
  },
  RULE: {
    tone: "rule",
    short: "Rule",
    title: "Quoted from the encoded regulation or the event configuration, not from data.",
  },
  RULE_FIA: {
    tone: "rule",
    short: "Rule · FIA",
    title: "Quoted from a cited FIA regulation article. Hover the value for the document reference.",
  },
  DERIVED_TELEMETRY: {
    tone: "derived",
    short: "Derived · telemetry",
    title: "A rule landmark positioned by aligning the published circuit map to observed corner distances in our own telemetry. A measured placement, not a quoted figure.",
  },
  UNVERIFIED: {
    tone: "unverified",
    short: "Unverified",
    title: "A carried-over or assumed value with no resolved 2026 citation. Any number derived from it inherits that weakness, and the system must not be described as legal by construction while it stands.",
  },
  UNRESOLVED: {
    tone: "unverified",
    short: "Unresolved",
    title: "The regulation requires this value but does not publish it for this event. It is not a default and must not be treated as one.",
  },
};

export function meaningOf(tag: Provenance | string | null | undefined): ProvenanceMeaning {
  if (typeof tag === "string") {
    const hit = MEANINGS[tag.toUpperCase()];
    if (hit) return hit;
  }
  return {
    tone: "unknown",
    short: tag ? String(tag) : "Untagged",
    title: tag
      ? `The backend sent the provenance tag "${tag}", which this build does not recognise. Treat the number as unverified until the tag is known.`
      : "This value arrived with no provenance tag. API.md 43 requires one, so treat it as unverified.",
  };
}

/**
 * True when a tag means "a model produced this", which is the set the UI must never
 * describe with observational language. Used to keep words like "measured", "actual" and
 * "battery" away from values that have not earned them.
 */
export function isModelled(tag: Provenance | string | null | undefined): boolean {
  const tone = meaningOf(tag).tone;
  return tone === "inferred" || tone === "simulated";
}

/** The five core tags, in the order the legend shows them. */
export const CORE_TAGS: Provenance[] = ["OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE"];
