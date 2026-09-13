import { chromium } from "playwright";
const browser = await chromium.launch({
  executablePath: "C:/Users/dagam/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe",
});
const page = await browser.newPage();
const urls = [];
page.on("request", (r) => { if (r.url().includes("worker") || r.resourceType() === "other") urls.push(r.url()); });
await page.goto("http://localhost:3000/sim", { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
console.log(urls.filter((u,i,a)=>a.indexOf(u)===i));
await browser.close();
