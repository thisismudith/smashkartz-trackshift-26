/**
 * Serves a replay bundle to the browser from a directory OUTSIDE the repository.
 *
 * API.md 2 requires the UI to work against the static bundle with no backend running. The
 * browser cannot read a local directory, so something has to serve it. The obvious move —
 * copy the bundle into `frontend/public/` — is the one thing the integration brief forbids:
 * it puts generated JSON inside the repo where `git add -A` will eventually commit it. So
 * the bundle stays where `build_replay_bundle.py --out` wrote it and this handler streams
 * it, keeping the repo clean by construction rather than by remembering to gitignore.
 *
 * Point it at a bundle with:
 *   TRACKSHIFT_REPLAY_DIR=/path/to/bundle npm run dev
 *
 * Only the nine filenames the generator writes are serviceable. That is an allowlist, not
 * a sanitiser: `path` comes from the URL, and `../../` games against a joined path are a
 * real risk on a dev server that someone will inevitably run bound to 0.0.0.0. An allowlist
 * of literal basenames cannot traverse, so there is nothing to get wrong.
 */
import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";

/** Exactly what scripts/serve/build_replay_bundle.py writes. */
const SERVEABLE = new Set([
  "meta.json",
  "track.json",
  "rules.json",
  "battles.json",
  "validation.json",
  "timeline.json",
  "policies.json",
  "plan.json",
  "simulate.json",
  "bundle_manifest.json",
]);

function problem(status: number, code: string, message: string) {
  return NextResponse.json({ detail: { code, message } }, { status });
}

export async function GET(_req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const dir = process.env.TRACKSHIFT_REPLAY_DIR;
  if (!dir) {
    return problem(
      503,
      "REPLAY_DIR_UNSET",
      "TRACKSHIFT_REPLAY_DIR is not set, so there is no replay bundle to read. Build one with " +
        "`PYTHONPATH=src .venv/bin/python scripts/serve/build_replay_bundle.py --out <dir>` and " +
        "start the dev server with TRACKSHIFT_REPLAY_DIR=<dir>.",
    );
  }

  const segments = (await ctx.params).path ?? [];
  const name = segments.length === 1 ? segments[0] : "";
  if (!SERVEABLE.has(name)) {
    return problem(
      404,
      "NOT_IN_BUNDLE",
      `"${segments.join("/")}" is not a replay bundle file. The bundle contains: ${[...SERVEABLE].join(", ")}.`,
    );
  }

  try {
    const body = await readFile(path.join(dir, name), "utf8");
    return new NextResponse(body, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        // A bundle is rebuilt in place while developing; a cached copy would show stale runs.
        "Cache-Control": "no-store",
        // So the UI can say which mode answered without inspecting the body.
        "X-TrackShift-Source": "replay",
      },
    });
  } catch (e) {
    const err = e as NodeJS.ErrnoException;
    if (err.code === "ENOENT") {
      return problem(
        404,
        "BUNDLE_FILE_MISSING",
        `${name} is not in ${dir}. The bundle may be incomplete, or TRACKSHIFT_REPLAY_DIR may point somewhere else.`,
      );
    }
    return problem(500, "BUNDLE_READ_FAILED", `Could not read ${name} from ${dir}: ${err.message}`);
  }
}
