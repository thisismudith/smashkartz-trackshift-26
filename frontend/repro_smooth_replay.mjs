import { chromium } from "playwright";

const BASE = "http://localhost:3000";
const browser = await chromium.launch({
  executablePath: "C:/Users/dagam/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
page.on("pageerror", (err) => console.log("pageerror:", err.message));
let credibleFireCount = 0;
page.on("console", (msg) => {
  if (msg.text().startsWith("READY")) console.log(msg.text());
  if (msg.text().startsWith("CREDIBLE_FIRE")) { credibleFireCount++; if (credibleFireCount <= 15) console.log(msg.text()); }
});

await page.addInitScript(() => {
  window.__poseLog = [];
  const NativeWorker = window.Worker;
  window.Worker = class extends NativeWorker {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (ev) => {
        const msg = ev.data;
        if (msg && msg.type === "ready") {
          window.__driverList = msg.driverList;
          console.log("READY driverList[0]=" + msg.driverList[0] + " total=" + msg.driverList.length);
        }
        if (msg && msg.type === "pose") {
          const floats = new Float32Array(msg.buffer.slice(0));
          window.__poseLog.push({ t: performance.now(), sessionTime: msg.sessionTime, station0: floats[0], status0: floats[12], speed0: floats[4] });
          if (window.__poseLog.length > 20000) window.__poseLog.shift();
        }
      });
    }
  };
});

console.log("navigating to Replay /sim ...");
await page.goto(`${BASE}/sim`, { waitUntil: "networkidle" });
await page.waitForSelector("canvas", { timeout: 20000 });
await page.waitForTimeout(1500);
await page.getByRole("button", { name: "Play", exact: true }).click().catch(async () => {
  await page.getByRole("button", { name: "Play / pause (space)" }).click();
});
await page.getByRole("button", { name: "20x" }).click().catch(() => console.log("no 20x button found"));
console.log("playing, collecting 25s...");
await page.waitForTimeout(25000);

const poseLog = await page.evaluate(() => window.__poseLog);
console.log("pose samples:", poseLog.length);
let maxStation = 0;
for (const p of poseLog) if (p.station0 > maxStation) maxStation = p.station0;
let backwardJumps = 0, worstBackward = 0, forwardSpikes = 0, worstForward = 0;
for (let i = 1; i < poseLog.length; i++) {
  const a = poseLog[i - 1], b = poseLog[i];
  let d = b.station0 - a.station0;
  const wrap = maxStation * 0.5;
  if (d < -wrap) d += maxStation;
  if (d > wrap) d -= maxStation;
  const dtS = b.sessionTime - a.sessionTime;
  const impliedKph = dtS > 0 ? (d / dtS) * 3.6 : 0;
  const STATUS = ["grid", "track", "pit", "finished", "retired", "gap"];
  if (d < -0.5) {
    backwardJumps++; if (-d > worstBackward) worstBackward = -d;
    console.log(`  BACKWARD at sessionTime=${b.sessionTime.toFixed(2)} d=${d.toFixed(2)}m a=${a.station0.toFixed(1)}(${STATUS[a.status0]},${a.speed0}kph) b=${b.station0.toFixed(1)}(${STATUS[b.status0]},${b.speed0}kph) dtS=${dtS.toFixed(3)}`);
  }
  if (impliedKph > 500) {
    forwardSpikes++; if (impliedKph > worstForward) worstForward = impliedKph;
    console.log(`  FORWARD SPIKE at sessionTime=${b.sessionTime.toFixed(2)} impliedKph=${impliedKph.toFixed(0)} a=${a.station0.toFixed(1)}(${STATUS[a.status0]},${a.speed0}kph) b=${b.station0.toFixed(1)}(${STATUS[b.status0]},${b.speed0}kph) dtS=${dtS.toFixed(3)}`);
  }
}
console.log("total CREDIBLE_FIRE events:", credibleFireCount);
console.log(`max station: ${maxStation.toFixed(1)}`);
console.log(`backward jumps: ${backwardJumps} worst=${worstBackward.toFixed(2)}m`);
console.log(`forward spikes: ${forwardSpikes} worst=${worstForward.toFixed(0)}kph`);
await browser.close();
