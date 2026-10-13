import test from "node:test";
import assert from "node:assert/strict";
// Run with Node's native TypeScript stripping:
// node --experimental-strip-types --test web/tests/encodings.test.mjs
import { ENCODINGS, applyEncoding } from "../lib/encodings.ts";

test("catalogue contains unique labelled entries and rejects unknown ids", () => {
  assert.equal(ENCODINGS.length, 30);
  const ids = new Set();
  for (const entry of ENCODINGS) {
    assert.ok(!ids.has(entry.id));
    ids.add(entry.id);
    assert.ok(entry.label && entry.detail);
  }
  for (const id of ["unknown", "toString", "__proto__"]) {
    assert.throws(() => applyEncoding(id, ""), /Unknown encoding:/);
  }
});

test("base64 known vectors and UTF-8 round-trips", () => {
  const cases = [
    ["", ""], ["f", "Zg=="], ["fo", "Zm8="], ["foo", "Zm9v"],
    ["Hello", "SGVsbG8="], ["café", "Y2Fmw6k="],
    ["你好", "5L2g5aW9"], ["😀", "8J+YgA=="],
  ];
  for (const [input, encoded] of cases) {
    assert.equal(applyEncoding("base64_encode", input), encoded);
    assert.equal(applyEncoding("base64_decode", encoded), input);
  }
  assert.equal(applyEncoding("base64_decode", "Zg"), "f");
  const input = "\uFEFFtext\0é中文😀";
  assert.equal(applyEncoding("base64_decode", applyEncoding("base64_encode", input)), input);
});

test("base64url uses only the URL-safe alphabet and round-trips", () => {
  for (const input of ["", "f", "café", "你好", "😀\uFFFF"]) {
    const encoded = applyEncoding("base64url_encode", input);
    assert.doesNotMatch(encoded, /[+/=]/);
    assert.equal(applyEncoding("base64url_decode", encoded), input);
  }
  assert.equal(applyEncoding("base64url_encode", "😀\uFFFF"), "8J-YgO-_vw");
});

test("base64 decoders reject malformed encodings and invalid UTF-8", () => {
  for (const id of ["base64_decode", "base64url_decode"]) {
    for (const input of ["!", "A", "Zg=", "Z===", "Zg====", "Zh==", "=", "Z g=="]) {
      assert.throws(() => applyEncoding(id, input), /Invalid Base64/);
    }
    assert.throws(() => applyEncoding(id, "wA=="), /Invalid UTF-8/);
  }
  assert.throws(() => applyEncoding("base64_decode", "8J-YgA"), /Invalid Base64/);
  assert.throws(() => applyEncoding("base64url_decode", "8J+YgA=="), /Invalid Base64URL/);
});

test("hex encodes lowercase UTF-8 bytes and round-trips", () => {
  assert.equal(applyEncoding("hex_encode", "é😀"), "c3a9f09f9880");
  assert.equal(applyEncoding("hex_decode", "C3A9F09F9880"), "é😀");
  for (const input of ["", "ASCII", "café 中文 😀", "\uFEFF\0"]) {
    assert.equal(applyEncoding("hex_decode", applyEncoding("hex_encode", input)), input);
  }
  for (const input of ["0", "gg", "0x12", "ab cd"]) {
    assert.throws(() => applyEncoding("hex_decode", input), /Invalid hex/);
  }
  assert.throws(() => applyEncoding("hex_decode", "ff"), /Invalid UTF-8/);
});

test("URL components round-trip and reject malformed escapes", () => {
  const input = "café 中文 😀 &/?=+";
  assert.equal(applyEncoding("url_decode", applyEncoding("url_encode", input)), input);
  assert.equal(applyEncoding("url_encode", "a b+c"), "a%20b%2Bc");
  assert.throws(() => applyEncoding("url_decode", "%ZZ"), /URI malformed/);
});

test("HTML handles the five predefined entities and numeric Unicode entities", () => {
  const input = `&<>"'`;
  assert.equal(applyEncoding("html_encode", input), "&amp;&lt;&gt;&quot;&apos;");
  assert.equal(applyEncoding("html_decode", applyEncoding("html_encode", input)), input);
  assert.equal(applyEncoding("html_decode", "&#233; &#x4E2D; &#X1F600;"), "é 中 😀");
  assert.equal(applyEncoding("html_decode", "&amp;lt; &unknown;"), "&lt; &unknown;");
  for (const input of ["&#x110000;", "&#55296;"]) {
    assert.throws(() => applyEncoding("html_decode", input), /Invalid numeric HTML entity/);
  }
});

test("number bases preserve integers beyond Number.MAX_SAFE_INTEGER exactly", () => {
  assert.equal(applyEncoding("base16_to_base10", "FFFFFFFFFFFFFFFF"), "18446744073709551615");
  const decimal = "1234567890123456789012345678901234567890";
  for (const from of [2, 8, 10, 16]) {
    for (const to of [2, 8, 10, 16]) {
      if (from === to) continue;
      for (const value of [BigInt(0), BigInt(decimal), -BigInt(decimal)]) {
        assert.equal(applyEncoding(`base${from}_to_base${to}`, value.toString(from)), value.toString(to));
      }
    }
  }
  assert.equal(applyEncoding("base16_to_base10", "+00FF"), "255");
});

test("invalid digits and non-integer number inputs throw clear errors", () => {
  for (const [base, invalid] of [[2, "102"], [8, "89"], [10, "1a"], [16, "fg"]]) {
    const to = base === 10 ? 16 : 10;
    for (const input of [invalid, "", "+", "-", "1.5", " 10", "0x10", "1_0", "--1"]) {
      assert.throws(() => applyEncoding(`base${base}_to_base${to}`, input), new RegExp(`Invalid base-${base} integer`));
    }
  }
});

test("all six case transforms handle mixed-case multi-word text", () => {
  const cases = [
    ["upper", "HELLO WORLD AGAIN"], ["lower", "hello world again"],
    ["title", "Hello World Again"], ["camel", "helloWorldAgain"],
    ["snake", "hello_world_again"], ["kebab", "hello-world-again"],
  ];
  for (const [id, expected] of cases) {
    assert.equal(applyEncoding(id, "hELLo WoRLD aGAin"), expected);
    assert.equal(applyEncoding(id, ""), "");
  }
  assert.equal(applyEncoding("camel", "  déjà_VU--中文  "), "déjàVu中文");
  assert.equal(applyEncoding("snake", "one\ttwo\nTHREE"), "one_two_three");
});

test("JSON pretty-print and minify preserve values and expose parser errors", () => {
  const compact = '{"hello":[1,true,null,"é"]}';
  const pretty = '{\n  "hello": [\n    1,\n    true,\n    null,\n    "é"\n  ]\n}';
  assert.equal(applyEncoding("json_pretty", compact), pretty);
  assert.equal(applyEncoding("json_minify", pretty), compact);
  for (const id of ["json_pretty", "json_minify"]) {
    for (const input of ["{bad}", "", '{"x":}']) {
      let parserMessage;
      try { JSON.parse(input); } catch (error) { parserMessage = error.message; }
      assert.throws(() => applyEncoding(id, input), { message: `Invalid JSON: ${parserMessage}` });
    }
    assert.equal(applyEncoding(id, "null"), "null");
  }
});
