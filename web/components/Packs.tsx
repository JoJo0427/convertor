"use client";

/* Engine packs: the capabilities that are not in the base install.
 *
 * The rule this panel exists to honour is that a route needing a pack stays
 * **visible and marked**, never hidden. Hiding it teaches the user the app
 * cannot do the thing; marking it teaches them it can, and what it costs. So
 * every pack is listed here whatever its state, and the only thing that changes
 * is the control on the right.
 *
 * Nothing on this page downloads anything until a button is pressed. There is no
 * "recommended", no pre-selection, and no nagging — for a tool whose whole claim
 * is that files never leave the machine, reaching for the network unasked would
 * be the one unforgivable behaviour.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, watchPack } from "@/lib/api";
import type { EngineError, Pack, PackJob } from "@/lib/types";
import { Bar, Button, ErrorBlock, Icons, Spinner } from "./ui";

function megabytes(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(0)} MB`;
}

/** "142 MB of 298 MB" while downloading; the phase alone otherwise. */
function progressLabel(job: PackJob): string {
  if (job.status !== "running") return job.phase;
  if (job.total > 0 && job.downloaded > 0) {
    return `${job.phase} · ${megabytes(job.downloaded)} of ${megabytes(job.total)}`;
  }
  return job.phase;
}

export function Packs({
  packs,
  onChanged,
}: {
  packs: Pack[];
  /** Called when a pack finishes installing or is removed, so the caller can
   *  refresh the routes and engine list that just changed underneath it. */
  onChanged: () => void;
}) {
  const [jobs, setJobs] = useState<Record<string, PackJob>>({});
  const [errors, setErrors] = useState<Record<string, EngineError>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const watchers = useRef<Record<string, () => void>>({});

  const follow = useCallback(
    (id: string) => {
      watchers.current[id]?.();
      watchers.current[id] = watchPack(
        id,
        (job) => setJobs((prev) => ({ ...prev, [id]: job })),
        (job) => {
          setJobs((prev) => ({ ...prev, [id]: job }));
          if (job.status === "failed" && job.error) {
            setErrors((prev) => ({ ...prev, [id]: job.error as EngineError }));
          }
          // A finished install changes what the app can convert, so the caller
          // has to hear about it — not only this panel.
          onChanged();
        },
      );
    },
    [onChanged],
  );

  // Reattach to anything already running: a reload mid-download must not look
  // like the download stopped.
  useEffect(() => {
    packs.forEach((pack) => {
      if (pack.job?.status === "running" && !watchers.current[pack.id]) {
        setJobs((prev) => ({ ...prev, [pack.id]: pack.job as PackJob }));
        follow(pack.id);
      }
    });
  }, [packs, follow]);

  useEffect(
    () => () => {
      Object.values(watchers.current).forEach((stop) => stop());
    },
    [],
  );

  async function install(pack: Pack) {
    setErrors(({ [pack.id]: _drop, ...rest }) => rest);
    setBusy((prev) => ({ ...prev, [pack.id]: true }));
    try {
      const { job } = await api.installPack(pack.id);
      setJobs((prev) => ({ ...prev, [pack.id]: job }));
      follow(pack.id);
    } catch (error) {
      if (error instanceof ApiError) {
        setErrors((prev) => ({ ...prev, [pack.id]: error.info }));
      }
    } finally {
      setBusy((prev) => ({ ...prev, [pack.id]: false }));
    }
  }

  async function cancel(pack: Pack) {
    try {
      await api.cancelPack(pack.id);
    } catch {
      /* the stream reports the outcome either way */
    }
  }

  async function remove(pack: Pack) {
    setBusy((prev) => ({ ...prev, [pack.id]: true }));
    try {
      await api.removePack(pack.id);
      setJobs(({ [pack.id]: _drop, ...rest }) => rest);
      onChanged();
    } catch (error) {
      if (error instanceof ApiError) {
        setErrors((prev) => ({ ...prev, [pack.id]: error.info }));
      }
    } finally {
      setBusy((prev) => ({ ...prev, [pack.id]: false }));
    }
  }

  const installed = packs.filter((p) => p.installed).length;

  return (
    <div className="rule">
      <p className="t-label px-3 py-1.5">
        Engine packs · {installed} of {packs.length} installed
      </p>

      {packs.map((pack) => {
        const job = jobs[pack.id] ?? pack.job;
        const running = job?.status === "running";
        const error = errors[pack.id];

        return (
          <div key={pack.id} className="border-b border-[var(--line)]">
            <div className="flex items-start gap-3 px-3 py-2">
              <span
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                style={{
                  background: pack.installed ? "var(--ok)" : "var(--ink-faint)",
                }}
                aria-hidden
              />

              <span className="min-w-0 flex-1">
                <span className="block t-small t-strong">{pack.label}</span>
                <span className="block t-micro text-[var(--ink-faint)]">
                  {pack.summary}
                </span>
                <span className="mt-0.5 block t-micro text-[var(--ink-faint)]">
                  Unlocks {pack.unlocks.join(", ")}
                </span>

                {/* Why a pack cannot be fetched, and the manual route instead of
                    a button that would fail. */}
                {!pack.installed && !pack.installable && (
                  <span className="mt-1 block t-micro text-[var(--ink-faint)]">
                    {pack.unavailableReason}
                    {pack.manualHint && (
                      <>
                        {" "}
                        <span className="t-strong">{pack.manualHint}</span>
                      </>
                    )}
                  </span>
                )}
              </span>

              <span className="shrink-0 text-right">
                {!pack.installed && (
                  <>
                    <span className="num block t-small">{pack.megabytes} MB</span>
                    {/* A 128 MB download that lands as 413 MB of files is a
                        surprise worth spending one line to avoid. */}
                    {pack.installedMegabytes > pack.megabytes * 1.2 && (
                      <span className="num block t-micro text-[var(--ink-faint)]">
                        {pack.installedMegabytes} MB on disk
                      </span>
                    )}
                  </>
                )}
                {pack.installed && pack.diskMegabytes > 0 && (
                  <span className="num block t-small">{pack.diskMegabytes} MB</span>
                )}

                {pack.installed ? (
                  <span className="mt-0.5 flex items-center justify-end gap-2">
                    <span className="t-micro text-[var(--ok)]">installed</span>
                    {/* Removable only when Convertor put it there. A pack that
                        is satisfied by something already on the machine is not
                        ours to delete, and a Remove button that refuses is worse
                        than no button. */}
                    {pack.diskMegabytes > 0 ? (
                      <Button
                        variant="quiet"
                        size="sm"
                        disabled={busy[pack.id]}
                        onClick={() => remove(pack)}
                        title={`Remove ${pack.label} and free ${pack.diskMegabytes} MB`}
                      >
                        Remove
                      </Button>
                    ) : (
                      <span className="t-micro text-[var(--ink-faint)]">
                        already on this machine
                      </span>
                    )}
                  </span>
                ) : running ? (
                  <Button
                    variant="quiet"
                    size="sm"
                    className="mt-0.5"
                    onClick={() => cancel(pack)}
                  >
                    Cancel
                  </Button>
                ) : pack.installable ? (
                  <Button
                    variant="default"
                    size="sm"
                    className="mt-0.5"
                    disabled={busy[pack.id]}
                    onClick={() => install(pack)}
                  >
                    {busy[pack.id] ? <Spinner /> : "Install"}
                  </Button>
                ) : (
                  <span className="mt-0.5 block t-micro text-[var(--ink-faint)]">
                    unavailable
                  </span>
                )}
              </span>
            </div>

            {running && job && (
              <div className="px-3 pb-2">
                <Bar
                  value={job.progress ?? 0}
                  indeterminate={job.progress === null}
                />
                <p className="mt-1 flex items-baseline justify-between t-micro text-[var(--ink-faint)]">
                  <span>{progressLabel(job)}</span>
                  <span className="num">{job.elapsed}s</span>
                </p>
              </div>
            )}

            {job?.status === "cancelled" && (
              <p className="px-3 pb-2 t-micro text-[var(--ink-faint)]">
                Cancelled. Nothing was installed.
              </p>
            )}

            {error && (
              <div className="px-3 pb-2">
                {/* The Install button is already the retry: after a failure
                    the pack is untouched, so pressing it again is the whole
                    recovery. Dismissing just clears the message. */}
                <ErrorBlock
                  error={error}
                  onDismiss={() =>
                    setErrors(({ [pack.id]: _drop, ...rest }) => rest)
                  }
                />
              </div>
            )}
          </div>
        );
      })}

      <p className="flex items-start gap-2 px-3 py-1.5 t-micro text-[var(--ink-faint)]">
        <Icons.shield className="mt-[2px] h-3 w-3 shrink-0" />
        <span>
          Packs are downloaded from the projects that make them — The Document
          Foundation, pandoc, 7-zip.org, PyPI — only when you press Install, and
          are checked against a known checksum before anything is unpacked. A
          cancelled or failed download leaves nothing behind. Removing a pack
          deletes its files and frees the space.
        </span>
      </p>
    </div>
  );
}
