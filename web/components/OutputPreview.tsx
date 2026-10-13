"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { isDesktop, reveal } from "@/lib/desktop";
import type { OutputFile } from "@/lib/types";
import { Button, Icons } from "./ui";

/** Keep the workspace mounted: media must never replace the app's webview. */
export function OutputPreview({ output, onClose }: { output: OutputFile; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [failed, setFailed] = useState(false);
  const [fileError, setFileError] = useState(false);
  const url = api.outputUrl(output.path);
  const kind = output.previewKind ?? (
    ["png", "jpeg", "webp", "gif", "svg"].includes(output.formatId) ? "image" : "document"
  );

  useEffect(() => {
    const dialog = dialogRef.current!;
    if (!dialog.open) dialog.showModal();
    // Removing the dialog also removes its media player. Closing it in an
    // effect cleanup would fire onClose during React's development remount.
  }, []);

  return (
    <dialog ref={dialogRef} className="output-preview" aria-label={`Preview ${output.name}`}
      onClose={onClose}
      onKeyDown={(event) => event.stopPropagation()}>
      <header className="flex flex-none flex-wrap items-center gap-3 border-b border-[var(--line)] p-3">
        <Button autoFocus onClick={() => dialogRef.current?.close()}>Back to conversion</Button>
        <h2 className="t-strong min-w-0 flex-1 truncate" title={output.name}>{output.name}</h2>
        {isDesktop() ? (
          <Button onClick={async () => setFileError(!(await reveal(output.path)))}>
            <Icons.folder /> Show file
          </Button>
        ) : (
          <a className="btn btn-default" href={api.outputUrl(output.path, true)} download={output.name}>Download</a>
        )}
      </header>
      {fileError && <p role="alert" className="px-3 py-2 text-[var(--fail)]">Could not show the file in its folder. It is saved at {output.path}.</p>}
      <div className="preview-content">
        {failed ? (
          <p role="alert" className="p-6 text-[var(--ink-muted)]">This format cannot be displayed here. You can still access the converted file using the control above, or return to your conversion.</p>
        ) : kind === "video" ? (
          <video controls playsInline preload="metadata" src={url} aria-label={output.name} onError={() => setFailed(true)} />
        ) : kind === "audio" ? (
          <audio controls preload="metadata" src={url} aria-label={output.name} onError={() => setFailed(true)} />
        ) : kind === "image" ? (
          <img src={url} alt={output.name} onError={() => setFailed(true)} />
        ) : (
          <iframe src={url} title={output.name} sandbox="" />
        )}
      </div>
      <p className="flex-none border-t border-[var(--line)] px-3 py-2 t-small text-[var(--ink-muted)]">Press Escape or Back to conversion to return. Your results stay in the workspace.</p>
    </dialog>
  );
}
