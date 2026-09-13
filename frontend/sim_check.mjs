import { chromium } from "playwright";

const browser = await chromium.launch({
  executablePath: "C:/Users/dagam/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe",
});
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on("pageerror", (e) => {
  const frame = String(e.stack || "").split("\n")[1] || "";
  errs.push(`PAGEERROR ${e.message} @@ ${frame.trim()}`);
});
page.on("console", (m) => { if (m.type() === "error") errs.push(m.text().slice(0, 200)); });

await page.goto("http://localhost:3000/sim", { waitUntil: "networkidle" });
await page.waitForSelector("canvas", { timeout: 25000 });
for (const sel of await page.$$("select")) {
  const opts = await sel.$$eval("option", (o) => o.map((x) => x.textContent));
  const b = opts.find((o) => o && o.includes("British"));
  if (b) { await sel.selectOption({ label: b }); break; }
}
await page.waitForTimeout(3500);
const play = page.getByRole("button", { name: "Play", exact: true });
if (await play.count()) await play.first().click();
const t = page.getByRole("button", { name: "20x" });
if (await t.count()) await t.first().click();
await page.waitForTimeout(8000);
const rows = await page.$$("tbody tr");
if (rows.length > 5) await rows[5].click();
await page.waitForTimeout(4500);

console.log("minimap svg:", (await page.$$("svg[aria-label='Circuit plan with car positions']")).length);
// Shift+/ opens the reference modal; Escape closes it; Escape again hides the HUD.
await page.keyboard.press("Shift+Slash");
await page.waitForTimeout(700);
console.log("modal open:", (await page.$$("[role=dialog]")).length === 1);
await page.screenshot({ path: "sim_modal.png" });
await page.keyboard.press("Escape");
await page.waitForTimeout(500);
console.log("modal closed:", (await page.$$("[role=dialog]")).length === 0);
await page.keyboard.press("Escape");
await page.waitForTimeout(500);
const restore = await page.$$("text=Esc · show HUD");
console.log("hud hidden:", restore.length > 0);
await page.screenshot({ path: "sim_hidden.png" });
await page.keyboard.press("Escape");
await page.waitForTimeout(500);
console.log("leaderboard rows:", (await page.$$("tbody tr")).length);
await page.screenshot({ path: "sim_check.png" });
console.log("ERRORS:");
for (const e of errs.slice(0, 5)) console.log("  " + e);
await browser.close();
