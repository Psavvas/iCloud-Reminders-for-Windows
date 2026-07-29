import { chromium } from "playwright";
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, unlinkSync } from "fs";
import path from "path";

// Renders src/index.html against a stubbed Tauri bridge and screenshots it.
//   node tools/screenshots/shoot.mjs
//
// The mock and the temporary page are copied into src/ only for the duration
// of the run: everything under src/ is bundled into the app, so neither may be
// left behind.
const HERE = path.dirname(new URL(import.meta.url).pathname);
const REPO = path.resolve(HERE, "../..");
const OUT = path.join(REPO, "docs/screenshots");

mkdirSync(OUT, { recursive: true });

const TMP_HTML = path.join(REPO, "src/__shot.html");
const TMP_MOCK = path.join(REPO, "src/__mock.js");

copyFileSync(path.join(HERE, "mock.js"), TMP_MOCK);
writeFileSync(
  TMP_HTML,
  readFileSync(path.join(REPO, "src/index.html"), "utf8").replace(
    '<script src="app.js"></script>',
    '<script src="__mock.js"></script>\n<script src="app.js"></script>'
  )
);
const cleanup = () => { for (const f of [TMP_HTML, TMP_MOCK]) { try { unlinkSync(f); } catch {} } };
process.on("exit", cleanup);

const browser = await chromium.launch({ executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome" });

async function shoot(name, { dark = false, prep = null, width = 1180, height = 760 } = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    colorScheme: dark ? "dark" : "light",
  });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log(`  ! page error: ${e.message}`));
  await page.goto("file://" + TMP_HTML);
  await page.waitForTimeout(700);
  if (prep) await prep(page);
  await page.waitForTimeout(450);
  const file = path.join(OUT, name + ".png");
  await page.screenshot({ path: file });
  console.log("  wrote " + name + ".png");
  await ctx.close();
}

const selectInbox = async (page) => {
  await page.evaluate(() => {
    const rows = [...document.querySelectorAll(".list-row")];
    const inbox = rows.find((r) => r.textContent.includes("Inbox"));
    if (inbox) inbox.click();
  });
  await page.waitForTimeout(400);
};

const clickSmart = async (page, label) => {
  await page.evaluate((l) => {
    const r = [...document.querySelectorAll(".smart-row")].find((x) =>
      x.textContent.includes(l)
    );
    if (r) r.click();
  }, label);
  await page.waitForTimeout(400);
};

const openDetail = async (page) => {
  await selectInbox(page);
  await page.evaluate(() => {
    const r = [...document.querySelectorAll(".reminder")].find((x) =>
      x.textContent.includes("KIPR")
    );
    if (r) r.click();
  });
  await page.waitForTimeout(400);
};

console.log("rendering...");

await shoot("01-main-light", { prep: openDetail });
await shoot("02-main-dark", { dark: true, prep: openDetail });

await shoot("03-tag-filter", {
  prep: async (page) => {
    await page.evaluate(() => {
      const c = [...document.querySelectorAll(".chip")].find((x) =>
        x.textContent.includes("school")
      );
      if (c) c.click();
    });
    await page.waitForTimeout(400);
  },
});

await shoot("04-conflict", {
  prep: async (page) => {
    await page.evaluate(() => { window.__MOCK_CONFLICT = true; });
    await selectInbox(page);
    await page.evaluate(() => window.showConflicts && window.showConflicts());
    await page.waitForTimeout(500);
  },
});


await shoot("07-new-reminder", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click("#new-btn");
    await page.waitForTimeout(350);
    await page.fill("#new-title", "Order lab safety goggles");
    await page.fill("#new-notes", "Needed before Thursday's titration.");
    await page.selectOption("#new-priority", "5");
  },
});


await shoot("08-today", { prep: (p) => clickSmart(p, "Today") });

await shoot("09-search-global", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click("#search-btn");
    await page.waitForTimeout(280);
    await page.click("#search-scope");
    await page.fill("#search", "lab");
    await page.waitForTimeout(350);
  },
});

await shoot("10-deleted", { prep: (p) => clickSmart(p, "Deleted"), dark: true });

await shoot("11-settings", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click("#settings-btn");
    await page.waitForTimeout(350);
  },
});

await shoot("05-signin", {
  prep: async (page) => {
    await page.evaluate(() => {
      document.getElementById("app").classList.add("hidden");
      document.getElementById("gate").classList.remove("hidden");
      for (const s of document.querySelectorAll(".gate-step")) s.classList.add("hidden");
      document.getElementById("gate-login").classList.remove("hidden");
      document.getElementById("apple-id").value = "you@icloud.com";
      document.getElementById("password").value = "............";
    });
  },
  width: 900, height: 620,
});

await shoot("06-sidecar-down", {
  prep: async (page) => {
    await page.evaluate(() => {
      document.getElementById("app").classList.add("hidden");
      document.getElementById("gate").classList.remove("hidden");
      for (const s of document.querySelectorAll(".gate-step")) s.classList.add("hidden");
      document.getElementById("gate-sidecar").classList.remove("hidden");
      document.getElementById("sidecar-detail").textContent =
        "Could not find the sync service. Run scripts\\build-sidecar.ps1 to build it.\n\n" +
        "Looked in:\nC:\\Program Files\\iCloud Reminders\\reminders-sidecar.exe\n" +
        "D:\\source\\reminders-sync\\dist-sidecar\\reminders-sidecar.exe";
    });
  },
  width: 900, height: 620,
});

await browser.close();
cleanup();
console.log("done -> docs/screenshots/");
