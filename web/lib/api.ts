import type {
  EngineError,
  FormatDetail,
  FormatSummary,
  HistoryEntry,
  InputFile,
  Job,
  Pack,
  PackJob,
  PackSummary,
  Plan,
  PlanDetails,
  Preset,
  Settings,
  StorageStats,
  SystemInfo,
} from "./types";

/** Where the engine lives.
 *
 *  Served from Docker the interface and the engine share an origin, so a bare
 *  "/api" path is proxied server-side. In the desktop app there is no proxy:
 *  the Tauri shell starts the engine on a free loopback port and injects it
 *  before the page loads. Everything else in this file is identical either way.
 */
const ENGINE: string =
  (typeof window !== "undefined" &&
    (window as unknown as { __CONVERTOR_ENGINE__?: string }).__CONVERTOR_ENGINE__) ||
  "";

/** Absolute URL for an engine path, for anywhere a raw href is needed. */
export function engineUrl(path: string): string {
  return `${ENGINE}${path}`;
}

export class ApiError extends Error {
  info: EngineError;
  constructor(info: EngineError) {
    super(info.title);
    this.info = info;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${ENGINE}${path}`, init);
  } catch {
    throw new ApiError({
      code: "unreachable",
      title: "The conversion engine is not responding",
      detail:
        "The interface is running but could not reach the engine service. It may " +
        "still be starting up.",
      suggestions: [
        "Wait a few seconds and try again.",
        "Check the engine container with: docker compose logs engine",
      ],
    });
  }

  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      /* fall through to the generic shape below */
    }
    const wrapped = payload as { error?: EngineError; detail?: { error?: EngineError } };
    const info = wrapped?.error ?? wrapped?.detail?.error;
    throw new ApiError(
      info ?? {
        code: `http_${response.status}`,
        title: "The engine rejected this request",
        detail: `The request to ${path} failed with status ${response.status}.`,
        suggestions: ["Try again, or restart the engine container."],
      },
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  system: () => request<SystemInfo>("/api/system"),

  listFiles: () => request<{ files: InputFile[] }>("/api/files"),

  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    return request<{ files: InputFile[]; rejected: { name: string; error: EngineError }[] }>(
      "/api/files",
      { method: "POST", body: form },
    );
  },

  paste: (content: string, name: string) =>
    request<{ files: InputFile[] }>("/api/files/paste", json({ content, name })),

  removeFile: (id: string) => request<{ ok: true }>(`/api/files/${id}`, { method: "DELETE" }),

  plan: (fileIds: string[]) => request<Plan>("/api/plan", json({ fileIds })),

  planDetails: (
    fileIds: string[],
    target: string,
    mode: string,
    options: Record<string, unknown>,
  ) => request<PlanDetails>("/api/plan/details", json({ fileIds, target, mode, options })),

  createJob: (body: {
    fileIds: string[];
    target: string;
    mode: string;
    options: Record<string, unknown>;
    preset: string | null;
  }) => request<Job>("/api/jobs", json(body)),

  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, json({})),
  retryJob: (id: string, itemIds?: string[]) =>
    request<Job>(`/api/jobs/${id}/retry`, json({ itemIds })),

  downloadAllUrl: (id: string) => engineUrl(`/api/jobs/${id}/download`),
  outputUrl: (path: string, download = false) =>
    engineUrl(
      `/api/outputs?path=${encodeURIComponent(path)}${download ? "&download=true" : ""}`,
    ),

  formats: (q = "") =>
    request<{ formats: FormatSummary[]; categories: { id: string; label: string }[] }>(
      `/api/formats?q=${encodeURIComponent(q)}`,
    ),
  format: (id: string) => request<FormatDetail>(`/api/formats/${id}`),

  presets: (target = "") =>
    request<{ presets: Preset[] }>(`/api/presets?target=${encodeURIComponent(target)}`),
  savePreset: (body: {
    label: string;
    description: string;
    sourceFormat: string | null;
    targetFormat: string;
    values: Record<string, unknown>;
  }) => request<{ preset: Preset }>("/api/presets", json(body)),
  deletePreset: (id: string) =>
    request<{ ok: true }>(`/api/presets/${id}`, { method: "DELETE" }),

  history: (params: { q?: string; target?: string; status?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.q) search.set("q", params.q);
    if (params.target) search.set("target", params.target);
    if (params.status) search.set("status", params.status);
    return request<{ history: HistoryEntry[]; enabled: boolean }>(
      `/api/history?${search.toString()}`,
    );
  },
  deleteHistory: (id: string, purge: boolean) =>
    request<{ ok: true }>(`/api/history/${id}?purge=${purge}`, { method: "DELETE" }),
  clearHistory: (purge: boolean) =>
    request<{ removed: number }>(`/api/history?purge=${purge}`, { method: "DELETE" }),
  rerun: (id: string, overrides: Record<string, unknown> = {}) =>
    request<Job>(`/api/history/${id}/rerun`, json(overrides)),

  settings: () => request<{ settings: Settings }>("/api/settings"),
  updateSettings: (values: Partial<Settings>) =>
    request<{ settings: Settings }>("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }),

  packs: () => request<PackSummary>("/api/packs"),
  installPack: (id: string) =>
    request<{ pack: Pack; job: PackJob }>(`/api/packs/${id}/install`, json({})),
  cancelPack: (id: string) =>
    request<{ pack: Pack; job: PackJob | null }>(`/api/packs/${id}/cancel`, json({})),
  removePack: (id: string) =>
    request<{ pack: Pack }>(`/api/packs/${id}`, { method: "DELETE" }),

  storage: () => request<StorageStats>("/api/storage"),
  cleanStorage: (scopes: string[]) =>
    request<{ removed: Record<string, number>; storage: StorageStats }>(
      "/api/storage/clean",
      json({ scopes }),
    ),
};

/** Subscribe to a job's live state. Falls back to polling if the stream drops. */
export function watchJob(
  id: string,
  onUpdate: (job: Job) => void,
  onDone: (job: Job) => void,
): () => void {
  let closed = false;
  let poller: ReturnType<typeof setInterval> | null = null;

  const finish = (job: Job) => {
    if (["completed", "completed_with_warnings", "failed", "cancelled"].includes(job.status)) {
      onDone(job);
      return true;
    }
    return false;
  };

  const source = new EventSource(engineUrl(`/api/jobs/${id}/events`));

  source.onmessage = (event) => {
    if (closed) return;
    const job = JSON.parse(event.data) as Job;
    onUpdate(job);
    if (finish(job)) {
      closed = true;
      source.close();
    }
  };

  source.onerror = () => {
    if (closed) return;
    source.close();
    // The stream can drop behind a proxy; polling keeps the UI truthful.
    if (poller) return;
    poller = setInterval(async () => {
      try {
        const job = await api.job(id);
        onUpdate(job);
        if (finish(job)) {
          closed = true;
          if (poller) clearInterval(poller);
        }
      } catch {
        if (poller) clearInterval(poller);
      }
    }, 1000);
  };

  return () => {
    closed = true;
    source.close();
    if (poller) clearInterval(poller);
  };
}

/** Follow one pack install to its end.
 *
 *  Same shape as watchJob, and the same reason for the polling fallback: the
 *  stream is the nice path, not the only one. A 300 MB download that appears
 *  frozen because an EventSource died is worse than one that updates every
 *  second.
 */
export function watchPack(
  id: string,
  onUpdate: (job: PackJob) => void,
  onDone: (job: PackJob) => void,
): () => void {
  let closed = false;
  let poller: ReturnType<typeof setInterval> | null = null;

  const finish = (job: PackJob) => {
    if (job.status !== "running") {
      onDone(job);
      return true;
    }
    return false;
  };

  const source = new EventSource(engineUrl(`/api/packs/${id}/events`));

  source.onmessage = (event) => {
    if (closed) return;
    const job = JSON.parse(event.data) as PackJob;
    onUpdate(job);
    if (finish(job)) {
      closed = true;
      source.close();
    }
  };

  source.onerror = () => {
    if (closed || poller) return;
    source.close();
    poller = setInterval(async () => {
      try {
        const summary = await api.packs();
        const pack = summary.packs.find((p) => p.id === id);
        if (!pack?.job) return;
        onUpdate(pack.job);
        if (finish(pack.job) && poller) {
          closed = true;
          clearInterval(poller);
        }
      } catch {
        if (poller) clearInterval(poller);
      }
    }, 1000);
  };

  return () => {
    closed = true;
    source.close();
    if (poller) clearInterval(poller);
  };
}
