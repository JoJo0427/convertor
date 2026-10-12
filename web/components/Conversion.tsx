"use client";

import { useMemo, useState } from "react";
import type {
  ConversionOption,
  Plan,
  PlanDetails,
  Recommendation,
} from "@/lib/types";
import {
  Button,
  CATEGORY_ICON,
  Check,
  Disclosure,
  Empty,
  Field,
  Fmt,
  Icons,
  Section,
  Segmented,
  Select,
  TextInput,
} from "./ui";

export type Mode = "each" | "combined" | "split";

function FormatTile({
  item,
  selected,
  onSelect,
}: {
  item: Recommendation;
  selected: boolean;
  onSelect: () => void;
}) {
  const blocked = !item.route.available;
  const pack = item.route.missingPacks?.[0];
  const why = [
    item.route.direct ? item.summary : item.route.description,
    blocked
      ? pack
        ? `Needs the ${pack.label} pack — ${pack.megabytes} MB`
        : `Needs ${item.route.missingEngines.join(", ")}`
      : "",
  ]
    .filter(Boolean)
    .join(" — ");

  return (
    <button
      onClick={onSelect}
      aria-pressed={selected}
      className="choice format-choice"
      title={why}
    >
      <span className="flex w-full items-center gap-2">
        <span className="flex-1 text-[13px] font-[600]">{item.label}</span>
        <Fmt>{item.formatId}</Fmt>
        {selected && <Icons.check className="h-3 w-3 shrink-0" />}
      </span>
      <span className="t-small text-[var(--ink-muted)]">{item.summary || item.reason}</span>
      {!item.route.direct && (
        <span className="t-small text-[var(--ink-muted)]">
          {item.route.description}
        </span>
      )}
      {blocked && (
        <span className="t-small" style={{ color: "var(--warn)" }}>
          {pack ? `Needs ${pack.label} · ${pack.megabytes} MB download` : `Needs ${item.route.missingEngines.join(", ")}`}
        </span>
      )}
    </button>
  );
}

function FormatBrowser({
  list,
  target,
  onSelect,
  onClose,
}: {
  list: Recommendation[];
  target: string | null;
  onSelect: (formatId: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");

  const grouped = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = list.filter(
      (item) =>
        !needle ||
        item.label.toLowerCase().includes(needle) ||
        item.formatId.includes(needle) ||
        item.summary.toLowerCase().includes(needle),
    );
    const groups = new Map<string, Recommendation[]>();
    filtered.forEach((item) => {
      const bucket = groups.get(item.category) ?? [];
      bucket.push(item);
      groups.set(item.category, bucket);
    });
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [list, query]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-16">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div
        role="dialog"
        aria-label="All output formats"
        className="overlay relative flex max-h-[70vh] w-full max-w-[640px] flex-col"
      >
        <div className="flex h-8 flex-none items-center gap-2 border-b border-[var(--line)] px-2.5">
          <Icons.search className="h-3 w-3 shrink-0 text-[var(--ink-faint)]" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => event.key === "Escape" && onClose()}
            placeholder="Search every reachable format…"
            className="flex-1 bg-transparent text-[12.5px] outline-none placeholder:text-[var(--ink-faint)]"
          />
          <span className="num t-micro text-[var(--ink-faint)]">{list.length}</span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {grouped.length === 0 ? (
            <Empty title="Nothing matches" detail="No reachable format matches that search." />
          ) : (
            grouped.map(([category, items]) => (
              <div key={category}>
                <p className="t-label sticky top-0 border-b border-[var(--line)] bg-[var(--raised)] px-2.5 py-1">
                  {category}
                </p>
                {items.map((item) => (
                  <button
                    key={item.formatId}
                    onClick={() => {
                      onSelect(item.formatId);
                      onClose();
                    }}
                    className="row row-hover w-full items-start py-2 text-left"
                    style={
                      target === item.formatId
                        ? { background: "var(--select-wash)" }
                        : undefined
                    }
                  >
                    <Fmt>{item.formatId}</Fmt>
                    <span className="flex-1">
                      <span className="block t-small t-strong">{item.label}</span>
                      <span className="block t-small text-[var(--ink-muted)]">{item.summary}</span>
                      {!item.route.available && <span className="block t-small text-[var(--warn)]">
                        Needs {item.route.missingPacks?.map((pack) => pack.label).join(", ") || item.route.missingEngines.join(", ")}
                      </span>}
                    </span>
                    {!item.route.direct && (
                      <span className="num t-micro shrink-0 text-[var(--ink-faint)]">
                        {item.route.steps.length} steps
                      </span>
                    )}
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function OptionControl({
  option,
  value,
  onChange,
}: {
  option: ConversionOption;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (option.type === "boolean") {
    return (
      <Check
        checked={Boolean(value)}
        onChange={onChange}
        label={
          option.label +
          (option.appliesTo?.length > 0 ? ` · ${option.appliesTo.join(", ")} only` : "")
        }
        help={option.help}
      />
    );
  }
  const scope =
    option.appliesTo?.length > 0 ? ` · ${option.appliesTo.join(", ")} only` : "";

  return (
    <Field
      label={option.label + scope}
      help={option.help}
      unit={option.unit || undefined}
    >
      {(id) =>
        option.type === "select" ? (
          <Select
            id={id}
            value={String(value ?? "")}
            onChange={onChange}
            options={option.choices.map((choice) => ({
              value: choice.value,
              label: choice.label,
              help: choice.help,
            }))}
          />
        ) : (
          <TextInput
            id={id}
            type={option.type === "number" ? "number" : "text"}
            value={value === null || value === undefined ? "" : String(value)}
            min={option.min}
            max={option.max}
            step={option.step}
            onChange={(raw) =>
              onChange(option.type === "number" ? (raw === "" ? null : Number(raw)) : raw)
            }
          />
        )
      }
    </Field>
  );
}

export function Conversion({
  plan,
  mode,
  onModeChange,
  target,
  onTargetChange,
  details,
  options,
  onOptionChange,
  preset,
  onPresetChange,
  onSavePreset,
  onDeletePreset,
  fileCount,
}: {
  plan: Plan | null;
  mode: Mode;
  onModeChange: (mode: Mode) => void;
  target: string | null;
  onTargetChange: (formatId: string) => void;
  details: PlanDetails | null;
  options: Record<string, unknown>;
  onOptionChange: (id: string, value: unknown) => void;
  preset: string | null;
  onPresetChange: (id: string, values: Record<string, unknown>) => void;
  onSavePreset: (label: string) => void;
  onDeletePreset: (id: string) => void;
  fileCount: number;
}) {
  const [browsing, setBrowsing] = useState(false);
  const [savingName, setSavingName] = useState<string | null>(null);

  const list =
    mode === "combined"
      ? plan?.combine ?? []
      : mode === "split"
        ? plan?.split ?? []
        : plan?.recommendations ?? [];
  const featured = list.filter((item) => item.featured);
  const shown = (featured.length ? featured : list).slice(0, 12);
  const selected = list.find((item) => item.formatId === target);
  if (selected && !shown.includes(selected)) shown.push(selected);

  const visible = (details?.options ?? []).filter(
    (option) =>
      !option.dependsOn ||
      option.dependsOn.values.includes(String(options[option.dependsOn.option])),
  );
  const basic = visible.filter((option) => !option.advanced);
  const advanced = visible.filter((option) => option.advanced);

  if (fileCount === 0) {
    return (
      <section aria-label="Conversion" className="pane">
        <header className="pane-head">
          <h2 className="t-label">Conversion</h2>
        </header>
        <div className="pane-body">
          <Empty
            title="Nothing queued"
            detail="Add files on the left. The formats they can become appear here."
          />
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Conversion" className="pane">
      <header className="pane-head">
        <h2 className="t-label">Output</h2>
        <div className="ml-auto">
          <Segmented<Mode>
            ariaLabel="Conversion shape"
            value={mode}
            onChange={onModeChange}
            options={[
              { value: "each", label: "Each file" },
              { value: "combined", label: "Combine", disabled: !plan?.canCombine },
              { value: "split", label: "Split", disabled: !plan?.canSplit },
            ]}
          />
        </div>
      </header>

      <div className="pane-body">
        {plan?.mixed && mode === "each" && (
          <p className="rule px-3 py-1.5 t-micro text-[var(--ink-muted)]">
            Mixed input types — only formats every queued file can reach are offered.
          </p>
        )}

        {/* The output choice is the one decision this pane exists for, so it
            gets a real heading and the largest targets — not the same
            micro-label-plus-chips shape as everything under it. */}
        <section className="px-[var(--pad)] pb-4 pt-4">
          <div className="mb-3 flex items-baseline gap-2">
            <h3 className="t-head">
              {mode === "combined"
                ? "Combine into"
                : mode === "split"
                  ? "Split into"
                  : "Convert to"}
            </h3>
            {list.length > 0 && (
              <span className="num t-small text-[var(--ink-faint)]">
                {list.length} format{list.length === 1 ? "" : "s"}
              </span>
            )}
            {list.length > shown.length && (
              <button
                onClick={() => setBrowsing(true)}
                className="ml-auto t-small text-[var(--ink-muted)] underline-offset-2 hover:text-[var(--ink)] hover:underline"
              >
                Browse all
              </button>
            )}
          </div>
          {featured.length > 0 && list.length > 1 && mode === "each" && (
            <p className="mb-3 t-small text-[var(--ink-muted)]">
              Common outputs for these files appear first. Available converters and fewer conversion steps rank higher.
            </p>
          )}
          {!plan ? (
            <p role="status" className="py-4 t-small text-[var(--ink-muted)]">Checking output formats…</p>
          ) : shown.length === 0 ? (
            <Empty
              title={
                mode === "combined"
                  ? "These files cannot be combined"
                  : mode === "split"
                    ? "These files cannot be split"
                    : "No shared output format"
              }
              detail="Remove files that do not belong, or convert them in separate batches."
            />
          ) : (
            <div className="tile-grid">
              {shown.map((item) => (
                <FormatTile
                  key={item.formatId}
                  item={item}
                  selected={target === item.formatId}
                  onSelect={() => onTargetChange(item.formatId)}
                />
              ))}
            </div>
          )}
        </section>

        {/* Recently used formats, as a shortcut into the same choice above.
            There was a second strip here offering conversions by goal — "Make it
            smaller", "Send to others" — which named an intention rather than a
            format. It asked the user to translate what they wanted into our
            vocabulary and then guessed a format on their behalf. The format tiles
            and the settings below already say exactly what will happen, so the
            goal strip was a third way to express the same choice, less precisely.
            Recent stays: it offers formats, by name, that this person actually
            picked before. */}
        {mode === "each" && (plan?.recent?.length ?? 0) > 0 && (
            <div className="rule border-t border-[var(--line)] bg-[var(--raised)] px-[var(--pad)] py-2.5">
              {(
                <div className="flex gap-2.5">
                  <span className="mt-1.5 w-12 shrink-0 t-micro text-[var(--ink-faint)]">
                    Recent
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {plan!.recent.map((entry) => {
                      const Glyph = CATEGORY_ICON[entry.category] ?? Icons.file;
                      const active = target === entry.formatId;
                      return (
                        <button
                          key={entry.formatId}
                          onClick={() => onTargetChange(entry.formatId)}
                          aria-pressed={active}
                          title={`Used ${entry.uses} time${entry.uses === 1 ? "" : "s"}`}
                          className="shortcut"
                          style={
                            active
                              ? {
                                  borderColor: "var(--select)",
                                  background: "var(--select-wash)",
                                }
                              : undefined
                          }
                        >
                          <Glyph className="h-3.5 w-3.5 shrink-0 text-[var(--ink-faint)]" />
                          {entry.label}
                          <span className="num t-micro text-[var(--ink-faint)]">
                            {entry.uses}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

        {target && details && (
          <>
            {details.presets.length > 0 && (
              <Section
                title="Settings"
                right={
                  savingName === null ? (
                    <button
                      onClick={() => setSavingName("")}
                      className="t-micro text-[var(--ink-muted)] hover:text-[var(--ink)]"
                    >
                      save these as a preset
                    </button>
                  ) : undefined
                }
              >
                <div className="flex flex-wrap gap-1">
                  {details.presets.map((item) => (
                    <span key={item.id} className="relative inline-flex">
                      <button
                        onClick={() => onPresetChange(item.id, item.values)}
                        aria-pressed={preset === item.id}
                        title={item.description}
                        className="btn btn-default btn-sm"
                        style={
                          preset === item.id
                            ? { borderColor: "var(--select)", background: "var(--select-wash)" }
                            : undefined
                        }
                      >
                        {item.label}
                        {item.custom && (
                          <span
                            role="button"
                            tabIndex={0}
                            aria-label={`Delete preset ${item.label}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              onDeletePreset(item.id);
                            }}
                            className="text-[var(--ink-faint)] hover:text-[var(--fail)]"
                          >
                            <Icons.close className="h-2.5 w-2.5" />
                          </span>
                        )}
                      </button>
                    </span>
                  ))}
                </div>

                {savingName !== null && (
                  <div className="mt-2 flex gap-1.5">
                    <TextInput
                      value={savingName}
                      onChange={setSavingName}
                      placeholder="Preset name"
                      className="flex-1"
                    />
                    <Button size="sm" variant="quiet" onClick={() => setSavingName(null)}>
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={!savingName.trim()}
                      onClick={() => {
                        onSavePreset(savingName.trim());
                        setSavingName(null);
                      }}
                    >
                      Save
                    </Button>
                  </div>
                )}
              </Section>
            )}

            {basic.length > 0 && (
              <div className="px-[var(--pad)] pb-3">
                <div className="grid gap-2.5 sm:grid-cols-2">
                  {basic.map((option) => (
                    <OptionControl
                      key={option.id}
                      option={option}
                      value={options[option.id] ?? option.default}
                      onChange={(value) => onOptionChange(option.id, value)}
                    />
                  ))}
                </div>
              </div>
            )}

            {advanced.length > 0 && (
              <div className="rule px-3 py-2">
                <Disclosure title="Advanced" count={`${advanced.length}`}>
                  <div className="grid gap-2.5 sm:grid-cols-2">
                    {advanced.map((option) => (
                      <OptionControl
                        key={option.id}
                        option={option}
                        value={options[option.id] ?? option.default}
                        onChange={(value) => onOptionChange(option.id, value)}
                      />
                    ))}
                  </div>
                </Disclosure>
              </div>
            )}

            {details.plan.some((step) => step.command) && (
              <div className="rule px-3 py-2">
                <Disclosure title="Expert — what will run">
                  <div className="space-y-2">
                    {details.plan.map((step) => (
                      <div key={step.step}>
                        <p className="t-micro text-[var(--ink-faint)]">
                          {step.step}. {step.converter} · {step.engine}
                        </p>
                        {step.detail && (
                          <p className="t-micro text-[var(--ink-muted)]">{step.detail}</p>
                        )}
                        {step.command && (
                          <pre className="mono mt-1 overflow-x-auto whitespace-pre-wrap break-all bg-[var(--sunken)] p-2 text-[var(--ink-muted)]">
                            {step.command}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                </Disclosure>
              </div>
            )}
          </>
        )}
      </div>

      {browsing && (
        <FormatBrowser
          list={list}
          target={target}
          onSelect={onTargetChange}
          onClose={() => setBrowsing(false)}
        />
      )}
    </section>
  );
}
