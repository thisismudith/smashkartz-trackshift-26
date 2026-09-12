/**
 * A searchable multi-select.
 *
 * Built rather than installed because the app carries no UI dependency and the theme is specific:
 * square corners, 1px borders, uppercase micro-labels, flat alpha blends of the Haas palette.
 *
 * Two decisions worth knowing about:
 *
 * 1. EMPTY MEANS EMPTY. An empty selection shows nothing, and the caller renders an explicit
 *    "nothing selected" state. The tempting alternative -- empty implies all -- makes "I
 *    deselected everything" and "I have not chosen yet" render identically, which is the same
 *    class of error as zero-filling a missing value.
 *
 * 2. OPTIONS CARRY THEIR OWN COLOUR. The swatch comes from the option, not from its position in
 *    the list, so removing a driver never recolours the survivors.
 */
"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import s from "./filters.module.css";

export interface SelectOption {
  value: string;
  label: string;
  /** Optional grouping header, e.g. the team a driver drives for. */
  group?: string;
  /** Swatch colour. Belongs to the entity, never to its rank in the list. */
  colour?: string;
  /** Dashed swatch, matching a dashed series (the second car of a team). */
  dashed?: boolean;
  /** Small right-aligned hint: a car number, a lap count, a session count. */
  meta?: string;
  /** Extra text matched by the search box but not displayed, e.g. a full name. */
  keywords?: string;
  disabled?: boolean;
  /** Shown on hover when disabled. Never disable something without saying why. */
  disabledReason?: string;
}

export interface MultiSelectProps {
  label: string;
  options: SelectOption[];
  selected: readonly string[];
  onChange: (next: string[]) => void;
  /** Shown in the trigger when nothing is selected. */
  placeholder?: string;
  /** Hide the chip row (for a compact bar). Defaults to showing chips. */
  showChips?: boolean;
  /** Disable the whole control, with a reason. */
  disabled?: boolean;
  disabledReason?: string;
  /** Note rendered inside the panel, above the list. Use it to explain a facet's limits. */
  note?: string;
}

export function MultiSelect({
  label,
  options,
  selected,
  onChange,
  placeholder = "None",
  showChips = true,
  disabled = false,
  disabledReason,
  note,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const panelId = useId();

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const enabled = useMemo(() => options.filter((o) => !o.disabled), [options]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) =>
      `${o.label} ${o.group ?? ""} ${o.keywords ?? ""} ${o.meta ?? ""}`.toLowerCase().includes(q),
    );
  }, [options, query]);

  // Preserve the caller's option order inside each group, and the order groups first appear in.
  const groups = useMemo(() => {
    const map = new Map<string, SelectOption[]>();
    for (const o of filtered) {
      const g = o.group ?? "";
      const arr = map.get(g);
      if (arr) arr.push(o);
      else map.set(g, [o]);
    }
    return [...map.entries()];
  }, [filtered]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = (value: string) => {
    onChange(
      selectedSet.has(value) ? selected.filter((v) => v !== value) : [...selected, value],
    );
  };

  // Bulk actions apply to what is CURRENTLY VISIBLE, so "All" after a search means "all matches"
  // rather than silently selecting things the user cannot see.
  const visible = filtered.filter((o) => !o.disabled).map((o) => o.value);
  const selectAllVisible = () => onChange([...new Set([...selected, ...visible])]);
  const clearVisible = () => onChange(selected.filter((v) => !visible.includes(v)));
  const invertVisible = () =>
    onChange([
      ...selected.filter((v) => !visible.includes(v)),
      ...visible.filter((v) => !selectedSet.has(v)),
    ]);

  const count = selected.length;
  const total = enabled.length;
  const searching = query.trim().length > 0;

  return (
    <div className={s.root} ref={rootRef}>
      <button
        type="button"
        className={s.trigger}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="listbox"
        disabled={disabled}
        title={disabled ? disabledReason : undefined}
        onClick={() => {
          setOpen((v) => !v);
          window.setTimeout(() => searchRef.current?.focus(), 0);
        }}
      >
        <span className={s.triggerLabel}>{label}</span>
        <span className={s.triggerValue}>
          {count === 0 ? placeholder : count === total ? `All ${total}` : `${count} of ${total}`}
        </span>
        <span className={s.caret} aria-hidden="true" />
      </button>

      {open ? (
        <div className={s.panel} id={panelId} role="dialog" aria-label={label}>
          <input
            ref={searchRef}
            className={s.search}
            type="search"
            value={query}
            placeholder={`Search ${label.toLowerCase()}…`}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={`Search ${label}`}
          />

          <div className={s.bulk}>
            <button type="button" className={s.bulkBtn} onClick={selectAllVisible}>
              {searching ? "All matches" : "All"}
            </button>
            <button type="button" className={s.bulkBtn} onClick={clearVisible}>
              None
            </button>
            <button type="button" className={s.bulkBtn} onClick={invertVisible}>
              Invert
            </button>
            <span className={s.bulkCount}>
              {filtered.length} shown · {count} selected
            </span>
          </div>

          {note ? <p className={s.panelNote}>{note}</p> : null}

          <ul className={s.list} role="listbox" aria-multiselectable="true">
            {groups.length === 0 ? (
              <li className={s.noMatch}>No match for “{query}”.</li>
            ) : (
              groups.map(([group, items]) => (
                <li key={group || "__ungrouped__"}>
                  {group ? <p className={s.groupHead}>{group}</p> : null}
                  <ul className={s.groupList}>
                    {items.map((o) => {
                      const on = selectedSet.has(o.value);
                      return (
                        <li key={o.value}>
                          <button
                            type="button"
                            role="option"
                            aria-selected={on}
                            className={s.option}
                            disabled={o.disabled}
                            title={o.disabled ? o.disabledReason : undefined}
                            onClick={() => toggle(o.value)}
                          >
                            <span className={s.check} data-on={on ? "true" : undefined} aria-hidden="true" />
                            {o.colour ? (
                              <span
                                className={s.optSwatch}
                                data-dashed={o.dashed ? "true" : undefined}
                                style={{ background: o.colour }}
                              />
                            ) : null}
                            <span className={s.optLabel}>{o.label}</span>
                            {o.meta ? <span className={s.optMeta}>{o.meta}</span> : null}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}

      {showChips && count > 0 ? (
        <ul className={s.chips}>
          {selected.map((v) => {
            const o = options.find((x) => x.value === v);
            return (
              <li key={v}>
                <button
                  type="button"
                  className={s.chip}
                  onClick={() => toggle(v)}
                  title={`Remove ${o?.label ?? v}`}
                >
                  {o?.colour ? (
                    <span
                      className={s.optSwatch}
                      data-dashed={o.dashed ? "true" : undefined}
                      style={{ background: o.colour }}
                    />
                  ) : null}
                  {o?.label ?? v}
                  <span className={s.chipX} aria-hidden="true">
                    ×
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
