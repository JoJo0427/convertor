"""The conversion engine's HTTP surface."""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import (capabilities, config, converters, db, detect, engines, formats,
               installer, jobs, packs, registry)
from .errors import ConversionError


async def _exit_with_parent(pid: int) -> None:
    """Shut down when the process that started us is gone.

    In the desktop app the engine is a child of the window, and the window is
    supposed to kill it on close. That works for a graceful quit and fails for
    every other ending — SIGKILL, a crash, logging out, Force Quit — because a
    dying parent runs no cleanup code. The result is an orphaned Python holding a
    port and a few hundred megabytes, invisible to the person who closed the
    window, and a fresh one every launch.

    So the child takes responsibility for its own lifetime rather than trusting
    the parent to end it.
    """
    while True:
        await asyncio.sleep(2)
        if not _pid_alive(pid):
            # os._exit, not sys.exit: there is no one left to serve, and a clean
            # shutdown would wait on in-flight requests that can never be read.
            print(f"parent process {pid} is gone; shutting down", flush=True)
            os._exit(0)


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x0010_0000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by someone else. Not our parent, but not gone either.
        return True
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init()
    # Installed packs have to be on sys.path before anything asks whether their
    # modules import, because that question is how route availability is decided.
    activated = packs.activate_site_dirs()
    if activated:
        print(f"packs: {len(activated)} site directories activated", flush=True)
    # Probe the engines before registering converters: capability decides what
    # the product offers, so it has to be known first.
    await capabilities.discover()
    converters.load()
    await _clean_stale_temp()

    watchdog: asyncio.Task | None = None
    parent = os.environ.get("CONVERTOR_PARENT_PID")
    if parent and parent.isdigit():
        watchdog = asyncio.create_task(_exit_with_parent(int(parent)))

    yield

    if watchdog is not None:
        watchdog.cancel()


app = FastAPI(title="Convertor engine", version=config.VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ConversionError)
async def _conversion_error_handler(request: Request, exc: ConversionError):
    return JSONResponse(status_code=422, content={"error": exc.to_dict()})


def _fail(status: int, code: str, title: str, detail: str,
          suggestions: list[str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "title": title, "detail": detail,
                          "suggestions": suggestions or []}},
    )


def _safe_output_path(raw: str) -> Path:
    """Only ever serve files that genuinely live inside the workspace."""
    path = Path(raw).resolve()
    for root in (config.OUTPUT_DIR.resolve(), config.INBOX_DIR.resolve()):
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            return path
        raise _fail(404, "not_found", "That file no longer exists",
                    "It may have been cleared from the workspace.",
                    ["Run the conversion again to recreate it."])
    raise _fail(403, "outside_workspace", "That path is outside the workspace",
                "For safety the engine only serves files it created.")


# --------------------------------------------------------------------- system


@app.get("/api/system")
async def system() -> dict:
    from .converters import intelligent

    return {
        "version": config.VERSION,
        "engines": engines.status(),
        "intelligentWorkflows": {
            "available": intelligent.available(),
            "model": config.ANTHROPIC_MODEL if intelligent.available() else None,
        },
        "workspace": {
            "root": str(config.WORKSPACE_ROOT),
            "inbox": str(config.INBOX_DIR),
            "output": str(config.OUTPUT_DIR),
            "temp": str(config.TMP_DIR),
        },
        "limits": {"maxUploadBytes": config.MAX_UPLOAD_BYTES},
        "build": config.BUILD_KIND,
        "packs": packs.summary(),
        "converterCount": len(registry.all_converters()),
        "formatCount": len(formats.all_formats()),
        "curatedFormatCount": len(formats.curated()),
        "ffmpeg": capabilities.CAPS.summary(),
    }


@app.get("/api/packs")
async def pack_list() -> dict:
    return packs.summary()


def _pack_or_404(pack_id: str):
    pack = packs.BY_ID.get(pack_id)
    if pack is None:
        raise _fail(404, "not_found", "There is no such pack",
                    f"{pack_id!r} is not one of this build's engine packs.",
                    ["Reload the page to see the current list."])
    return pack


@app.post("/api/packs/{pack_id}/install")
async def pack_install(pack_id: str) -> dict:
    """Download and install one pack.

    Only ever reached from a click: the engine never starts an install on its
    own, which is the whole point of a tool that promises to work offline.
    """
    pack = _pack_or_404(pack_id)
    try:
        state = installer.start(pack)
    except ConversionError as error:
        raise HTTPException(status_code=409, detail={"error": error.to_dict()})
    return {"pack": pack.to_dict(), "job": state}


@app.post("/api/packs/{pack_id}/cancel")
async def pack_cancel(pack_id: str) -> dict:
    pack = _pack_or_404(pack_id)
    return {"pack": pack.to_dict(), "job": installer.cancel(pack_id)}


@app.delete("/api/packs/{pack_id}")
async def pack_remove(pack_id: str) -> dict:
    pack = _pack_or_404(pack_id)
    if not pack.root.exists():
        raise _fail(409, "pack_not_installed",
                    f"{pack.label} was not installed by Convertor",
                    "There is nothing in the workspace to remove. If this engine "
                    "is available, it came from your system rather than a pack.",
                    ["Nothing to do."])
    installer.remove(pack)
    # Routes that depended on it must stop claiming to work, and a converter
    # whose format set was decided at declaration time has to be re-declared.
    converters.load(force=True)
    return {"pack": pack.to_dict()}


@app.get("/api/packs/{pack_id}/events")
async def pack_events(pack_id: str):
    """Stream one install's progress.

    Polled rather than pushed: the installer runs in a thread and reports state
    through a lock, and a queue handed across that boundary would buy nothing
    over reading the state it already maintains.
    """
    _pack_or_404(pack_id)

    async def stream():
        previous: dict | None = None
        idle = 0.0
        while True:
            current = await installer.wait_for_change(pack_id, previous)
            if current != previous:
                previous = current
                idle = 0.0
                yield f"data: {json.dumps(current)}\n\n"
                if current and current["status"] != "running":
                    break
            else:
                idle += 1.0
                yield ": keep-alive\n\n"
                # Nothing is installing and nothing started: do not hold the
                # connection open forever waiting for something to report.
                if current is None and idle >= 20:
                    break

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------- files


async def _register_file(name: str, path: Path) -> dict:
    detection = await detect.detect(path, name)
    record = {
        "id": path.parent.name,
        "name": name,
        "path": str(path),
        "formatId": detection.format_id,
        "size": path.stat().st_size,
        "detection": {
            "formatId": detection.format_id,
            "confidence": detection.confidence,
            "metadata": detection.metadata,
            "notices": detection.notices,
            "preview": detection.preview,
        },
    }
    await db.save_file(record)
    return _file_payload(record)


def _file_payload(record: dict) -> dict:
    fmt = formats.get(record["formatId"] or "")
    detection = record["detection"]
    return {
        "id": record["id"],
        "name": record["name"],
        "size": record["size"],
        "formatId": record["formatId"],
        "formatLabel": fmt.label if fmt else "Unrecognised",
        "category": fmt.category if fmt else None,
        "categoryLabel": formats.CATEGORY_LABELS.get(fmt.category, "") if fmt else "",
        "summary": fmt.summary if fmt else "",
        "confidence": detection.get("confidence", "unknown"),
        "metadata": detection.get("metadata", {}),
        "notices": detection.get("notices", []),
        "preview": detection.get("preview"),
        "convertible": bool(record["formatId"]),
    }


@app.post("/api/files")
async def upload(files: list[UploadFile]) -> dict:
    added: list[dict] = []
    rejected: list[dict] = []

    for upload_file in files:
        name = Path(upload_file.filename or "file").name
        file_id = uuid.uuid4().hex[:12]
        folder = config.INBOX_DIR / file_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name

        written = 0
        try:
            with path.open("wb") as handle:
                while chunk := await upload_file.read(1024 * 1024):
                    written += len(chunk)
                    if written > config.MAX_UPLOAD_BYTES:
                        raise ValueError("too_large")
                    handle.write(chunk)
        except ValueError:
            shutil.rmtree(folder, ignore_errors=True)
            rejected.append({
                "name": name,
                "error": {
                    "code": "too_large",
                    "title": f"{name} is too large",
                    "detail": f"The limit is "
                              f"{config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB per file.",
                    "suggestions": [
                        "Raise CONVERTOR_MAX_UPLOAD_MB in your .env and restart.",
                    ],
                },
            })
            continue
        except OSError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            rejected.append({
                "name": name,
                "error": {
                    "code": "write_failed",
                    "title": f"{name} could not be saved",
                    "detail": str(exc)[:200],
                    "suggestions": ["Check that the workspace volume has free space."],
                },
            })
            continue

        added.append(await _register_file(name, path))

    return {"files": added, "rejected": rejected}


@app.post("/api/files/paste")
async def paste(payload: dict = Body(...)) -> dict:
    text = str(payload.get("content", ""))
    name = Path(str(payload.get("name") or "pasted.txt")).name
    if not text.strip():
        raise _fail(400, "empty_paste", "Nothing was pasted",
                    "The pasted content was empty.",
                    ["Paste some text, or add a file instead."])
    file_id = uuid.uuid4().hex[:12]
    folder = config.INBOX_DIR / file_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return {"files": [await _register_file(name, path)], "rejected": []}


@app.get("/api/files")
async def list_files() -> dict:
    return {"files": [_file_payload(r) for r in await db.all_files()]}


@app.delete("/api/files/{file_id}")
async def remove_file(file_id: str) -> dict:
    record = await db.get_file(file_id)
    if record:
        shutil.rmtree(Path(record["path"]).parent, ignore_errors=True)
        await db.delete_file(file_id)
    return {"ok": True}


@app.get("/api/files/{file_id}/raw")
async def file_raw(file_id: str):
    record = await db.get_file(file_id)
    if record is None:
        raise _fail(404, "not_found", "That file is not in the workspace",
                    "It may have been removed.", ["Add it again."])
    path = _safe_output_path(record["path"])
    fmt = formats.get(record["formatId"] or "")
    return FileResponse(path, media_type=fmt.mime if fmt else "application/octet-stream",
                        filename=record["name"])


# ------------------------------------------------------------------- planning


@app.post("/api/plan")
async def plan(payload: dict = Body(...)) -> dict:
    """What can these inputs become, and which choices are worth showing first."""
    file_ids: list[str] = payload.get("fileIds", [])
    records = [await db.get_file(f) for f in file_ids]
    present = [r for r in records if r and r["formatId"]]
    if not present:
        return {"recommendations": [], "combine": [], "sources": [],
                "canCombine": False}

    sources = [r["formatId"] for r in present]
    each = registry.recommend(sources, mode="each")
    combine = registry.recommend(sources, mode="combined") if len(present) > 1 else []
    split = registry.recommend(sources, mode="split")

    unsupported = [r["name"] for r in records if r and not r["formatId"]]

    reachable = {r.format_id for r in each}
    recent = [
        {
            **entry,
            "label": formats.label(entry["formatId"]),
            # Carries the category so the picker can show the right glyph.
            "category": (
                formats.get(entry["formatId"]).category
                if formats.get(entry["formatId"]) else ""
            ),
        }
        for entry in await db.recent_targets(8)
        if entry["formatId"] in reachable
    ][:6]

    return {
        "intents": registry.intents_for(sources, mode="each"),
        "recent": recent,
        "sources": sources,
        "sourceLabels": [formats.label(s) for s in sources],
        "recommendations": [r.to_dict() for r in each],
        "combine": [r.to_dict() for r in combine],
        "split": [r.to_dict() for r in split],
        "canCombine": bool(combine),
        "canSplit": bool(split),
        "unsupported": unsupported,
        "mixed": len(set(sources)) > 1,
    }


@app.post("/api/plan/details")
async def plan_details(payload: dict = Body(...)) -> dict:
    """Everything needed to configure and validate one specific conversion."""
    file_ids: list[str] = payload.get("fileIds", [])
    target: str = payload.get("target", "")
    mode: str = payload.get("mode", "each")
    chosen: dict = payload.get("options", {})

    records = [await db.get_file(f) for f in file_ids]
    present = [r for r in records if r and r["formatId"]]
    if not present or not target:
        raise _fail(400, "incomplete_plan", "The conversion is not fully specified",
                    "Both input files and an output format are needed.",
                    ["Add a file, then choose what to convert it into."])

    cardinality = registry.cardinality_for_mode(mode)
    sources = [r["formatId"] for r in present]
    route = registry.resolve_route(sources, target, mode)
    if route is None:
        distinct = sorted({formats.label(f) for f in sources})
        raise ConversionError(
            code="unsupported_route",
            title=(
                f"These files cannot be combined into one {formats.label(target)}"
                if mode == "combined"
                else f"{', '.join(distinct)} cannot be converted to "
                     f"{formats.label(target)}"
            ),
            detail="No converter handles this combination of inputs and output.",
            suggestions=[
                "Pick a different output format from the recommendations.",
                "Or convert each kind of file separately.",
            ],
        )

    inputs = [
        registry.InputFile(
            id=r["id"], name=r["name"], path=Path(r["path"]),
            format_id=r["formatId"], metadata=r["detection"].get("metadata", {}),
        )
        for r in present
    ]

    # A mixed batch takes several routes, so the settings shown are the union of
    # what each route accepts. Record which inputs each setting actually affects,
    # or a PDF-to-Word job silently grows an image page-size control.
    options: list[registry.Option] = []
    seen_options: dict[str, list[str]] = {}
    distinct_sources = list(dict.fromkeys(r["formatId"] for r in present))
    for source in distinct_sources:
        source_route = registry.find_route(source, target, cardinality=cardinality) or route
        for option in registry.options_for(source_route, source):
            if option.id not in seen_options:
                seen_options[option.id] = []
                options.append(option)
            seen_options[option.id].append(formats.label(source))
    defaults = {o.id: o.default for o in options}
    effective = {**defaults, **chosen}

    builtin = registry.presets_for(route, present[0]["formatId"])
    saved = await db.user_presets(target)

    # Per-file compatibility, so nothing is silently dropped later.
    compatibility = []
    for record in records:
        if record is None:
            continue
        if not record["formatId"]:
            compatibility.append({"id": record["id"], "name": record["name"],
                                  "compatible": False,
                                  "reason": "The format was not recognised"})
            continue
        if mode == "combined":
            ok = all(record["formatId"] in s.converter.inputs for s in route.steps[:1])
            reason = "" if ok else "Cannot be combined with the others into this format"
        else:
            ok = registry.find_route(record["formatId"], target,
                                     cardinality=cardinality) is not None
            reason = "" if ok else (
                f"{formats.label(record['formatId'])} has no path to "
                f"{formats.label(target)}"
            )
        compatibility.append({"id": record["id"], "name": record["name"],
                              "compatible": ok, "reason": reason})

    # Each distinct input format may travel a different path to the same output.
    # Reporting only the first would misdescribe a mixed batch.
    per_format: list[dict] = []
    if mode != "combined":
        for source in dict.fromkeys(r["formatId"] for r in present):
            source_route = registry.find_route(source, target, cardinality=cardinality)
            if source_route is None:
                continue
            count = sum(1 for r in present if r["formatId"] == source)
            per_format.append({
                "sourceId": source,
                "sourceLabel": formats.label(source),
                "fileCount": count,
                "description": source_route.describe(),
                "direct": source_route.direct,
                "steps": source_route.to_dict()["steps"],
            })

    return {
        "route": route.to_dict(),
        "plan": registry.plan_for(route, inputs, effective, present[0]["formatId"]),
        "routes": per_format,
        "options": [
            {
                **o.to_dict(),
                # Only meaningful when the batch is mixed; the UI hides it otherwise.
                "appliesTo": (
                    seen_options[o.id]
                    if len(distinct_sources) > 1
                    and len(seen_options[o.id]) < len(distinct_sources)
                    else []
                ),
            }
            for o in options
        ],
        "presets": [p.to_dict() for p in builtin] + [
            {"id": p["id"], "label": p["label"], "description": p["description"],
             "values": p["values"], "custom": True}
            for p in saved
        ],
        "warnings": _merged_warnings(present, route, inputs, effective, target,
                                     cardinality),
        "compatibility": compatibility,
        "outputEstimate": _estimate(inputs, target, mode),
        "defaults": defaults,
    }


def _merged_warnings(present, route, inputs, effective, target, cardinality) -> list[dict]:
    """Warnings for every route in the batch, de-duplicated by title."""
    merged: list[dict] = []
    seen: set[str] = set()
    for source in dict.fromkeys(r["formatId"] for r in present):
        source_route = registry.find_route(source, target, cardinality=cardinality) or route
        matching = [i for i in inputs if i.format_id == source]
        for notice in registry.warnings_for(source_route, matching, effective):
            if notice["title"] not in seen:
                seen.add(notice["title"])
                merged.append(notice)
    order = {"error": 0, "warning": 1, "info": 2}
    merged.sort(key=lambda n: order.get(n["level"], 3))
    return merged


def _estimate(inputs: list[registry.InputFile], target: str, mode: str) -> dict:
    """A conservative description of what the user will get back."""
    fmt = formats.get(target)
    label = fmt.label if fmt else target
    if mode == "combined":
        count, shape = 1, f"one {label} file"
    elif mode == "split":
        pages = sum(int(i.metadata.get("pages") or 0) for i in inputs)
        count = pages or len(inputs)
        shape = (
            f"{pages} {label} files, one per page" if pages
            else f"several {label} files per input"
        )
    elif target == "files":
        total = sum(int(i.metadata.get("entryCount") or 0) for i in inputs)
        count = total or len(inputs)
        shape = f"{total} extracted files" if total else "the archive contents"
    else:
        pages = sum(int(i.metadata.get("pages") or 0) for i in inputs)
        if pages and fmt and fmt.category == formats.IMAGE:
            count, shape = pages, f"{pages} images, one per page"
        else:
            count = len(inputs)
            shape = f"{count} {fmt.label if fmt else target} file" + ("s" if count > 1 else "")
    return {"fileCount": count, "description": shape}


# ----------------------------------------------------------------------- jobs


@app.post("/api/jobs")
async def create_job(payload: dict = Body(...)) -> dict:
    job = await jobs.create(
        file_ids=payload.get("fileIds", []),
        target=payload.get("target", ""),
        mode=payload.get("mode", "each"),
        options=payload.get("options", {}),
        preset=payload.get("preset"),
    )
    await jobs.start(job)
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
async def job_state(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise _fail(404, "not_found", "That conversion is no longer in memory",
                    "Jobs are kept for the life of the engine process.",
                    ["Check the history for its record."])
    return job.to_dict()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise _fail(404, "not_found", "That conversion is no longer in memory",
                    "It may have finished before this page connected.",
                    ["Check the history for its record."])

    async def stream():
        queue = job.subscribe()
        try:
            yield f"data: {json.dumps(job.to_dict())}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    # A comment frame keeps proxies from closing an idle stream.
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(payload)}\n\n"
                if payload["status"] in (jobs.COMPLETED, jobs.WARNING, jobs.FAILED,
                                         jobs.CANCELLED):
                    break
        finally:
            job.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise _fail(404, "not_found", "That conversion is no longer running", "", [])
    await jobs.cancel(job)
    return job.to_dict()


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, payload: dict = Body(default={})) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise _fail(404, "not_found", "That conversion is no longer in memory", "", [])
    await jobs.retry(job, payload.get("itemIds"))
    return job.to_dict()


@app.get("/api/jobs/{job_id}/download")
async def download_all(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise _fail(404, "not_found", "That conversion is no longer in memory", "", [])
    outputs = [o for item in job.items for o in item.outputs if o.path.exists()]
    if not outputs:
        raise _fail(404, "no_outputs", "There is nothing to download",
                    "This conversion produced no output files.",
                    ["Check the failed items and retry them."])
    if len(outputs) == 1:
        return FileResponse(outputs[0].path, filename=outputs[0].name)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for output in outputs:
            archive.write(output.path, arcname=output.name)
    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="converted-{job_id}.zip"'},
    )


@app.get("/api/outputs")
async def output_file(path: str = Query(...), download: bool = Query(False)):
    resolved = _safe_output_path(path)
    format_id = formats.from_extension(resolved.name)
    fmt = formats.get(format_id or "")
    return FileResponse(
        resolved,
        media_type=fmt.mime if fmt else "application/octet-stream",
        filename=resolved.name if download else None,
    )


# -------------------------------------------------------------------- formats


@app.get("/api/formats")
async def format_catalog(q: str = Query("")) -> dict:
    matches = formats.search(q)
    return {
        "formats": [
            {
                "id": f.id, "label": f.label, "category": f.category,
                "categoryLabel": formats.CATEGORY_LABELS[f.category],
                "extensions": list(f.extensions), "summary": f.summary,
                "commonUses": list(f.common_uses), "notes": list(f.notes),
            }
            for f in matches
        ],
        "categories": [
            {"id": key, "label": label}
            for key, label in formats.CATEGORY_LABELS.items()
        ],
    }


@app.get("/api/formats/{format_id}")
async def format_detail(format_id: str) -> dict:
    fmt = formats.get(format_id)
    if fmt is None:
        raise _fail(404, "unknown_format", "That format is not in the catalog",
                    f"Nothing is registered under '{format_id}'.",
                    ["Browse the full list in Format discovery."])

    outbound = registry.reachable_from(format_id)
    inbound = registry.sources_for(format_id)
    return {
        "id": fmt.id, "label": fmt.label, "category": fmt.category,
        "categoryLabel": formats.CATEGORY_LABELS[fmt.category],
        "extensions": list(fmt.extensions), "mime": fmt.mime,
        "summary": fmt.summary, "commonUses": list(fmt.common_uses),
        "notes": list(fmt.notes),
        "convertsTo": [
            {
                "id": target, "label": formats.label(target),
                "direct": route.direct, "available": route.available,
                "discovered": bool(getattr(formats.get(target), "discovered", False)),
                "via": route.describe() if not route.direct else "",
                "category": (formats.get(target).category if formats.get(target) else ""),
            }
            # Direct routes first, then curated formats before the ones
            # discovered from FFmpeg — otherwise "raw Dirac" outranks PNG and
            # the list stops being useful for discovery.
            for target, route in sorted(
                outbound.items(),
                key=lambda kv: (
                    not kv[1].direct,
                    bool(getattr(formats.get(kv[0]), "discovered", False)),
                    formats.label(kv[0]).lower(),
                ),
            )
        ],
        "convertsFrom": [
            {"id": source, "label": formats.label(source),
             "category": (formats.get(source).category if formats.get(source) else ""),
             "discovered": bool(getattr(formats.get(source), "discovered", False))}
            for source in sorted(
                inbound,
                key=lambda f: (
                    bool(getattr(formats.get(f), "discovered", False)),
                    formats.label(f).lower(),
                ),
            )
        ],
    }


@app.get("/api/converters")
async def converter_list() -> dict:
    return {
        "converters": [
            {
                "id": c.id, "label": c.label, "description": c.description,
                "inputs": sorted(c.inputs), "outputs": sorted(c.outputs),
                "cardinality": c.cardinality, "available": c.available,
                "missingEngines": c.missing_engines,
                "engines": list(c.engines),
            }
            for c in sorted(registry.all_converters(), key=lambda c: c.label)
        ]
    }


# -------------------------------------------------------------------- presets


@app.get("/api/presets")
async def list_presets(target: str = Query("")) -> dict:
    return {"presets": await db.user_presets(target or None)}


@app.post("/api/presets")
async def create_preset(payload: dict = Body(...)) -> dict:
    label = str(payload.get("label", "")).strip()
    target = str(payload.get("targetFormat", "")).strip()
    if not label or not target:
        raise _fail(400, "invalid_preset", "This preset needs a name",
                    "A preset must have a name and an output format.",
                    ["Give the preset a short, memorable name."])
    preset = {
        "id": uuid.uuid4().hex[:10],
        "label": label,
        "description": str(payload.get("description", "")),
        "sourceFormat": payload.get("sourceFormat"),
        "targetFormat": target,
        "values": payload.get("values", {}),
    }
    await db.save_preset(preset)
    return {"preset": {**preset, "custom": True}}


@app.delete("/api/presets/{preset_id}")
async def remove_preset(preset_id: str) -> dict:
    await db.delete_preset(preset_id)
    return {"ok": True}


# -------------------------------------------------------------------- history


@app.get("/api/history")
async def history(q: str = Query(""), target: str = Query(""),
                  status: str = Query("")) -> dict:
    records = await db.history(query=q, target=target, status=status)
    for record in records:
        record["targetLabel"] = formats.label(record["target"])
        record["live"] = jobs.get(record["id"]) is not None
        output_dir = Path(record["outputDir"]) if record["outputDir"] else None
        record["outputsExist"] = bool(output_dir and output_dir.exists()
                                      and any(output_dir.iterdir()))
    return {"history": records, "enabled": await db.get_setting("keepHistory")}


@app.delete("/api/history/{job_id}")
async def delete_history(job_id: str, purge: bool = Query(False)) -> dict:
    record = await db.get_job_record(job_id)
    if record and purge and record["outputDir"]:
        shutil.rmtree(record["outputDir"], ignore_errors=True)
    await db.delete_job_record(job_id)
    return {"ok": True}


@app.delete("/api/history")
async def clear_history(purge: bool = Query(False)) -> dict:
    if purge:
        for record in await db.history(limit=10_000):
            if record["outputDir"]:
                shutil.rmtree(record["outputDir"], ignore_errors=True)
    removed = await db.clear_history()
    return {"removed": removed}


@app.post("/api/history/{job_id}/rerun")
async def rerun(job_id: str, payload: dict = Body(default={})) -> dict:
    record = await db.get_job_record(job_id)
    if record is None:
        raise _fail(404, "not_found", "That history entry is gone",
                    "It may have been deleted.", ["Start a new conversion instead."])
    file_ids = [
        file_id
        for entry in record["inputs"]
        for file_id in (entry["id"] if isinstance(entry["id"], list) else [entry["id"]])
    ]
    missing = [f for f in file_ids if await db.get_file(f) is None]
    if missing:
        raise ConversionError(
            code="inputs_gone",
            title="The original files are no longer in the workspace",
            detail="A previous conversion can only be re-run while its inputs are "
                   "still here. They were removed or cleared.",
            suggestions=[
                "Add the files again — the settings from this run are still saved "
                "and will be applied.",
            ],
        )
    job = await jobs.create(
        file_ids=file_ids,
        target=payload.get("target") or record["target"],
        mode=payload.get("mode") or record["mode"],
        options={**record["options"], **payload.get("options", {})},
        preset=payload.get("preset", record["preset"]),
    )
    await jobs.start(job)
    return job.to_dict()


# ------------------------------------------------------------------- settings


@app.get("/api/settings")
async def read_settings() -> dict:
    return {"settings": await db.get_settings()}


@app.patch("/api/settings")
async def write_settings(payload: dict = Body(...)) -> dict:
    return {"settings": await db.update_settings(payload)}


# ------------------------------------------------------------------- storage


def _folder_stats(path: Path) -> dict:
    total = 0
    count = 0
    if path.exists():
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
                count += 1
    return {"path": str(path), "bytes": total, "files": count}


@app.get("/api/storage")
async def storage() -> dict:
    stats = await asyncio.gather(
        asyncio.to_thread(_folder_stats, config.INBOX_DIR),
        asyncio.to_thread(_folder_stats, config.OUTPUT_DIR),
        asyncio.to_thread(_folder_stats, config.TMP_DIR),
    )
    return {
        "inbox": stats[0],
        "output": stats[1],
        "temp": stats[2],
        "database": {
            "path": str(config.DB_PATH),
            "bytes": config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0,
        },
    }


async def _clean_stale_temp() -> None:
    """Drop leftover working folders from a previous run of the engine."""
    if not config.TMP_DIR.exists():
        return
    retention = float(await db.get_setting("tempRetentionHours") or 24) * 3600
    cutoff = time.time() - retention
    for entry in config.TMP_DIR.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


@app.post("/api/storage/clean")
async def clean_storage(payload: dict = Body(default={})) -> dict:
    scopes = payload.get("scopes", ["temp"])
    removed: dict[str, int] = {}

    if "temp" in scopes:
        before = _folder_stats(config.TMP_DIR)
        shutil.rmtree(config.TMP_DIR, ignore_errors=True)
        config.TMP_DIR.mkdir(parents=True, exist_ok=True)
        removed["temp"] = before["bytes"]

    if "inbox" in scopes:
        before = _folder_stats(config.INBOX_DIR)
        shutil.rmtree(config.INBOX_DIR, ignore_errors=True)
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        await db.execute("DELETE FROM files")
        removed["inbox"] = before["bytes"]

    if "output" in scopes:
        before = _folder_stats(config.OUTPUT_DIR)
        shutil.rmtree(config.OUTPUT_DIR, ignore_errors=True)
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        removed["output"] = before["bytes"]

    return {"removed": removed, "storage": await storage()}
