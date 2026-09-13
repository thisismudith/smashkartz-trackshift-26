/**
 * The one piece of shared chrome.
 *
 * Before this existed, nothing in the app linked to /sim or /sim/new -- the only links in the
 * whole codebase were / <-> /about, so the two surfaces that actually worked were reachable
 * only by typing the URL.
 *
 * It deliberately does NOT render on the simulator routes: the sim HUD owns the full viewport
 * and docks its own panels into all four corners, so a bar across the top would sit on top of
 * the leaderboard. Those routes get a single back affordance instead, inside their own HUD.
 */
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import s from "./nav.module.css";

interface Entry {
  href: string;
  label: string;
  /** Named so a reader knows why something is dimmed rather than guessing it is broken. */
  pending?: string;
}

/** The working path through observed evidence. These remain prominent on every analytical page. */
const PRIMARY_ENTRIES: Entry[] = [
  { href: "/sessions", label: "Sessions" },
  { href: "/insights", label: "Insights" },
  { href: "/rules", label: "Rules" },
  { href: "/decision", label: "Decision", pending: "awaiting strategic replay artifacts" },
];

/** Secondary evidence and parameter surfaces. They are useful, but not the first lap of analysis. */
const TOOL_ENTRIES: Entry[] = [
  { href: "/league", label: "League" },
  { href: "/lab", label: "Lab" },
  // This is only a link. The simulator itself owns its route and remains out of this workstream.
  { href: "/sim", label: "Replay" },
  { href: "/about", label: "About" },
];

/** Routes that own the whole viewport and must not get a bar over the top. */
const FULL_BLEED = [/^\/sim(\/|$)/];

export default function SiteNav() {
  const pathname = usePathname() ?? "/";
  if (FULL_BLEED.some((r) => r.test(pathname))) return null;

  return (
    <nav className={s.bar} aria-label="Primary">
      <Link href="/" className={s.brand}>
        Smash<em>Kartz</em>
      </Link>
      <div className={s.routes}>
        <NavLinks entries={PRIMARY_ENTRIES} pathname={pathname} className={s.list} />
        <span className={s.divider} aria-hidden="true" />
        <NavLinks entries={TOOL_ENTRIES} pathname={pathname} className={s.tools} />
      </div>
    </nav>
  );
}

function NavLinks({ entries, pathname, className }: { entries: Entry[]; pathname: string; className: string }) {
  return (
    <ul className={className}>
      {entries.map((e) => {
        const active = pathname === e.href || pathname.startsWith(e.href + "/");
        return (
          <li key={e.href}>
            <Link
              href={e.href}
              className={s.link}
              aria-current={active ? "page" : undefined}
              title={e.pending}
            >
              {e.label}
              {e.pending ? <span className={s.pendingDot} aria-hidden="true" /> : null}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
