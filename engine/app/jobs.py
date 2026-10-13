"""Job execution: progress, cancellation, retries and live event streaming.

A job holds one item per unit of work. Items are independent, so a single bad
file never costs the user the rest of the batch.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, db, formats, registry
from .errors import ConversionError
from .registry import InputFile, Route, RunContext

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
WARNING = "completed_with_warnings"
FAILED = "failed"
CANCELLED = "cancelled"
SKIPPED = "skipped"


class JobCancelled(Exception):
    """Raised inside a converter when the user cancels the job."""


@dataclass
class OutputFile:
    name: str
    path: Path
    size: int
    format_id: str

    def to_dict(self) -> dict:
        fmt = formats.get(self.format_id)
        return {
            "name": self.name,
            "path": str(self.path),
            "size": self.size,
            "formatId": self.format_id,
            "formatLabel": fmt.label if fmt else self.format_id,
            "previewKind": fmt.category if fmt and fmt.category in ("image", "audio", "video") else "document",
            "previewable": (bool(fmt and fmt.category in ("image", "audio", "video")) or self.format_id in (
                "png", "jpeg", "webp", "gif", "svg", "pdf", "txt", "md", "csv",
                "json", "html", "yaml", "xml", "md_table",
            )),
        }


@dataclass
class JobItem:
    id: str
    label: str
    input_ids: list[str]
    status: str = QUEUED
    progress: float = 0.0
    message: str = "Waiting"
    warnings: list[dict] = field(default_factory=list)
    error: dict | None = None
    outputs: list[OutputFile] = field(default_factory=list)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "inputIds": self.input_ids,
            "status": self.status,
            "progress": round(self.progress, 3),
            "message": self.message,
            "warnings": self.warnings,
            "error": self.error,
            "outputs": [o.to_dict() for o in self.outputs],
            "durationMs": (
                int((self.finished_at - self.started_at) * 1000)
                if self.started_at and self.finished_at else None
            ),
        }


@dataclass
class Job:
    id: str
    target: str
    mode: str
    options: dict
    preset: str | None
    route: Route
    items: list[JobItem]
    output_dir: Path
    workdir: Path
    input_names: list[str]
    cardinality: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = QUEUED
    cancelling: bool = False
    _listeners: list[asyncio.Queue] = field(default_factory=list, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)

    # -------------------------------------------------------------- reporting

    @property
    def progress(self) -> float:
        if not self.items:
            return 0.0
        return sum(i.progress for i in self.items) / len(self.items)

    def summary(self) -> dict:
        counts = {"completed": 0, "warnings": 0, "failed": 0, "cancelled": 0,
                  "skipped": 0, "pending": 0}
        for item in self.items:
            if item.status == COMPLETED:
                counts["completed"] += 1
            elif item.status == WARNING:
                counts["warnings"] += 1
            elif item.status == FAILED:
                counts["failed"] += 1
            elif item.status == CANCELLED:
                counts["cancelled"] += 1
            elif item.status == SKIPPED:
                counts["skipped"] += 1
            else:
                counts["pending"] += 1
        outputs = [o for i in self.items for o in i.outputs]
        counts["outputs"] = len(outputs)
        counts["outputBytes"] = sum(o.size for o in outputs)
        return counts

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "target": self.target,
            "targetLabel": formats.label(self.target),
            "mode": self.mode,
            "preset": self.preset,
            "options": self.options,
            "route": self.route.to_dict(),
            "progress": round(self.progress, 3),
            "items": [i.to_dict() for i in self.items],
            "summary": self.summary(),
            "createdAt": self.created_at,
            "finishedAt": self.finished_at,
            "outputDir": str(self.output_dir),
            "inputNames": self.input_names,
            "cancelling": self.cancelling,
        }

    # ---------------------------------------------------------------- events

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    def emit(self) -> None:
        payload = self.to_dict()
        for queue in list(self._listeners):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A slow reader must not stall the conversion; it will catch up
                # on the next update, which carries the full state anyway.
                pass


_JOBS: dict[str, Job] = {}
_WORKER_SEMAPHORE: asyncio.Semaphore | None = None
_WORKER_SEMAPHORE_LIMIT: int | None = None
_WORKER_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None
_FINISHED_JOB_LIMIT = 50


def _worker_semaphore() -> asyncio.Semaphore:
    global _WORKER_SEMAPHORE
    global _WORKER_SEMAPHORE_LIMIT
    global _WORKER_SEMAPHORE_LOOP

    limit = config.worker_concurrency()
    loop = asyncio.get_running_loop()
    if _WORKER_SEMAPHORE_LIMIT != limit or _WORKER_SEMAPHORE_LOOP is not loop:
        _WORKER_SEMAPHORE = asyncio.Semaphore(limit)
        _WORKER_SEMAPHORE_LIMIT = limit
        _WORKER_SEMAPHORE_LOOP = loop
    return _WORKER_SEMAPHORE


def _prune_finished_jobs() -> None:
    finished = sorted(
        (job for job in _JOBS.values() if job.finished_at is not None),
        key=lambda job: job.finished_at or 0.0,
        reverse=True,
    )
    for job in finished[_FINISHED_JOB_LIMIT:]:
        _JOBS.pop(job.id, None)


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def recent(limit: int = 20) -> list[Job]:
    return sorted(_JOBS.values(), key=lambda j: -j.created_at)[:limit]


async def _resolve_inputs(file_ids: list[str]) -> list[InputFile]:
    resolved: list[InputFile] = []
    for file_id in file_ids:
        record = await db.get_file(file_id)
        if record is None:
            raise ConversionError(
                code="file_missing",
                title="One of the files is no longer available",
                detail="It was removed from the workspace, or temporary files were "
                       "cleared since it was added.",
                suggestions=["Add the file again and start a new conversion."],
            )
        path = Path(record["path"])
        if not path.exists():
            raise ConversionError(
                code="file_missing",
                title=f"{record['name']} is no longer on disk",
                detail="The file was deleted from the workspace folder.",
                suggestions=["Add it again and start a new conversion."],
            )
        resolved.append(InputFile(
            id=record["id"], name=record["name"], path=path,
            format_id=record["formatId"] or "",
            metadata=record["detection"].get("metadata", {}),
        ))
    return resolved


async def create(
    *,
    file_ids: list[str],
    target: str,
    mode: str = "each",
    options: dict | None = None,
    preset: str | None = None,
) -> Job:
    inputs = await _resolve_inputs(file_ids)
    if not inputs:
        raise ConversionError(
            code="no_inputs",
            title="No files were selected",
            detail="A conversion needs at least one input file.",
            suggestions=["Add a file to the workspace, then choose an output format."],
        )

    source_formats = [i.format_id for i in inputs]
    cardinality = registry.cardinality_for_mode(mode)
    route = registry.resolve_route(source_formats, target, mode)
    if route is None:
        distinct = sorted({formats.label(f) for f in source_formats})
        raise ConversionError(
            code="unsupported_combination",
            title=(
                f"These files cannot be combined into one {formats.label(target)}"
                if mode == "combined"
                else f"These files cannot be turned into {formats.label(target)}"
            ),
            detail=(
                "No single converter handles " + ", ".join(distinct) +
                f" together as one {formats.label(target)}."
                if mode == "combined" else
                "No conversion path exists from " + ", ".join(distinct) +
                f" to {formats.label(target)}."
            ),
            suggestions=[
                "Convert each kind of file separately, then combine the results."
                if mode == "combined"
                else "Pick a different output format from the recommendations.",
                "Or bundle them into an archive, which accepts any mix of files."
                if mode == "combined" else
                "Browse everything these files can become in Format discovery.",
            ],
        )

    job_id = uuid.uuid4().hex[:12]
    output_dir = config.OUTPUT_DIR / job_id
    workdir = config.TMP_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)

    items: list[JobItem] = []
    if mode == "combined":
        items.append(JobItem(
            id=uuid.uuid4().hex[:8],
            label=f"{len(inputs)} files → one {formats.label(target)}",
            input_ids=[i.id for i in inputs],
        ))
    else:
        for item in inputs:
            # A file the route cannot serve is marked, never silently dropped.
            item_route = registry.find_route(item.format_id, target,
                                             cardinality=cardinality)
            entry = JobItem(id=uuid.uuid4().hex[:8], label=item.name,
                            input_ids=[item.id])
            if item_route is None:
                entry.status = SKIPPED
                entry.progress = 1.0
                entry.message = "Not compatible with this output"
                entry.error = {
                    "code": "incompatible",
                    "title": f"{formats.label(item.format_id)} cannot become "
                             f"{formats.label(target)}",
                    "detail": "This file was skipped so the rest of the batch could "
                              "continue.",
                    "suggestions": ["Convert it separately to a format it supports."],
                }
            items.append(entry)

    job = Job(
        id=job_id, target=target, mode=mode, options=options or {}, preset=preset,
        route=route, items=items, output_dir=output_dir, workdir=workdir,
        input_names=[i.name for i in inputs], cardinality=cardinality,
    )
    _JOBS[job_id] = job
    return job


async def start(job: Job) -> None:
    job._task = asyncio.create_task(_run(job))


async def _run_item(job: Job, item: JobItem) -> None:
    if item.status == SKIPPED:
        return

    item.status = RUNNING
    item.started_at = time.time()
    item.message = "Starting"
    job.emit()

    def progress(fraction: float, message: str) -> None:
        if job.cancelling:
            raise JobCancelled()
        item.progress = max(0.0, min(1.0, fraction))
        item.message = message
        job.emit()

    try:
        # Resolving the inputs and picking the route live inside the handler.
        #
        # They used to run above it. Both can raise — an input deleted between
        # creating the job and running it, or a route that no longer exists — and
        # the item had already been marked running by then. The exception escaped
        # every handler here, escaped `guarded`, and made the `gather` in `_run`
        # re-raise, so the code that finalises the job never ran. The item and its
        # job stayed "running" for the life of the process, with nothing to time
        # them out and nothing reporting why.
        inputs = await _resolve_inputs(item.input_ids)
        source = inputs[0].format_id
        route = (
            job.route if job.mode == "combined"
            else registry.require_route(source, job.target, cardinality=job.cardinality)
        )

        current_inputs = inputs
        step_count = len(route.steps)
        for step_index, step in enumerate(route.steps):
            step_base = step_index / step_count

            def step_progress(fraction: float, message: str, base=step_base) -> None:
                progress(base + fraction / step_count, message)

            step_out = (
                job.output_dir if step_index == step_count - 1
                else job.workdir / f"step-{step_index}-{item.id}"
            )
            step_out.mkdir(parents=True, exist_ok=True)

            ctx = RunContext(
                inputs=current_inputs,
                target=step.target,
                options=job.options,
                workdir=job.workdir,
                outdir=step_out,
                progress=step_progress,
            )
            produced = await step.converter.run(ctx)
            item.warnings.extend(ctx.warnings)

            if step_index < step_count - 1:
                current_inputs = [
                    InputFile(id=uuid.uuid4().hex[:8], name=path.name, path=path,
                              format_id=step.target)
                    for path in produced
                ]
            else:
                item.outputs = [
                    OutputFile(name=path.name, path=path,
                               size=path.stat().st_size if path.exists() else 0,
                               format_id=step.target)
                    for path in produced if path.exists()
                ]

        item.progress = 1.0
        has_warnings = any(w.get("level") == "warning" for w in item.warnings)
        item.status = WARNING if has_warnings else COMPLETED
        item.message = (
            "Converted with warnings" if has_warnings else "Converted"
        )

    except JobCancelled:
        item.status = CANCELLED
        item.message = "Cancelled"
        item.progress = 0.0
    except ConversionError as exc:
        item.status = FAILED
        item.progress = 1.0
        item.message = exc.title
        item.error = exc.to_dict()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, never swallowed
        item.status = FAILED
        item.progress = 1.0
        item.message = "This file could not be converted"
        item.error = {
            "code": "unexpected",
            "title": "This file could not be converted",
            "detail": (
                f"An unexpected problem occurred while processing {item.label}: "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            ),
            "suggestions": [
                "Try a different output format or preset.",
                "If the file opens correctly elsewhere, re-export it and try again.",
            ],
        }
    finally:
        item.finished_at = time.time()
        job.emit()


async def _run(job: Job) -> None:
    job.status = RUNNING
    job.emit()

    semaphore = _worker_semaphore()

    async def guarded(item: JobItem) -> None:
        async with semaphore:
            if job.cancelling:
                if item.status == QUEUED:
                    item.status = CANCELLED
                    item.message = "Cancelled"
                    job.emit()
                return
            try:
                await _run_item(job, item)
            except Exception as exc:  # noqa: BLE001 - one item must not end the job
                # A second line of defence behind _run_item's own handler. Whatever
                # gets past it stops here, because `gather` below re-raises the
                # first exception it sees and would abandon every other item in the
                # batch along with the job's own finalisation.
                item.status = FAILED
                item.finished_at = time.time()
                item.message = "This file could not be converted"
                item.error = {
                    "code": "unexpected",
                    "title": "This file could not be converted",
                    "detail": (
                        f"An unexpected problem occurred while processing "
                        f"{item.label}: {type(exc).__name__}: {str(exc)[:300]}"
                    ),
                    "suggestions": [
                        "Try a different output format or preset.",
                        "If the file opens correctly elsewhere, re-export it and "
                        "try again.",
                    ],
                }
                job.emit()

    pending = [i for i in job.items if i.status == QUEUED]
    if job.mode == "combined":
        # Combining is one unit of work; running it alongside itself is meaningless.
        for item in pending:
            await guarded(item)
    else:
        await asyncio.gather(*(guarded(i) for i in pending))

    summary = job.summary()
    if job.cancelling:
        job.status = CANCELLED
    elif summary["failed"] and not (summary["completed"] or summary["warnings"]):
        job.status = FAILED
    elif summary["failed"] or summary["warnings"] or summary["skipped"]:
        job.status = WARNING
    else:
        job.status = COMPLETED
    job.finished_at = time.time()
    job.emit()
    _prune_finished_jobs()

    await db.record_job({
        "id": job.id,
        "createdAt": job.created_at,
        "finishedAt": job.finished_at,
        "status": job.status,
        "target": job.target,
        "mode": job.mode,
        "preset": job.preset,
        "options": job.options,
        "inputs": [
            {"id": i.input_ids, "label": i.label} for i in job.items
        ],
        "route": job.route.to_dict(),
        "summary": summary,
        "outputDir": str(job.output_dir),
    })

    if await db.get_setting("autoCleanTemp"):
        shutil.rmtree(job.workdir, ignore_errors=True)


async def cancel(job: Job) -> None:
    job.cancelling = True
    job.emit()


async def retry(job: Job, item_ids: list[str] | None = None) -> None:
    """Re-queue failed items and run them again, keeping successful results."""
    targets = [
        i for i in job.items
        if i.status in (FAILED, CANCELLED)
        and (item_ids is None or i.id in item_ids)
    ]
    if not targets:
        return
    job.cancelling = False
    for item in targets:
        item.status = QUEUED
        item.progress = 0.0
        item.message = "Waiting"
        item.error = None
        item.warnings = []
        item.outputs = []
        item.started_at = None
        item.finished_at = None
    job.status = RUNNING
    job.finished_at = None
    job.workdir.mkdir(parents=True, exist_ok=True)
    job.emit()
    job._task = asyncio.create_task(_run(job))


def forget(job_id: str) -> None:
    _JOBS.pop(job_id, None)
