"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import type { Settings, StorageStats, SystemInfo } from "@/lib/types";
import { Button, Check, Field, Icons, Section, Spinner, TextInput } from "@/components/ui";
import { Packs } from "@/components/Packs";

function StorageRow({
  title,
  detail,
  stats,
  action,
  onClear,
  danger,
}: {
  title: string;
  detail: string;
  stats: { path: string; bytes: number; files?: number };
  action?: string;
  onClear?: () => void;
  danger?: boolean;
}) {
  return (
    <div className="flex items-start gap-3 border-b border-[var(--line)] px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="t-small t-strong">{title}</p>
        <p className="t-micro text-[var(--ink-faint)]">{detail}</p>
        <p className="mono mt-0.5 truncate text-[var(--ink-faint)]">{stats.path}</p>
      </div>
      <div className="shrink-0 text-right">
        <p className="num t-small t-strong">{bytes(stats.bytes)}</p>
        {stats.files !== undefined && (
          <p className="num t-micro text-[var(--ink-faint)]">
            {stats.files} file{stats.files === 1 ? "" : "s"}
          </p>
        )}
      </div>
      {onClear && (
        <Button
          size="sm"
          variant={danger ? "danger" : "default"}
          disabled={stats.bytes === 0}
          onClick={onClear}
        >
          {action ?? "Clear"}
        </Button>
      )}
    </div>
  );
}

export default function PrivacyPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [storage, setStorage] = useState<StorageStats | null>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  const [working, setWorking] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [s, st, sy] = await Promise.allSettled([
      api.settings(),
      api.storage(),
      api.system(),
    ]);
    if (s.status === "fulfilled") setSettings(s.value.settings);
    if (st.status === "fulfilled") setStorage(st.value);
    if (sy.status === "fulfilled") setSystem(sy.value);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const update = async (values: Partial<Settings>) => {
    const result = await api.updateSettings(values).catch(() => null);
    if (result) setSettings(result.settings);
  };

  const clean = async (scopes: string[], label: string) => {
    setWorking(true);
    try {
      const result = await api.cleanStorage(scopes);
      setStorage(result.storage);
      const freed = Object.values(result.removed).reduce((sum, value) => sum + value, 0);
      setFlash(`${label} cleared — ${bytes(freed)} freed.`);
      setTimeout(() => setFlash(null), 5000);
    } finally {
      setWorking(false);
    }
  };

  const ai = system?.intelligentWorkflows.available;

  return (
    <section className="pane m-[var(--gap)] h-[calc(100%-var(--gap)*2)]">
      <header className="pane-head">
        <h2 className="t-label">Privacy and storage</h2>
        {working && (
          <span className="ml-auto flex items-center gap-1.5 t-micro text-[var(--ink-muted)]">
            <Spinner /> clearing
          </span>
        )}
        {flash && !working && (
          <span className="ml-auto t-micro" style={{ color: "var(--ok)" }}>
            {flash}
          </span>
        )}
      </header>

      <div className="pane-body mx-auto w-full max-w-[820px]">
        <Section title="Where your files go">
          <ul className="space-y-1">
            {[
              "Files you add are written to the workspace volume and read from there.",
              "Converted results are written next to them and never leave this machine.",
              "Temporary working files are removed when a conversion finishes.",
            ].map((line) => (
              <li key={line} className="flex gap-1.5 t-small text-[var(--ink-muted)]">
                <Icons.check className="mt-[3px] h-3 w-3 shrink-0" style={{ color: "var(--ok)" }} />
                {line}
              </li>
            ))}
            <li className="flex gap-1.5 t-small">
              <Icons.warn
                className="mt-[3px] h-3 w-3 shrink-0"
                style={{ color: ai ? "var(--warn)" : "var(--ok)" }}
              />
              <span style={{ color: ai ? "var(--warn)" : "var(--ink-muted)" }}>
                {ai ? (
                  <>
                    Intelligent workflows are on. Those conversions — and only those —
                    send file contents to Anthropic&rsquo;s API to be read.
                  </>
                ) : (
                  "Intelligent workflows are off, so no conversion contacts an outside service at all."
                )}
              </span>
            </li>
          </ul>
        </Section>

        <div className="rule">
          <p className="t-label px-3 py-1.5">What is stored right now</p>
          {storage ? (
            <>
              <StorageRow
                title="Added files"
                detail="The inputs currently in your queue."
                stats={storage.inbox}
                action="Remove all"
                onClear={() => clean(["inbox"], "Added files")}
              />
              <StorageRow
                title="Converted results"
                detail="Everything produced by past conversions."
                stats={storage.output}
                action="Delete all"
                danger
                onClear={() => clean(["output"], "Converted results")}
              />
              <StorageRow
                title="Temporary files"
                detail="Intermediate files from conversions in progress or interrupted runs."
                stats={storage.temp}
                onClear={() => clean(["temp"], "Temporary files")}
              />
              <StorageRow
                title="Local database"
                detail="History, saved presets and settings. No file contents are stored here."
                stats={{ path: storage.database.path, bytes: storage.database.bytes }}
              />
            </>
          ) : (
            <div className="flex h-20 items-center justify-center text-[var(--ink-faint)]">
              <Spinner />
            </div>
          )}
        </div>

        <Section title="Controls">
          {settings ? (
            <div className="space-y-2.5">
              <Check
                checked={settings.keepHistory}
                onChange={(value) => update({ keepHistory: value })}
                label="Keep a conversion history"
                help="Records what was converted, when, and with which settings, so it can be repeated. File contents are never recorded."
              />
              <Check
                checked={settings.autoCleanTemp}
                onChange={(value) => update({ autoCleanTemp: value })}
                label="Remove temporary files automatically"
                help="Deletes each conversion's working folder as soon as it finishes. Turn off only when debugging a failure."
              />
              {/* These four used to be environment variables, which meant a
                  .env edit and an engine restart — not something a desktop user
                  can do at all. Every one takes effect on the next conversion. */}
              <div className="grid gap-3 border-t border-[var(--line)] pt-3 sm:grid-cols-2">
                <Field
                  label="Largest file accepted"
                  help="Uploads above this are refused with a reason rather than failing part-way."
                >
                  {(id) => (
                  <div className="flex items-center gap-2">
                    <TextInput
                      id={id}
                      type="number"
                      min={1}
                      value={settings.maxUploadMb}
                      onChange={(value) =>
                        update({ maxUploadMb: Math.max(1, Number(value) || 1) })
                      }
                    />
                    <span className="shrink-0 t-small text-[var(--ink-faint)]">MB</span>
                  </div>
                  )}
                </Field>
                <Field
                  label="Files converted at once"
                  help="Higher finishes a batch sooner and uses more memory and CPU."
                >
                  {(id) => (
                  <TextInput
                    id={id}
                    type="number"
                    min={1}
                    max={16}
                    value={settings.workerConcurrency}
                    onChange={(value) =>
                      update({
                        workerConcurrency: Math.min(16, Math.max(1, Number(value) || 1)),
                      })
                    }
                  />
                  )}
                </Field>
                <Field
                  label="Anthropic API key"
                  help="Only needed for the optional intelligent conversions. Stored in this machine's workspace database and sent to no one but Anthropic, when you run one of those conversions."
                >
                  {(id) => (
                  <TextInput
                    id={id}
                    type="password"
                    value={settings.anthropicApiKey}
                    placeholder="sk-ant-…"
                    onChange={(value) => update({ anthropicApiKey: value })}
                  />
                  )}
                </Field>
                <Field label="Model" help="Used for intelligent conversions.">
                  {(id) => (
                  <TextInput
                    id={id}
                    value={settings.aiModel}
                    onChange={(value) => update({ aiModel: value })}
                  />
                  )}
                </Field>
              </div>

              <div className="flex flex-wrap gap-1.5 pt-1">
                <Button
                  size="sm"
                  variant="danger"
                  onClick={async () => {
                    await api.clearHistory(false).catch(() => undefined);
                    setFlash("Conversion history cleared.");
                    setTimeout(() => setFlash(null), 5000);
                    refresh();
                  }}
                >
                  <Icons.trash /> Clear conversion history
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => clean(["inbox", "output", "temp"], "Everything")}
                >
                  Delete every file in the workspace
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex h-20 items-center justify-center text-[var(--ink-faint)]">
              <Spinner />
            </div>
          )}
        </Section>

        {system?.packs && system.packs.packs.length > 0 && (
          <Packs packs={system.packs.packs} onChanged={refresh} />
        )}

        {system && (
          <div className="rule">
            <p className="t-label px-3 py-1.5">Installed engines</p>
            {system.engines.map((engine) => (
              <div key={engine.id} className="row">
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: engine.available ? "var(--ok)" : "var(--warn)" }}
                />
                <span className="w-28 shrink-0 t-small t-strong">{engine.label}</span>
                <span className="flex-1 truncate t-micro text-[var(--ink-faint)]">
                  {engine.purpose}
                </span>
                <span className="t-micro text-[var(--ink-faint)]">
                  {engine.available ? "ready" : "not installed"}
                </span>
              </div>
            ))}
            {system.ffmpeg?.available && (
              <p className="px-3 py-1.5 t-micro text-[var(--ink-faint)]">
                FFmpeg capability is discovered at startup, not hardcoded:{" "}
                <span className="num">{system.ffmpeg.readableFormats}</span> formats
                readable, <span className="num">{system.ffmpeg.writableFormats}</span>{" "}
                writable, <span className="num">{system.ffmpeg.encoders}</span> encoders,{" "}
                <span className="num">{system.ffmpeg.filters}</span> filters.
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
