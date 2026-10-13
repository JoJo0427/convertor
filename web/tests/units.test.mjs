import test from "node:test";
import assert from "node:assert/strict";
// No existing test loader/build step: run with Node's native TypeScript stripping:
// node --experimental-strip-types --test web/tests/units.test.mjs
import { FAMILIES, convertUnit, familyOf } from "../lib/units.ts";

function close(actual, expected) {
  assert.ok(Math.abs(actual - expected) <= 1e-12 * Math.max(1, Math.abs(expected)),
    `Expected ${actual} to be within floating-point tolerance of ${expected}`);
}

test("catalogue contains exactly the requested families and unique units", () => {
  const expected = {
    length: "nm um mm cm m km inch foot yard mile nmi",
    mass: "mg g kg tonne oz lb stone",
    temperature: "celsius fahrenheit kelvin",
    volume: "ml litre m3 tsp tbsp floz_us cup_us pint_us quart_us gallon_us pint_uk gallon_uk",
    area: "mm2 cm2 m2 hectare km2 sqinch sqfoot sqyard acre sqmile",
    speed: "mps kph mph knot fps",
    data: "bit byte kb mb gb tb pb kib mib gib tib",
    time: "ms second minute hour day week month year",
  };
  assert.deepEqual(FAMILIES.map((family) => family.id), Object.keys(expected));
  const ids = new Set();
  for (const family of FAMILIES) {
    assert.ok(family.label);
    assert.deepEqual(family.units.map((unit) => unit.id), expected[family.id].split(" "));
    for (const unit of family.units) {
      assert.ok(!ids.has(unit.id));
      ids.add(unit.id);
      assert.ok(unit.label && unit.symbol);
      assert.equal(familyOf(unit.id), family);
    }
  }
  assert.equal(familyOf("unknown"), null);
  assert.equal(familyOf("toString"), null);
});

test("known exact definitions across every family", () => {
  const cases = [
    [1, "inch", "m", 0.0254], [1, "mile", "m", 1609.344],
    [1, "nmi", "m", 1852], [1, "lb", "kg", 0.45359237],
    [16, "oz", "lb", 1], [1, "stone", "lb", 14],
    [0, "celsius", "fahrenheit", 32],
    [1, "gallon_us", "litre", 3.785411784],
    [1, "gallon_uk", "litre", 4.54609],
    [1, "pint_us", "floz_us", 16], [1, "pint_uk", "litre", 0.56826125],
    [1, "cup_us", "tbsp", 16], [1, "tbsp", "tsp", 3],
    [1, "hectare", "m2", 10000], [1, "acre", "m2", 4046.8564224],
    [1, "sqinch", "m2", 0.00064516], [1, "sqmile", "acre", 640],
    [1, "mph", "mps", 0.44704], [1, "knot", "kph", 1.852],
    [1, "fps", "mps", 0.3048], [1, "byte", "bit", 8],
    [1, "hour", "second", 3600], [1, "week", "day", 7],
    [1, "month", "day", 30.436875], [1, "year", "day", 365.2425],
    [1, "year", "month", 12],
  ];
  for (const [value, from, to, expected] of cases) close(convertUnit(value, from, to), expected);
  assert.equal(convertUnit(1, "mile", "m"), 1609.344);
});

test("temperature offsets, boiling point, and reverse conversions", () => {
  assert.equal(convertUnit(0, "celsius", "fahrenheit"), 32);
  assert.equal(convertUnit(0, "celsius", "kelvin"), 273.15);
  assert.equal(convertUnit(32, "fahrenheit", "kelvin"), 273.15);
  assert.equal(convertUnit(273.15, "kelvin", "celsius"), 0);
  assert.equal(convertUnit(273.15, "kelvin", "fahrenheit"), 32);
  assert.equal(convertUnit(100, "celsius", "fahrenheit"), 212);
  assert.equal(convertUnit(212, "fahrenheit", "celsius"), 100);
  assert.equal(convertUnit(-40, "celsius", "fahrenheit"), -40);
  close(convertUnit(0, "kelvin", "fahrenheit"), -459.67);
});

test("decimal storage and binary storage remain distinct at every scale", () => {
  for (const [index, id] of ["kb", "mb", "gb", "tb", "pb"].entries()) {
    assert.equal(convertUnit(1, id, "byte"), 1000 ** (index + 1));
  }
  for (const [index, id] of ["kib", "mib", "gib", "tib"].entries()) {
    assert.equal(convertUnit(1, id, "byte"), 1024 ** (index + 1));
  }
  assert.equal(convertUnit(1, "kib", "kb"), 1.024);
});

test("all unit pairs round-trip and same-unit conversions preserve values", () => {
  for (const family of FAMILIES) {
    for (const from of family.units) {
      for (const value of [0, -17.25, 123.456789]) {
        assert.equal(convertUnit(value, from.id, from.id), value);
        for (const to of family.units) {
          close(convertUnit(convertUnit(value, from.id, to.id), to.id, from.id), value);
        }
      }
    }
  }
});

test("cross-family and unknown-unit conversions throw clear errors", () => {
  assert.throws(() => convertUnit(1, "m", "kg"), /m .*kg .*different unit families/);
  assert.throws(() => convertUnit(1, "celsius", "second"), /different unit families/);
  assert.throws(() => convertUnit(1, "unknown", "m"), /Unknown unit: unknown/);
  assert.throws(() => convertUnit(1, "m", "unknown"), /Unknown unit: unknown/);
  assert.throws(() => convertUnit(1, "unknown", "unknown"), /Unknown unit: unknown/);
});
