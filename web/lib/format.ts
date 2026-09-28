export function bytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  const scaled = value / 1024 ** index;
  return `${scaled >= 100 || index === 0 ? Math.round(scaled) : scaled.toFixed(1)} ${units[index]}`;
}

export function duration(ms: number | null): string {
  if (ms === null || ms < 0) return "";
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function clock(seconds: number): string {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export function when(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  const elapsed = (Date.now() - date.getTime()) / 1000;
  if (elapsed < 60) return "just now";
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} min ago`;
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)} h ago`;
  if (elapsed < 604800) return `${Math.floor(elapsed / 86400)} d ago`;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function exactTime(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** Human description of the detected characteristics of one input file. */
export function describeMetadata(
  category: string | null,
  metadata: Record<string, unknown>,
): string[] {
  const parts: string[] = [];
  const num = (key: string) => {
    const value = metadata[key];
    return typeof value === "number" ? value : null;
  };

  if (category === "image") {
    const w = num("width");
    const h = num("height");
    if (w && h) parts.push(`${w} × ${h}`);
    const mp = num("megapixels");
    if (mp && mp >= 0.1) parts.push(`${mp} MP`);
    if (metadata.hasAlpha === true) parts.push("has transparency");
    const frames = num("frames");
    if (frames && frames > 1) parts.push(`${frames} frames`);
  } else if (category === "document" || metadata.pages) {
    const pages = num("pages");
    if (pages) parts.push(`${pages} page${pages === 1 ? "" : "s"}`);
    if (metadata.hasTextLayer === false) parts.push("scanned — no text layer");
    if (metadata.encrypted === true) parts.push("password protected");
  } else if (category === "data") {
    const cols = num("columnCount");
    if (cols) parts.push(`${cols} column${cols === 1 ? "" : "s"}`);
  } else if (category === "audio" || category === "video") {
    const seconds = num("duration");
    if (seconds) parts.push(clock(seconds));
    const w = num("width");
    const h = num("height");
    if (w && h) parts.push(`${w} × ${h}`);
    if (typeof metadata.videoCodec === "string") parts.push(String(metadata.videoCodec));
    else if (typeof metadata.audioCodec === "string") parts.push(String(metadata.audioCodec));
  } else if (category === "archive") {
    const entries = num("entryCount");
    if (entries) parts.push(`${entries} file${entries === 1 ? "" : "s"} inside`);
  } else if (category === "text") {
    const words = num("words");
    if (words) parts.push(`${words.toLocaleString()} words`);
  }

  return parts;
}
