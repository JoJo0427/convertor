export type Encoding = { id: string; label: string; detail: string };

type Transform = (input: string) => string;

const conversions = new Map<string, Transform>();

function encoding(id: string, label: string, detail: string, transform: Transform): Encoding {
  conversions.set(id, transform);
  return { id, label, detail };
}

const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

function encodeBase64(input: string): string {
  const bytes = new TextEncoder().encode(input);
  let output = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const value = (bytes[i] << 16) | ((bytes[i + 1] ?? 0) << 8) | (bytes[i + 2] ?? 0);
    output += alphabet[(value >>> 18) & 63] + alphabet[(value >>> 12) & 63];
    output += i + 1 < bytes.length ? alphabet[(value >>> 6) & 63] : "=";
    output += i + 2 < bytes.length ? alphabet[value & 63] : "=";
  }
  return output;
}

function decodeUTF8(bytes: Uint8Array): string {
  try {
    // Preserve a leading BOM as text, so encoding and decoding round-trip it.
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new Error("Invalid UTF-8 text");
  }
}

function decodeBase64(input: string, url = false): string {
  const name = url ? "Base64URL" : "Base64";
  const valid = url ? /^[A-Za-z0-9_-]*={0,2}$/ : /^[A-Za-z0-9+/]*={0,2}$/;
  const raw = input.replace(/=+$/, "");
  if (!valid.test(input) || raw.length % 4 === 1 ||
      (input.includes("=") && input.length % 4 !== 0)) {
    throw new Error(`Invalid ${name} input`);
  }
  const normalized = raw.replace(/-/g, "+").replace(/_/g, "/");
  const bytes = new Uint8Array(Math.floor(normalized.length * 6 / 8));
  let buffer = 0;
  let bits = 0;
  let index = 0;
  for (const character of normalized) {
    buffer = (buffer << 6) | alphabet.indexOf(character);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes[index++] = (buffer >>> bits) & 255;
    }
  }
  if ((buffer & ((1 << bits) - 1)) !== 0) throw new Error(`Invalid ${name} padding bits`);
  return decodeUTF8(bytes);
}

const entities = new Map([
  ["&", "&amp;"], ["<", "&lt;"], [">", "&gt;"], ['"', "&quot;"], ["'", "&apos;"],
]);
const characters = new Map([...entities].map(([character, entity]) => [entity, character]));

function decodeEntities(input: string): string {
  // One pass preserves literal entities that were themselves escaped.
  return input.replace(/&(amp|lt|gt|quot|apos|#\d+|#[xX][\da-fA-F]+);/g, (entity: string) => {
    const named = characters.get(entity);
    if (named !== undefined) return named;
    const hex = entity[2].toLowerCase() === "x";
    const point = Number.parseInt(entity.slice(hex ? 3 : 2, -1), hex ? 16 : 10);
    if (point > 0x10ffff || (point >= 0xd800 && point <= 0xdfff)) {
      throw new Error(`Invalid numeric HTML entity: ${entity}`);
    }
    return String.fromCodePoint(point);
  });
}

function decodeHex(input: string): string {
  if (!/^(?:[\da-fA-F]{2})*$/.test(input)) throw new Error("Invalid hex input: expected pairs of hexadecimal digits");
  const bytes = new Uint8Array(input.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = Number.parseInt(input.slice(i * 2, i * 2 + 2), 16);
  return decodeUTF8(bytes);
}

function convertBase(input: string, from: number, to: number): string {
  // Accept an optional sign and bare digits; prefixes and whitespace are rejected.
  const digits = input.replace(/^[+-]/, "").toLowerCase();
  if (!digits || [...digits].some((digit) => !"0123456789abcdef".slice(0, from).includes(digit))) {
    throw new Error(`Invalid base-${from} integer: ${input}`);
  }
  let value = BigInt(0);
  for (const digit of digits) value = value * BigInt(from) + BigInt("0123456789abcdef".indexOf(digit));
  if (input.startsWith("-")) value = -value;
  return value.toString(to);
}

function words(input: string): string[] {
  // Split on punctuation/spacing while preserving Unicode letters and numbers.
  return input.match(/[\p{L}\p{M}\p{N}]+/gu)?.map((word) => word.toLowerCase()) ?? [];
}

function capitalize(input: string): string {
  const [first, ...rest] = [...input];
  return first ? first.toUpperCase() + rest.join("") : "";
}

function formatJSON(input: string, pretty: boolean): string {
  try {
    return JSON.stringify(JSON.parse(input), null, pretty ? 2 : undefined);
  } catch (error) {
    throw new Error(`Invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

const bases = [2, 8, 10, 16];

export const ENCODINGS: Encoding[] = [
  encoding("base64_encode", "Base64 encode", "UTF-8 text to Base64", encodeBase64),
  encoding("base64_decode", "Base64 decode", "Base64 to UTF-8 text", decodeBase64),
  encoding("base64url_encode", "Base64URL encode", "URL-safe Base64 without padding", (input) =>
    encodeBase64(input).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")),
  encoding("base64url_decode", "Base64URL decode", "URL-safe Base64 to UTF-8 text", (input) => decodeBase64(input, true)),
  encoding("url_encode", "URL encode", "Encode a URI component", encodeURIComponent),
  encoding("url_decode", "URL decode", "Decode a URI component", decodeURIComponent),
  encoding("html_encode", "HTML escape", "Escape the five XML predefined entities", (input) =>
    input.replace(/[&<>"']/g, (character) => entities.get(character)!)),
  encoding("html_decode", "HTML unescape", "Decode XML predefined and numeric entities", decodeEntities),
  encoding("hex_encode", "Hex encode", "UTF-8 text to lowercase hexadecimal", (input) =>
    Array.from(new TextEncoder().encode(input), (byte) => byte.toString(16).padStart(2, "0")).join("")),
  encoding("hex_decode", "Hex decode", "Hexadecimal bytes to UTF-8 text", decodeHex),
  ...bases.flatMap((from) => bases.filter((to) => to !== from).map((to) =>
    encoding(`base${from}_to_base${to}`, `Base ${from} → Base ${to}`, "Signed integer conversion with exact precision", (input) => convertBase(input, from, to)))),
  encoding("upper", "Uppercase", "Convert all letters to uppercase", (input) => input.toUpperCase()),
  encoding("lower", "Lowercase", "Convert all letters to lowercase", (input) => input.toLowerCase()),
  encoding("title", "Title case", "Capitalize each word, separated by spaces", (input) => words(input).map(capitalize).join(" ")),
  encoding("camel", "camelCase", "Join words with initial capitals after the first word", (input) =>
    words(input).map((word, index) => index === 0 ? word : capitalize(word)).join("")),
  encoding("snake", "snake_case", "Join lowercase words with underscores", (input) => words(input).join("_")),
  encoding("kebab", "kebab-case", "Join lowercase words with hyphens", (input) => words(input).join("-")),
  encoding("json_pretty", "JSON pretty-print", "Format JSON with two-space indentation", (input) => formatJSON(input, true)),
  encoding("json_minify", "JSON minify", "Remove insignificant JSON whitespace", (input) => formatJSON(input, false)),
];

export function applyEncoding(id: string, input: string): string {
  const transform = conversions.get(id);
  if (!transform) throw new Error(`Unknown encoding: ${id}`);
  return transform(input);
}
