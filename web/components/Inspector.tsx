"use client";

import { useEffect, useState } from "react";
import { api, engineUrl } from "@/lib/api";
import { isDesktop, reveal } from "@/lib/desktop";
import { bytes, duration } from "@/lib/format";
import type { EngineError, InputFile, Job, JobItem, OutputFile, PlanDetails } from "@/lib/types";
import { OutputPreview } from "./OutputPreview";
import {
  Bar,
  Button,
  Empty,
  ErrorBlock,
  Icons,
  Kbd,
  NoticeRow,
  Section,
  Spinner,
  Status,
  statusColour,
} from "./ui";

const RUNNING = new Set(["queued", "running"]);
const BROWSER_IMAGES = new Set(["png", "jpeg", "jpg", "webp", "avif", "gif", "bmp", "svg", "ico"]);

/** One compact row per batch item — a queue, not a stack of cards. */
function ResultRow({ item, inputFiles }: { item: JobItem; inputFiles: InputFile[] }) {
  const [expanded, setExpanded] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [preview, setPreview] = useState<OutputFile | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const sources = item.inputIds.map((id) => inputFiles.find((file) => file.id === id));
  const inputBytes = sources.length > 0 && sources.every(Boolean)
    ? sources.reduce((sum, file) => sum + file!.size, 0) : null;
  const outputBytes = item.outputs.reduce((sum, output) => sum + output.size, 0);
  const source = sources.length === 1 ? sources[0] : undefined;
  const output = item.outputs.length === 1 ? item.outputs[0] : undefined;
  const canCompare = source && output && BROWSER_IMAGES.has(source.formatId ?? "") &&
    BROWSER_IMAGES.has(output.formatId);
  const warnings = item.warnings.filter((notice) => notice.level === "warning");

  return (
    <li className="border-b border-[var(--line)]">
      <div className="flex items-center gap-2 px-3 py-1">
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: statusColour(item.status) }}
        />
        <span className="flex-1 truncate t-small" title={item.label}>
          {item.label}
        </span>
        {item.status === "running" ? (
          <span className="num t-micro" style={{ color: "var(--running)" }}>
            {Math.round(item.progress * 100)}%
          </span>
        ) : (
          item.durationMs !== null && (
            <span className="num t-micro text-[var(--ink-faint)]">
              {duration(item.durationMs)}
            </span>
          )
        )}
      </div>

      {item.status === "running" && <Bar value={item.progress} status="running" />}

      {item.outputs.length > 0 && inputBytes !== null && (
        <p className="num px-3 pb-1 t-small text-[var(--ink-muted)]">
          Input {bytes(inputBytes)} → Output {bytes(outputBytes)}
          {inputBytes > 0 && ` · ${Math.round(Math.abs(outputBytes - inputBytes) / inputBytes * 100)}% ${outputBytes <= inputBytes ? "smaller" : "larger"}`}
        </p>
      )}
      {item.outputs.length > 0 && (
        <ul id={`outputs-${item.id}`} className="pb-1">
          {(expanded ? item.outputs : item.outputs.slice(0, 3)).map((output) => (
            <li key={output.path} className="flex items-center gap-1.5 px-3 py-[1px] pl-[22px]">
              {isDesktop() ? (
                <button className="mono flex-1 truncate text-left hover:underline" title={output.name}
                  onClick={async () => {
                    if (output.previewable) setPreview(output);
                    else if (!(await reveal(output.path))) setFileError(`Could not show ${output.name}. Saved at ${output.path}`);
                  }}>{output.name}</button>
              ) : <a
                href={api.outputUrl(output.path, true)}
                className="mono flex-1 truncate hover:underline"
                title={output.name}
              >
                {output.name}
              </a>}
              <span className="num t-micro shrink-0 text-[var(--ink-faint)]">
                {bytes(output.size)}
              </span>
              {output.previewable && (
                <button
                  onClick={() => setPreview(output)}
                  aria-label={`Preview ${output.name}`}
                  className="shrink-0 text-[var(--ink-faint)] hover:text-[var(--ink)]"
                >
                  <Icons.eye className="h-3 w-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {fileError && <p role="alert" className="px-3 py-2 t-small text-[var(--fail)]">{fileError}</p>}
      {preview && <OutputPreview output={preview} onClose={() => setPreview(null)} />}
      {item.outputs.length > 3 && (
        <button className="btn btn-quiet btn-sm mx-3 mb-2" aria-expanded={expanded}
          aria-controls={`outputs-${item.id}`} onClick={() => setExpanded(!expanded)}>
          {expanded ? "Show fewer files" : `Show all ${item.outputs.length} files`}
        </button>
      )}
      {canCompare && (
        <div className="px-3 pb-2">
          <button className="btn btn-default btn-sm" aria-expanded={comparing}
            aria-controls={`comparison-${item.id}`} onClick={() => setComparing(!comparing)}>
            {comparing ? "Hide comparison" : "Compare images"}
          </button>
          {comparing && (
            <div id={`comparison-${item.id}`} className="mt-2">
              <div className="grid grid-cols-2 gap-2">
                {[
                  { label: "Original", name: source.name, url: engineUrl(`/api/files/${source.id}/raw`) },
                  { label: "Converted", name: output.name, url: api.outputUrl(output.path) },
                ].map((image) => (
                  <figure key={image.label}>
                    <figcaption className="mb-1 t-small t-strong">{image.label}</figcaption>
                    {/* Native images preserve the local engine URL in desktop builds. */}
                    <img src={image.url} alt={`${image.label}: ${image.name}`} className="comparison-image" />
                  </figure>
                ))}
              </div>
              <p className="mt-1 t-small text-[var(--ink-muted)]">Scaled previews. Open the output preview to inspect it at full size.</p>
            </div>
          )}
        </div>
      )}

      {(warnings.length > 0 || item.error) && (
        <div className="px-3 pb-1.5 pl-[22px]">
          {warnings.map((notice, index) => (
            <p key={index} className="t-micro">
              <span style={{ color: "var(--warn)" }}>{notice.title}.</span>{" "}
              <span className="text-[var(--ink-faint)]">{notice.detail}</span>
            </p>
          ))}
          {item.error && (
            <div className="mt-1">
              <ErrorBlock error={item.error} />
            </div>
          )}
        </div>
      )}
    </li>
  );
}

export function Inspector({
  canConvert,
  inputFiles,
  details,
  detailsLoading,
  target,
  fileCount,
  job,
  error,
  starting,
  onConvert,
  onCancel,
  onRetry,
  onReset,
  onDismissError,
}: {
  canConvert: boolean;
  inputFiles: InputFile[];
  details: PlanDetails | null;
  detailsLoading: boolean;
  target: string | null;
  fileCount: number;
  job: Job | null;
  error: EngineError | null;
  starting: boolean;
  onConvert: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onReset: () => void;
  onDismissError: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const running = job !== null && RUNNING.has(job.status);
  useEffect(() => {
    if (error || (job && !RUNNING.has(job.status))) setExpanded(true);
  }, [error, job?.status]);
  useEffect(() => {
    if (!detailsLoading && details && (
      details.warnings.length > 0 || details.compatibility.some((entry) => !entry.compatible)
    )) setExpanded(true);
  }, [details, detailsLoading]);
  const incompatible = (details?.compatibility ?? []).filter((entry) => !entry.compatible);
  const blocking = (details?.warnings ?? []).filter((notice) => notice.level === "error");

  return (
    <aside aria-label="Inspector" className={`pane inspector ${expanded ? "inspector-expanded" : ""}`}>
      <header className="pane-head">
        <h2 className="t-label">{job ? "Result" : "Plan"}</h2>
        <button className="btn btn-quiet btn-sm inspector-toggle" aria-expanded={expanded}
          aria-controls="conversion-inspector-body" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Hide details" : job ? "Show results" : "Review plan"}
        </button>
        {job && (
          <Status
            status={job.status}
            label={
              running
                ? job.cancelling
                  ? "Stopping"
                  : `${Math.round(job.progress * 100)}%`
                : undefined
            }
            className="ml-auto"
          />
        )}
      </header>

      <div id="conversion-inspector-body" className="pane-body inspector-body">
        {error && (
          <div className="rule">
            <ErrorBlock error={error} onDismiss={onDismissError} />
          </div>
        )}

        {!job && !target && (
          <Empty
            title={fileCount === 0 ? "Nothing to plan" : "No output chosen"}
            detail={
              fileCount === 0
                ? "Add files, then pick what they should become."
                : "Pick an output format to see exactly what will happen before anything runs."
            }
          />
        )}

        {!job && target && (
          <>
            {detailsLoading || !details ? (
              <div className="px-3 py-3">
                <div className="h-3 w-2/3 animate-pulse bg-[var(--raised)]" />
                <div className="mt-1.5 h-3 w-full animate-pulse bg-[var(--raised)]" />
              </div>
            ) : (
              <>
                <Section title="What will happen">
                  {details.routes.length > 1 ? (
                    <ul className="space-y-1.5">
                      {details.routes.map((entry) => (
                        <li key={entry.sourceId}>
                          <p className="t-small t-strong">{entry.description}</p>
                          <p className="num t-micro text-[var(--ink-faint)]">
                            {entry.fileCount} {entry.sourceLabel} file
                            {entry.fileCount === 1 ? "" : "s"}
                            {entry.direct ? "" : " · more than one step"}
                          </p>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="t-small t-strong">
                      {details.routes[0]?.description ?? details.route.description}
                    </p>
                  )}
                  <p className="mt-1.5 t-micro text-[var(--ink-muted)]">
                    You will get {details.outputEstimate.description}.
                  </p>
                </Section>

                {details.plan.length > 1 && (
                  <Section title="Steps">
                    <ol className="space-y-0.5">
                      {details.plan.map((step) => (
                        <li key={step.step} className="flex gap-1.5 t-micro">
                          <span className="num w-3 shrink-0 text-right text-[var(--ink-faint)]">
                            {step.step}
                          </span>
                          <span className="flex-1 text-[var(--ink-muted)]">
                            {step.converter}
                          </span>
                          <span className="shrink-0 text-[var(--ink-faint)]">
                            {step.engine}
                          </span>
                        </li>
                      ))}
                    </ol>
                  </Section>
                )}

                {incompatible.length > 0 && (
                  <Section title={`Skipping ${incompatible.length}`}>
                    <ul className="space-y-0.5">
                      {incompatible.slice(0, 5).map((entry) => (
                        <li key={entry.id} className="t-micro">
                          <span className="t-strong">{entry.name}</span>
                          <span className="text-[var(--ink-faint)]"> — {entry.reason}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="mt-1 t-micro text-[var(--ink-faint)]">
                      The rest still convert.
                    </p>
                  </Section>
                )}

                {details.warnings.length > 0 && (
                  <Section title="Before you convert">
                    <div className="-mx-3 divide-y divide-[var(--line)] border-y border-[var(--line)]">
                      {details.warnings.map((notice, index) => (
                        <NoticeRow key={index} notice={notice} />
                      ))}
                    </div>
                  </Section>
                )}
              </>
            )}
          </>
        )}

        {job && (
          <>
            <Section title="Summary">
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                {[
                  ["converted", job.summary.completed, "var(--ok)"],
                  ["with warnings", job.summary.warnings, "var(--warn)"],
                  ["failed", job.summary.failed, "var(--fail)"],
                  ["skipped", job.summary.skipped, "var(--idle)"],
                ]
                  .filter(([, value]) => Number(value) > 0)
                  .map(([label, value, colour]) => (
                    <div key={String(label)} className="flex items-baseline justify-between gap-2">
                      <span className="t-micro text-[var(--ink-faint)]">{String(label)}</span>
                      <span className="num t-small t-strong" style={{ color: String(colour) }}>
                        {String(value)}
                      </span>
                    </div>
                  ))}
              </div>
              {job.summary.outputs > 0 && (
                <p className="num mt-1.5 t-micro text-[var(--ink-faint)]">
                  {job.summary.outputs} file{job.summary.outputs === 1 ? "" : "s"} ·{" "}
                  {bytes(job.summary.outputBytes)}
                </p>
              )}
              {isDesktop() ? (
                <button
                  onClick={() => reveal(job.outputDir)}
                  className="mono mt-1 block w-full truncate text-left text-[var(--ink-faint)] underline-offset-2 hover:text-[var(--ink)] hover:underline"
                  title="Show in Finder"
                >
                  {job.outputDir}
                </button>
              ) : (
                <p className="mono mt-1 truncate text-[var(--ink-faint)]" title={job.outputDir}>
                  {job.outputDir}
                </p>
              )}
            </Section>

            <div className="rule">
              <p className="t-label px-3 py-1.5">
                {job.mode === "combined" ? "Output" : `Files · ${job.items.length}`}
              </p>
              <ul>
                {job.items.map((item) => (
                  <ResultRow key={item.id} item={item} inputFiles={inputFiles} />
                ))}
              </ul>
            </div>
          </>
        )}
      </div>

      {/* The action bar is pinned: the primary action never scrolls away. */}
      <footer className="flex-none border-t border-[var(--line)] p-2">
        {!job && target && !detailsLoading && details && (
          <p className="mb-2 t-small text-[var(--ink-muted)]">
            {details.outputEstimate.description}
            {details.warnings.some((notice) => notice.level === "warning") && " · Review conversion warnings above"}
          </p>
        )}
        {running ? (
          <>
            <Bar value={job!.progress} status="running" />
            <div className="mt-2 flex items-center gap-2">
              <span className="flex-1 truncate t-micro text-[var(--ink-muted)]">
                {job!.cancelling
                  ? "stopping after the current file"
                  : `${job!.summary.completed + job!.summary.warnings} of ${job!.items.length} done`}
              </span>
              <Button size="sm" onClick={onCancel} disabled={job!.cancelling}>
                <Icons.stop /> Cancel
              </Button>
            </div>
          </>
        ) : job ? (
          <div className="flex gap-1.5">
            {job.summary.outputs > 0 &&
              (isDesktop() ? (
                <button
                  onClick={() => reveal(job.outputDir)}
                  className="btn btn-primary flex-1"
                >
                  <Icons.folder />
                  {job.summary.outputs > 1
                    ? `Show ${job.summary.outputs} files`
                    : "Show file"}
                </button>
              ) : (
                <a href={api.downloadAllUrl(job.id)} className="btn btn-primary flex-1">
                  <Icons.down />
                  {job.summary.outputs > 1 ? `Download ${job.summary.outputs}` : "Download"}
                </a>
              ))}
            {(job.summary.failed > 0 || job.summary.cancelled > 0) && (
              <Button onClick={onRetry}>
                <Icons.refresh /> Retry
              </Button>
            )}
            <Button variant="quiet" onClick={onReset}>
              Adjust
            </Button>
          </div>
        ) : (
          <>
            <Button
              variant="primary"
              className="w-full"
              disabled={!target || !canConvert}
              onClick={onConvert}
            >
              {starting ? <Spinner /> : <Icons.arrow />}
              {starting ? "Starting" : "Convert"}
              {target && canConvert && <Kbd>⌘↵</Kbd>}
            </Button>
            {!target && <p className="mt-1 text-center t-small text-[var(--ink-muted)]">Choose an output format to continue.</p>}
            {target && detailsLoading && <p role="status" className="mt-1 text-center t-small text-[var(--ink-muted)]">Checking conversion plan…</p>}
            {blocking.length > 0 && (
              <p className="mt-1 text-center t-micro" style={{ color: "var(--fail)" }}>
                {blocking[0].title}
              </p>
            )}
          </>
        )}
      </footer>
    </aside>
  );
}
