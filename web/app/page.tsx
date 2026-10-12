"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, watchJob } from "@/lib/api";
import type { EngineError, InputFile, Job, Plan, PlanDetails } from "@/lib/types";
import { Queue } from "@/components/Queue";
import { Conversion, type Mode } from "@/components/Conversion";
import { Inspector } from "@/components/Inspector";
import { ErrorBlock } from "@/components/ui";
import { canStartConversion } from "@/lib/conversion-state";

export default function WorkspacePage() {
  const [files, setFiles] = useState<InputFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>("each");
  const [plan, setPlan] = useState<Plan | null>(null);

  const [target, setTarget] = useState<string | null>(null);
  const [details, setDetails] = useState<PlanDetails | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [detailsKey, setDetailsKey] = useState<string | null>(null);
  const [detailsContext, setDetailsContext] = useState<string | null>(null);
  const [options, setOptions] = useState<Record<string, unknown>>({});
  const [preset, setPreset] = useState<string | null>(null);
  const [presetRevision, setPresetRevision] = useState(0);

  const [job, setJob] = useState<Job | null>(null);
  const [jobFiles, setJobFiles] = useState<InputFile[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<EngineError | null>(null);

  const stopWatching = useRef<(() => void) | null>(null);
  const addRef = useRef<HTMLInputElement | null>(null);
  const submitting = useRef(false);
  const fileIds = files.map((file) => file.id);
  const fileKey = fileIds.join(",");
  const requestKey = JSON.stringify([fileKey, target, mode, options]);
  const contextKey = JSON.stringify([fileKey, target, mode]);
  const running = job?.status === "queued" || job?.status === "running";
  const busy = starting || running;
  const canConvert = canStartConversion({
    target, fileCount: files.length, busy: busy || uploading,
    hasJob: job !== null, details,
    planIsCurrent: !detailsLoading && detailsKey === requestKey,
  });

  const fail = (thrown: unknown) => {
    if (thrown instanceof ApiError) setError(thrown.info);
    else
      setError({
        code: "unknown",
        title: "Something in the interface went wrong",
        detail: String(thrown).slice(0, 300),
        suggestions: ["Reload the page and try again."],
      });
  };

  /* --------------------------------------------------------------- files */

  useEffect(() => {
    api
      .listFiles()
      .then((data) => setFiles(data.files))
      .catch(fail);
  }, []);

  const addFiles = useCallback(async (incoming: File[]) => {
    if (busy) return;
    setUploading(true);
    setError(null);
    try {
      const result = await api.upload(incoming);
      setFiles((current) => [...current, ...result.files]);
      if (result.rejected.length) setError(result.rejected[0].error);
    } catch (thrown) {
      fail(thrown);
    } finally {
      setUploading(false);
    }
  }, [busy]);

  const addPasted = useCallback(async (text: string, name: string) => {
    if (busy) return;
    setUploading(true);
    try {
      const result = await api.paste(text, name);
      setFiles((current) => [...current, ...result.files]);
    } catch (thrown) {
      fail(thrown);
    } finally {
      setUploading(false);
    }
  }, [busy]);

  const removeFile = useCallback(async (id: string) => {
    setFiles((current) => current.filter((file) => file.id !== id));
    setSelectedId((current) => (current === id ? null : current));
    await api.removeFile(id).catch(() => undefined);
  }, []);

  const clearFiles = useCallback(async () => {
    const ids = files.map((file) => file.id);
    setFiles([]);
    setSelectedId(null);
    setJob(null);
    setTarget(null);
    await Promise.all(ids.map((id) => api.removeFile(id).catch(() => undefined)));
  }, [files]);

  const moveFile = useCallback((from: number, to: number) => {
    if (busy) return;
    setFiles((current) => {
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }, [busy]);

  /* ---------------------------------------------------------------- plan */

  useEffect(() => {
    if (!fileIds.length) {
      setPlan(null);
      setTarget(null);
      return;
    }
    let cancelled = false;
    api
      .plan(fileIds)
      .then((data) => {
        if (cancelled) return;
        setPlan(data);
        if (mode === "combined" && !data.canCombine) setMode("each");
        if (mode === "split" && !data.canSplit) setMode("each");
      })
      .catch((thrown) => !cancelled && fail(thrown));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileKey]);

  useEffect(() => {
    if (!plan || !target) return;
    const list =
      mode === "combined" ? plan.combine : mode === "split" ? plan.split : plan.recommendations;
    if (!list.some((item) => item.formatId === target)) setTarget(null);
  }, [plan, mode, target]);

  /* ------------------------------------------------------------- details */

  useEffect(() => {
    if (!target || !fileIds.length) {
      setDetails(null);
      setDetailsLoading(false);
      return;
    }
    let cancelled = false;
    setDetailsLoading(true);
    const timer = setTimeout(() => {
      api
        .planDetails(fileIds, target, mode, options)
        .then((data) => {
          if (cancelled) return;
          setDetails(data);
          setDetailsKey(requestKey);
          setDetailsContext(contextKey);
        })
        .catch((thrown) => {
          if (cancelled) return;
          setDetails(null);
          fail(thrown);
        })
        .finally(() => !cancelled && setDetailsLoading(false));
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, presetRevision]);

  const chooseTarget = (formatId: string) => {
    setTarget(formatId);
    setOptions({});
    setPreset(null);
    setJob(null);
  };

  const changeOption = (id: string, value: unknown) => {
    setOptions((current) => ({ ...current, [id]: value }));
    setPreset(null);
  };

  const applyPreset = (id: string, values: Record<string, unknown>) => {
    setPreset(id);
    setOptions((current) => ({ ...current, ...values }));
  };

  const savePreset = async (label: string) => {
    if (!target) return;
    try {
      await api.savePreset({
        label,
        description: "",
        sourceFormat: plan?.sources[0] ?? null,
        targetFormat: target,
        values: { ...details?.defaults, ...options },
      });
      setPresetRevision((current) => current + 1);
    } catch (thrown) {
      fail(thrown);
    }
  };

  const deletePreset = async (id: string) => {
    try {
      await api.deletePreset(id);
      setPresetRevision((current) => current + 1);
    } catch (thrown) {
      fail(thrown);
    }
  };

  /* ----------------------------------------------------------------- job */

  const attach = useCallback((started: Job) => {
    setJob(started);
    stopWatching.current?.();
    stopWatching.current = watchJob(started.id, setJob, setJob);
  }, []);

  useEffect(() => () => stopWatching.current?.(), []);

  const convert = useCallback(async () => {
    if (!target || !canConvert || submitting.current) return;
    submitting.current = true;
    setStarting(true);
    setError(null);
    try {
      setJobFiles(files);
      attach(await api.createJob({ fileIds, target, mode, options: { ...details?.defaults, ...options }, preset }));
    } catch (thrown) {
      fail(thrown);
    } finally {
      submitting.current = false;
      setStarting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, canConvert, fileKey, mode, JSON.stringify(options), preset, attach, details, files]);

  const cancel = async () => {
    if (!job) return;
    try {
      setJob(await api.cancelJob(job.id));
    } catch (thrown) {
      fail(thrown);
    }
  };

  const retry = async () => {
    if (!job) return;
    try {
      attach(await api.retryJob(job.id));
    } catch (thrown) {
      fail(thrown);
    }
  };

  /* ------------------------------------------------------------ keyboard */

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      const typing =
        event.target instanceof HTMLElement &&
        ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);

      if (meta && event.key === "Enter") {
        event.preventDefault();
        convert();
      } else if (!busy && meta && event.key.toLowerCase() === "o") {
        event.preventDefault();
        addRef.current?.click();
      } else if (!busy && !typing && meta && event.key === "Backspace" && selectedId) {
        event.preventDefault();
        removeFile(selectedId);
      } else if (!busy && !typing && !meta && ["1", "2", "3"].includes(event.key)) {
        const next = (["each", "combined", "split"] as Mode[])[Number(event.key) - 1];
        if (next === "combined" && !plan?.canCombine) return;
        if (next === "split" && !plan?.canSplit) return;
        setMode(next);
        setJob(null);
      } else if (!typing && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
        if (!files.length) return;
        event.preventDefault();
        const at = files.findIndex((file) => file.id === selectedId);
        const next =
          event.key === "ArrowDown"
            ? Math.min(files.length - 1, at + 1)
            : Math.max(0, at <= 0 ? 0 : at - 1);
        setSelectedId(files[next].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [convert, selectedId, removeFile, files, plan, busy]);

  return (
    <div className={`workspace ${files.length === 0 && !job ? "workspace-empty" : ""}`}>
      <input
        ref={addRef}
        type="file"
        multiple
        className="sr-only"
        onChange={(event) => {
          const chosen = Array.from(event.target.files ?? []);
          if (chosen.length) addFiles(chosen);
          event.target.value = "";
        }}
      />

      <fieldset className="workspace-editor" disabled={busy}>
        <legend className="sr-only">Conversion inputs and settings</legend>
        <Queue
          files={files}
          loading={uploading}
          ordered={mode === "combined"}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onAdd={addFiles}
          onPaste={addPasted}
          onRemove={removeFile}
          onMove={moveFile}
          onClear={clearFiles}
        />

        {files.length > 0 && <Conversion
          plan={plan}
          mode={mode}
          onModeChange={(next) => {
            setMode(next);
            setJob(null);
          }}
          target={target}
          onTargetChange={chooseTarget}
          details={detailsContext === contextKey ? details : null}
          options={{ ...details?.defaults, ...options }}
          onOptionChange={changeOption}
          preset={preset}
          onPresetChange={applyPreset}
          onSavePreset={savePreset}
          onDeletePreset={deletePreset}
          fileCount={files.length}
        />}
      </fieldset>

      {(files.length > 0 || job) ? (
        <Inspector
          canConvert={canConvert}
          inputFiles={jobFiles}
          details={details}
          detailsLoading={detailsLoading || (details !== null && detailsKey !== requestKey)}
          target={target}
          fileCount={files.length}
          job={job}
          error={error}
          starting={starting}
          onConvert={convert}
          onCancel={cancel}
          onRetry={retry}
          onReset={() => setJob(null)}
          onDismissError={() => setError(null)}
        />
      ) : error && (
        <div className="welcome-error"><ErrorBlock error={error} onDismiss={() => setError(null)} /></div>
      )}
    </div>
  );
}
