/**
 * The app-level claim about what the whole page is made of.
 *
 * It reads `GET /meta` once and answers a single question loudly: is anything on this
 * screen calibrated real-race evidence? Right now the answer is no — `app.py` returns
 * `release_ready: false` and declares `synthetic_fixture`, and `fixture.py` is explicit
 * that its outputs "are development evidence, not observed race data".
 *
 * So the banner is not decoration and it is not dismissible. A viewer who scrolls past it
 * and reads a pass probability must not be able to reach the conclusion that the number
 * describes a race that happened. The two forbidden phrasings — "live telemetry" and
 * "final release" — are precisely the ones a synthetic fixture invites, which is why the
 * required sentence is a constant in this file rather than something each page writes.
 *
 * It renders on every route except the simulator, which owns the full viewport (the same
 * exclusion SiteNav makes, for the same reason). The simulator does not read this API.
 */
"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { fetchMeta } from "@/serve/client";
import { makeSource, type SourceMode } from "@/serve/source";
import type { MetaResponse } from "@/serve/types";
import type { Result } from "@/serve/guards";
import s from "./status.module.css";

/** The exact sentence the integration contract requires. Do not reword per-page. */
export const SYNTHETIC_NOTICE = "Synthetic development mode — not calibrated real-race evidence.";

const FULL_BLEED = [/^\/sim(\/|$)/];

/**
 * True when the backend says this is not release evidence. Either signal alone is enough:
 * `release_ready: false` is the explicit gate, and a declared `synthetic_fixture` means a
 * fixture is answering even if some future build forgets to set the flag.
 */
export function isSynthetic(meta: MetaResponse): boolean {
  return meta.release_ready === false || Boolean(meta.synthetic_fixture);
}

export default function ApiStatusBanner() {
  const pathname = usePathname() ?? "/";
  const [state, setState] = useState<Result<MetaResponse> | null>(null);
  const [mode, setMode] = useState<SourceMode>("live");

  useEffect(() => {
    let alive = true;
    // Try live first; fall back to the replay bundle so the banner is still truthful when
    // no backend is running but a bundle is mounted.
    (async () => {
      const first = await fetchMeta(makeSource("live"));
      if (first.ok) {
        if (alive) { setState(first); setMode("live"); }
        return;
      }
      const second = await fetchMeta(makeSource("replay"));
      if (alive) { setState(second.ok ? second : first); setMode(second.ok ? "replay" : "live"); }
    })();
    return () => { alive = false; };
  }, []);

  if (FULL_BLEED.some((r) => r.test(pathname))) return null;
  if (!state) return null;

  if (!state.ok) {
    // Not an error the user caused, and most of this app does not need the API — so it is
    // stated compactly rather than alarmingly. It is still stated: silence here would let
    // a stale page look connected.
    return (
      <div className={`${s.bar} ${s.offline}`} role="status">
        <span className={s.tag}>Model API</span>
        <span className={s.text}>
          Not reachable — {state.message}. Pages that read the model backend will show this as an
          error rather than a number.
        </span>
      </div>
    );
  }

  const meta = state.data;
  if (!isSynthetic(meta)) {
    return (
      <div className={`${s.bar} ${s.ok}`} role="status">
        <span className={s.tag}>Model API</span>
        <span className={s.text}>
          {meta.mode} · api {meta.api_version} · {meta.git_commit}
        </span>
      </div>
    );
  }

  return (
    <div className={`${s.bar} ${s.synthetic}`} role="alert">
      <span className={s.tagStrong}>Synthetic</span>
      <span className={s.text}>
        <strong className={s.notice}>{SYNTHETIC_NOTICE}</strong>{" "}
        {meta.synthetic_fixture ? (
          <>
            Fixture <code className={s.code}>{meta.synthetic_fixture}</code>.{" "}
          </>
        ) : null}
        {meta.release_ready === false ? "Release-blocked. " : null}
        Served {mode === "replay" ? "from a replay bundle" : `over ${meta.mode}`}, api{" "}
        {meta.api_version}, commit <code className={s.code}>{meta.git_commit}</code>.
        {meta.stubs.length > 0 ? ` Stubbed routes: ${meta.stubs.join(", ")}.` : ""}
      </span>
    </div>
  );
}
