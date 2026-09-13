import { chromium } from "playwright";

const BASE = "http://localhost:3000";

const browser = await chromium.launch({
  executablePath: "C:\\Users\\dagam\\AppData\\Local\\ms-playwright\\chromium-1228\\chrome-win64\\chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push("pageerror: " + err.message));

console.log("navigating...");
await page.goto(`${BASE}/sim/new`, { waitUntil: "networkidle" });

// wait for the Grand Prix select to populate
await page.waitForSelector("select", { timeout: 15000 });
await page.selectOption("select", { label: "British Grand Prix" });
console.log("selected British Grand Prix");
await page.waitForTimeout(500);

// ensure the full field is selected (should be default, but be explicit)
const allBtn = page.getByRole("button", { name: "All" });
if (await allBtn.count()) await allBtn.click();
await page.waitForTimeout(200);

const startBtn = page.getByRole("button", { name: "Start race" });
await startBtn.click();
console.log("clicked start race");

await page.waitForSelector("canvas", { timeout: 15000 });
await page.waitForTimeout(1000);

// play + max out the UI speed. The toggle button reads exactly "Play" before the
// first click; the ALWAYS-VISIBLE separate "Pause" button would make a name regex
// match two elements (strict-mode violation), so target the exact initial label.
await page.getByRole("button", { name: "Play", exact: true }).click();
await page.getByRole("button", { name: "20x" }).click();
console.log("playing at 20x");

async function dumpLeaderboard() {
  const rows = await page.$$eval("table.leaderboard tbody tr, table tbody tr", (trs) =>
    trs.map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent))
  );
  return rows;
}

let found = null;
for (let i = 0; i < 40; i++) {
  await page.waitForTimeout(3000);
  const rows = await dumpLeaderboard();
  const nonGridCount = rows.filter((r) => r[4] && r[4] !== "grid").length;
  const gridRows = rows.filter((r) => r[4] === "grid");
  console.log(`poll ${i}: ${rows.length} rows, ${gridRows.length} on grid, sample:`, rows.slice(0, 3));
  if (gridRows.length > 0 && nonGridCount >= rows.length - gridRows.length && nonGridCount > rows.length * 0.5) {
    found = { rows, poll: i };
    break;
  }
}

if (found) {
  console.log("REPRO FOUND at poll", found.poll);
  console.log(JSON.stringify(found.rows, null, 1));
  await page.screenshot({ path: "repro_alo.png", fullPage: false });
  console.log("screenshot saved");
} else {
  console.log("no stuck-grid row observed after full poll window");
  const rows = await dumpLeaderboard();
  console.log("final rows:", JSON.stringify(rows, null, 1));
  await page.screenshot({ path: "repro_alo_final.png", fullPage: false });
}

console.log("console errors:", consoleErrors.slice(0, 20));

await browser.close();
