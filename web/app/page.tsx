"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, watchJob } from "@/lib/api";
import type { EngineError, InputFile, Job, Plan, PlanDetails } from "@/lib/types";
import { Queue } from "@/components/Queue";
import { Conversion, type Mode } from "@/components/Conversion";
import { Inspector } from "@/components/Inspector";

export default function WorkspacePage() {
  const [files, setFiles] = useState<InputFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>("each");
  const [plan, setPlan] = useState<Plan | null>(null);

  const [target, setTarget] = useState<string | null>(null);
  const [details, setDetails] = useState<PlanDetails | null>(null);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [options, setOptions] = useState<Record<string, unknown>>({});
  const [preset, setPreset] = useState<string | null>(null);

  const [job, setJob] = useState<Job | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<EngineError | null>(null);

  const stopWatching = useRef<(() => void) | null>(null);
  const addRef = useRef<HTMLInputElement | null>(null);
  const fileIds = files.map((file) => file.id);
  const fileKey = fileIds.join(",");

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
      .catch(() => undefined);
  }, []);

  const addFiles = useCallback(async (incoming: File[]) => {
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
  }, []);

  const addPasted = useCallback(async (text: string, name: string) => {
    setUploading(true);
    try {
      const result = await api.paste(text, name);
      setFiles((current) => [...current, ...result.files]);
    } catch (thrown) {
      fail(thrown);
    } finally {
      setUploading(false);
    }
  }, []);

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
    setFiles((current) => {
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }, []);

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
          setOptions((current) => ({ ...data.defaults, ...current }));
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
  }, [target, mode, fileKey, JSON.stringify(options)]);

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
        values: options,
      });
      setDetails(await api.planDetails(fileIds, target, mode, options));
    } catch (thrown) {
      fail(thrown);
    }
  };

  const deletePreset = async (id: string) => {
    try {
      await api.deletePreset(id);
      if (target) setDetails(await api.planDetails(fileIds, target, mode, options));
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
    if (!target || starting) return;
    setStarting(true);
    setError(null);
    try {
      attach(await api.createJob({ fileIds, target, mode, options, preset }));
    } catch (thrown) {
      fail(thrown);
    } finally {
      setStarting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, starting, fileKey, mode, JSON.stringify(options), preset, attach]);

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
      } else if (meta && event.key.toLowerCase() === "o") {
        event.preventDefault();
        addRef.current?.click();
      } else if (meta && event.key === "Backspace" && selectedId) {
        event.preventDefault();
        removeFile(selectedId);
      } else if (!typing && !meta && ["1", "2", "3"].includes(event.key)) {
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
  }, [convert, selectedId, removeFile, files, plan]);

  return (
    <div className="workspace grid-cols-1 lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)_340px]">
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

      <Conversion
        plan={plan}
        mode={mode}
        onModeChange={(next) => {
          setMode(next);
          setJob(null);
        }}
        target={target}
        onTargetChange={chooseTarget}
        details={details}
        options={options}
        onOptionChange={changeOption}
        preset={preset}
        onPresetChange={applyPreset}
        onSavePreset={savePreset}
        onDeletePreset={deletePreset}
        fileCount={files.length}
      />

      <div className="hidden h-full min-h-0 xl:block">
        <Inspector
          details={details}
          detailsLoading={detailsLoading}
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
      </div>
    </div>
  );
}
