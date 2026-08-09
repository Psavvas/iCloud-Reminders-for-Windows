// Renders the built UI against a stubbed Tauri bridge and screenshots it.
//   npm run shoot
//
// Shoots dist/, not the sources: what ships is what gets captured. The mock is
// injected ahead of the app bundle so the bridge exists before React mounts.
// Both temporary files live in dist/ and are removed on exit.
import { chromium } from "playwright";
import {
  readFileSync, writeFileSync, mkdirSync, copyFileSync, unlinkSync, existsSync,
} from "fs";
import path from "path";
import http from "node:http";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const OUT = path.join(REPO, "docs/screenshots");
const DIST = path.join(REPO, "dist");

mkdirSync(OUT, { recursive: true });

if (!existsSync(path.join(DIST, "index.html"))) {
  console.error("dist/ not built. Run: npm --prefix src-react run build");
  process.exit(1);
}

const TMP_HTML = path.join(DIST, "__shot.html");
const TMP_MOCK = path.join(DIST, "__mock.js");
copyFileSync(path.join(HERE, "mock.js"), TMP_MOCK);
writeFileSync(
  TMP_HTML,
  readFileSync(path.join(DIST, "index.html"), "utf8").replace(
    "<head>",
    '<head>\n<script src="./__mock.js"></script>'
  )
);
const cleanup = () => {
  for (const f of [TMP_HTML, TMP_MOCK]) {
    try { unlinkSync(f); } catch { /* already gone */ }
  }
};
process.on("exit", cleanup);

// Vite emits <script type="module">, which browsers refuse to load over
// file:// for CORS reasons. Tauri serves the frontend over http://tauri.localhost,
// so this only affects the tooling -- serve dist/ over HTTP to match.
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json",
};
const server = http.createServer((req, res) => {
  const rel = decodeURIComponent(req.url.split("?")[0]).replace(/^\/+/, "") || "index.html";
  const file = path.join(DIST, rel);
  if (!file.startsWith(DIST) || !existsSync(file)) {
    res.writeHead(404).end("not found");
    return;
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
  res.end(readFileSync(file));
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const ORIGIN = `http://127.0.0.1:${server.address().port}`;

const browser = await chromium.launch({
  executablePath: "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
  // Playwright hides scrollbars by default for deterministic shots. The app
  // styles them, so hiding them is exactly what must not happen here.
  ignoreDefaultArgs: ["--hide-scrollbars"],
});

async function shoot(
  name,
  {
    dark = false, prep = null, width = 1180, height = 760, flags = {},
    // Captures are deterministic with motion off. A shot whose whole subject is
    // an animation has to opt back in, and skip the settle that would outrun it.
    motion = false,
    settle = 400,
  } = {}
) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    colorScheme: dark ? "dark" : "light",
    reducedMotion: motion ? "no-preference" : "reduce",
  });
  if (Object.keys(flags).length) {
    await ctx.addInitScript((f) => Object.assign(window, f), flags);
  }
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log(`  ! page error: ${e.message}`));
  await page.goto(`${ORIGIN}/__shot.html`);
  await page.waitForSelector(".app, .gate", { timeout: 15000 });
  await page.waitForTimeout(500);
  if (prep) await prep(page);
  if (settle) await page.waitForTimeout(settle);
  await page.screenshot({ path: path.join(OUT, name + ".png") });
  console.log("  wrote " + name + ".png");
  await ctx.close();
}

// `.row` in the Apple sidebar, `.nav-item` in the Fluent one -- the two panes
// are separate components and share no class names.
const clickRow = async (page, text) => {
  await page.evaluate((t) => {
    const r = [...document.querySelectorAll(".row, .nav-item")].find((x) =>
      x.textContent.includes(t)
    );
    if (r) r.click();
  }, text);
  await page.waitForTimeout(400);
};

const selectInbox = (page) => clickRow(page, "Inbox");

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
  flags: { __MOCK_CONFLICT: true },
  prep: async (page) => {
    await selectInbox(page);
    await page.waitForTimeout(600);
  },
});

// The composer, as it looks once it has lifted into a card and been given a
// date. This is the app's main way of creating a reminder, so it is the shot
// that has to stay honest.
const composeInPlace = async (page) => {
  await selectInbox(page);
  await page.click(".add-btn, .win-primary");
  await page.waitForTimeout(300);
  await page.fill(".cc-title", "Order lab safety goggles");
  await page.fill(".cc-note", "Needed before Thursday's titration.");
  await page.click('.tool[aria-label="Due date"]');
  await page.waitForTimeout(250);
  await page.evaluate(() => {
    const item = [...document.querySelectorAll(".composer-menu .menu-item")].find(
      (b) => b.textContent.includes("Tomorrow")
    );
    if (item) item.click();
  });
  await page.waitForTimeout(300);
};

await shoot("07-new-reminder", { prep: composeInPlace });

// The date menu open, which is the part of the gesture a still frame otherwise
// cannot show.
await shoot("20-composer-dates", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click(".add-btn");
    await page.waitForTimeout(300);
    await page.fill(".cc-title", "Order lab safety goggles");
    await page.click('.tool[aria-label="Due date"]');
    await page.waitForTimeout(300);
  },
});

await shoot("08-today", { prep: (p) => clickRow(p, "Today") });

// A long list, so the scrollbar is on screen, caught mid-fade as a reminder is
// ticked off. Both are timing-dependent, which is exactly why they are pinned
// in a screenshot rather than checked by eye.
await shoot("19-list-scroll-and-leave", {
  motion: true,
  settle: 0, // the subject is a 220ms animation; settling would outlast it
  prep: async (page) => {
    await clickRow(page, "Chores");
    await page.waitForTimeout(500);
    await page.evaluate(() => {
      document.querySelector(".reminder input[type=checkbox]").click();
    });
    await page.waitForTimeout(100); // partway through the 220ms exit
  },
});

// Sync progress. The bar only exists while a sync is running, so the events the
// sidecar would send are dispatched by hand.
await shoot("17-sync-progress", {
  prep: async (page) => {
    await selectInbox(page);
    await page.evaluate(() => {
      window.__emit("sidecar://sync_started", { mode: "full", determinate: true });
    });
    // The estimate is elapsed-time-derived and suppressed while that is too
    // small to divide by, so let real seconds pass before the progress event.
    await page.waitForTimeout(7000);
    await page.evaluate(() => {
      window.__emit("sidecar://sync_progress", {
        stage: "reminders", list: "STEM Precalculus Honors",
        done: 3, of: 13, total: 412, percent: 15.2,
      });
    });
  },
});

await shoot("15-upcoming-dates", { prep: (p) => clickRow(p, "Upcoming") });

await shoot("16-sort-menu", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click('button[title="Sort"]');
    await page.waitForTimeout(300);
  },
});

await shoot("09-search-global", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click(".search-wrap .icon-btn.round");
    await page.waitForTimeout(320);
    await page.click(".scope-btn");
    await page.fill('.search-box input[type="search"]', "lab");
    await page.waitForTimeout(450);
  },
});

await shoot("10-deleted", { prep: (p) => clickRow(p, "Deleted"), dark: true });

await shoot("11-settings", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click('button[title^="Settings"]');
    await page.waitForTimeout(350);
  },
});

await shoot("13-print-options", {
  prep: async (page) => {
    await selectInbox(page);
    await page.click('button[title^="Print"]');
    await page.waitForTimeout(350);
  },
});

await shoot("12-onboarding", {
  flags: { __MOCK_ONBOARD: true },
  prep: async (page) => {
    await page.waitForSelector(".onboard", { timeout: 10000 });
    for (let i = 0; i < 2; i++) {
      await page.click(".onboard-actions .primary");
      await page.waitForTimeout(250);
    }
  },
});

await shoot("18-restoring", {
  width: 900,
  height: 620,
  flags: { __MOCK_RESTORING: true },
  prep: (page) => page.waitForSelector(".gate-step", { timeout: 10000 }),
});

// --------------------------------------------------------- the other interface
//
// Everything above is the Apple interface. These four are the Fluent one, and
// exist because the two share no stylesheet: a change that looks right in one
// says nothing at all about the other.

const winui = { __MOCK_WINUI: true };

await shoot("21-winui-light", {
  flags: winui,
  prep: async (page) => {
    await selectInbox(page);
    await page.evaluate(() => {
      const r = [...document.querySelectorAll(".win-row")].find((x) =>
        x.textContent.includes("KIPR")
      );
      if (r) r.click();
    });
    await page.waitForTimeout(400);
  },
});

await shoot("22-winui-dark", {
  dark: true,
  flags: winui,
  prep: (p) => clickRow(p, "Upcoming"),
});

await shoot("23-winui-composer", { flags: winui, prep: composeInPlace });

await shoot("24-winui-settings", {
  flags: winui,
  prep: async (page) => {
    await selectInbox(page);
    await page.click('.nav-item[title^="Settings"]');
    await page.waitForTimeout(350);
  },
});

// The detail pane as a form, which is the part that changed most.
await shoot("25-winui-detail", {
  flags: winui,
  prep: async (page) => {
    await selectInbox(page);
    await page.evaluate(() => {
      const r = [...document.querySelectorAll(".win-row")].find((x) =>
        x.textContent.includes("Renew library books")
      );
      if (r) r.click();
    });
    await page.waitForTimeout(400);
  },
});

await shoot("05-signin", {
  width: 900,
  height: 620,
  flags: { __MOCK_SIGNED_OUT: true },
  prep: async (page) => {
    await page.waitForSelector("#apple-id", { timeout: 10000 });
    await page.fill("#apple-id", "you@icloud.com");
    await page.fill("#password", "............");
  },
});

await browser.close();
server.close();
cleanup();
console.log("done -> docs/screenshots/");
