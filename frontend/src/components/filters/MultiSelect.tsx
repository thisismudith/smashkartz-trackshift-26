/**
 * A searchable multi-select.
 *
 * Built rather than installed because the app carries no UI dependency and the theme is specific:
 * square corners, 1px borders, uppercase micro-labels, flat alpha blends of the Haas palette.
 *
 * Two decisions worth knowing about:
 *
 * 1. `emptyMeans` decides what an empty selection MEANS, and the control must be consistent
 *    with it end to end.
 *
 *    "all" (a filter facet): empty is the default, unfiltered state. The trigger reads "All",
 *    and the All button CLEARS the selection rather than enumerating every option -- ticking
 *    13 circuits to say "all circuits" produces 13 chips and a URL nobody can read, to express
 *    the state you were already in. There is no None, because "none" is not a filter, it is an
 *    empty page.
 *
 *    "none" (a series picker, e.g. which drivers to plot): empty is a real choice and shows
 *    nothing, so All and None are both meaningful and both enumerate.
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
  /**
   * What an empty selection means. "all" for a filter facet (empty = unfiltered, and the All
   * button clears); "none" for a series picker (empty = show nothing). Default "none".
   */
  emptyMeans?: "all" | "none";
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
  emptyMeans = "none",
}: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const panelId = useId();

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const enabled = useMemo(() => options.filter((o) => !o.disabled), [options]);

  const searching = query.trim().length > 0;

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
    const next = selectedSet.has(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
    // Under emptyMeans="all", "everything ticked" and "nothing ticked" are the SAME state, so
    // collapse to the canonical empty one. Otherwise ticking the last box would leave 13 chips
    // and a long URL describing the default.
    if (emptyMeans === "all" && next.length === enabled.length) onChange([]);
    else onChange(next);
  };

  // Bulk actions apply to what is CURRENTLY VISIBLE, so "All" after a search means "all matches"
  // rather than silently selecting things the user cannot see.
  const visible = filtered.filter((o) => !o.disabled).map((o) => o.value);
  const impliesAll = emptyMeans === "all";
  // With emptyMeans="all", selecting everything IS the empty state -- so All clears instead of
  // enumerating. While a search is active it still means "add these matches", because clearing
  // there would throw away a narrowing the user just made.
  const selectAllVisible = () =>
    impliesAll && !searching ? onChange([]) : onChange([...new Set([...selected, ...visible])]);
  const clearVisible = () => onChange(selected.filter((v) => !visible.includes(v)));
  const invertVisible = () =>
    onChange([
      ...selected.filter((v) => !visible.includes(v)),
      ...visible.filter((v) => !selectedSet.has(v)),
    ]);

  const count = selected.length;
  const total = enabled.length;

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
          setOpen((v) => {
            const next = !v;
            // preventScroll matters more than it looks: the bar sits near the top of the page,
            // so a plain focus() scrolls the panel into view and yanks the reader back to the
            // top mid-analysis. That jump is what makes a filter feel like a page reload.
            if (next) window.setTimeout(() => searchRef.current?.focus({ preventScroll: true }), 0);
            return next;
          });
        }}
      >
        <span className={s.triggerLabel}>{label}</span>
        <span className={s.triggerValue}>
          {count === 0
            ? placeholder
            : count === total && impliesAll
              ? placeholder
              : `${count} of ${total}`}
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
              {searching ? "Add matches" : "All"}
            </button>
            {impliesAll ? null : (
              <button type="button" className={s.bulkBtn} onClick={clearVisible}>
                None
              </button>
            )}
            <button type="button" className={s.bulkBtn} onClick={invertVisible}>
              Invert
            </button>
            <span className={s.bulkCount}>
              {filtered.length} shown · {count === 0 && impliesAll ? "all" : `${count} selected`}
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
