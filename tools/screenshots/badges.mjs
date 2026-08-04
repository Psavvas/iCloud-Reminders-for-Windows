// Renders the README's badge strip to docs/badges.png.
//   npm run badges
//
// Self-hosted rather than shields.io: the README then has no external image
// dependency, renders offline and in a fork, and -- the reason it is here
// rather than hand-written -- the result can actually be looked at before it
// ships. Only facts that do not drift go on it; anything with a number in it
// belongs in prose, where a test can check it.
import { chromium } from "playwright";
import { mkdirSync } from "fs";
import path from "path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, "../../docs");
mkdirSync(OUT, { recursive: true });

const BADGES = [
  { label: "platform", value: "Windows 10 / 11", color: "#0078D4" },
  { label: "shell", value: "Tauri v2", color: "#1E9E9E" },
  { label: "sidecar", value: "Python 3.11+", color: "#3776AB" },
  { label: "ui", value: "React 19", color: "#2A8FA8" },
  { label: "api", value: "unofficial", color: "#B45309" },
];

const html = `<!doctype html><meta charset="utf-8"><style>
  body { margin: 0; background: #fff; }
  .row { display: inline-flex; gap: 6px; padding: 6px; }
  .badge {
    display: inline-flex; height: 20px; border-radius: 4px; overflow: hidden;
    font: 600 11px/20px "DejaVu Sans", Verdana, system-ui, sans-serif;
    white-space: nowrap;
  }
  .k { background: #4A4A4F; color: #fff; padding: 0 7px; }
  .v { color: #fff; padding: 0 7px; }
</style>
<div class="row">${BADGES.map(
  (b) =>
    `<span class="badge"><span class="k">${b.label}</span>` +
    `<span class="v" style="background:${b.color}">${b.value}</span></span>`
).join("")}</div>`;

const browser = await chromium.launch({
  executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
});
// 3x so the strip stays crisp on a high-DPI screen at its 26px display height.
const page = await browser.newPage({ deviceScaleFactor: 3 });
await page.setContent(html);
await page.locator(".row").screenshot({ path: path.join(OUT, "badges.png") });
await browser.close();
console.log("wrote docs/badges.png");
