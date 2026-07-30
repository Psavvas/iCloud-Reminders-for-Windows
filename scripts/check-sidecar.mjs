// Fails the bundle early, with a message that says what to do, if the frozen
// sidecar isn't there. Without this the app installs fine and only reveals the
// problem at first launch, as "the sync service isn't running".
//
// Paths come from tauri.conf.json rather than being guessed here, so this check
// and the bundler cannot drift apart.
import { existsSync, readFileSync } from "fs";
import path from "path";

const HERE = path.dirname(new URL(import.meta.url).pathname);
const SRC_TAURI = path.resolve(HERE, "..", "src-tauri");

const conf = JSON.parse(readFileSync(path.join(SRC_TAURI, "tauri.conf.json"), "utf8"));
const resources = conf?.bundle?.resources ?? {};
const sources = Array.isArray(resources) ? resources : Object.keys(resources);

// Globs are the bundler's problem; only concrete paths can be checked here.
const concrete = sources.filter((s) => !s.includes("*"));
const missing = concrete
  .map((s) => path.resolve(SRC_TAURI, s))
  .filter((p) => !existsSync(p));

if (missing.length) {
  console.error("\n  The sync service has not been built.\n");
  for (const m of missing) console.error(`  Missing: ${m}`);
  console.error("\n  Run this first:\n");
  console.error("      .\\scripts\\build-sidecar.ps1\n");
  console.error("  The installer bundles that executable; without it the app");
  console.error("  installs but cannot start.\n");
  process.exit(1);
}

console.log(`sidecar present (${concrete.length} bundled resource(s) checked)`);
