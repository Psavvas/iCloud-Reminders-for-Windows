// Runs an npm command against src-react, from the repo root.
//
//   node scripts/ui.mjs install
//   node scripts/ui.mjs run build
//
// This exists because `npm --prefix src-react <cmd>` is not safe to call from a
// root npm script on Windows. The root postinstall used to be
// `npm --prefix src-react install`; on windows-latest the nested npm ignored the
// prefix and re-entered the *root* package instead -- every error frame in the
// failing run reported the repo root as its path -- so postinstall fired again,
// and again, about twenty times, one process per second. It stopped only when
// the accumulated node_modules/.bin entries pushed PATH past the Windows length
// limit and `npm` itself stopped resolving. The same scripts do not recurse on
// Linux, which is why this reached CI rather than a developer.
//
// npm exports its own configuration to lifecycle scripts as npm_config_*, and
// the prefix entries are what the nested process picked up. Three things keep
// it from coming back: those variables are stripped, the working directory is
// named explicitly rather than inferred from a flag, and a guard variable makes
// re-entry impossible even if some future npm resolves the directory some third
// way. The install is then checked to have actually landed in src-react, so a
// wrong-package install fails loudly instead of silently leaving the frontend
// without its dependencies.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const GUARD = "REMINDERS_SYNC_UI_NPM";

// Set only for the child below, so a nested root install exits instead of
// looping. Reaching this branch means the prefix stripping stopped working.
if (process.env[GUARD]) {
  console.error(
    "scripts/ui.mjs re-entered itself; refusing to recurse. " +
      "The nested npm resolved the repo root instead of src-react.",
  );
  process.exit(1);
}

const args = process.argv.slice(2);
if (args.length === 0) {
  console.error("usage: node scripts/ui.mjs <npm arguments>");
  process.exit(2);
}

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const ui = join(root, "src-react");

const env = { ...process.env, [GUARD]: "1" };
for (const key of Object.keys(env)) {
  if (/^npm_config_(prefix|local_prefix|global_prefix)$/.test(key)) {
    delete env[key];
  }
}

// npm is npm.cmd on Windows, which spawnSync will not find without a shell.
const result = spawnSync("npm", args, {
  cwd: ui,
  env,
  stdio: "inherit",
  shell: process.platform === "win32",
});

if (result.error) {
  console.error(`failed to run npm in src-react: ${result.error.message}`);
  process.exit(1);
}

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

if (args[0] === "install" && !existsSync(join(ui, "node_modules"))) {
  console.error(
    "npm install reported success but src-react/node_modules does not exist, " +
      "so it installed something other than the frontend.",
  );
  process.exit(1);
}
