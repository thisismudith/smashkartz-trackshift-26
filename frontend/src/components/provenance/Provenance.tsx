/**
 * The badge that sits beside every number, and the component that renders a missing one.
 *
 * API.md 8 makes two demands this file answers together: show the provenance tag next to
 * every number, and make the tags tell apart. The second is why the badge is not just a
 * coloured dot — on a three-hue palette five tags cannot each have their own colour, and a
 * colour-blind reader would lose the distinction regardless. Each tag therefore carries a
 * word, and shape (border weight, fill, letter-spacing) does the work colour cannot.
 *
 * `<Value>` exists so that rendering a number and rendering its absence go through the
 * same component. If they were separate, the absent case would eventually be written as
 * `{q.value ?? 0}` at some call site and a missing gap would read as a car alongside.
 */
import type { ReactNode } from "react";
import type { Displayed } from "@/serve/format";
import { meaningOf } from "@/serve/provenance";
import type { Provenance as Tag } from "@/serve/types";
import s from "./provenance.module.css";

export function ProvenanceBadge({ tag, source }: { tag?: Tag | string | null; source?: string }) {
  const m = meaningOf(tag);
  // The citation, when there is one, belongs in the same tooltip as the tag's meaning:
  // they answer the same question and splitting them means hovering twice.
  const title = source ? `${m.title}\n\nSource: ${source}` : m.title;
  return (
    <abbr className={`${s.badge} ${s[m.tone]}`} title={title} data-tone={m.tone}>
      {m.short}
    </abbr>
  );
}

/**
 * A formatted value with its tag, or "Unavailable" with the reason it is missing.
 * The reason is in the tooltip AND marked up, so it survives a screen reader.
 */
export function Value({ d, source, className }: { d: Displayed; source?: string; className?: string }) {
  if (d.kind === "unavailable") {
    return (
      <span className={`${s.value} ${className ?? ""}`}>
        <abbr className={s.unavailable} title={d.reason}>
          Unavailable
        </abbr>
        <ProvenanceBadge tag={d.provenance} source={source} />
        <span className={s.srOnly}>: {d.reason}</span>
      </span>
    );
  }
  return (
    <span className={`${s.value} ${className ?? ""}`}>
      <span className={s.number}>{d.text}</span>
      <ProvenanceBadge tag={d.provenance} source={source} />
    </span>
  );
}

/** A labelled row in a panel: term on the left, value and tag on the right. */
export function Field({
  label,
  d,
  source,
  note,
}: {
  label: string;
  d: Displayed;
  source?: string;
  note?: ReactNode;
}) {
  return (
    <div className={s.field}>
      <dt className={s.label}>{label}</dt>
      <dd className={s.data}>
        <Value d={d} source={source} />
        {note ? <span className={s.note}>{note}</span> : null}
      </dd>
    </div>
  );
}

/** The legend. Rendered once per page that shows tagged numbers. */
export function ProvenanceLegend({ tags }: { tags: Array<Tag | string> }) {
  return (
    <div className={s.legend}>
      <span className={s.legendLabel}>Provenance</span>
      <ul className={s.legendList}>
        {tags.map((t) => {
          const m = meaningOf(t);
          return (
            <li key={String(t)} className={s.legendItem}>
              <ProvenanceBadge tag={t} />
              <span className={s.legendText}>{m.title}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
