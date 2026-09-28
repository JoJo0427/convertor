export type Level = "info" | "warning" | "error";

export interface Notice {
  level: Level;
  title: string;
  detail: string;
}

export interface EngineError {
  code: string;
  title: string;
  detail: string;
  suggestions: string[];
}

export interface Preview {
  kind: "text" | "table" | "list" | "media";
  text?: string;
  columns?: string[];
  rows?: string[][];
  items?: string[];
  streams?: { type: string; codec: string }[];
}

export interface InputFile {
  id: string;
  name: string;
  size: number;
  formatId: string | null;
  formatLabel: string;
  category: string | null;
  categoryLabel: string;
  summary: string;
  confidence: "certain" | "likely" | "uncertain" | "unknown";
  metadata: Record<string, unknown>;
  notices: Notice[];
  preview: Preview | null;
  convertible: boolean;
}

export interface RouteStep {
  converterId: string;
  converter: string;
  from: string;
  to: string;
}

export interface MissingPack {
  id: string;
  label: string;
  megabytes: number;
}

export interface Route {
  steps: RouteStep[];
  direct: boolean;
  available: boolean;
  missingEngines: string[];
  missingPacks: MissingPack[];
  cardinality: string;
  description: string;
}

export interface Recommendation {
  formatId: string;
  label: string;
  category: string;
  summary: string;
  reason: string;
  featured: boolean;
  score: number;
  route: Route;
}

export interface OptionChoice {
  value: string;
  label: string;
  help: string;
}

export interface ConversionOption {
  id: string;
  label: string;
  type: "select" | "number" | "boolean" | "text";
  default: unknown;
  help: string;
  choices: OptionChoice[];
  min: number | null;
  max: number | null;
  step: number | null;
  unit: string;
  advanced: boolean;
  dependsOn: { option: string; values: string[] } | null;
  /** Non-empty only when a mixed batch means this setting affects some inputs. */
  appliesTo: string[];
}

export interface Preset {
  id: string;
  label: string;
  description: string;
  values: Record<string, unknown>;
  custom?: boolean;
}

export interface FormatRoute {
  sourceId: string;
  sourceLabel: string;
  fileCount: number;
  description: string;
  direct: boolean;
  steps: RouteStep[];
}

export interface PlanStep {
  step: number;
  converter: string;
  from: string;
  to: string;
  engine: string;
  detail: string;
  command: string;
}

export interface PlanDetails {
  route: Route;
  plan: PlanStep[];
  routes: FormatRoute[];
  options: ConversionOption[];
  presets: Preset[];
  warnings: Notice[];
  compatibility: { id: string; name: string; compatible: boolean; reason: string }[];
  outputEstimate: { fileCount: number; description: string };
  defaults: Record<string, unknown>;
}

export interface Intent {
  id: string;
  label: string;
  detail: string;
  best: string;
  options: { formatId: string; label: string; reason: string; direct: boolean }[];
}

export interface RecentTarget {
  formatId: string;
  label: string;
  category: string;
  uses: number;
  lastUsed: number;
}

export interface Plan {
  intents: Intent[];
  recent: RecentTarget[];
  sources: string[];
  sourceLabels: string[];
  recommendations: Recommendation[];
  combine: Recommendation[];
  split: Recommendation[];
  canCombine: boolean;
  canSplit: boolean;
  unsupported: string[];
  mixed: boolean;
}

export interface OutputFile {
  name: string;
  path: string;
  size: number;
  formatId: string;
  formatLabel: string;
  previewable: boolean;
}

export type ItemStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "cancelled"
  | "skipped";

export interface JobItem {
  id: string;
  label: string;
  inputIds: string[];
  status: ItemStatus;
  progress: number;
  message: string;
  warnings: Notice[];
  error: EngineError | null;
  outputs: OutputFile[];
  durationMs: number | null;
}

export interface JobSummary {
  completed: number;
  warnings: number;
  failed: number;
  cancelled: number;
  skipped: number;
  pending: number;
  outputs: number;
  outputBytes: number;
}

export interface Job {
  id: string;
  status: ItemStatus;
  target: string;
  targetLabel: string;
  mode: "each" | "combined" | "split";
  preset: string | null;
  options: Record<string, unknown>;
  route: Route;
  progress: number;
  items: JobItem[];
  summary: JobSummary;
  createdAt: number;
  finishedAt: number | null;
  outputDir: string;
  inputNames: string[];
  cancelling: boolean;
}

export interface EngineStatus {
  id: string;
  label: string;
  purpose: string;
  available: boolean;
  installHint: string;
}

/** Live state of one pack install. Absent when nothing has been started. */
export interface PackJob {
  pack_id: string;
  status: "running" | "completed" | "failed" | "cancelled";
  /** What is happening, in words — shown verbatim. */
  phase: string;
  /** null when the server sent no Content-Length: the bar goes indeterminate
   *  rather than showing a percentage nobody computed. */
  progress: number | null;
  downloaded: number;
  total: number;
  error: EngineError | null;
  elapsed: number;
}

export interface Pack {
  id: string;
  label: string;
  summary: string;
  unlocks: string[];
  /** Download size. */
  megabytes: number;
  /** What it will occupy once installed — for a pip pack, roughly three times
   *  the download, so quoting only the download would understate the cost. */
  installedMegabytes: number;
  /** Measured size of what is installed right now, 0 when it is not. */
  diskMegabytes: number;
  installed: boolean;
  engines: string[];
  /** False when this build or platform has no source for it. */
  installable: boolean;
  /** Why not, when installable is false. */
  unavailableReason: string;
  /** The manual route, for packs that cannot be fetched automatically. */
  manualHint: string;
  job: PackJob | null;
}

export interface PackSummary {
  packs: Pack[];
  installedCount: number;
  totalCount: number;
  complete: boolean;
  platform: string;
  manageable: boolean;
}

export interface SystemInfo {
  version: string;
  build: string;
  packs: PackSummary;
  engines: EngineStatus[];
  intelligentWorkflows: { available: boolean; model: string | null };
  workspace: { root: string; inbox: string; output: string; temp: string };
  limits: { maxUploadBytes: number };
  converterCount: number;
  formatCount: number;
  curatedFormatCount: number;
  ffmpeg: {
    available: boolean;
    version: string;
    readableFormats: number;
    writableFormats: number;
    encoders: number;
    decoders: number;
    filters: number;
  };
}

export interface FormatSummary {
  id: string;
  label: string;
  category: string;
  categoryLabel: string;
  extensions: string[];
  summary: string;
  commonUses: string[];
  notes: string[];
}

export interface FormatDetail extends FormatSummary {
  mime: string;
  convertsTo: {
    id: string;
    label: string;
    direct: boolean;
    available: boolean;
    discovered: boolean;
    via: string;
    category: string;
  }[];
  convertsFrom: {
    id: string;
    label: string;
    category: string;
    discovered: boolean;
  }[];
}

export interface HistoryEntry {
  id: string;
  createdAt: number;
  finishedAt: number | null;
  status: ItemStatus;
  target: string;
  targetLabel: string;
  mode: string;
  preset: string | null;
  options: Record<string, unknown>;
  inputs: { id: string | string[]; label: string }[];
  route: Route;
  summary: JobSummary;
  outputDir: string | null;
  live: boolean;
  outputsExist: boolean;
}

export interface Settings {
  keepHistory: boolean;
  autoCleanTemp: boolean;
  tempRetentionHours: number;
  outputRetentionDays: number;
  defaultPreset: string;
  /** These four were environment variables needing a restart. They are settings
   *  now, and a change takes effect on the next conversion. */
  maxUploadMb: number;
  workerConcurrency: number;
  /** Masked on read — the real key is write-only. Sending a masked value back is
   *  ignored by the engine, so an empty field means "leave it alone". */
  anthropicApiKey: string;
  hasAnthropicKey: boolean;
  aiModel: string;
}

export interface StorageStats {
  inbox: { path: string; bytes: number; files: number };
  output: { path: string; bytes: number; files: number };
  temp: { path: string; bytes: number; files: number };
  database: { path: string; bytes: number };
}

/* ------------------------------------------------------------- downloading */

export interface DownloadQuality {
  id: string;
  label: string;
  detail: string;
}

export interface DownloadProbe {
  kind: "single" | "playlist";
  title: string;
  count: number;
  duration: number | null;
  thumbnail: string | null;
  uploader: string;
  extension?: string;
}

export interface Download {
  id: string;
  url: string;
  title: string;
  status: "running" | "completed" | "failed" | "cancelled";
  phase: string;
  /** null when the server sent no total, so the bar goes indeterminate. */
  progress: number | null;
  downloaded: number;
  total: number;
  speed: string;
  eta: string;
  files: string[];
  directory: string;
  error: EngineError | null;
  elapsed: number;
}

export interface DownloadOverview {
  available: boolean;
  defaultDirectory: string;
  /** False when FFmpeg is missing, which caps quality at a single pre-merged stream. */
  canMerge: boolean;
  qualities: DownloadQuality[];
  downloads: Download[];
}
