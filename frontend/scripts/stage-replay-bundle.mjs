#!/usr/bin/env node
/**
 * Stage the replay bundle into `public/api/replay/` for the static export.
 *
 * The bundle used to be streamed by a Next route handler from TRACKSHIFT_REPLAY_DIR, a
 * directory outside the repo, precisely so generated files never landed in `public/`.
 * `output: "export"` leaves no server to run that handler, so the nine JSON files have to
 * be real files under `public/` instead. `.gitignore` holds the original line of defence:
 * `/public/api/replay/` is ignored, so a staged copy still cannot be committed.
 *
 * ABSENT IS NOT EMPTY. With no TRACKSHIFT_REPLAY_DIR set, or a directory that holds no
 * JSON, this says so and exits 0 rather than failing the build: replay is one of two
 * delivery modes and a site built without it is a site whose replay toggle reports the
 * bundle as unavailable, which is the behaviour the UI already has. It exits non-zero only
 * when the variable names a directory that genuinely is not there, because that is a typo
 * rather than a decision.
 *
 *   TRACKSHIFT_REPLAY_DIR=/path/to/artifacts/demo/2026_british_grand_prix \
 *     npm run replay:public && npm run build
 */
import { cp, mkdir, readdir, rm, stat } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEST = resolve(HERE, "..", "public", "api", "replay");

const source = process.env.TRACKSHIFT_REPLAY_DIR;

if (!source) {
  console.log(
    "replay:public — TRACKSHIFT_REPLAY_DIR is not set, so no replay bundle was staged.\n" +
    "  The site will build; its replay mode will report the bundle as unavailable.",
  );
  process.exit(0);
}

const from = resolve(source);
let info;
try {
  info = await stat(from);
} catch {
  console.error(
    `replay:public — TRACKSHIFT_REPLAY_DIR points at ${from}, which does not exist.\n` +
    "  Unset it to build without a replay bundle, or correct the path.",
  );
  process.exit(1);
}
if (!info.isDirectory()) {
  console.error(`replay:public — ${from} is not a directory.`);
  process.exit(1);
}

const names = (await readdir(from)).filter((n) => n.endsWith(".json"));
if (names.length === 0) {
  console.log(`replay:public — no .json files in ${from}; nothing staged.`);
  process.exit(0);
}

// Cleared first so a stale file from an earlier bundle cannot be served alongside a newer
// one -- a mixed bundle is the kind of wrong answer nobody would think to look for.
await rm(DEST, { recursive: true, force: true });
await mkdir(DEST, { recursive: true });
for (const name of names) {
  await cp(join(from, name), join(DEST, name));
}

console.log(`replay:public — staged ${names.length} file(s) into public/api/replay/`);
