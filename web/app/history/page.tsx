"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { bytes, exactTime, when } from "@/lib/format";
import type { EngineError, HistoryEntry } from "@/lib/types";
import {
  Button,
  Empty,
  ErrorBlock,
  Fmt,
  Icons,
  Select,
  Spinner,
  Status,
} from "@/components/ui";

function Row({
  entry,
  busy,
  onRerun,
  onDelete,
}: {
  entry: HistoryEntry;
  busy: boolean;
  onRerun: (entry: HistoryEntry) => void;
  onDelete: (entry: HistoryEntry, purge: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const inputs = entry.inputs.map((item) => item.label);
  const settings = Object.entries(entry.options).filter(
    ([, value]) => value !== "" && value !== null,
  );

  return (
    <li className="border-b border-[var(--line)]">
      <div className="row row-hover" style={{ minHeight: 34 }}>
        <Status status={entry.status} />
        <span
          className="w-24 shrink-0 truncate t-micro text-[var(--ink-faint)]"
          title={exactTime(entry.createdAt)}
        >
          {when(entry.createdAt)}
        </span>
        <span className="flex-1 truncate t-small" title={inputs.join(", ")}>
          {inputs.slice(0, 2).join(", ")}
          {inputs.length > 2 && (
            <span className="text-[var(--ink-faint)]"> +{inputs.length - 2}</span>
          )}
        </span>
        <Icons.arrow className="h-3 w-3 shrink-0 text-[var(--ink-faint)]" />
        <Fmt>{entry.target}</Fmt>
        {entry.mode !== "each" && (
          <span className="t-micro text-[var(--ink-faint)]">{entry.mode}</span>
        )}
        {entry.summary.outputs > 0 && (
          <span className="num w-16 shrink-0 text-right t-micro text-[var(--ink-faint)]">
            {bytes(entry.summary.outputBytes)}
          </span>
        )}
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            onClick={() => onRerun(entry)}
            disabled={busy}
            className="btn btn-quiet btn-sm px-1.5"
            title="Run again with the same settings"
          >
            {busy ? <Spinner /> : <Icons.refresh />}
          </button>
          <button
            onClick={() => setOpen(!open)}
            className="btn btn-quiet btn-sm px-1.5"
            title="Details"
          >
            <Icons.chevron className={`h-3 w-3 ${open ? "rotate-90" : ""}`} />
          </button>
          <button
            onClick={() => onDelete(entry, false)}
            className="btn btn-quiet btn-sm px-1.5"
            title="Forget this entry"
          >
            <Icons.trash />
          </button>
        </div>
      </div>

      {open && (
        <div className="bg-[var(--sunken)] px-3 py-2">
          <p className="t-micro text-[var(--ink-muted)]">{entry.route.description}</p>
          {entry.summary.outputs > 0 && (
            <p className="num t-micro text-[var(--ink-faint)]">
              {entry.summary.outputs} output{entry.summary.outputs === 1 ? "" : "s"}
              {!entry.outputsExist && " · files since cleared"}
            </p>
          )}
          {settings.length > 0 && (
            <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 lg:grid-cols-3">
              {settings.map(([key, value]) => (
                <div key={key} className="flex justify-between gap-2">
                  <dt className="t-micro text-[var(--ink-faint)]">{key}</dt>
                  <dd className="mono truncate">{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          {entry.outputsExist && (
            <button
              onClick={() => onDelete(entry, true)}
              className="btn btn-danger btn-sm mt-2 px-1.5"
            >
              Delete entry and its output files
            </button>
          )}
        </div>
      )}
    </li>
  );
}

export default function HistoryPage() {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<EngineError | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.history({ q: query, status });
      setEntries(data.history);
      setEnabled(data.enabled);
    } catch (thrown) {
      if (thrown instanceof ApiError) setError(thrown.info);
    } finally {
      setLoading(false);
    }
  }, [query, status]);

  useEffect(() => {
    const timer = setTimeout(load, 200);
    return () => clearTimeout(timer);
  }, [load]);

  const rerun = async (entry: HistoryEntry) => {
    setBusyId(entry.id);
    setError(null);
    try {
      await api.rerun(entry.id);
      window.location.href = "/";
    } catch (thrown) {
      if (thrown instanceof ApiError) setError(thrown.info);
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (entry: HistoryEntry, purge: boolean) => {
    await api.deleteHistory(entry.id, purge).catch(() => undefined);
    load();
  };

  return (
    <section className="pane m-[var(--gap)] h-[calc(100%-var(--gap)*2)]">
      <header className="pane-head">
        <h2 className="t-label">History</h2>
        <span className="num t-micro text-[var(--ink-faint)]">{entries.length}</span>
        <div className="ml-2 flex flex-1 items-center gap-1.5">
          <Icons.search className="h-3 w-3 shrink-0 text-[var(--ink-faint)]" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by file name or format…"
            aria-label="Search history"
            className="w-full max-w-[280px] bg-transparent text-[12.5px] outline-none placeholder:text-[var(--ink-faint)]"
          />
        </div>
        <div className="w-36">
          <Select
            value={status}
            onChange={setStatus}
            className="!h-6 text-[11.5px]"
            options={[
              { value: "", label: "Any status" },
              { value: "completed", label: "Completed" },
              { value: "completed_with_warnings", label: "With warnings" },
              { value: "failed", label: "Failed" },
              { value: "cancelled", label: "Cancelled" },
            ]}
          />
        </div>
        {entries.length > 0 && (
          <Button
            size="sm"
            variant="quiet"
            onClick={async () => {
              await api.clearHistory(false).catch(() => undefined);
              load();
            }}
          >
            <Icons.trash /> Clear
          </Button>
        )}
      </header>

      {error && (
        <div className="rule">
          <ErrorBlock error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {!enabled && (
        <p className="rule px-3 py-1.5 t-micro text-[var(--ink-muted)]">
          History recording is switched off in Privacy, so nothing new is being saved.
        </p>
      )}

      <div className="pane-body">
        {loading && entries.length === 0 ? (
          <div className="flex h-24 items-center justify-center text-[var(--ink-faint)]">
            <Spinner />
          </div>
        ) : entries.length === 0 ? (
          <Empty
            title={query || status ? "Nothing matches" : "No conversions yet"}
            detail={
              query || status
                ? "Try a different search or clear the filter."
                : "Once you convert something it appears here, and can be run again with the same settings."
            }
          />
        ) : (
          <ul>
            {entries.map((entry) => (
              <Row
                key={entry.id}
                entry={entry}
                busy={busyId === entry.id}
                onRerun={rerun}
                onDelete={remove}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
