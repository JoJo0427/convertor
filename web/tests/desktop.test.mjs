import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../lib/desktop.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const desktop = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

test("desktop bundle exposes the bridge used by file actions", async () => {
  const config = JSON.parse(readFileSync(new URL("../../desktop/src-tauri/tauri.conf.json", import.meta.url), "utf8"));
  assert.equal(config.app.withGlobalTauri, true);
  const calls = [];
  globalThis.window = { __TAURI__: { core: { invoke: async (...args) => calls.push(args) } } };
  try {
    assert.equal(desktop.isDesktop(), true);
    assert.equal(await desktop.reveal("/tmp/converted.mp4"), true);
    assert.deepEqual(calls, [["reveal", { path: "/tmp/converted.mp4" }]]);
  } finally {
    delete globalThis.window;
  }
});

test("browser and failed native actions return false without navigating", async () => {
  assert.equal(desktop.isDesktop(), false);
  assert.equal(await desktop.reveal("/tmp/converted.mp4"), false);
  globalThis.window = { __TAURI__: { core: { invoke: async () => { throw new Error("missing file"); } } } };
  try { assert.equal(await desktop.reveal("/tmp/converted.mp4"), false); }
  finally { delete globalThis.window; }
});
