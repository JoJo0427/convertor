"use client";

/* Value conversion: currency, units, text encodings.
 *
 * A separate screen from file conversion for the same reason the downloader is
 * one — it is a different kind of work. Nothing is queued, nothing is written to
 * disk, and the answer appears as you type. Putting these in the file workspace
 * would mean a queue that sometimes holds files and sometimes holds a number.
 *
 * Everything here is arithmetic done in this browser, with one exception:
 * exchange rates come from someone else's server. That exception is marked on
 * screen rather than buried, because the rest of the app earns its keep by not
 * sending anything anywhere.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import type { EngineError, RateTable } from "@/lib/types";
import { ENCODINGS, applyEncoding } from "@/lib/encodings";
import { FAMILIES, convertUnit } from "@/lib/units";
import { Button, ErrorBlock, Field, Icons, Segmented, Select, Spinner, TextInput } from "@/components/ui";

type Mode = "currency" | "units" | "text";

/** Enough precision to be useful, without pretending to more than we have. */
function pretty(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude < 0.001 || magnitude >= 1e12)) {
    return value.toExponential(6);
  }
  const decimals = magnitude >= 100 ? 2 : magnitude >= 1 ? 4 : 6;
  return value
    .toFixed(decimals)
    .replace(/\.?0+$/, "")
    .replace(/\B(?=(\d{3})+(?!\d))/, "");
}

function age(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} minutes old`;
  const hours = Math.round(seconds / 3600);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} old`;
  return `${Math.round(hours / 24)} days old`;
}

export default function ToolsPage() {
  const [mode, setMode] = useState<Mode>("currency");

  return (
    <main id="main" className="mx-auto w-full max-w-[640px] px-4 py-10">
      <header className="mb-6">
        <h1 className="t-title">Convert values</h1>
        <p className="mt-1 t-small text-[var(--ink-muted)]">
          Currencies, measurements and text encodings. Answers appear as you
          type — nothing is queued and nothing is written to disk.
        </p>
      </header>

      <div className="mb-5">
        <Segmented<Mode>
          value={mode}
          onChange={setMode}
          ariaLabel="What to convert"
          options={[
            { value: "currency", label: "Currency" },
            { value: "units", label: "Measurements" },
            { value: "text", label: "Text" },
          ]}
        />
      </div>

      {mode === "currency" ? <Currency /> : mode === "units" ? <Units /> : <Text />}
    </main>
  );
}

/* ------------------------------------------------------------------ currency */

function Currency() {
  const [table, setTable] = useState<RateTable | null>(null);
  const [error, setError] = useState<EngineError | null>(null);
  const [loading, setLoading] = useState(true);
  const [amount, setAmount] = useState("100");
  const [from, setFrom] = useState("USD");
  const [to, setTo] = useState("EUR");

  const load = useCallback(async (base: string, refresh = false) => {
    setLoading(true);
    setError(null);
    try {
      setTable(await api.rates(base, refresh));
    } catch (e) {
      if (e instanceof ApiError) setError(e.info);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(from);
  }, [from, load]);

  // The arithmetic is done here rather than by asking the engine per keystroke:
  // one rate table answers every amount, and a round trip per character typed
  // would make the field feel broken.
  const rate = table?.rates?.[to];
  const parsed = Number(amount.replace(/,/g, ""));
  const result = rate !== undefined && Number.isFinite(parsed) ? parsed * rate : null;

  const options = useMemo(
    () => (table?.currencies ?? []).map((c) => ({ value: c, label: c })),
    [table],
  );

  if (loading && !table) {
    return (
      <div className="pane flex items-center justify-center gap-2 p-8 t-small text-[var(--ink-muted)]">
        <Spinner /> Fetching today&rsquo;s rates…
      </div>
    );
  }

  if (error && !table) {
    return <ErrorBlock error={error} onDismiss={() => load(from, true)} />;
  }

  return (
    <div className="space-y-4">
      <div className="pane p-4">
        <div className="grid gap-3 sm:grid-cols-[1.3fr_1fr_1fr] sm:items-end">
          <Field label="Amount">
            {(id) => (
              <TextInput id={id} value={amount} onChange={setAmount} placeholder="100" />
            )}
          </Field>
          <Field label="From">
            {(id) => <Select id={id} value={from} onChange={setFrom} options={options} />}
          </Field>
          <Field label="To">
            {(id) => <Select id={id} value={to} onChange={setTo} options={options} />}
          </Field>
        </div>

        <div className="mt-4 border-t border-[var(--line)] pt-3">
          <p className="num text-[26px] leading-tight text-[var(--ink)]">
            {result === null ? "—" : `${pretty(result)} ${to}`}
          </p>
          {rate !== undefined && (
            <p className="mt-1 t-micro text-[var(--ink-faint)]">
              1 {from} = {pretty(rate)} {to}
            </p>
          )}
        </div>
      </div>

      {/* The network boundary, stated. */}
      <div className="flex flex-wrap items-center gap-2 t-micro text-[var(--ink-faint)]">
        <Icons.globe className="h-3 w-3 shrink-0" />
        <span>
          Rates from {table?.source || "the rate service"}
          {table?.published ? `, published ${table.published}` : ""}
          {table ? ` · ${age(table.ageSeconds)}` : ""}
        </span>
        <Button size="sm" variant="quiet" onClick={() => load(from, true)} disabled={loading}>
          {loading ? <Spinner /> : "Refresh"}
        </Button>
      </div>

      {table?.stale && (
        <p className="t-micro" style={{ color: "var(--warn)" }}>
          These rates could not be refreshed, so they are being used from the last
          successful fetch. Treat them as indicative.
        </p>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------- units */

function Units() {
  const [familyId, setFamilyId] = useState(FAMILIES[0].id);
  const family = FAMILIES.find((f) => f.id === familyId) ?? FAMILIES[0];
  const [value, setValue] = useState("1");
  const [from, setFrom] = useState(family.units[0].id);
  const [to, setTo] = useState(family.units[1]?.id ?? family.units[0].id);

  // Switching family must also move the two unit pickers into it, or the next
  // conversion is across families and throws.
  function changeFamily(next: string) {
    const target = FAMILIES.find((f) => f.id === next) ?? FAMILIES[0];
    setFamilyId(next);
    setFrom(target.units[0].id);
    setTo(target.units[1]?.id ?? target.units[0].id);
  }

  const parsed = Number(value.replace(/,/g, ""));
  let result: number | null = null;
  let failure = "";
  if (Number.isFinite(parsed)) {
    try {
      result = convertUnit(parsed, from, to);
    } catch (e) {
      failure = e instanceof Error ? e.message : String(e);
    }
  }

  const unitOptions = family.units.map((u) => ({
    value: u.id,
    label: u.symbol ? `${u.label} (${u.symbol})` : u.label,
  }));
  const toUnit = family.units.find((u) => u.id === to);

  return (
    <div className="space-y-4">
      <div className="pane p-4">
        <Field label="Measuring">
          {(id) => (
            <Select
              id={id}
              value={familyId}
              onChange={changeFamily}
              options={FAMILIES.map((f) => ({ value: f.id, label: f.label }))}
            />
          )}
        </Field>

        <div className="mt-3 grid gap-3 sm:grid-cols-[1.3fr_1fr_1fr] sm:items-end">
          <Field label="Value">
            {(id) => <TextInput id={id} value={value} onChange={setValue} placeholder="1" />}
          </Field>
          <Field label="From">
            {(id) => <Select id={id} value={from} onChange={setFrom} options={unitOptions} />}
          </Field>
          <Field label="To">
            {(id) => <Select id={id} value={to} onChange={setTo} options={unitOptions} />}
          </Field>
        </div>

        <div className="mt-4 border-t border-[var(--line)] pt-3">
          <p className="num text-[26px] leading-tight text-[var(--ink)]">
            {result === null
              ? "—"
              : `${pretty(result)}${toUnit?.symbol ? ` ${toUnit.symbol}` : ""}`}
          </p>
          {failure && (
            <p className="mt-1 t-micro" style={{ color: "var(--fail)" }}>
              {failure}
            </p>
          )}
        </div>
      </div>

      <p className="flex items-start gap-2 t-micro text-[var(--ink-faint)]">
        <Icons.shield className="mt-[2px] h-3 w-3 shrink-0" />
        <span>
          Worked out on this machine. Exact definitions are used where a unit has
          one — an inch is 0.0254 m, a pound 0.45359237 kg — and decimal storage
          units (kB = 1000 bytes) are kept separate from binary ones
          (KiB = 1024 bytes).
        </span>
      </p>
    </div>
  );
}

/* ---------------------------------------------------------------------- text */

function Text() {
  const [encoding, setEncoding] = useState(ENCODINGS[0].id);
  const [input, setInput] = useState("");

  const entry = ENCODINGS.find((e) => e.id === encoding) ?? ENCODINGS[0];
  let output = "";
  let failure = "";
  if (input) {
    try {
      output = applyEncoding(encoding, input);
    } catch (e) {
      // The message names the offending input, so it is shown rather than
      // replaced with a generic one.
      failure = e instanceof Error ? e.message : String(e);
    }
  }

  return (
    <div className="space-y-4">
      <div className="pane p-4">
        <Field label="Conversion" help={entry.detail}>
          {(id) => (
            <Select
              id={id}
              value={encoding}
              onChange={setEncoding}
              options={ENCODINGS.map((e) => ({ value: e.id, label: e.label }))}
            />
          )}
        </Field>

        <div className="mt-3">
          <label htmlFor="tool-input" className="mb-1 block t-small text-[var(--ink-muted)]">
            Input
          </label>
          <textarea
            id="tool-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={5}
            spellCheck={false}
            placeholder="Paste or type here"
            className="w-full resize-y rounded-none border border-[var(--line)] bg-[var(--surface)] px-3 py-2 font-[var(--font-mono)] text-[13px] text-[var(--ink)] outline-none placeholder:text-[var(--ink-faint)] focus:border-[var(--select)]"
          />
        </div>

        <div className="mt-3">
          <span className="mb-1 flex items-baseline justify-between">
            <span className="t-small text-[var(--ink-muted)]">Result</span>
            {output && (
              <Button
                size="sm"
                variant="quiet"
                onClick={() => navigator.clipboard?.writeText(output)}
              >
                Copy
              </Button>
            )}
          </span>
          {failure ? (
            <p
              className="border border-[var(--line)] px-3 py-2 t-small"
              style={{ color: "var(--fail)" }}
              role="alert"
            >
              {failure}
            </p>
          ) : (
            <textarea
              readOnly
              value={output}
              rows={5}
              aria-label="Result"
              className="w-full resize-y rounded-none border border-[var(--line)] bg-[var(--sunken)] px-3 py-2 font-[var(--font-mono)] text-[13px] text-[var(--ink)] outline-none"
            />
          )}
        </div>
      </div>

      <p className="flex items-start gap-2 t-micro text-[var(--ink-faint)]">
        <Icons.shield className="mt-[2px] h-3 w-3 shrink-0" />
        <span>
          Done in this browser. Nothing you paste here is sent anywhere, and
          non-ASCII text — accents, CJK, emoji — survives a round trip intact.
        </span>
      </p>
    </div>
  );
}
