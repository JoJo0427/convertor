import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../lib/conversion-state.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const { canStartConversion } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

const ready = {
  target: "png", fileCount: 1, busy: false, hasJob: false, planIsCurrent: true,
  details: {
    route: { available: true }, warnings: [],
    compatibility: [{ compatible: true }],
  },
};

test("a current, available plan can run", () => {
  assert.equal(canStartConversion(ready), true);
});

for (const [name, change] of [
  ["missing target", { target: null }],
  ["empty queue", { fileCount: 0 }],
  ["upload or job in progress", { busy: true }],
  ["existing result before Adjust", { hasJob: true }],
  ["options or inputs changed since planning", { planIsCurrent: false }],
  ["plan has not loaded", { details: null }],
]) {
  test(`both submission paths are blocked: ${name}`, () => {
    assert.equal(canStartConversion({ ...ready, ...change }), false);
  });
}

test("missing engines and blocking warnings prevent submission", () => {
  for (const change of [
    { route: { available: false } },
    { warnings: [{ level: "error" }] },
    { compatibility: [{ compatible: false }] },
    { compatibility: [] },
  ]) {
    assert.equal(canStartConversion({ ...ready, details: { ...ready.details, ...change } }), false);
  }
});

test("a warning and partially compatible batch remain convertible", () => {
  assert.equal(canStartConversion({ ...ready, fileCount: 2, details: {
    ...ready.details, warnings: [{ level: "warning" }],
    compatibility: [{ compatible: false }, { compatible: true }],
  } }), true);
});
