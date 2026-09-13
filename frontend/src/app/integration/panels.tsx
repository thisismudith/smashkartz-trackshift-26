/**
 * Panel chrome, and the four ways a panel can fail to have data.
 *
 * `ErrorState` is the acceptance requirement "malformed/unsupported API responses render a
 * visible error state", and it distinguishes the four failure kinds because they need
 * different actions from the reader:
 *
 *   network      the backend is not running        -> start it, or switch to replay
 *   http         it ran and refused                -> read the code; 404 may mean wrong id
 *   malformed    it answered with a shape we can't -> the contract moved; this is a bug
 *   unsupported  this mode has no such file        -> switch mode, or rebuild the bundle
 *
 * A single "failed to load" would collapse all four and leave the reader guessing. Each
 * state therefore names the URL it tried, so the same request can be reproduced with curl.
 */
import type { ReactNode } from "react";
import type { Result } from "@/serve/guards";
import s from "./integration.module.css";

export function Panel({
  id,
  title,
  kicker,
  children,
  aside,
}: {
  id: string;
  title: string;
  kicker: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section className={s.panel} id={id} aria-labelledby={`${id}-h`}>
      <header className={s.panelHead}>
        <div>
          <p className={s.kicker}>{kicker}</p>
          <h2 className={s.panelTitle} id={`${id}-h`}>
            {title}
          </h2>
        </div>
        {aside ? <div className={s.panelAside}>{aside}</div> : null}
      </header>
      {children}
    </section>
  );
}

const KIND_WORD: Record<string, string> = {
  network: "Backend not reachable",
  http: "The service refused the request",
  malformed: "Unsupported response shape",
  unsupported: "Not available in this mode",
};

const KIND_ADVICE: Record<string, string> = {
  network:
    "Start the development service, or switch the source above to the replay bundle — the UI is built to work from the bundle alone.",
  http: "Check the error code. UNKNOWN_BATTLE means the battle id is not in this source's index; take ids from /battles rather than typing one.",
  malformed:
    "This is a contract change, not a transient failure: the response parsed as JSON but is not the shape this build renders. Nothing is displayed rather than guessing at the fields.",
  unsupported:
    "The replay bundle is nine files; this route is not one of them. Switch to the live service for it, or read the fallback shown below.",
};

export function ErrorState({ result, what }: { result: Extract<Result<unknown>, { ok: false }>; what: string }) {
  return (
    <div className={`${s.error} ${s[`error_${result.kind}`] ?? ""}`} role="alert">
      <p className={s.errorHead}>
        <span className={s.errorKind}>{KIND_WORD[result.kind] ?? result.kind}</span>
        {result.code ? <code className={s.errorCode}>{result.code}</code> : null}
        {result.status ? <span className={s.errorStatus}>HTTP {result.status}</span> : null}
      </p>
      <p className={s.errorWhat}>{what}</p>
      <p className={s.errorMessage}>{result.message}</p>
      <p className={s.errorAdvice}>{KIND_ADVICE[result.kind]}</p>
      {result.url ? (
        <p className={s.errorUrl}>
          Requested <code>{result.url}</code> in {result.mode} mode.
        </p>
      ) : null}
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className={s.loading} role="status">
      Loading {what}…
    </p>
  );
}

/** A short, framed statement about what a number is and is not. */
export function Callout({ tone = "note", children }: { tone?: "note" | "warn"; children: ReactNode }) {
  return <p className={`${s.callout} ${tone === "warn" ? s.calloutWarn : ""}`}>{children}</p>;
}

/** Renders the zero-violation assertion the acceptance checks ask for. */
export function RuleViolations({ n, context }: { n: number | undefined; context: string }) {
  if (n === undefined) {
    return (
      <span className={s.violationsUnknown}>
        Rule violations: <abbr title={`${context} did not report a rule_violations count, so zero cannot be asserted.`}>Unavailable</abbr>
      </span>
    );
  }
  if (n === 0) {
    return (
      <span className={s.violationsOk}>
        Rule violations: 0 <span className={s.violationsNote}>— {context} reported zero; the rule engine filtered illegal actions before scoring</span>
      </span>
    );
  }
  return (
    <span className={s.violationsBad} role="alert">
      Rule violations: {n} <span className={s.violationsNote}>— {context} must report zero. Treat this result as invalid.</span>
    </span>
  );
}
