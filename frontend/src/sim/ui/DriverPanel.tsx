"use client";

import type { DashboardRow, Provenance } from "../contract/types";
import type { RuleQuantity, RuleSet } from "../data/source";
import {
  describePositionProvenance, meterState, type MeterState, type OrderedDashboardRow,
} from "../replay/dashboard";
import styles from "./panels.module.css";

/**
 * The row as the dashboard actually produces it.
 *
 * buildDashboardSnapshot emits OrderedDashboardRow, but the snapshot crosses a
 * postMessage boundary and the store types it back as the narrower DashboardRow
 * (store/simStore.ts). The extra labels are present at runtime, so they are accepted
 * here as optional and every reader treats an absent one as "not stated" rather than
 * inventing a value. Once the store is typed as OrderedDashboardSnapshot these become
 * required and this alias can go.
 */
export type PanelRow = DashboardRow
  & Partial<Pick<OrderedDashboardRow, "orderSource" | "positionProvenance" | "gapProvenance">>;

/** A number plus a bar showing how much of its limit it uses. Without a limit the bar
 * is omitted entirely -- an uncalibrated bar is worse than no bar. */
function Meter({
  label, value, unit, limit, limitVerified, digits = 2, invert = false, signed = false,
}: {
  label: string;
  value: number;
  unit: string;
  limit: number | null;
  /** Whether the rule engine traced this limit to a regulation. An unverified limit
   * still grades the bar, but being past it is reported as the model failing to close
   * rather than as a breach. */
  limitVerified?: boolean;
  digits?: number;
  /** true when a LOW value is the concerning one (a nearly empty store). */
  invert?: boolean;
  /** true to print a leading + on a non-negative value (a signed balance). */
  signed?: boolean;
}) {
  const m: MeterState = meterState(value, limit, { verified: limitVerified, invert });
  const title = limit !== null && m.fraction !== null
    ? `${(m.fraction * 100).toFixed(0)}% of ${limit} ${unit}`
    : undefined;
  return (
    <div className={styles.meterRow}>
      <span className={styles.meterLabel}>{label}</span>
      <span className={styles.meterValue} data-level={m.level}>
        {signed && value >= 0 ? "+" : ""}{value.toFixed(digits)}
        <span className={styles.meterUnit}>{unit}</span>
      </span>
      {limit !== null ? (
        <span className={styles.meterTrack} title={title}>
          <span className={styles.meterFill} data-level={m.level} style={{ width: `${m.fillPct}%` }} />
        </span>
      ) : null}
    </div>
  );
}

/** The sentence that has to appear when a reconstruction runs past an unsourced budget.
 * It names the shortfall, names the placeholder, and says which of the two is being
 * doubted -- so the reader cannot mistake it for a penalty notice. */
function DoesNotClose({
  label, value, limit, unit, quantity,
}: {
  label: string;
  value: number;
  limit: number;
  unit: string;
  quantity: RuleQuantity;
}) {
  const m = meterState(value, limit, { verified: quantity.verified });
  if (!m.modelDoesNotClose || m.excess === null) return null;
  return (
    <p className={styles.meterAside}>
      {label} runs {m.excess.toFixed(2)} {unit} past the {limit.toFixed(2)} {unit}{" "}
      {quantity.accounting_window.replace(/_/g, " ")} budget. That budget is an unsourced
      placeholder (<code>verified:false</code>) and the {label.toLowerCase()} itself is
      reconstructed from a power balance, not measured — so this is the model failing to
      close, not a rule being broken. Nothing here says the car did anything it should
      not have.
    </p>
  );
}

function Bar({ label, pct, kind }: { label: string; pct: number; kind?: "brake" }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className={styles.barRow}>
      <span className={styles.barLabel}>{label}</span>
      <span className={styles.barTrack}>
        <span className={styles.barFill} data-kind={kind} style={{ width: `${clamped}%` }} />
      </span>
      <span className={styles.barValue}>{Math.round(clamped)}%</span>
    </div>
  );
}

/**
 * The focused car: measured telemetry on top, the Python energy twin's estimate below.
 *
 * The split is the point. Speed, gear, throttle and brake are in the feed. Battery
 * charge, MGU-K power, harvest and deployment are NOT -- the 2026 public feed carries
 * none of them, and the DRS channel it does carry reads zero in all 11.57 M samples of
 * the season. Everything under the Inferred heading is reconstructed from a
 * longitudinal power balance in scripts/simdata/twin.py with a stated uncertainty, and
 * is never allowed to sit in the same block as a measurement.
 *
 * The car's POSITION gets the same treatment. Speed can be measured on a lap whose x/y
 * trace is unusable, so the telemetry block being OBSERVED says nothing about where the
 * car is drawn: that carries its own provenance and its own heading.
 */
export function DriverPanel({ row, rules }: { row: PanelRow | null; rules: RuleSet | null }) {
  if (!row) {
    return (
      <div className={styles.panel}>
        <div className={styles.panelTitle}>Focused car</div>
        <p className={styles.provNote}>
          Click a car in the scene, or a row in the leaderboard, to read its telemetry and
          energy estimate here.
        </p>
      </div>
    );
  }

  // Limits come from scripts/simdata/rules.py via the artifact -- never typed in here.
  // Absent rules means absent thresholds, not invented ones.
  const budget = rules?.energy_budget;
  const storeMj = budget?.ers_store_capacity.value_mj ?? null;
  const deployMj = budget?.deploy_budget.value_mj ?? null;
  const harvestMj = budget?.harvest_budget.value_mj ?? null;
  // The ELECTRICAL ceiling: the envelope's highest breakpoint, whatever the curve shape.
  // Shown for context only; it is not a bound on wheel power.
  const envelopeKw = rules
    ? Math.max(...Object.values(rules.power_envelope).flatMap((e) => e.max_power_kw))
    : null;
  const thresholdsProvisional = Boolean(budget) && [
    budget!.ers_store_capacity, budget!.deploy_budget, budget!.harvest_budget,
  ].some((q) => !q.verified);

  // Where this car's position came from. Absent (an older snapshot shape) is stated as
  // unknown rather than assumed measured.
  const posProv: Provenance | null = row.positionProvenance ?? null;
  const pos = posProv ? describePositionProvenance(posProv) : null;
  // Only two of these are styled in panels.module.css (observed = white border,
  // inferred = red, missing = red text). A reconstruction gets the red border because
  // it is a reconstruction standing in for a measurement; a RULE placement is a
  // legitimate documented position, so it takes the neutral default.
  const posKind = posProv === "OBSERVED" ? "observed"
    : posProv === "DERIVED" || posProv === "INFERRED" ? "inferred"
    : posProv ? "rule"
    : "missing";

  return (
    <div className={styles.panel}>
      <div className={styles.panelTitle}>{row.driver} · {row.team ?? "—"}</div>

      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind="observed">Observed</span>
        <span>measured telemetry</span>
      </div>

      <div className={styles.speedBig}>
        {Math.round(row.speedKph)}<span className={styles.speedUnit}>km/h</span>
        <span className={styles.gearBadge}>{row.gear ? `G${row.gear}` : "N"}</span>
      </div>
      <Bar label="Throttle" pct={row.throttlePct} />
      <Bar label="Brake" pct={row.brake ? 100 : 0} kind="brake" />

      <dl className={styles.grid}>
        <dt>Tyre</dt><dd>{row.compound ?? "—"} ({row.tyreLife ?? "—"} laps)</dd>
        <dt>Lap</dt><dd>{Math.round(row.lapProgress * 100)}%</dd>
        <dt>Last lap</dt><dd>{row.lastLapS !== null ? `${row.lastLapS.toFixed(3)} s` : "—"}</dd>
        <dt>Status</dt><dd>{row.status}</dd>
      </dl>

      {/* Position and the two numbers derived from it sit under their own heading. The
          telemetry pill above covers the speed trace and must not be read as covering
          where the car is: at Monaco 91% of laps have no usable x/y at all. */}
      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind={posKind}>
          {pos ? pos.label : "Unstated"}
        </span>
        <span>position{row.orderSource ? ` · order ${row.orderSource.toLowerCase()}` : ""}</span>
      </div>

      <dl className={styles.grid}>
        <dt>Gap</dt><dd>{row.gapToLeader}</dd>
        <dt>Interval</dt><dd>{row.interval}</dd>
      </dl>

      <p className={styles.provNote}>
        {pos
          ? pos.note
          : "This snapshot does not state where the position came from, so it is not"
            + " claimed to be measured."}
      </p>

      <div className={styles.provRow}>
        <span className={styles.provPill} data-kind="inferred">Inferred</span>
        <span>energy twin · computed in Python</span>
      </div>
      {row.energy ? (
        <>
          <Meter label="Store" value={row.energy.socEndMj} unit="MJ"
                 limit={storeMj} limitVerified={budget?.ers_store_capacity.verified} invert />
          <p className={styles.meterAside}>
            ± {row.energy.socUncertaintyMj.toFixed(2)} MJ
          </p>
          <Meter label="Deployed" value={row.energy.ersEnergyUsedMj} unit="MJ"
                 limit={deployMj} limitVerified={budget?.deploy_budget.verified} />
          {budget && deployMj !== null ? (
            <DoesNotClose label="Deployment" value={row.energy.ersEnergyUsedMj}
                          limit={deployMj} unit="MJ" quantity={budget.deploy_budget} />
          ) : null}
          <Meter label="Harvested" value={row.energy.ersEnergyHarvestedMj} unit="MJ"
                 limit={harvestMj} limitVerified={budget?.harvest_budget.verified} />
          {budget && harvestMj !== null ? (
            <DoesNotClose label="Harvest" value={row.energy.ersEnergyHarvestedMj}
                          limit={harvestMj} unit="MJ" quantity={budget.harvest_budget} />
          ) : null}
          <Meter label="Net" value={row.energy.energyBalanceMj} unit="MJ" limit={null} signed />
          {/* Deliberately ungraded. The rule engine's envelope caps ELECTRICAL (MGU-K)
              power at 350 kW; these two are WHEEL power, which is engine plus electrical
              and measured at p99 around 680-720 kW. Grading one against the other is a
              category error that paints every lap red. */}
          <Meter label="Peak pwr" value={row.energy.peakWheelPowerKw} unit="kW"
                 limit={null} digits={0} />
          <Meter label="Braking" value={row.energy.peakBrakingKw} unit="kW"
                 limit={null} digits={0} />
          {envelopeKw !== null ? (
            <p className={styles.meterAside}>
              Wheel power, ungraded — the {envelopeKw.toFixed(0)} kW envelope caps
              electrical power only.
            </p>
          ) : null}
          {thresholdsProvisional ? (
            <p className={styles.meterAside}>
              Colour scale is provisional: the 2026 limits it grades against are
              unsourced placeholders in the rule engine, flagged <code>verified:false</code>.
              A bar past its limit is the reconstruction disagreeing with a placeholder,
              never a finding against the car.
            </p>
          ) : null}
          {row.energy.envelopeCapViolations > 0 ? (
            <p className={styles.provNote}>
              {row.energy.envelopeCapViolations} sample(s) this lap needed more electrical
              power than the 2026 envelope allows — the reconstruction, the envelope, or
              both are wrong there. Shown rather than clipped away.
            </p>
          ) : null}
          {row.energy.warnings.length > 0 ? (
            <ul className={styles.warnList}>
              {row.energy.warnings.map((w) => <li key={w}>{w}</li>)}
            </ul>
          ) : null}
          {/* The provenance pill above is the always-visible tag the contract requires;
              this is the elaboration, folded so the numbers stay on screen. */}
          <details className={styles.noteFold}>
            <summary>Where these come from</summary>
            <p className={styles.provNote}>
              Battery charge, MGU-K power, harvest and deployment are not in the 2026
              public feed. They are reconstructed in Python from a longitudinal power
              balance (mass·a·v plus drag, rolling and gradient terms) against the
              measured speed trace, with a stated uncertainty. They are estimates, never
              measurements.
            </p>
          </details>
        </>
      ) : (
        <p className={styles.provNote}>No energy estimate for this lap.</p>
      )}
    </div>
  );
}
