"use client";

import { useId, useState, type ReactNode } from "react";
import type { EngineError, ItemStatus, Level, Notice } from "@/lib/types";

/* ------------------------------------------------------------------ icons */
/* 14px stroked glyphs. Functional only — DESIGN.md forbids decorative icons. */

type IconProps = { className?: string; style?: React.CSSProperties };

const svg = (path: ReactNode) =>
  function Icon({ className = "h-3.5 w-3.5", style }: IconProps) {
    return (
      <svg
        className={className}
        style={style}
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.4}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        {path}
      </svg>
    );
  };

export const Icons = {
  add: svg(<><path d="M8 3.5v9M3.5 8h9" /></>),
  close: svg(<path d="M4 4l8 8M12 4l-8 8" />),
  check: svg(<path d="M3.5 8.5l3 3 6-7" />),
  chevron: svg(<path d="M6 3.5l4.5 4.5L6 12.5" />),
  arrow: svg(<path d="M2.5 8h11m0 0l-4-4m4 4l-4 4" />),
  down: svg(<><path d="M8 2.5v8" /><path d="M4.5 7.5L8 11l3.5-3.5" /><path d="M2.5 13.5h11" /></>),
  search: svg(<><circle cx="7" cy="7" r="4.25" /><path d="M10.2 10.2l3 3" /></>),
  trash: svg(<><path d="M2.5 4.5h11M6 4.5V3h4v1.5" /><path d="M4 4.5l.7 9h6.6l.7-9" /></>),
  refresh: svg(<><path d="M13.5 7A5.5 5.5 0 003.6 4.4M2.5 2.5v3h3" /><path d="M2.5 9a5.5 5.5 0 009.9 2.6M13.5 13.5v-3h-3" /></>),
  stop: svg(<rect x="4" y="4" width="8" height="8" rx="1" />),
  eye: svg(<><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" /><circle cx="8" cy="8" r="1.8" /></>),
  folder: svg(<path d="M1.5 4a1 1 0 011-1h3l1.5 2h6.5a1 1 0 011 1v6.5a1 1 0 01-1 1h-11a1 1 0 01-1-1z" />),
  grip: svg(<><circle cx="6" cy="4" r=".7" /><circle cx="6" cy="8" r=".7" /><circle cx="6" cy="12" r=".7" /><circle cx="10" cy="4" r=".7" /><circle cx="10" cy="8" r=".7" /><circle cx="10" cy="12" r=".7" /></>),
  terminal: svg(<><rect x="1.5" y="2.5" width="13" height="11" rx="1" /><path d="M4.5 6.5L6.5 8l-2 1.5M8.5 10h3" /></>),
  sun: svg(<><circle cx="8" cy="8" r="2.75" /><path d="M8 1.5v1.5M8 13v1.5M2.5 8H1M15 8h-1.5M4.1 4.1L3 3M13 13l-1.1-1.1M4.1 11.9L3 13M13 3l-1.1 1.1" /></>),
  moon: svg(<path d="M13.5 9.5A6 6 0 015.5 2 6.2 6.2 0 108 14a6 6 0 005.5-4.5z" />),
  history: svg(<><circle cx="8" cy="8" r="6" /><path d="M8 4.5V8l2.5 1.5" /></>),
  shield: svg(<><path d="M8 1.5L3 3.5v4c0 3 2 5.5 5 6.5 3-1 5-3.5 5-6.5v-4z" /><path d="M6 8l1.5 1.5L10.5 6.5" /></>),
  layers: svg(<><path d="M8 1.5L14 5 8 8.5 2 5z" /><path d="M2 8l6 3.5L14 8" /><path d="M2 11l6 3.5L14 11" /></>),
  info: svg(<><circle cx="8" cy="8" r="6" /><path d="M8 7.2v4M8 5v.01" /></>),

  // Goal glyphs. Each maps to one distinct intent; none is decorative.
  compress: svg(<><path d="M2 8h4M14 8h-4" /><path d="M4.5 5.5L6.5 8 4.5 10.5M11.5 5.5L9.5 8l2 2.5" /></>),
  pencil: svg(<><path d="M11 2.5l2.5 2.5L6 12.5 3 13l.5-3z" /><path d="M10 3.5l2.5 2.5" /></>),
  send: svg(<><path d="M14 2L7 9" /><path d="M14 2l-4.5 12-2.5-5-5-2.5z" /></>),
  globe: svg(<><circle cx="8" cy="8" r="6" /><path d="M2.2 6.5h11.6M2.2 9.5h11.6" /><path d="M8 2a11 11 0 000 12a11 11 0 000-12z" /></>),
  gem: svg(<><path d="M4.5 2.5h7L14 6l-6 7.5L2 6z" /><path d="M2 6h12M6 2.5L5 6l3 7.5L11 6l-1-3.5" /></>),
  speaker: svg(<><path d="M3 6h2.5L9 3v10L5.5 10H3z" /><path d="M11.5 6a3 3 0 010 4" /></>),
  printer: svg(<><path d="M4.5 6V2.5h7V6" /><path d="M4.5 12H3a1 1 0 01-1-1V7a1 1 0 011-1h10a1 1 0 011 1v4a1 1 0 01-1 1h-1.5" /><path d="M4.5 9.5h7v4h-7z" /></>),

  // Category glyphs, for identifying a format at a glance.
  file: svg(<><path d="M9.5 1.5H4a1 1 0 00-1 1v11a1 1 0 001 1h8a1 1 0 001-1V5z" /><path d="M9.5 1.5V5H13" /></>),
  text: svg(<><path d="M3 4h10M3 7.5h10M3 11h6" /></>),
  image: svg(<><rect x="2" y="3" width="12" height="10" rx="1.5" /><circle cx="5.5" cy="6.5" r="1" /><path d="M2.5 11l3-2.5 2.5 2 2-1.5 3.5 3" /></>),
  table: svg(<><rect x="2" y="3" width="12" height="10" rx="1.5" /><path d="M2 6.5h12M6 6.5V13" /></>),
  media: svg(<><rect x="2" y="3.5" width="12" height="9" rx="1.5" /><path d="M6.5 6.5l3.5 2-3.5 2z" /></>),
  archive: svg(<><path d="M2 5h12v8a1 1 0 01-1 1H3a1 1 0 01-1-1z" /><path d="M2 5V3a1 1 0 011-1h10a1 1 0 011 1v2" /><path d="M6.5 8h3" /></>),
  warn: svg(<><path d="M8 2.2L1.8 13.2h12.4z" /><path d="M8 6.5v3M8 11.4v.01" /></>),
  error: svg(<><circle cx="8" cy="8" r="6" /><path d="M6 6l4 4M10 6l-4 4" /></>),
};

/** Format category → glyph. Used where a format must be identified quickly;
 *  never as decoration on something already unambiguous. */
export const CATEGORY_ICON: Record<string, (p: IconProps) => ReactNode> = {
  document: Icons.file,
  text: Icons.text,
  image: Icons.image,
  data: Icons.table,
  audio: Icons.media,
  video: Icons.media,
  archive: Icons.archive,
  subtitle: Icons.text,
};

/* ------------------------------------------------------------------ status */

const STATE: Record<string, { colour: string; wash: string; label: string }> = {
  queued: { colour: "var(--idle)", wash: "transparent", label: "Queued" },
  running: { colour: "var(--running)", wash: "var(--running-wash)", label: "Running" },
  completed: { colour: "var(--ok)", wash: "var(--ok-wash)", label: "Done" },
  completed_with_warnings: { colour: "var(--warn)", wash: "var(--warn-wash)", label: "Warnings" },
  failed: { colour: "var(--fail)", wash: "var(--fail-wash)", label: "Failed" },
  cancelled: { colour: "var(--idle)", wash: "transparent", label: "Cancelled" },
  skipped: { colour: "var(--idle)", wash: "transparent", label: "Skipped" },
};

export function statusColour(status: string): string {
  return STATE[status]?.colour ?? "var(--idle)";
}

/** A 6px dot plus a word. Never a filled pill. */
export function Status({
  status,
  label,
  className = "",
}: {
  status: ItemStatus | string;
  label?: string;
  className?: string;
}) {
  const state = STATE[status] ?? STATE.queued;
  return (
    <span className={`inline-flex items-center gap-1.5 t-small ${className}`}>
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ background: state.colour }}
      />
      <span style={{ color: state.colour }}>{label ?? state.label}</span>
    </span>
  );
}

/** Format identifier — the one place a pill is allowed. */
export function Fmt({ children }: { children: ReactNode }) {
  return <span className="fmt">{children}</span>;
}

/* ----------------------------------------------------------------- buttons */

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "default" | "quiet" | "danger";
  size?: "sm" | "md";
};

export function Button({
  variant = "default",
  size = "md",
  className = "",
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      className={`btn btn-${variant} ${size === "sm" ? "btn-sm" : ""} ${className}`}
    />
  );
}

/* ----------------------------------------------------------------- regions */

export function Section({
  title,
  right,
  children,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`px-3 py-3 ${className}`}>
      <header className="mb-2 flex h-4 items-center justify-between gap-2">
        <h3 className="t-label">{title}</h3>
        {right}
      </header>
      {children}
    </section>
  );
}

export function Disclosure({
  title,
  count,
  children,
  defaultOpen = false,
}: {
  title: string;
  count?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex h-6 w-full items-center gap-1.5 text-left"
      >
        <Icons.chevron
          className={`h-3 w-3 shrink-0 text-[var(--ink-faint)] transition-transform duration-[120ms] ${
            open ? "rotate-90" : ""
          }`}
        />
        <span className="t-label">{title}</span>
        {count && <span className="ml-auto t-micro text-[var(--ink-faint)]">{count}</span>}
      </button>
      {open && <div className="pt-2">{children}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ inputs */

export function Field({
  label,
  help,
  unit,
  children,
}: {
  label: string;
  help?: string;
  unit?: string;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1 block t-small text-[var(--ink-muted)]">
        {label}
        {unit && <span className="ml-1 text-[var(--ink-faint)]">({unit})</span>}
      </label>
      {children(id)}
      {help && (
        <p className="mt-1 t-micro text-[var(--ink-faint)]">{help}</p>
      )}
    </div>
  );
}

export function Select({
  id,
  value,
  onChange,
  options,
  className = "",
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string; help?: string }[];
  className?: string;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={`ctl w-full ${className}`}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value} title={option.help}>
          {option.label}
          {option.help ? ` — ${option.help}` : ""}
        </option>
      ))}
    </select>
  );
}

export function TextInput({
  id,
  value,
  onChange,
  placeholder,
  type = "text",
  min,
  max,
  step,
  className = "",
}: {
  id?: string;
  value: string | number;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  className?: string;
}) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      placeholder={placeholder}
      min={min ?? undefined}
      max={max ?? undefined}
      step={step ?? undefined}
      onChange={(event) => onChange(event.target.value)}
      className={`ctl w-full ${type === "number" ? "num" : ""} ${className}`}
    />
  );
}

export function Check({
  checked,
  onChange,
  label,
  help,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  help?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2">
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className="mt-[1px] flex h-[13px] w-[13px] shrink-0 items-center justify-center rounded-[3px] border transition-colors duration-[120ms]"
        style={{
          borderColor: checked ? "var(--select)" : "var(--line-strong)",
          background: checked ? "var(--solid)" : "transparent",
        }}
      >
        {checked && (
          <svg viewBox="0 0 16 16" className="h-2.5 w-2.5" style={{ color: "var(--solid-ink)" }}>
            <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2.2"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </button>
      <span>
        <span className="block t-small">{label}</span>
        {help && <span className="mt-0.5 block t-micro text-[var(--ink-faint)]">{help}</span>}
      </span>
    </label>
  );
}

export function Segmented<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string; disabled?: boolean }[];
  ariaLabel: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className="inline-flex h-6 items-center rounded-[4px] border border-[var(--line-strong)]"
    >
      {options.map((option, index) => (
        <button
          key={option.value}
          role="tab"
          aria-selected={value === option.value}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
          className={`h-full px-2 text-[11.5px] font-[550] transition-colors duration-[120ms]
            disabled:cursor-not-allowed disabled:opacity-40
            ${index > 0 ? "border-l border-[var(--line-strong)]" : ""}
            ${
              value === option.value
                ? "bg-[var(--solid)] text-[var(--solid-ink)]"
                : "text-[var(--ink-muted)] hover:bg-[var(--raised)] hover:text-[var(--ink)]"
            }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- progress */

export function Bar({
  value,
  status = "running",
  indeterminate = false,
}: {
  value: number;
  status?: string;
  indeterminate?: boolean;
}) {
  return (
    <div
      className={`relative h-[3px] w-full overflow-hidden bg-[var(--line)] ${
        indeterminate ? "indeterminate" : ""
      }`}
      role="progressbar"
      aria-valuenow={indeterminate ? undefined : Math.round(value * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      {!indeterminate && (
        <div
          className="h-full transition-[width] duration-[120ms] ease-out"
          style={{ width: `${Math.max(1, value * 100)}%`, background: statusColour(status) }}
        />
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- notices */

const NOTICE_ICON: Record<Level, (p: IconProps) => ReactNode> = {
  info: Icons.info,
  warning: Icons.warn,
  error: Icons.error,
};

const NOTICE_COLOUR: Record<Level, string> = {
  info: "var(--ink-faint)",
  warning: "var(--warn)",
  error: "var(--fail)",
};

export function NoticeRow({ notice }: { notice: Notice }) {
  const Icon = NOTICE_ICON[notice.level];
  return (
    <div className="flex gap-2 px-3 py-2">
      <Icon className="mt-[2px] h-3 w-3 shrink-0" />
      <div style={{ color: "inherit" }}>
        <p className="t-small t-strong" style={{ color: NOTICE_COLOUR[notice.level] }}>
          {notice.title}
        </p>
        <p className="t-small text-[var(--ink-muted)]">{notice.detail}</p>
      </div>
    </div>
  );
}

export function NoticeList({ notices }: { notices: Notice[] }) {
  if (!notices.length) return null;
  return (
    <div className="-mx-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
      {notices.map((notice, index) => (
        <NoticeRow key={`${notice.title}-${index}`} notice={notice} />
      ))}
    </div>
  );
}

export function ErrorBlock({
  error,
  onDismiss,
}: {
  error: EngineError;
  onDismiss?: () => void;
}) {
  return (
    <div
      className="border-l-2 px-3 py-2"
      style={{ borderColor: "var(--fail)", background: "var(--fail-wash)" }}
    >
      <div className="flex items-start gap-2">
        <div className="flex-1">
          <p className="t-small t-strong" style={{ color: "var(--fail)" }}>
            {error.title}
          </p>
          <p className="mt-0.5 t-small text-[var(--ink-muted)]">{error.detail}</p>
          {error.suggestions.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {error.suggestions.map((suggestion) => (
                <li key={suggestion} className="flex gap-1.5 t-small text-[var(--ink-muted)]">
                  <span className="text-[var(--ink-faint)]">→</span>
                  <span>{suggestion}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        {onDismiss && (
          <button
            onClick={onDismiss}
            aria-label="Dismiss"
            className="btn btn-quiet btn-sm -mr-1 px-1"
          >
            <Icons.close className="h-3 w-3" />
          </button>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- empty */

export function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="px-3 py-8 text-center">
      <p className="t-small t-strong text-[var(--ink-muted)]">{title}</p>
      <p className="mx-auto mt-1 max-w-[34ch] t-small text-[var(--ink-faint)]">{detail}</p>
    </div>
  );
}

export function Spinner({ className = "h-3 w-3" }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" fill="none" opacity="0.2" />
      <path d="M14 8a6 6 0 00-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
    </svg>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="mono rounded-[3px] border border-[var(--line-strong)] px-1 text-[9.5px] text-[var(--ink-faint)]">
      {children}
    </kbd>
  );
}
