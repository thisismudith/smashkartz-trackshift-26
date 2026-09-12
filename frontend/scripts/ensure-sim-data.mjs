/**
 * Build the sim artifacts if they are not there, before `npm run dev` / `npm run build`.
 *
 * public/sim is gitignored BUILD OUTPUT, so the normal state of a fresh clone -- or of a
 * second machine that just pulled -- is that it holds nothing. The app then fetched
 * /sim/index.json, got the dev server's HTML 404 page back, and reported
 * `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`, which reads like corrupt
 * data rather than data that was never built.
 *
 * This closes that: if the artifacts are absent and the raw mirror IS present, it runs the
 * Python build. What it deliberately does NOT do is pretend it can fix the other case --
 * the mirror is 8 GB per season and nothing here can conjure it -- so a machine without
 * one gets a precise instruction instead of a stack trace, and dev still starts (the app
 * now reports the missing artifacts legibly on its own).
 *
 * Run directly to check or repair at any time:  node scripts/ensure-sim-data.mjs
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const REPO = dirname(FRONTEND);
const SIM = join(FRONTEND, "public", "sim");
const YEAR = process.env.TRACKSHIFT_YEAR || "2026";

const say = (m) => console.log(`[sim-data] ${m}`);

/** Artifacts are usable only if the index AND everything it names are on disk.
 *  A half-populated directory is the case a bare existsSync(index.json) would miss. */
function artifactsPresent() {
  const pointerPath = join(SIM, "index.json");
  if (!existsSync(pointerPath)) return { ok: false, why: "no public/sim/index.json" };
  let index;
  try {
    const pointer = JSON.parse(readFileSync(pointerPath, "utf8"));
    const indexPath = join(SIM, pointer.latest);
    if (!existsSync(indexPath)) return { ok: false, why: `index.json names ${pointer.latest}, which is absent` };
    index = JSON.parse(readFileSync(indexPath, "utf8"));
  } catch (e) {
    return { ok: false, why: `index unreadable (${e.message})` };
  }
  const needed = [index.catalogue, index.params, ...Object.values(index.tracks ?? {})];
  for (const perSession of Object.values(index.sessions ?? {})) {
    for (const files of Object.values(perSession)) needed.push(files.manifest, files.bin);
  }
  const missing = needed.filter((n) => n && !existsSync(join(SIM, n)));
  if (missing.length) return { ok: false, why: `${missing.length} artifact(s) named by the index are absent` };
  return { ok: true, tracks: Object.keys(index.tracks ?? {}).length, year: index.year };
}

/** The same three layouts scripts/simdata/paths.py accepts, in the same order. */
function rawMirror() {
  const candidates = [
    join(REPO, "data", "raw", "tracinginsights", YEAR),
    join(REPO, "data", "raw", YEAR),
    join(REPO, "data", YEAR),
  ];
  return candidates.find((c) => existsSync(c)) ?? null;
}

function python() {
  for (const exe of [process.env.TRACKSHIFT_PYTHON, "python", "python3", "py"].filter(Boolean)) {
    const probe = spawnSync(exe, ["--version"], { stdio: "ignore", shell: false });
    if (!probe.error && probe.status === 0) return exe;
  }
  return null;
}

const state = artifactsPresent();
if (state.ok) {
  say(`ok - ${state.tracks} circuit(s) built${state.year ? ` from ${state.year}` : ""}`);
  process.exit(0);
}

// Artifacts hosted elsewhere: the local directory is supposed to be empty.
if (process.env.NEXT_PUBLIC_SIM_BASE) {
  say(`${state.why}, but NEXT_PUBLIC_SIM_BASE is set - using ${process.env.NEXT_PUBLIC_SIM_BASE}`);
  process.exit(0);
}

say(`${state.why}`);

const mirror = rawMirror();
if (!mirror) {
  say(`no raw ${YEAR} mirror either, so there is nothing to build from.`);
  say(`  1. download it:  .\\scripts\\data\\download_initial_dataset.ps1 -Years ${YEAR}`);
  say(`  2. build it:     python scripts/build_sim_data.py --year ${YEAR} --all --jobs 0 --fresh --prune`);
  say(`Or set NEXT_PUBLIC_SIM_BASE to where the artifacts are hosted.`);
  say(`Starting anyway - the app will report the missing artifacts on each panel.`);
  process.exit(0);
}

const exe = python();
if (!exe) {
  say(`found the raw mirror at ${mirror} but no python on PATH to build with.`);
  say(`Install Python, or set TRACKSHIFT_PYTHON to the interpreter. Starting anyway.`);
  process.exit(0);
}

say(`building from ${mirror} - first run takes about a minute for all circuits`);
const build = spawnSync(
  exe,
  ["scripts/build_sim_data.py", "--year", YEAR, "--all", "--jobs", "0", "--fresh", "--prune"],
  { cwd: REPO, stdio: "inherit", shell: false },
);

if (build.status !== 0) {
  say(`build failed (exit ${build.status}). Starting anyway; run it by hand to see why.`);
  process.exit(0);
}

const after = artifactsPresent();
say(after.ok ? `built ${after.tracks} circuit(s) from ${YEAR}` : `build finished but ${after.why}`);
process.exit(0);
