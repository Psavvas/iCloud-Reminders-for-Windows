// Renders the print view through the real print stylesheet and writes a PDF,
// so the paper layout can be checked without a printer.
import { chromium } from "playwright";
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, unlinkSync } from "fs";
import path from "path";

const HERE = path.dirname(new URL(import.meta.url).pathname);
const REPO = path.resolve(HERE, "../..");
const OUT = path.join(REPO, "docs/screenshots");
mkdirSync(OUT, { recursive: true });

const TMP_HTML = path.join(REPO, "src/__print.html");
const TMP_MOCK = path.join(REPO, "src/__mock.js");
copyFileSync(path.join(HERE, "mock.js"), TMP_MOCK);
writeFileSync(TMP_HTML, readFileSync(path.join(REPO, "src/index.html"), "utf8").replace(
  '<script src="app.js"></script>',
  '<script src="__mock.js"></script>\n<script src="app.js"></script>'
));

const browser = await chromium.launch({
  executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
});
const page = await browser.newPage({ viewport: { width: 1180, height: 760 } });
page.on("pageerror", (e) => console.log("  ! " + e.message));
await page.goto("file://" + TMP_HTML);
await page.waitForTimeout(800);

// Select a list, then build the print view exactly as the Print button does.
await page.evaluate(() => {
  const r = [...document.querySelectorAll(".list-row")].find((x) =>
    x.textContent.includes("Inbox"));
  if (r) r.click();
});
await page.waitForTimeout(400);
await page.evaluate(() =>
  window.buildPrintView({ groupBy: "due", notes: true, completed: true })
);
await page.waitForTimeout(400);

const chars = await page.evaluate(() => document.getElementById("print-view").innerText.length);
console.log(`print view populated: ${chars} chars`);

await page.emulateMedia({ media: "print" });
await page.pdf({ path: path.join(OUT, "print-preview.pdf"), format: "A4",
                 printBackground: true });
console.log("wrote print-preview.pdf");

// Also a PNG of page one, for a quick look.
await page.screenshot({ path: path.join(OUT, "14-print-output.png"), fullPage: false });
console.log("wrote 14-print-output.png");

for (const f of [TMP_HTML, TMP_MOCK]) { try { unlinkSync(f); } catch {} }
await browser.close();
