/**
 * The wrapper every chart wears: title, units, provenance tag, legend, note, and the
 * "show table" fallback.
 *
 * The table is not a nicety. A chart whose contrast the validator only WARNed on, or whose
 * series outnumber the three validated hues (driver series wear team colours), is only
 * legible because identity is also available as text -- so the table ships with every chart
 * rather than being added when someone asks (UI.md section 7.3).
 */
"use client";

import { useId, useState, type ReactNode } from "react";
import s from "./charts.module.css";

export type Provenance = "OBSERVED" | "DERIVED" | "INFERRED" | "SIMULATED" | "RULE" | "MISSING";

export interface LegendEntry {
  label: string;
  colour: string;
  shape?: "line" | "dot";
  /** Drawn as a dashed swatch, matching a dashed line (teammates). */
  dashed?: boolean;
}

export interface TableSpec {
  columns: string[];
  rows: (string | number | null)[][];
}

export function ChartFrame({
  title,
  units,
  provenance,
  legend,
  note,
  table,
  hidden,
  onToggleSeries,
  controls,
  children,
}: {
  title: string;
  units?: string;
  provenance?: Provenance;
  /** Omit for a single series -- the title names it (UI.md section 7.3). */
  legend?: LegendEntry[];
  note?: ReactNode;
  table?: TableSpec;
  /** Labels currently hidden. Supply with onToggleSeries to make the legend interactive. */
  hidden?: ReadonlySet<string>;
  /** Called with a series label when its legend entry is clicked. The PARENT decides what to do
   * (usually: stop passing that series to the marks). Kept out here so the frame never has to
   * know how the caller builds its series. */
  onToggleSeries?: (label: string) => void;
  /** Chart-local controls docked into the header, right of the title. */
  controls?: ReactNode;
  children: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();

  return (
    <figure className={s.figure}>
      <div className={s.head}>
        <figcaption className={s.title}>{title}</figcaption>
        {units ? <span className={s.units}>{units}</span> : null}
        {provenance ? (
          <span className={s.prov} data-kind={provenance.toLowerCase()}>
            {provenance}
          </span>
        ) : null}
      </div>

      {controls ? <div className={s.controls}>{controls}</div> : null}

      {legend && legend.length > 1 ? (
        <div className={s.legend}>
          {legend.map((e) => {
            const off = hidden?.has(e.label) ?? false;
            const swatch = (
              <span
                className={s.swatch}
                data-shape={e.shape ?? "line"}
                data-dashed={e.dashed ? "true" : undefined}
                style={{ background: e.colour }}
              />
            );
            if (!onToggleSeries) {
              return (
                <span key={e.label} className={s.legendItem}>
                  {swatch}
                  {e.label}
                </span>
              );
            }
            return (
              <button
                key={e.label}
                type="button"
                className={s.legendItem}
                aria-pressed={!off}
                title={off ? `Show ${e.label}` : `Hide ${e.label}`}
                onClick={() => onToggleSeries(e.label)}
              >
                {swatch}
                {e.label}
              </button>
            );
          })}
        </div>
      ) : null}

      <div className={s.plotWrap}>{children}</div>

      {note ? <p className={s.note}>{note}</p> : null}

      {table ? (
        <>
          <button
            type="button"
            className={s.tableToggle}
            aria-expanded={showTable}
            aria-controls={tableId}
            onClick={() => setShowTable((v) => !v)}
          >
            {showTable ? "Hide table" : "Show table"}
          </button>
          {showTable ? (
            <div className={s.tableWrap} id={tableId}>
              <table className={s.table}>
                <thead>
                  <tr>
                    {table.columns.map((c) => (
                      <th key={c} scope="col">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((r, i) => (
                    <tr key={i}>
                      {r.map((cell, j) => (
                        <td key={j}>{cell === null ? "—" : cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}
    </figure>
  );
}

/**
 * The empty state for a panel whose model does not exist yet.
 *
 * Named deliberately: "missing means missing" (API.md section 1.5) applied to a whole panel.
 * It states which model is absent and which route will serve it, so the gap reads as a known
 * boundary rather than as something forgotten.
 */
export function AwaitingModel({
  title,
  model,
  route,
  detail,
}: {
  title: string;
  model: string;
  route?: string;
  detail?: ReactNode;
}) {
  return (
    <figure className={s.figure}>
      <div className={s.head}>
        <figcaption className={s.title}>{title}</figcaption>
      </div>
      <div className={s.empty}>
        <span className={s.emptyHead}>Awaiting model — {model}</span>
        {detail ? <span className={s.emptyBody}>{detail}</span> : null}
        {route ? <span className={s.emptyMeta}>source when available: {route}</span> : null}
      </div>
    </figure>
  );
}

/** The empty state for data that is absent or untrustworthy for THIS session (UI.md 1.4). */
export function NoData({ title, reason }: { title: string; reason: ReactNode }) {
  return (
    <figure className={s.figure}>
      <div className={s.head}>
        <figcaption className={s.title}>{title}</figcaption>
        <span className={s.prov} data-kind="missing">
          unavailable
        </span>
      </div>
      <div className={s.empty}>
        <span className={s.emptyBody}>{reason}</span>
      </div>
    </figure>
  );
}
