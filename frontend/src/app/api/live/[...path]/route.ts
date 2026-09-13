/**
 * Same-origin proxy to the development model service.
 *
 * WHY THIS EXISTS. `src/trackshift/serve/app.py` mounts no CORS middleware, so a browser at
 * http://localhost:3000 cannot call http://127.0.0.1:8000 directly — the request is blocked
 * before it is sent and arrives in the UI as an indistinguishable network failure. The two
 * ways to fix that are to add CORS to the backend or to proxy from the frontend. API.md 9
 * assigns `src/trackshift/serve/app.py` to a different owner, and a CORS policy is a
 * deployment decision rather than a UI one, so the UI proxies instead. Nothing in the
 * backend changes, and the browser only ever talks to its own origin.
 *
 * It also solves a smaller annoyance honestly: the brief's port 8000 is not always free, so
 * the target is configurable per request rather than only by restarting the dev server.
 *
 * SSRF. A dev server that forwards arbitrary URLs is an open proxy, and this one runs bound
 * to 0.0.0.0 by default. `__base` is therefore validated against a loopback allowlist: http
 * or https, and a hostname of 127.0.0.1 / localhost / ::1. Anything else is refused. That
 * keeps the escape hatch (point at another local port) without turning the dev server into
 * a way to reach the rest of the network.
 */
import { NextResponse } from "next/server";

/** Where the service lives when the request does not say. Matches the integration brief. */
const DEFAULT_BASE = process.env.TRACKSHIFT_API_ORIGIN || "http://127.0.0.1:8000/api/v1";

const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

function problem(status: number, code: string, message: string) {
  return NextResponse.json({ detail: { code, message } }, { status });
}

/** Returns the validated base URL, or a string describing why it was refused. */
function resolveBase(raw: string | null): { base: string } | { refused: string } {
  const candidate = raw ?? DEFAULT_BASE;
  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    return { refused: `"${candidate}" is not an absolute URL.` };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { refused: `protocol ${url.protocol} is not allowed.` };
  }
  if (!LOOPBACK.has(url.hostname)) {
    return {
      refused:
        `host "${url.hostname}" is not loopback. This proxy only forwards to a service on this ` +
        "machine, so it cannot be used to reach anything else on the network.",
    };
  }
  return { base: candidate.replace(/\/+$/, "") };
}

async function forward(req: Request, path: string[], method: "GET" | "POST") {
  const incoming = new URL(req.url);
  const resolved = resolveBase(incoming.searchParams.get("__base"));
  if ("refused" in resolved) {
    return problem(400, "PROXY_TARGET_REFUSED", resolved.refused);
  }

  // Everything except our own control parameter is passed through to the service.
  const forwarded = new URLSearchParams(incoming.searchParams);
  forwarded.delete("__base");
  const qs = forwarded.toString();
  const target = `${resolved.base}/${path.map(encodeURIComponent).join("/")}${qs ? `?${qs}` : ""}`;

  const body = method === "POST" ? await req.text() : undefined;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body,
      cache: "no-store",
    });
  } catch (e) {
    return problem(
      502,
      "UPSTREAM_UNREACHABLE",
      `Could not reach the model service at ${target}: ${e instanceof Error ? e.message : "unknown error"}. ` +
        "Start it with `PYTHONPATH=src .venv/bin/uvicorn trackshift.serve.app:app --host 127.0.0.1 --port 8000`.",
    );
  }

  const text = await upstream.text();
  const headers = new Headers({
    "Content-Type": upstream.headers.get("content-type") ?? "application/json",
    "Cache-Control": "no-store",
    "X-TrackShift-Proxied-To": target,
  });
  // API.md 3.7 signals stub and unverified responses in headers; they must survive the hop.
  for (const h of ["x-trackshift-stub", "x-trackshift-unverified"]) {
    const v = upstream.headers.get(h);
    if (v) headers.set(h, v);
  }
  return new NextResponse(text, { status: upstream.status, headers });
}

export async function GET(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path ?? [], "GET");
}

export async function POST(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return forward(req, (await ctx.params).path ?? [], "POST");
}
