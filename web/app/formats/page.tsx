"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { FormatDetail, FormatSummary } from "@/lib/types";
import { Empty, Fmt, Icons, Section, Spinner } from "@/components/ui";

const SUGGESTIONS = [
  "editable document",
  "web image",
  "spreadsheet",
  "transparent image",
  "scan",
  "markdown",
];

function Detail({ id, onPick }: { id: string; onPick: (id: string) => void }) {
  const [detail, setDetail] = useState<FormatDetail | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    api
      .format(id)
      .then((data) => !cancelled && setDetail(data))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!detail) {
    return (
      <div className="flex h-32 items-center justify-center text-[var(--ink-faint)]">
        <Spinner />
      </div>
    );
  }

  const direct = detail.convertsTo.filter((item) => item.direct && !item.discovered);
  const discovered = detail.convertsTo.filter((item) => item.direct && item.discovered);
  const indirect = detail.convertsTo.filter((item) => !item.direct);

  const Chips = ({
    items,
    dashed = false,
  }: {
    items: { id: string; label: string; available?: boolean; via?: string }[];
    dashed?: boolean;
  }) => (
    <div className="flex flex-wrap gap-1">
      {items.map((item) => (
        <button
          key={item.id}
          onClick={() => onPick(item.id)}
          title={item.via || undefined}
          className="btn btn-sm"
          style={{
            border: `1px ${dashed ? "dashed" : "solid"} var(--line-strong)`,
            color: item.available === false ? "var(--warn)" : undefined,
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );

  return (
    <div className="pane-body">
      <div className="rule px-3 py-3">
        <div className="flex flex-wrap items-baseline gap-2">
          <h1 className="t-head">{detail.label}</h1>
          <Fmt>{detail.id}</Fmt>
          <span className="mono text-[var(--ink-faint)]">
            {detail.extensions.join(" ")}
          </span>
        </div>
        <p className="mt-1 t-small text-[var(--ink-muted)]">{detail.summary}</p>
      </div>

      {detail.commonUses.length > 0 && (
        <Section title="Used for">
          <p className="t-small text-[var(--ink-muted)]">
            {detail.commonUses.join(" · ")}
          </p>
        </Section>
      )}

      {detail.notes.length > 0 && (
        <Section title="Worth knowing">
          <ul className="space-y-1">
            {detail.notes.map((note) => (
              <li key={note} className="flex gap-1.5 t-small text-[var(--ink-muted)]">
                <span style={{ color: "var(--warn)" }}>·</span>
                {note}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title={`Converts to · ${detail.convertsTo.length}`}>
        {direct.length > 0 && <Chips items={direct} />}
        {discovered.length > 0 && (
          <>
            <p className="mb-1 mt-2 t-micro text-[var(--ink-faint)]">
              also supported by the installed FFmpeg
            </p>
            <Chips items={discovered} />
          </>
        )}
        {indirect.length > 0 && (
          <>
            <p className="mb-1 mt-2 t-micro text-[var(--ink-faint)]">
              through an intermediate step
            </p>
            <Chips items={indirect} dashed />
          </>
        )}
      </Section>

      <Section title={`Converts from · ${detail.convertsFrom.length}`}>
        <Chips items={detail.convertsFrom} />
      </Section>

      <div className="px-3 pb-4">
        <a href="/" className="btn btn-primary">
          <Icons.arrow /> Convert something to {detail.label}
        </a>
      </div>
    </div>
  );
}

export default function FormatsPage() {
  const [query, setQuery] = useState("");
  const [all, setAll] = useState<FormatSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    api
      .formats(query)
      .then((data) => setAll(data.formats))
      .catch(() => undefined);
  }, [query]);

  const grouped = useMemo(() => {
    const groups = new Map<string, FormatSummary[]>();
    all.forEach((format) => {
      const bucket = groups.get(format.categoryLabel) ?? [];
      bucket.push(format);
      groups.set(format.categoryLabel, bucket);
    });
    return [...groups.entries()];
  }, [all]);

  return (
    <div className="workspace grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section className="pane">
        <header className="pane-head">
          <h2 className="t-label">Formats</h2>
          <span className="num t-micro text-[var(--ink-faint)]">{all.length}</span>
          <div className="ml-auto flex flex-1 items-center gap-1.5 pl-4">
            <Icons.search className="h-3 w-3 shrink-0 text-[var(--ink-faint)]" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by name, extension, or what you want to do…"
              aria-label="Search formats"
              className="w-full bg-transparent text-[12.5px] outline-none placeholder:text-[var(--ink-faint)]"
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                aria-label="Clear search"
                className="btn btn-quiet btn-sm px-1"
              >
                <Icons.close className="h-3 w-3" />
              </button>
            )}
          </div>
        </header>

        {!query && (
          <div className="rule flex flex-wrap items-center gap-1 px-3 py-1.5">
            <span className="t-micro text-[var(--ink-faint)]">try</span>
            {SUGGESTIONS.map((suggestion) => (
              <button
                key={suggestion}
                onClick={() => setQuery(suggestion)}
                className="btn btn-quiet btn-sm px-1.5"
              >
                {suggestion}
              </button>
            ))}
          </div>
        )}

        <div className="pane-body">
          {grouped.length === 0 ? (
            <Empty title="No formats match" detail="Try a different word, or clear the search." />
          ) : (
            grouped.map(([category, items]) => (
              <div key={category}>
                <p className="t-label sticky top-0 border-b border-[var(--line)] bg-[var(--raised)] px-3 py-1">
                  {category} · {items.length}
                </p>
                {items.map((format) => (
                  <button
                    key={format.id}
                    onClick={() => setSelected(format.id)}
                    aria-pressed={selected === format.id}
                    className="row row-hover w-full text-left"
                    style={
                      selected === format.id
                        ? { background: "var(--select-wash)", boxShadow: "inset 2px 0 0 var(--select)" }
                        : undefined
                    }
                  >
                    <Fmt>{format.id}</Fmt>
                    <span className="w-40 shrink-0 truncate t-small t-strong">
                      {format.label}
                    </span>
                    <span className="flex-1 truncate t-micro text-[var(--ink-faint)]">
                      {format.summary}
                    </span>
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </section>

      <section className="pane">
        <header className="pane-head">
          <h2 className="t-label">Detail</h2>
        </header>
        {selected ? (
          <Detail id={selected} onPick={setSelected} />
        ) : (
          <div className="pane-body">
            <Empty
              title="Pick a format"
              detail="See what it is for, what it converts from and to, and where it loses information."
            />
          </div>
        )}
      </section>
    </div>
  );
}
