"use client";

import { useCallback, useRef, useState } from "react";
import { bytes, describeMetadata } from "@/lib/format";
import type { InputFile, Preview } from "@/lib/types";
import { Button, Empty, Fmt, Icons, Kbd, Spinner } from "./ui";

function PreviewBlock({ preview }: { preview: Preview }) {
  if (preview.kind === "text" && preview.text) {
    return (
      <pre className="mono max-h-28 overflow-auto whitespace-pre-wrap bg-[var(--sunken)] p-2 text-[var(--ink-muted)]">
        {preview.text.slice(0, 600)}
      </pre>
    );
  }
  if (preview.kind === "table" && preview.columns) {
    return (
      <div className="overflow-x-auto border-y border-[var(--line)]">
        <table className="w-full">
          <thead>
            <tr>
              {preview.columns.map((column) => (
                <th
                  key={column}
                  className="t-label whitespace-nowrap border-b border-[var(--line)] px-2 py-1 text-left"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(preview.rows ?? []).slice(0, 4).map((row, index) => (
              <tr key={index}>
                {row.map((cell, cellIndex) => (
                  <td
                    key={cellIndex}
                    className="mono max-w-28 truncate border-b border-[var(--line)] px-2 py-1 text-[var(--ink-muted)]"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (preview.kind === "list" && preview.items) {
    return (
      <ul className="mono max-h-28 overflow-auto bg-[var(--sunken)] p-2 text-[var(--ink-muted)]">
        {preview.items.map((item) => (
          <li key={item} className="truncate">
            {item}
          </li>
        ))}
      </ul>
    );
  }
  if (preview.kind === "media" && preview.streams) {
    return (
      <div className="flex flex-wrap gap-1 py-1">
        {preview.streams.map((stream, index) => (
          <Fmt key={index}>
            {stream.type}·{stream.codec}
          </Fmt>
        ))}
      </div>
    );
  }
  return null;
}

function QueueRow({
  file,
  index,
  ordered,
  selected,
  onSelect,
  onRemove,
  onMove,
}: {
  file: InputFile;
  index: number;
  ordered: boolean;
  selected: boolean;
  onSelect: () => void;
  onRemove: () => void;
  onMove: (from: number, to: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const facts = describeMetadata(file.category, file.metadata);
  const blocking = file.notices.filter((notice) => notice.level === "error");
  const hasDetail = Boolean(file.preview) || file.notices.length > 0;

  return (
    <li
      draggable={ordered}
      onDragStart={(event) => {
        event.dataTransfer.setData("text/plain", String(index));
        event.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(event) => ordered && event.preventDefault()}
      onDrop={(event) => {
        if (!ordered) return;
        event.preventDefault();
        const from = Number(event.dataTransfer.getData("text/plain"));
        if (!Number.isNaN(from) && from !== index) onMove(from, index);
      }}
      className="border-b border-[var(--line)]"
      style={{
        background: selected ? "var(--select-wash)" : undefined,
        boxShadow: selected ? "inset 2px 0 0 var(--select)" : undefined,
      }}
    >
      <div
        onClick={onSelect}
        className="flex cursor-default items-start gap-2 px-3 py-1.5 transition-colors duration-[120ms] hover:bg-[var(--raised)]"
      >
        {ordered && (
          <span
            className="mt-[3px] cursor-grab text-[var(--ink-faint)] active:cursor-grabbing"
            title="Drag to reorder"
          >
            <Icons.grip className="h-3 w-3" />
          </span>
        )}

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            {ordered && (
              <span className="num t-micro w-3 shrink-0 text-right text-[var(--ink-faint)]">
                {index + 1}
              </span>
            )}
            <span className="flex-1 truncate t-strong" title={file.name}>
              {file.name}
            </span>
            <button
              onClick={(event) => {
                event.stopPropagation();
                onRemove();
              }}
              aria-label={`Remove ${file.name}`}
              className="btn btn-quiet h-4 shrink-0 px-1"
            >
              <Icons.close className="h-3 w-3" />
            </button>
          </div>

          <div className="mt-0.5 flex items-center gap-1.5 overflow-hidden">
            <Fmt>{file.convertible ? file.formatId : "unknown"}</Fmt>
            <span className="num t-micro shrink-0 text-[var(--ink-faint)]">
              {bytes(file.size)}
            </span>
            {facts.length > 0 && (
              <span className="truncate t-micro text-[var(--ink-faint)]">
                {facts.join(" · ")}
              </span>
            )}
            {hasDetail && (
              <button
                onClick={(event) => {
                  event.stopPropagation();
                  setOpen(!open);
                }}
                className="ml-auto shrink-0 t-micro text-[var(--ink-faint)] hover:text-[var(--ink)]"
              >
                {open ? "less" : blocking.length ? "problem" : "peek"}
              </button>
            )}
          </div>

          {blocking.length > 0 && !open && (
            <p className="mt-0.5 truncate t-micro" style={{ color: "var(--fail)" }}>
              {blocking[0].title}
            </p>
          )}
        </div>
      </div>

      {open && (
        <div className="space-y-1.5 bg-[var(--sunken)] px-3 py-2">
          {file.preview && <PreviewBlock preview={file.preview} />}
          {file.notices.map((notice, index) => (
            <p key={index} className="t-micro">
              <span
                style={{
                  color:
                    notice.level === "error"
                      ? "var(--fail)"
                      : notice.level === "warning"
                        ? "var(--warn)"
                        : "var(--ink-muted)",
                }}
              >
                {notice.title}.
              </span>{" "}
              <span className="text-[var(--ink-faint)]">{notice.detail}</span>
            </p>
          ))}
        </div>
      )}
    </li>
  );
}

export function Queue({
  files,
  loading,
  ordered,
  selectedId,
  onSelect,
  onAdd,
  onPaste,
  onRemove,
  onMove,
  onClear,
}: {
  files: InputFile[];
  loading: boolean;
  ordered: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onAdd: (files: File[]) => void;
  onPaste: (text: string, name: string) => void;
  onRemove: (id: string) => void;
  onMove: (from: number, to: number) => void;
  onClear: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const depth = useRef(0);

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      const dropped = Array.from(event.dataTransfer.files);
      if (dropped.length) onAdd(dropped);
    },
    [onAdd],
  );

  const incompatible = files.filter((file) => !file.convertible).length;

  return (
    <section
      aria-label="Input queue"
      className="pane relative"
      onDragEnter={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        depth.current += 1;
        setDragging(true);
      }}
      onDragLeave={() => {
        depth.current -= 1;
        if (depth.current <= 0) setDragging(false);
      }}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes("Files")) event.preventDefault();
      }}
      onDrop={handleDrop}
    >
      <header className="pane-head">
        <h2 className="t-label">Queue</h2>
        {files.length > 0 && (
          <span className="num t-micro text-[var(--ink-faint)]">
            {files.length}
            {incompatible > 0 && ` · ${incompatible} unusable`}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => setPasting(!pasting)}
            className="btn btn-quiet btn-sm px-1.5"
            title="Paste text as a file"
          >
            Paste
          </button>
          {files.length > 0 && (
            <button
              onClick={onClear}
              className="btn btn-quiet btn-sm px-1"
              title="Remove all"
            >
              <Icons.trash />
            </button>
          )}
        </div>
      </header>

      <button
        onClick={() => inputRef.current?.click()}
        className="add-target"
        aria-label="Add files"
      >
        <Icons.add className="h-4 w-4" />
        <span className="text-[13px] font-[600]">Add files</span>
        <span className="t-micro text-[var(--ink-faint)]">
          or drop them here · <Kbd>⌘O</Kbd>
        </span>
      </button>

      <input
        ref={inputRef}
        type="file"
        multiple
        className="sr-only"
        onChange={(event) => {
          const chosen = Array.from(event.target.files ?? []);
          if (chosen.length) onAdd(chosen);
          event.target.value = "";
        }}
      />

      {pasting && (
        <div className="rule bg-[var(--sunken)] p-2">
          <textarea
            autoFocus
            value={pasteText}
            onChange={(event) => setPasteText(event.target.value)}
            rows={4}
            placeholder="Paste Markdown, CSV, JSON, HTML or text…"
            className="ctl mono h-auto w-full resize-y py-1.5"
          />
          <div className="mt-1.5 flex justify-end gap-1.5">
            <Button size="sm" variant="quiet" onClick={() => setPasting(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={!pasteText.trim()}
              onClick={() => {
                onPaste(pasteText, "pasted.md");
                setPasteText("");
                setPasting(false);
              }}
            >
              Add
            </Button>
          </div>
        </div>
      )}

      {loading && (
        <div className="rule flex items-center gap-1.5 px-3 py-1.5 t-small text-[var(--ink-muted)]">
          <Spinner /> inspecting
        </div>
      )}

      <div className="pane-body">
        {files.length === 0 && !loading ? (
          <Empty
            title="Nothing queued"
            detail="Documents, images, spreadsheets, media, archives — read on this machine, never uploaded."
          />
        ) : (
          <>
            {ordered && files.length > 1 && (
              <p className="rule px-3 py-1 t-micro text-[var(--ink-faint)]">
                Order below is the order used in the combined output.
              </p>
            )}
            <ul>
              {files.map((file, index) => (
                <QueueRow
                  key={file.id}
                  file={file}
                  index={index}
                  ordered={ordered}
                  selected={selectedId === file.id}
                  onSelect={() => onSelect(file.id)}
                  onRemove={() => onRemove(file.id)}
                  onMove={onMove}
                />
              ))}
            </ul>
          </>
        )}
      </div>

      {dragging && (
        <div
          className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center border-2 border-dashed"
          style={{ borderColor: "var(--focus)", background: "var(--select-wash)" }}
        >
          <span className="t-strong">Drop to add</span>
        </div>
      )}
    </section>
  );
}
