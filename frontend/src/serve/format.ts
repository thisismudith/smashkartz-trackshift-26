/**
 * Turning a wire value into words, with "Unavailable" as a first-class outcome.
 *
 * API.md 1.5: a quantity that is unavailable is `null` with a `reason`, never fabricated.
 * The failure mode this module exists to prevent is the one-character version of that bug:
 *
 *     {value ?? 0}          // a missing gap silently becomes "0.00 s" — a car alongside
 *     {value?.toFixed(2)}   // a missing gap silently becomes "" — an empty cell
 *
 * Both read as information. `formatQuantity` returns a tagged union instead, so a caller
 * cannot render a missing value without deciding what to say about it, and the default
 * thing to say carries the backend's own reason.
 *
 * Note the deliberate asymmetry: a real zero IS shown as zero. "Missing means missing"
 * cuts both ways — turning an observed 0.0 into "Unavailable" would be the same class of
 * error in the other direction.
 */
import type { Provenance, Quantity, RuleValue } from "./types";

export type Displayed =
  | { kind: "value"; text: string; provenance?: Provenance; unit?: string }
  | { kind: "unavailable"; text: "Unavailable"; reason: string; provenance?: Provenance };

const DEFAULT_REASON =
  "the backend returned null for this field and gave no reason; treat it as missing, not as zero";

export interface FormatOpts {
  /** Significant decimals. Default 2. */
  digits?: number;
  /** Overrides the unit from the payload (e.g. when converting). */
  unit?: string;
  /** Used when the payload omits `reason`. */
  fallbackReason?: string;
}

function fmtNumber(n: number, digits: number): string {
  if (!Number.isFinite(n)) return String(n);
  // Probabilities and lambdas need more than 2 dp to be legible; big kW figures need none.
  const abs = Math.abs(n);
  if (abs !== 0 && abs < 0.01) return n.toExponential(2);
  return n.toFixed(digits);
}

export function formatQuantity(q: Quantity | null | undefined, opts: FormatOpts = {}): Displayed {
  const digits = opts.digits ?? 2;
  if (q == null) {
    return {
      kind: "unavailable",
      text: "Unavailable",
      reason: opts.fallbackReason ?? "this field is not present in the response",
    };
  }
  if (q.value === null || q.value === undefined || typeof q.value !== "number" || Number.isNaN(q.value)) {
    return {
      kind: "unavailable",
      text: "Unavailable",
      reason: q.reason ?? opts.fallbackReason ?? DEFAULT_REASON,
      provenance: q.provenance,
    };
  }
  const unit = opts.unit ?? q.unit;
  return {
    kind: "value",
    text: unit ? `${fmtNumber(q.value, digits)} ${unit}` : fmtNumber(q.value, digits),
    provenance: q.provenance,
    unit,
  };
}

/** Same contract for a bare number that may be absent. */
export function formatNumber(
  n: number | null | undefined,
  opts: FormatOpts & { provenance?: Provenance } = {},
): Displayed {
  return formatQuantity(
    n === null || n === undefined ? { value: null, provenance: opts.provenance } : { value: n, provenance: opts.provenance, unit: opts.unit },
    opts,
  );
}

/** A rule number, whose "provenance" is its `value_source` and whose reason is its source note. */
export function formatRuleValue(r: RuleValue | null | undefined, opts: FormatOpts = {}): Displayed {
  if (r == null) {
    return { kind: "unavailable", text: "Unavailable", reason: opts.fallbackReason ?? "this rule key is not present in the configuration" };
  }
  if (r.value === null || r.value === undefined) {
    return {
      kind: "unavailable",
      text: "Unavailable",
      // An UNRESOLVED rule carries its explanation in `source`; it is the reason.
      reason: r.source ?? r.note ?? "the regulation does not publish this value for this event",
      provenance: r.value_source,
    };
  }
  return {
    kind: "value",
    text: opts.unit ? `${fmtNumber(r.value, opts.digits ?? 2)} ${opts.unit}` : fmtNumber(r.value, opts.digits ?? 2),
    provenance: r.value_source,
    unit: opts.unit,
  };
}

/** Percentage for probabilities. Keeps 0 as "0.0%" — a real zero probability is information. */
export function formatProbability(p: number | null | undefined, reason?: string): Displayed {
  if (p === null || p === undefined || typeof p !== "number" || Number.isNaN(p)) {
    return { kind: "unavailable", text: "Unavailable", reason: reason ?? DEFAULT_REASON };
  }
  return { kind: "value", text: `${(p * 100).toFixed(1)}%` };
}

/**
 * Sorts a rival-state distribution into descending order for the bars.
 * API.md 8 requires this be shown as a distribution, never collapsed to its argmax.
 */
export function rankDistribution(p: Record<string, number> | undefined): Array<{ state: string; p: number }> {
  if (!p) return [];
  return Object.entries(p)
    .filter(([, v]) => typeof v === "number" && Number.isFinite(v))
    .map(([state, v]) => ({ state, p: v }))
    .sort((a, b) => b.p - a.p);
}
