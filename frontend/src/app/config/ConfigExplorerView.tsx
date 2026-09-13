/**
 * Every configurable, non-learned variable in config/*.yaml that a decision or
 * model reads (AGENTS.md / MODELS.md / API.md, transcribed in
 * src/config-explorer/data.ts) — searchable, categorized, collapsible.
 *
 * Editing a field only changes local state here. There is no documented route
 * in API.md for writing config back, so this does not pretend to save
 * anywhere — "Copy overrides" hands the diff to whoever owns config/*.yaml,
 * which is the honest version of a save button until that route exists.
 */
"use client";

import { useMemo, useState } from "react";
import { CONFIG_CATEGORIES, snapshotOf, type ConfigCategory, type ConfigVariable, type ValueSource } from "@/config-explorer/data";
import { EVENT_PROFILES, DEFAULT_EVENT_CIRCUIT, eventProfile, type EventProfile } from "@/config-explorer/events";
import { CHART } from "@/lib/palette";
import { ChartFrame, Plot, Grid, XAxis, BandLabels, HBars, niceTicks } from "@/sim/charts";
import s from "./config.module.css";

const SOURCE_BADGE: Record<ValueSource, { label: string; tone: "ok" | "warn" | "risk" | "muted" }> = {
  RULE_FIA: { label: "RULE_FIA", tone: "ok" },
  OBSERVED_RCM: { label: "OBSERVED_RCM", tone: "ok" },
  OBSERVED: { label: "OBSERVED", tone: "ok" },
  DERIVED_TELEMETRY: { label: "DERIVED_TELEMETRY", tone: "ok" },
  UNVERIFIED: { label: "UNVERIFIED", tone: "warn" },
  PROXY_HISTORICAL_DRS: { label: "PROXY (historical DRS)", tone: "risk" },
  DEVELOPMENT_ONLY: { label: "DEVELOPMENT ONLY", tone: "muted" },
};

type Overrides = Record<string, ConfigVariable["value"]>;

function matches(v: ConfigVariable, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const haystack = [
    v.label,
    v.id,
    v.file,
    v.path,
    v.note ?? "",
    v.source,
    v.valueSource,
    ...v.affects,
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(q);
}

function formatDefault(v: ConfigVariable["value"]): string {
  if (v === null) return "— not set";
  if (Array.isArray(v)) return `[${v.join(", ")}]`;
  return String(v);
}

function VariableInput({
  variable,
  value,
  onChange,
}: {
  variable: ConfigVariable;
  value: ConfigVariable["value"];
  onChange: (next: ConfigVariable["value"]) => void;
}) {
  if (variable.inputKind === "boolean") {
    return (
      <label className={s.boolField}>
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        <span>{value ? "true" : "false"}</span>
      </label>
    );
  }

  if (variable.inputKind === "number") {
    return (
      <div className={s.numberField}>
        <input
          type="number"
          value={typeof value === "number" ? value : ""}
          min={variable.min}
          max={variable.max}
          step={variable.step ?? 1}
          placeholder={value === null ? "not set" : undefined}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
        {variable.unit ? <span className={s.unit}>{variable.unit}</span> : null}
      </div>
    );
  }

  if (variable.inputKind === "list-number") {
    const list = Array.isArray(value) ? (value as number[]) : [];
    return (
      <div className={s.listField}>
        <input
          type="text"
          value={list.join(", ")}
          onChange={(e) => {
            const parsed = e.target.value
              .split(",")
              .map((t) => t.trim())
              .filter((t) => t !== "")
              .map(Number);
            onChange(parsed.every((n) => Number.isFinite(n)) ? parsed : list);
          }}
        />
        {variable.unit ? <span className={s.unit}>{variable.unit}</span> : null}
      </div>
    );
  }

  // list-string
  const list = Array.isArray(value) ? (value as string[]) : [];
  return (
    <div className={s.listField}>
      <input
        type="text"
        value={list.join(", ")}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(",")
              .map((t) => t.trim())
              .filter((t) => t !== ""),
          )
        }
      />
    </div>
  );
}

function VariableRow({
  variable,
  value,
  changed,
  onChange,
  onReset,
}: {
  variable: ConfigVariable;
  value: ConfigVariable["value"];
  changed: boolean;
  onChange: (next: ConfigVariable["value"]) => void;
  onReset: () => void;
}) {
  const badge = SOURCE_BADGE[variable.valueSource];
  return (
    <div className={s.row} data-changed={changed}>
      <div className={s.rowHead}>
        <span className={s.rowLabel}>{variable.label}</span>
        <span className={s.badge} data-tone={badge.tone}>
          {badge.label}
        </span>
        {changed ? (
          <button type="button" className={s.resetButton} onClick={onReset} title="Reset to snapshot default">
            reset
          </button>
        ) : null}
      </div>

      <div className={s.rowBody}>
        <VariableInput variable={variable} value={value} onChange={onChange} />
        {changed ? <span className={s.defaultNote}>snapshot default: {formatDefault(variable.value)}</span> : null}
      </div>

      <p className={s.rowMeta}>
        <code>{variable.file}</code> · <code>{variable.path}</code>
      </p>

      {variable.note ? <p className={s.rowNote}>{variable.note}</p> : null}

      <div className={s.affects}>
        {variable.affects.map((a) => (
          <span key={a} className={s.affectsChip}>
            {a}
          </span>
        ))}
      </div>

      <p className={s.rowSource}>source: {variable.source}</p>
    </div>
  );
}

/**
 * The per-event category, rebuilt whenever the race selector changes.
 * config/rules/2026/<event>.yaml — everything here differs by circuit; the
 * season-wide values (envelope, energy budgets, detection GAP threshold) stay
 * in the other categories, since every event's own overrides: block is empty.
 */
function buildEventCategory(p: EventProfile): ConfigCategory {
  const ns = `event.${p.circuit}`;
  return {
    id: "event-specific",
    title: `Event-specific defaults — ${p.eventDisplay}`,
    description: p.completeInMirror
      ? "Measured or observed per circuit — these are facts about the event, not tunable model parameters, but every one of them is a default a decision reads before any override applies."
      : "This event's mirror is incomplete (Practice 1 only, no corners.json) — geometry and detection-line fields are unavailable rather than guessed.",
    variables: [
      {
        id: `${ns}.lap_length_m`,
        label: "Lap length (measured)",
        file: `config/rules/2026/${p.event}.yaml`,
        path: "lap_length_m.value",
        value: p.lapLengthM,
        unit: "m",
        valueSource: "DERIVED_TELEMETRY",
        source: "median max(distance) over sampled Race laps, lap 1 excluded",
        note: `Published circuit length is ${p.publishedLengthM} m (${p.deltaVsPublishedPct.toFixed(2)}% delta) — telemetry reads shorter because straight-line integration between samples undershoots the arc through a corner. Segments are built on the measured value, not the published one.`,
        affects: ["M03 track segmentation", "GET /track/{event}"],
        inputKind: "number",
        min: 2500,
        max: 7500,
        step: 0.1,
      },
      {
        id: `${ns}.corner_count`,
        label: "Corner count",
        file: `config/rules/2026/${p.event}.yaml`,
        path: "geometry.corner_count",
        value: p.cornerCount,
        valueSource: p.completeInMirror ? "OBSERVED" : "UNVERIFIED",
        source: p.completeInMirror ? "corners.json" : "no corners.json in this event's mirror",
        affects: ["M03 track segmentation", "corner_type classification"],
        inputKind: "number",
        min: 0,
        max: 25,
        step: 1,
      },
      {
        id: `${ns}.rotation_deg`,
        label: "Official map rotation",
        file: `config/rules/2026/${p.event}.yaml`,
        path: "geometry.rotation_deg",
        value: p.rotationDeg,
        unit: "deg",
        valueSource: p.completeInMirror ? "OBSERVED" : "UNVERIFIED",
        source: "corners.json Rotation field",
        note: "Display metadata for the printed circuit map only — never applied as a rotation to the ring, cars, or camera.",
        affects: ["map-style view orientation"],
        inputKind: "number",
        min: 0,
        max: 360,
        step: 1,
      },
      {
        id: `${ns}.detection_line_m`,
        label: "Detection Line position",
        file: `config/rules/2026/${p.event}.yaml`,
        path: "overtake.detection_line_m.value",
        value: p.detectionLineM,
        unit: "m",
        valueSource: p.detectionLineM !== null ? "DERIVED_TELEMETRY" : "UNVERIFIED",
        source:
          p.detectionLineM !== null
            ? "FIA Race Director's Competition Notes place it at Safety Car Line 1; position measured from pit-entry telemetry across 2026 Race/Sprint in-laps"
            : "No FIA event note states this circuit's line, and no heuristic may manufacture one",
        affects: ["M20 Overtake state machine", "M07 opportunity dataset", "GET /track/{event} lines"],
        inputKind: "number",
        min: 0,
        max: 8000,
        step: 10,
      },
      {
        id: `${ns}.overtake_enabled_messages`,
        label: "OVERTAKE ENABLED messages (season)",
        file: `config/rules/2026/${p.event}.yaml`,
        path: "race_control.overtake_enabled_messages",
        value: p.overtakeEnabledMessages,
        valueSource: "OBSERVED_RCM",
        source: "extract_race_control.py over this event's rcm.json",
        affects: ["M02/C7 normal_race_model_eligible gate"],
        inputKind: "number",
        min: 0,
        max: 20,
        step: 1,
      },
      {
        id: `${ns}.overtake_disabled_messages`,
        label: "OVERTAKE DISABLED messages (season)",
        file: `config/rules/2026/${p.event}.yaml`,
        path: "race_control.overtake_disabled_messages",
        value: p.overtakeDisabledMessages,
        valueSource: "OBSERVED_RCM",
        source: "extract_race_control.py over this event's rcm.json",
        affects: ["M02/C7 normal_race_model_eligible gate"],
        inputKind: "number",
        min: 0,
        max: 20,
        step: 1,
      },
      {
        id: `${ns}.complete_in_mirror`,
        label: "Complete in mirror",
        file: `config/rules/2026/${p.event}.yaml`,
        path: "complete_in_mirror",
        value: p.completeInMirror,
        valueSource: "OBSERVED",
        source: "presence of corners.json and full session set in the raw mirror",
        affects: ["every downstream builder's event scope"],
        inputKind: "boolean",
      },
    ],
  };
}

/**
 * Ranks every complete circuit by one measure and highlights the selected race, so the
 * event-specific numbers above have somewhere to sit relative to the other 13 — real,
 * measured/observed data (§ choosing-a-form: magnitude across categories -> ranked bars,
 * one hue, selected entity carries the accent per "color follows the entity").
 */
function CircuitComparison({ selected }: { selected: string }) {
  const withLap = EVENT_PROFILES.filter((p) => p.completeInMirror);
  const rankedByLap = [...withLap].sort((a, b) => b.lapLengthM - a.lapLengthM);
  const lapDomain = niceTicks(0, Math.max(...rankedByLap.map((p) => p.lapLengthM)), 5);

  const withDetection = withLap.filter((p) => p.detectionLineM !== null);
  const rankedByDetection = [...withDetection].sort(
    (a, b) => (b.detectionLineM ?? 0) - (a.detectionLineM ?? 0),
  );
  const detectionDomain = niceTicks(0, Math.max(...rankedByDetection.map((p) => p.detectionLineM ?? 0)), 5);
  const missingDetection = withLap.filter((p) => p.detectionLineM === null).map((p) => p.eventDisplay);

  return (
    <details className={s.category}>
      <summary className={s.categorySummary}>
        <span className={s.categoryTitle}>Compare across circuits</span>
        <span className={s.categoryCount}>{withLap.length}</span>
      </summary>
      <p className={s.categoryDescription}>
        Every complete 2026 circuit, ranked by a measured or observed quantity from its own rule
        file. The selected race is highlighted; the rest recede to grey.
      </p>

      <div className={s.charts}>
        <ChartFrame
          title="Lap length, measured"
          units="m"
          provenance="DERIVED"
          note="Telemetry-measured, not the published circuit length — see the event category above for why the two differ."
          table={{
            columns: ["circuit", "lap length (m)"],
            rows: rankedByLap.map((p) => [p.eventDisplay, p.lapLengthM.toFixed(1)]),
          }}
        >
          <Plot
            xDomain={[0, lapDomain.niceMax]}
            yDomain={[0, rankedByLap.length]}
            height={Math.max(220, rankedByLap.length * 22 + 50)}
            margin={{ left: 132, right: 18, top: 8, bottom: 34 }}
            ariaLabel="Measured lap length in metres, ranked across every complete 2026 circuit"
          >
            <Grid x y={false} />
            <XAxis label="metres" count={5} format={(v) => v.toFixed(0)} />
            <BandLabels labels={rankedByLap.map((p) => p.eventDisplay)} />
            <HBars
              values={rankedByLap.map((p) => p.lapLengthM)}
              colour={(i) => (rankedByLap[i].circuit === selected ? CHART.series[0] : CHART.status.muted)}
            />
          </Plot>
        </ChartFrame>

        <ChartFrame
          title="Detection Line position"
          units="m"
          provenance="DERIVED"
          note={
            missingDetection.length > 0
              ? `No FIA note states a Detection Line for: ${missingDetection.join(", ")} — omitted rather than guessed.`
              : undefined
          }
          table={{
            columns: ["circuit", "detection line (m)"],
            rows: rankedByDetection.map((p) => [p.eventDisplay, (p.detectionLineM ?? 0).toFixed(0)]),
          }}
        >
          <Plot
            xDomain={[0, detectionDomain.niceMax]}
            yDomain={[0, rankedByDetection.length]}
            height={Math.max(220, rankedByDetection.length * 22 + 50)}
            margin={{ left: 132, right: 18, top: 8, bottom: 34 }}
            ariaLabel="Detection Line position in metres, ranked across circuits with a stated line"
          >
            <Grid x y={false} />
            <XAxis label="metres" count={5} format={(v) => v.toFixed(0)} />
            <BandLabels labels={rankedByDetection.map((p) => p.eventDisplay)} />
            <HBars
              values={rankedByDetection.map((p) => p.detectionLineM ?? 0)}
              colour={(i) =>
                rankedByDetection[i].circuit === selected ? CHART.series[1] : CHART.status.muted
              }
            />
          </Plot>
        </ChartFrame>
      </div>
    </details>
  );
}

export default function ConfigExplorerView() {
  const [query, setQuery] = useState("");
  const [overrides, setOverrides] = useState<Overrides>({});
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  const [circuit, setCircuit] = useState(DEFAULT_EVENT_CIRCUIT);

  const profile = eventProfile(circuit);
  const eventCategory = useMemo(() => buildEventCategory(profile), [profile]);

  const categories = useMemo(
    () => [CONFIG_CATEGORIES[0], eventCategory, ...CONFIG_CATEGORIES.slice(1)],
    [eventCategory],
  );

  const totalVariables = useMemo(
    () => categories.reduce((n, c) => n + c.variables.length, 0),
    [categories],
  );

  const filtered = useMemo(
    () =>
      categories
        .map((c) => ({
          ...c,
          variables: c.variables.filter((v) => matches(v, query)),
        }))
        .filter((c) => c.variables.length > 0),
    [categories, query],
  );

  const matchedCount = useMemo(() => filtered.reduce((n, c) => n + c.variables.length, 0), [filtered]);
  const changedCount = Object.keys(overrides).length;

  function setValue(id: string, next: ConfigVariable["value"]) {
    setOverrides((prev) => ({ ...prev, [id]: next }));
  }
  function resetValue(id: string) {
    setOverrides((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }
  function resetAll() {
    setOverrides({});
  }
  async function copyOverrides() {
    const payload = Object.fromEntries(
      Object.entries(overrides).map(([id, value]) => {
        const v = categories.flatMap((c) => c.variables).find((v) => v.id === id);
        return [v ? `${v.file}#${v.path}` : id, value];
      }),
    );
    await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
    setCopyState("copied");
    setTimeout(() => setCopyState("idle"), 1500);
  }

  return (
    <main className={s.main}>
      <header className={s.head}>
        <p className={s.kicker}>System configuration</p>
        <h1 className={s.title}>
          Every <em>Variable</em>
        </h1>
        <p className={s.lede}>
          Every configurable, non-learned value that a decision, model, or eligibility gate reads —
          transcribed from <code>config/*.yaml</code> and cross-referenced against{" "}
          <code>TrackShift AGENTS.md</code>, <code>MODELS.md</code>, and <code>API.md</code>. This is
          a snapshot, not a live read: <code>{snapshotOf.join(", ")}</code> are the files it came from.
          Editing here changes only what this page shows — there is no route in <code>API.md</code>{" "}
          yet for writing config back, so nothing is silently saved.
        </p>
      </header>

      <div className={s.eventBar}>
        <label className={s.eventField}>
          <span>Race</span>
          <select value={circuit} onChange={(e) => setCircuit(e.target.value)}>
            {EVENT_PROFILES.map((p) => (
              <option key={p.circuit} value={p.circuit} disabled={!p.completeInMirror}>
                {p.eventDisplay}
                {p.completeInMirror ? "" : " (incomplete mirror)"}
              </option>
            ))}
          </select>
        </label>
        <label className={s.eventField}>
          <span>Year</span>
          <select value="2026" disabled>
            <option value="2026">2026</option>
          </select>
        </label>
        <p className={s.eventNote}>
          2026 is the only season with a 2026 Overtake rule config — 2022–2025 predate it and use
          historical-DRS proxies instead (AGENTS.md §41), which this explorer does not model as
          rule defaults. The &quot;Event-specific defaults&quot; category below updates with the
          race you pick; every other category is season-wide and does not change with it, because
          no event currently overrides a season value (every <code>overrides:</code> block in{" "}
          <code>config/rules/2026/*.yaml</code> is empty today).
        </p>
      </div>

      <div className={s.toolbar}>
        <input
          type="search"
          className={s.search}
          placeholder="Search variables, files, models, routes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className={s.count}>
          {matchedCount} / {totalVariables} variables
        </span>
        {changedCount > 0 ? (
          <>
            <span className={s.count}>{changedCount} changed</span>
            <button type="button" className={s.toolbarButton} onClick={resetAll}>
              Reset all
            </button>
            <button type="button" className={s.toolbarButton} onClick={copyOverrides}>
              {copyState === "copied" ? "Copied" : "Copy overrides"}
            </button>
          </>
        ) : null}
      </div>

      <div className={s.categories}>
        {filtered.map((c) => (
          <details key={c.id} className={s.category} open>
            <summary className={s.categorySummary}>
              <span className={s.categoryTitle}>{c.title}</span>
              <span className={s.categoryCount}>{c.variables.length}</span>
            </summary>
            <p className={s.categoryDescription}>{c.description}</p>
            <div className={s.rows}>
              {c.variables.map((v) => (
                <VariableRow
                  key={v.id}
                  variable={v}
                  value={v.id in overrides ? overrides[v.id] : v.value}
                  changed={v.id in overrides}
                  onChange={(next) => setValue(v.id, next)}
                  onReset={() => resetValue(v.id)}
                />
              ))}
            </div>
          </details>
        ))}
        {filtered.length === 0 ? <p className={s.empty}>No variable matches &quot;{query}&quot;.</p> : null}
      </div>

      {query === "" ? <CircuitComparison selected={circuit} /> : null}
    </main>
  );
}
