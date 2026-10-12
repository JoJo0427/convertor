"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { SystemInfo } from "@/lib/types";
import { Icons, Kbd, Spinner } from "./ui";

const NAV = [
  { href: "/", label: "Convert" },
  { href: "/download", label: "Media downloader" },
  { href: "/formats", label: "Formats" },
  { href: "/history", label: "History" },
  { href: "/privacy", label: "Privacy" },
];

type ThemeId = "formal" | "blush" | "denim" | "earth";

const THEMES: {
  id: ThemeId;
  label: string;
  note: string;
  swatch: [string, string, string];
}[] = [
  {
    id: "formal",
    label: "Formal",
    note: "Achromatic, densest",
    swatch: ["#ffffff", "#cfcfd3", "#1a1a1c"],
  },
  {
    id: "blush",
    label: "Blush",
    note: "Rounded face, larger type",
    swatch: ["#fbefef", "#f9dfdf", "#f5afaf"],
  },
  {
    id: "denim",
    label: "Denim",
    note: "Indigo on cotton",
    swatch: ["#eae0cf", "#94b4c1", "#213448"],
  },
  {
    id: "earth",
    label: "Earth",
    note: "Clay, sage and oat",
    swatch: ["#ece7d1", "#8e977d", "#8a7650"],
  },
];

function Appearance() {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeId>("formal");
  const [mode, setMode] = useState<"light" | "dark">("light");

  useEffect(() => {
    const root = document.documentElement;
    setTheme((root.dataset.theme as ThemeId) || "formal");
    setMode((root.dataset.mode as "light" | "dark") || "light");
  }, []);

  const applyTheme = (next: ThemeId) => {
    document.documentElement.dataset.theme = next;
    localStorage.setItem("convertor-theme", next);
    setTheme(next);
  };

  const applyMode = (next: "light" | "dark") => {
    document.documentElement.dataset.mode = next;
    localStorage.setItem("convertor-mode", next);
    setMode(next);
  };

  const current = THEMES.find((entry) => entry.id === theme) ?? THEMES[0];

  return (
    <div className="flex items-center gap-1">
      <div className="relative">
        <button
          onClick={() => setOpen(!open)}
          aria-haspopup="menu"
          aria-expanded={open}
          className="btn btn-quiet btn-sm gap-1.5 px-1.5"
          title="Appearance"
        >
          <span className="flex h-3 w-3 overflow-hidden rounded-full border border-[var(--line-strong)]">
            {current.swatch.map((colour) => (
              <span key={colour} className="h-full flex-1" style={{ background: colour }} />
            ))}
          </span>
          <span className="hidden sm:inline">{current.label}</span>
        </button>

        {open && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
            <div role="menu" className="overlay absolute right-0 top-7 z-50 w-[248px] p-1">
              {THEMES.map((entry) => (
                <button
                  key={entry.id}
                  role="menuitemradio"
                  aria-checked={theme === entry.id}
                  onClick={() => {
                    applyTheme(entry.id);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded-[var(--radius)] px-2 py-1.5 text-left transition-colors duration-[120ms] hover:bg-[var(--raised)]"
                  style={
                    theme === entry.id ? { background: "var(--select-wash)" } : undefined
                  }
                >
                  <span className="flex h-4 w-4 shrink-0 overflow-hidden rounded-full border border-[var(--line-strong)]">
                    {entry.swatch.map((colour) => (
                      <span
                        key={colour}
                        className="h-full flex-1"
                        style={{ background: colour }}
                      />
                    ))}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block t-small t-strong">{entry.label}</span>
                    <span className="block t-micro text-[var(--ink-faint)]">
                      {entry.note}
                    </span>
                  </span>
                  {theme === entry.id && <Icons.check className="h-3 w-3 shrink-0" />}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      <button
        onClick={() => applyMode(mode === "dark" ? "light" : "dark")}
        aria-label={`Switch to ${mode === "dark" ? "light" : "dark"} mode`}
        className="btn btn-quiet h-6 px-1.5"
      >
        {mode === "dark" ? <Icons.sun /> : <Icons.moon />}
      </button>
    </div>
  );
}

function EngineStatus({ info }: { info: SystemInfo | null }) {
  const [open, setOpen] = useState(false);

  if (!info) {
    return (
      <span className="flex items-center gap-1.5 t-small text-[var(--ink-faint)]">
        <Spinner /> connecting
      </span>
    );
  }

  const missing = info.engines.filter((engine) => !engine.available);
  const ffmpeg = info.ffmpeg;

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex h-6 items-center gap-1.5 rounded-[4px] px-1.5 t-small
          text-[var(--ink-muted)] transition-colors duration-[120ms] hover:bg-[var(--raised)]"
      >
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: missing.length ? "var(--warn)" : "var(--ok)" }}
        />
        {/* The counts are useful context, not essential; below sm they would
            push the bar past the viewport, so only the state dot survives. */}
        <span className="hidden sm:inline">
          <span className="num">{info.converterCount}</span> converters
        </span>
        <span className="hidden text-[var(--ink-faint)] md:inline">·</span>
        <span className="hidden md:inline">
          <span className="num">{info.formatCount}</span> formats
        </span>
        <span className="sr-only">
          {info.converterCount} converters, {info.formatCount} formats
        </span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="overlay absolute right-0 top-7 z-50 w-[320px] p-3">
            <p className="t-label mb-2">Engines</p>
            <ul className="-mx-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
              {info.engines.map((engine) => (
                <li key={engine.id} className="flex items-center gap-2 px-3 py-1.5">
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: engine.available ? "var(--ok)" : "var(--warn)" }}
                  />
                  <span className="flex-1 t-small">{engine.label}</span>
                  <span className="t-micro text-[var(--ink-faint)]">
                    {engine.available ? "ready" : "not installed"}
                  </span>
                </li>
              ))}
            </ul>

            {ffmpeg?.available && (
              <div className="mt-3">
                <p className="t-label mb-1.5">FFmpeg capability, as discovered</p>
                <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                  {[
                    ["formats readable", ffmpeg.readableFormats],
                    ["formats writable", ffmpeg.writableFormats],
                    ["encoders", ffmpeg.encoders],
                    ["filters", ffmpeg.filters],
                  ].map(([label, value]) => (
                    <div key={String(label)} className="flex justify-between gap-2">
                      <dt className="t-micro text-[var(--ink-faint)]">{label}</dt>
                      <dd className="t-micro num">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
                <p className="mono mt-1.5 truncate text-[var(--ink-faint)]">
                  {ffmpeg.version.replace("ffmpeg version ", "")}
                </p>
              </div>
            )}

            <p className="mt-3 t-micro text-[var(--ink-faint)]">
              {info.intelligentWorkflows.available
                ? `Intelligent workflows on — ${info.intelligentWorkflows.model}. Those conversions send file contents to Anthropic.`
                : "Intelligent workflows off. Every conversion runs on this machine."}
            </p>
          </div>
        </>
      )}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [info, setInfo] = useState<SystemInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .system()
        .then((data) => !cancelled && setInfo(data))
        .catch(() => !cancelled && setInfo(null));
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="flex h-dvh flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50
          focus:rounded-[4px] focus:bg-[var(--surface)] focus:px-2 focus:py-1 focus:t-small"
      >
        Skip to content
      </a>

      <header className="flex h-12 flex-none items-center gap-3 border-b border-[var(--line)] bg-[var(--surface)] px-4">
        {/* shrink-0 so the wordmark is never the thing that gives way. Adding a
            fifth nav item pushed the header past the available width on a phone
            and the brand was compressed until "Convertor" was clipped — the one
            piece of text in the app that should never be cut. */}
        <Link href="/" className="flex shrink-0 items-center gap-1.5">
          <Icons.layers className="h-3.5 w-3.5" />
          <span className="text-[13px] font-[600] tracking-[-0.01em]">Convertor</span>
        </Link>

        {/* The nav scrolls instead of crushing its neighbours. scrollbar-none
            keeps that invisible on desktop, where it never triggers. */}
        <nav
          className="flex min-w-0 items-center overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          aria-label="Main"
        >
          {NAV.map((item) => {
            const active =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`flex h-6 shrink-0 items-center rounded-[4px] px-2 text-[12px] font-[550]
                  transition-colors duration-[120ms] ${
                    active
                      ? "bg-[var(--select-wash)] text-[var(--ink)]"
                      : "text-[var(--ink-muted)] hover:bg-[var(--raised)] hover:text-[var(--ink)]"
                  }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <span className="hidden items-center gap-1 t-micro text-[var(--ink-faint)] lg:flex">
            <Icons.shield className="h-3 w-3" /> local
          </span>
          <EngineStatus info={info} />
          <Appearance />
        </div>
      </header>

      <main id="main" className="min-h-0 flex-1 bg-[var(--canvas)]">
        {children}
      </main>
    </div>
  );
}
