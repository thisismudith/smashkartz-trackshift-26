import { chromium } from "playwright";

const BASE = "http://localhost:3000";
const browser = await chromium.launch({
  executablePath: "C:\\Users\\dagam\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (err) => console.log("pageerror:", err.message));

// Snoop every worker->main "pose" message by wrapping Worker before any app code runs.
// POSE_FLOATS_PER_CAR is 13 per worker/protocol.ts; station is float 0 of each car block.
await page.addInitScript(() => {
  window.__poseLog = [];
  window.__rafLog = [];
  const NativeWorker = window.Worker;
  window.Worker = class extends NativeWorker {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (ev) => {
        const msg = ev.data;
        if (msg && msg.type === "pose") {
          const floats = new Float32Array(msg.buffer.slice(0));
          window.__poseLog.push({ t: performance.now(), sessionTime: msg.sessionTime, station0: floats[0], lateral0: floats[1] });
          if (window.__poseLog.length > 20000) window.__poseLog.shift();
        }
      });
    }
  };
  let last = performance.now();
  function raf(now) {
    window.__rafLog.push(now - last);
    last = now;
    if (window.__rafLog.length > 20000) window.__rafLog.shift();
    requestAnimationFrame(raf);
  }
  requestAnimationFrame(raf);
});

console.log("navigating...");
await page.goto(`${BASE}/sim/new`, { waitUntil: "networkidle" });
await page.waitForSelector("select", { timeout: 15000 });
await page.selectOption("select", { label: "British Grand Prix" });
await page.waitForTimeout(500);
await page.getByRole("button", { name: "Start race" }).click();
await page.waitForSelector("canvas", { timeout: 15000 });
await page.waitForTimeout(500);
await page.getByRole("button", { name: "Play", exact: true }).click();
await page.getByRole("button", { name: "20x" }).click();
console.log("playing at 20x, collecting 25s of live data...");
await page.waitForTimeout(25000);

const { poseLog, rafLog, trackLen } = await page.evaluate(() => ({
  poseLog: window.__poseLog, rafLog: window.__rafLog, trackLen: null,
}));

console.log("pose samples:", poseLog.length, "raf samples:", rafLog.length);

// frame pacing
const sorted = [...rafLog].sort((a, b) => a - b);
const p50 = sorted[Math.floor(sorted.length * 0.5)];
const p95 = sorted[Math.floor(sorted.length * 0.95)];
const p99 = sorted[Math.floor(sorted.length * 0.99)];
const long = rafLog.filter((x) => x > 33).length;
console.log(`raf gap ms: p50=${p50?.toFixed(2)} p95=${p95?.toFixed(2)} p99=${p99?.toFixed(2)} frames>33ms=${long}/${rafLog.length}`);

// station backward-jump check (assume track length ~5831 for British GP; use robust
// heuristic: a "backward" jump is stationM decreasing by more than a small epsilon
// without being explainable by a start/finish wrap, i.e. NOT dropping from near-max to near-zero)
let maxStation = 0;
for (const p of poseLog) if (p.station0 > maxStation) maxStation = p.station0;
console.log("observed max station0:", maxStation.toFixed(1));

let backwardJumps = 0, forwardSpikes = 0, worstBackward = 0, worstForward = 0;
for (let i = 1; i < poseLog.length; i++) {
  const a = poseLog[i - 1], b = poseLog[i];
  let d = b.station0 - a.station0;
  const wrap = maxStation * 0.5;
  if (d < -wrap) d += maxStation; // legit wrap forward across the line
  if (d > wrap) d -= maxStation; // legit wrap backward (shouldn't happen going forward)
  // sessionTime, not wall-clock t: at 20x playback, wall-clock dt understates elapsed
  // SIM time by 20x, which read as a fake "746,842 kph" spike on the first pass here --
  // that was this script's own bug, not the app's.
  const dtS = b.sessionTime - a.sessionTime;
  const impliedKph = dtS > 0 ? (d / dtS) * 3.6 : 0;
  if (d < -0.5) { backwardJumps++; if (-d > worstBackward) worstBackward = -d; }
  if (impliedKph > 500) { forwardSpikes++; if (impliedKph > worstForward) worstForward = impliedKph; }
}
console.log(`backward jumps (>0.5m, wrap-corrected): ${backwardJumps}, worst=${worstBackward.toFixed(2)}m`);
console.log(`forward spikes (>500kph implied): ${forwardSpikes}, worst=${worstForward.toFixed(0)}kph`);

await browser.close();
