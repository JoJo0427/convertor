export type Unit = { id: string; label: string; symbol: string };
export type UnitFamily = { id: string; label: string; units: Unit[] };

type Definition = [id: string, label: string, symbol: string, factor: number];
type Conversion = { toBase: (value: number) => number; fromBase: (value: number) => number };

const conversions = new Map<string, Conversion>();

function family(id: string, label: string, definitions: Definition[]): UnitFamily {
  return {
    id,
    label,
    units: definitions.map(([unitId, unitLabel, symbol, factor]) => {
      conversions.set(unitId, {
        toBase: (value) => value * factor,
        fromBase: (value) => value / factor,
      });
      return { id: unitId, label: unitLabel, symbol };
    }),
  };
}

const inch = 0.0254;
const foot = 0.3048;
const yard = 0.9144;
const mile = 1609.344;
const pound = 0.45359237;
// US liquid gallon: exactly 231 cubic inches; imperial gallon: exactly 4.54609 L.
const gallonUS = 0.003785411784;
const gallonUK = 0.00454609;
const day = 86400;

export const FAMILIES: UnitFamily[] = [
  family("length", "Length", [
    ["nm", "Nanometre", "nm", 1e-9],
    ["um", "Micrometre", "µm", 1e-6],
    ["mm", "Millimetre", "mm", 0.001],
    ["cm", "Centimetre", "cm", 0.01],
    ["m", "Metre", "m", 1],
    ["km", "Kilometre", "km", 1000],
    ["inch", "Inch", "in", inch],
    ["foot", "Foot", "ft", foot],
    ["yard", "Yard", "yd", yard],
    ["mile", "Mile", "mi", mile],
    ["nmi", "Nautical mile", "nmi", 1852],
  ]),
  family("mass", "Mass", [
    ["mg", "Milligram", "mg", 1e-6],
    ["g", "Gram", "g", 0.001],
    ["kg", "Kilogram", "kg", 1],
    ["tonne", "Tonne", "t", 1000],
    ["oz", "Ounce", "oz", pound / 16],
    ["lb", "Pound", "lb", pound],
    ["stone", "Stone", "st", pound * 14],
  ]),
  {
    id: "temperature", label: "Temperature", units: [
      { id: "celsius", label: "Celsius", symbol: "°C" },
      { id: "fahrenheit", label: "Fahrenheit", symbol: "°F" },
      { id: "kelvin", label: "Kelvin", symbol: "K" },
    ],
  },
  family("volume", "Volume", [
    ["ml", "Millilitre", "mL", 1e-6],
    ["litre", "Litre", "L", 0.001],
    ["m3", "Cubic metre", "m³", 1],
    ["tsp", "US teaspoon", "tsp (US)", gallonUS / 768],
    ["tbsp", "US tablespoon", "tbsp (US)", gallonUS / 256],
    ["floz_us", "US fluid ounce", "fl oz (US)", gallonUS / 128],
    ["cup_us", "US customary cup", "cup (US)", gallonUS / 16],
    ["pint_us", "US liquid pint", "pt (US)", gallonUS / 8],
    ["quart_us", "US liquid quart", "qt (US)", gallonUS / 4],
    ["gallon_us", "US liquid gallon", "gal (US)", gallonUS],
    ["pint_uk", "Imperial pint", "pt (UK)", gallonUK / 8],
    ["gallon_uk", "Imperial gallon", "gal (UK)", gallonUK],
  ]),
  family("area", "Area", [
    ["mm2", "Square millimetre", "mm²", 1e-6],
    ["cm2", "Square centimetre", "cm²", 1e-4],
    ["m2", "Square metre", "m²", 1],
    ["hectare", "Hectare", "ha", 10000],
    ["km2", "Square kilometre", "km²", 1e6],
    ["sqinch", "Square inch", "in²", inch ** 2],
    ["sqfoot", "Square foot", "ft²", foot ** 2],
    ["sqyard", "Square yard", "yd²", yard ** 2],
    ["acre", "Acre", "ac", 43560 * foot ** 2],
    ["sqmile", "Square mile", "mi²", mile ** 2],
  ]),
  family("speed", "Speed", [
    ["mps", "Metres per second", "m/s", 1],
    ["kph", "Kilometres per hour", "km/h", 1000 / 3600],
    ["mph", "Miles per hour", "mph", mile / 3600],
    ["knot", "Knot", "kn", 1852 / 3600],
    ["fps", "Feet per second", "ft/s", foot],
  ]),
  family("data", "Digital storage", [
    ["bit", "Bit", "bit", 1 / 8],
    ["byte", "Byte", "B", 1],
    ["kb", "Kilobyte", "kB", 1000],
    ["mb", "Megabyte", "MB", 1000 ** 2],
    ["gb", "Gigabyte", "GB", 1000 ** 3],
    ["tb", "Terabyte", "TB", 1000 ** 4],
    ["pb", "Petabyte", "PB", 1000 ** 5],
    ["kib", "Kibibyte", "KiB", 1024],
    ["mib", "Mebibyte", "MiB", 1024 ** 2],
    ["gib", "Gibibyte", "GiB", 1024 ** 3],
    ["tib", "Tebibyte", "TiB", 1024 ** 4],
  ]),
  family("time", "Time", [
    ["ms", "Millisecond", "ms", 0.001],
    ["second", "Second", "s", 1],
    ["minute", "Minute", "min", 60],
    ["hour", "Hour", "h", 3600],
    ["day", "Day", "d", day],
    ["week", "Week", "wk", day * 7],
    // Gregorian mean averages, not the lengths of particular calendar months or years.
    ["month", "Month (average)", "mo", day * 30.436875],
    ["year", "Year (average)", "yr", day * 365.2425],
  ]),
];

// Temperature uses Celsius as its base and affine functions instead of ratios.
conversions.set("celsius", { toBase: (value) => value, fromBase: (value) => value });
conversions.set("fahrenheit", {
  toBase: (value) => (value - 32) * 5 / 9,
  fromBase: (value) => value * 9 / 5 + 32,
});
conversions.set("kelvin", {
  toBase: (value) => value - 273.15,
  fromBase: (value) => value + 273.15,
});

export function familyOf(unitId: string): UnitFamily | null {
  return FAMILIES.find((entry) => entry.units.some((unit) => unit.id === unitId)) ?? null;
}

export function convertUnit(value: number, fromId: string, toId: string): number {
  const from = familyOf(fromId);
  const to = familyOf(toId);
  if (!from) throw new Error(`Unknown unit: ${fromId}`);
  if (!to) throw new Error(`Unknown unit: ${toId}`);
  if (from.id !== to.id) {
    throw new Error(`Cannot convert ${fromId} (${from.label}) to ${toId} (${to.label}): different unit families`);
  }
  if (fromId === toId) return value;
  return conversions.get(toId)!.fromBase(conversions.get(fromId)!.toBase(value));
}
