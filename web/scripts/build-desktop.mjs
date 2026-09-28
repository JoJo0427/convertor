// Static export for the desktop app.
//
// The desktop build has no server: the interface talks straight to the engine
// on loopback, so the /api proxy route is dead code there — and Next refuses to
// static-export a route handler at all. It is moved aside for the build and put
// back afterwards, in a finally, so an interrupted build cannot leave the tree
// missing a file the Docker build needs.

import { execSync } from "node:child_process";
import { existsSync, renameSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const live = resolve(root, "app/api");
const parked = resolve(root, ".api-parked");

const moved = existsSync(live);
if (moved) {
  rmSync(parked, { recursive: true, force: true });
  mkdirSync(dirname(parked), { recursive: true });
  renameSync(live, parked);
}

try {
  execSync("next build", {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, CONVERTOR_TARGET: "desktop" },
  });
} finally {
  if (moved) renameSync(parked, live);
}
