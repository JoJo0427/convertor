"use client";

/* The downloader.
 *
 * A deliberately different screen from the converter, because it is a different
 * tool: it takes a link rather than a file, and it is the one place in this app
 * that reaches out to the network. Sharing the converter's layout would imply
 * they are the same kind of operation, and quietly undercut the promise the rest
 * of the app makes.
 *
 * So the shape is the opposite of the converter's dense three-pane workspace: one
 * column, centred, with the address field as the largest thing on the screen and
 * everything else below it in the order it is decided — what to fetch, how, and
 * where it lands. There is nothing to arrange and nothing to queue.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, watchDownload } from "@/lib/api";
import { isDesktop, reveal } from "@/lib/desktop";
import type {
  Download,
  DownloadOverview,
  DownloadProbe,
  DownloadQuality,
  EngineError,
} from "@/lib/types";
import {
  Bar,
  Button,
  Check,
  ErrorBlock,
  Icons,
  Spinner,
  TextInput,
} from "@/components/ui";

/* The four choices, defined here rather than fetched.
 *
 * They were coming from the engine, which meant the entire "What to save" section
 * was empty until a network round trip resolved — and stayed empty forever if it
 * failed. A heading with nothing under it, no explanation. These are four fixed
 * options that never change at runtime; the engine has no say in them, so asking
 * it was pure risk for no benefit.
 *
 * The engine is still asked for the things only it knows: where files go by
 * default, whether yt-dlp loaded, and whether FFmpeg is there to merge streams.
 */
const QUALITIES: DownloadQuality[] = [
  {
    id: "best",
    label: "Best available",
    detail: "Highest quality video and audio, merged into one file.",
  },
  {
    id: "1080p",
    label: "Up to 1080p",
    detail: "Full HD. Smaller than the best available, still sharp.",
  },
  {
    id: "720p",
    label: "Up to 720p",
    detail: "HD. A good balance of size and quality.",
  },
  {
    id: "audio",
    label: "Audio only",
    detail: "Extracts the sound as an MP3. No video.",
  },
];

function seconds(total: number | null): string {
  if (!total) return "";
  const m = Math.floor(total / 60);
  const s = Math.round(total % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function megabytes(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

export default function DownloadPage() {
  const [overview, setOverview] = useState<DownloadOverview | null>(null);
  const [url, setUrl] = useState("");
  const [quality, setQuality] = useState("best");
  const [directory, setDirectory] = useState("");
  const [subtitles, setSubtitles] = useState(false);

  const [probing, setProbing] = useState(false);
  const [found, setFound] = useState<DownloadProbe | null>(null);
  const [error, setError] = useState<EngineError | null>(null);
  const [active, setActive] = useState<Download | null>(null);
  const stop = useRef<(() => void) | null>(null);

  const refresh = useCallback(async () => {
    const data = await api.downloads().catch(() => null);
    if (!data) return;
    setOverview(data);
    setDirectory((current) => current || data.defaultDirectory);
    const running = data.downloads.find((d) => d.status === "running");
    if (running && !stop.current) {
      setActive(running);
      stop.current = watchDownload(running.id, setActive, (done) => {
        setActive(done);
        stop.current = null;
        refresh();
      });
    }
  }, []);

  useEffect(() => {
    refresh();
    return () => stop.current?.();
  }, [refresh]);

  // The address is checked only when asked. Probing is itself a network request,
  // so doing it as the user types would mean the app contacted a site because
  // something was pasted, which is not the same as being asked to.
  async function check() {
    if (!url.trim()) return;
    setProbing(true);
    setError(null);
    setFound(null);
    try {
      setFound(await api.probeDownload(url.trim()));
    } catch (e) {
      if (e instanceof ApiError) setError(e.info);
    } finally {
      setProbing(false);
    }
  }

  async function begin() {
    setError(null);
    try {
      const { download } = await api.startDownload({
        url: url.trim(),
        quality,
        directory,
        subtitles,
      });
      setActive(download);
      stop.current?.();
      stop.current = watchDownload(download.id, setActive, (done) => {
        setActive(done);
        stop.current = null;
        refresh();
      });
    } catch (e) {
      if (e instanceof ApiError) setError(e.info);
    }
  }

  async function cancel() {
    if (active) await api.cancelDownload(active.id).catch(() => null);
  }

  const running = active?.status === "running";
  const unavailable = overview && !overview.available;

  return (
    <main id="main" className="mx-auto w-full max-w-[640px] px-4 py-10">
      <header className="mb-7">
        <h1 className="t-title">Download</h1>
        <p className="mt-1 t-small text-[var(--ink-muted)]">
          Save a video or its audio from a link. This is the only part of
          Convertor that uses the internet — everything else stays on this
          machine.
        </p>
      </header>

      {unavailable && (
        <div className="mb-6">
          <ErrorBlock
            error={{
              code: "downloader_missing",
              title: "The downloader is not available",
              detail:
                "yt-dlp ships with Convertor but could not be loaded in this build.",
              suggestions: ["Reinstalling Convertor should restore it."],
            }}
          />
        </div>
      )}

      {/* The address field is the largest control on the page because pasting a
          link is the only thing this screen really asks for. */}
      <div className="pane p-4">
        <label htmlFor="download-url" className="mb-1.5 block t-label">
          Link
        </label>
        <div className="flex gap-2">
          <input
            id="download-url"
            type="url"
            inputMode="url"
            spellCheck={false}
            placeholder="https://…"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setFound(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (found ? begin() : check());
            }}
            disabled={running || !!unavailable}
            className="min-w-0 flex-1 rounded-none border border-[var(--line)] bg-[var(--surface)] px-3 py-2.5 text-[15px] text-[var(--ink)] outline-none placeholder:text-[var(--ink-faint)] focus:border-[var(--select)]"
          />
          <Button
            variant="default"
            onClick={check}
            disabled={!url.trim() || probing || running || !!unavailable}
          >
            {probing ? <Spinner /> : "Check"}
          </Button>
        </div>

        {found && (
          <div className="mt-3 flex items-start gap-3 border-t border-[var(--line)] pt-3">
            {found.thumbnail && (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={found.thumbnail}
                alt=""
                className="h-12 w-20 shrink-0 object-cover"
              />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate t-small t-strong">{found.title}</p>
              <p className="t-micro text-[var(--ink-faint)]">
                {found.uploader}
                {found.kind === "playlist"
                  ? ` · ${found.count} videos`
                  : found.duration
                    ? ` · ${seconds(found.duration)}`
                    : ""}
              </p>
            </div>
          </div>
        )}
      </div>

      {/* What to fetch. Four choices, not a format matrix: the decision people
          actually make here is "video or just the sound, and how big". */}
      <section className="mt-5">
        <p className="t-label mb-2">What to save</p>
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {QUALITIES.map((option) => {
            const selected = quality === option.id;
            return (
              <button
                key={option.id}
                onClick={() => setQuality(option.id)}
                aria-pressed={selected}
                title={option.detail}
                disabled={running}
                className="choice"
              >
                <span className="t-small t-strong">{option.label}</span>
                <span className="t-micro text-[var(--ink-faint)]">
                  {option.detail}
                </span>
              </button>
            );
          })}
        </div>

        {overview && !overview.canMerge && (
          <p className="mt-2 t-micro text-[var(--warn)]">
            FFmpeg could not be found, so video and audio cannot be merged and
            quality is capped at whatever the site offers as a single stream.
          </p>
        )}

        {/* The app's own checkbox, not a raw one. Every other tick in Convertor
            is a <Check>; a bare browser checkbox here looked like a different
            program had drawn it. */}
        <div className="mt-3">
          <Check
            checked={subtitles}
            onChange={setSubtitles}
            label="Also save subtitles"
            help="English, when the site publishes them."
          />
        </div>
      </section>

      {/* Where it lands, shown as a real path rather than hidden in a workspace. */}
      <section className="mt-5">
        <p className="t-label mb-2">Save to</p>
        <TextInput
          id="download-directory"
          value={directory}
          onChange={setDirectory}
          disabled={running}
          placeholder={overview?.defaultDirectory ?? "…"}
        />
        <p className="mt-1 t-micro text-[var(--ink-faint)]">
          {overview
            ? "The folder is created if it does not exist."
            : "Finding the default folder…"}
        </p>
      </section>

      <div className="mt-6 flex items-center gap-2">
        <Button
          variant="primary"
          onClick={begin}
          disabled={!url.trim() || running || !!unavailable}
        >
          {running ? "Downloading…" : "Download"}
        </Button>
        {running && (
          <Button variant="quiet" onClick={cancel}>
            Cancel
          </Button>
        )}
      </div>

      {error && (
        <div className="mt-5">
          <ErrorBlock error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {active && (
        <section className="mt-6 pane p-4">
          <div className="flex items-baseline justify-between gap-3">
            <p className="min-w-0 truncate t-small t-strong">
              {active.title || active.url}
            </p>
            <span className="num shrink-0 t-micro text-[var(--ink-faint)]">
              {active.elapsed}s
            </span>
          </div>

          {running && (
            <div className="mt-2">
              <Bar
                value={active.progress ?? 0}
                indeterminate={active.progress === null}
              />
              <p className="mt-1 flex items-baseline justify-between t-micro text-[var(--ink-faint)]">
                <span>{active.phase}</span>
                <span className="num">
                  {active.total > 0 &&
                    `${megabytes(active.downloaded)} of ${megabytes(active.total)}`}
                  {active.speed && ` · ${active.speed}`}
                  {active.eta && ` · ${active.eta} left`}
                </span>
              </p>
            </div>
          )}

          {active.status === "completed" && (
            <div className="mt-2">
              <p className="t-small" style={{ color: "var(--ok)" }}>
                Saved to {active.directory}
              </p>
              <ul className="mt-1 space-y-0.5">
                {active.files.map((file) => (
                  <li
                    key={file}
                    className="flex items-center gap-1.5 t-micro text-[var(--ink-muted)]"
                  >
                    <Icons.file className="h-3 w-3 shrink-0" />
                    <span className="truncate">{file.split("/").pop()}</span>
                  </li>
                ))}
              </ul>
              {isDesktop() && (
                <Button
                  variant="default"
                  size="sm"
                  className="mt-2"
                  onClick={() => reveal(active.files[0] ?? active.directory)}
                >
                  Show in Finder
                </Button>
              )}
            </div>
          )}

          {active.status === "cancelled" && (
            <p className="mt-2 t-micro text-[var(--ink-faint)]">
              Cancelled. Any partial file was left in the folder.
            </p>
          )}

          {active.status === "failed" && active.error && (
            <div className="mt-2">
              <ErrorBlock error={active.error} />
            </div>
          )}
        </section>
      )}
    </main>
  );
}
