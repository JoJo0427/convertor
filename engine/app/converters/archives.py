"""Archive extraction and creation."""

from __future__ import annotations

import asyncio
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Sequence

from .. import engines, formats
from ..errors import ConversionError, corrupt_file, password_protected
from ..registry import (
    MANY_TO_ONE,
    ONE_TO_MANY,
    Choice,
    Converter,
    InputFile,
    Option,
    RunContext,
    register,
)
from .base import NAMING_OPTION, OVERWRITE_OPTION, safe_stem

READABLE = ("zip", "tar", "targz", "tarbz2", "tarxz", "sevenzip", "rar")
WRITABLE = ("zip", "tar", "targz", "tarbz2", "tarxz", "sevenzip")


def _is_unsafe(name: str) -> bool:
    """Reject entries that would escape the extraction folder."""
    path = Path(name)
    return path.is_absolute() or ".." in path.parts


async def _extract(ctx: RunContext) -> list[Path]:
    outputs: list[Path] = []
    total = len(ctx.inputs)
    flatten = bool(ctx.option("flatten", False))
    keep_folder = bool(ctx.option("subfolder", True))

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Opening {item.name}")
        destination = ctx.outdir / safe_stem(item.name) if keep_folder else ctx.outdir
        destination.mkdir(parents=True, exist_ok=True)

        def work() -> list[Path]:
            written: list[Path] = []
            skipped: list[str] = []

            if item.format_id == "zip":
                try:
                    with zipfile.ZipFile(item.path) as zf:
                        members = [m for m in zf.infolist() if not m.is_dir()]
                        for member in members:
                            if _is_unsafe(member.filename):
                                skipped.append(member.filename)
                                continue
                            name = (
                                Path(member.filename).name if flatten
                                else member.filename
                            )
                            out = destination / name
                            out.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                with zf.open(member) as src, out.open("wb") as dst:
                                    shutil.copyfileobj(src, dst)
                            except RuntimeError as exc:
                                if "password" in str(exc).lower():
                                    raise password_protected(item.name)
                                raise
                            written.append(out)
                except zipfile.BadZipFile:
                    raise corrupt_file(item.name, "The archive index could not be read.")

            elif item.format_id in ("tar", "targz", "tarbz2", "tarxz"):
                try:
                    with tarfile.open(item.path) as tf:
                        for member in tf.getmembers():
                            if not member.isfile():
                                continue
                            if _is_unsafe(member.name):
                                skipped.append(member.name)
                                continue
                            name = Path(member.name).name if flatten else member.name
                            out = destination / name
                            out.parent.mkdir(parents=True, exist_ok=True)
                            extracted = tf.extractfile(member)
                            if extracted is None:
                                continue
                            with extracted as src, out.open("wb") as dst:
                                shutil.copyfileobj(src, dst)
                            written.append(out)
                except tarfile.TarError as exc:
                    raise corrupt_file(item.name, str(exc)[:200])
            else:
                return []

            if skipped:
                ctx.warn(
                    "Some entries were skipped for safety",
                    f"{len(skipped)} entries used absolute or parent-directory paths "
                    "that would write outside the output folder.",
                )
            return written

        if item.format_id in ("sevenzip", "rar"):
            engines.require("sevenzip")
            await engines.run(
                "sevenzip",
                ["x", str(item.path), f"-o{destination}", "-y",
                 *(["-spe"] if not flatten else [])],
                input_name=item.name,
            )
            written = [p for p in destination.rglob("*") if p.is_file()]
        else:
            written = await asyncio.to_thread(work)

        if not written:
            raise ConversionError(
                code="empty_archive",
                title="This archive contains no files",
                detail=f"{item.name} opened correctly but held no extractable entries.",
                suggestions=["Check the archive in another tool to confirm its contents."],
            )
        outputs.extend(written)
        ctx.note(
            f"Extracted {item.name}",
            f"{len(written)} files were written to "
            f"{destination.name if keep_folder else 'the output folder'}.",
        )
        ctx.progress(index / total, f"Extracted {item.name}")
    return outputs


async def _create(ctx: RunContext) -> list[Path]:
    from .base import target_path

    stem = ctx.option("archive_name", "") or f"{safe_stem(ctx.inputs[0].name)}-archive"
    out = target_path(ctx, str(stem), ctx.target)
    total = len(ctx.inputs)
    level = int(ctx.option("compression_level", 6))

    def work() -> None:
        if ctx.target == "zip":
            mode = zipfile.ZIP_DEFLATED if level > 0 else zipfile.ZIP_STORED
            with zipfile.ZipFile(out, "w", mode, compresslevel=level or None) as zf:
                for index, item in enumerate(ctx.inputs, start=1):
                    ctx.progress((index - 1) / total, f"Adding {item.name}")
                    zf.write(item.path, arcname=item.name)
        elif ctx.target in ("tar", "targz", "tarbz2", "tarxz"):
            mode = {"tar": "w", "targz": "w:gz", "tarbz2": "w:bz2",
                    "tarxz": "w:xz"}[ctx.target]
            # xz takes a preset, gzip and bzip2 take a compression level.
            kwargs: dict = {}
            if ctx.target in ("targz", "tarbz2"):
                kwargs = {"compresslevel": max(1, level)}
            elif ctx.target == "tarxz":
                kwargs = {"preset": max(0, min(9, level))}
            with tarfile.open(out, mode, **kwargs) as tf:
                for index, item in enumerate(ctx.inputs, start=1):
                    ctx.progress((index - 1) / total, f"Adding {item.name}")
                    tf.add(item.path, arcname=item.name)

    if ctx.target == "sevenzip":
        engines.require("sevenzip")
        args = ["a", "-t7z", f"-mx={level}", str(out),
                *[str(i.path) for i in ctx.inputs]]
        await engines.run("sevenzip", args, input_name=out.name)
    else:
        await asyncio.to_thread(work)

    total_input = sum(i.path.stat().st_size for i in ctx.inputs)
    packed = out.stat().st_size
    if total_input:
        ratio = 100 - int(packed / total_input * 100)
        ctx.note(
            "Archive created",
            f"{total} files packed into one {formats.label(ctx.target)}, "
            f"{ratio}% smaller than the originals combined."
            if ratio > 0 else
            f"{total} files packed into one {formats.label(ctx.target)}.",
        )
    return [out]


def register_all() -> None:
    register(Converter(
        id="archive.extract",
        label="Extract archive",
        description="Unpack the files held inside an archive.",
        inputs=frozenset(READABLE),
        outputs=frozenset(("files",)),
        run=_extract,
        cardinality=ONE_TO_MANY,
        quality=95,
        options_for=lambda s, t: [
            Option(id="subfolder", label="Extract into a folder per archive",
                   type="boolean", default=True,
                   help="Keeps the contents of each archive separate in the output folder.",
                   advanced=False),
            Option(id="flatten", label="Ignore folders inside the archive",
                   type="boolean", default=False,
                   help="Put every file directly in the output folder, discarding the "
                        "archive's own folder structure.", advanced=False),
        ],
        warnings_for=lambda inputs, target, options: [
            *(
                [{
                    "level": "info",
                    "title": "RAR archives are extract-only",
                    "detail": "Files can be read out of a RAR archive, but new RAR "
                              "archives cannot be created.",
                }] if any(i.format_id == "rar" for i in inputs) else []
            ),
            {
                "level": "info",
                "title": "Extracted files keep their original formats",
                "detail": "Run a second conversion on the results if you also need to "
                          "change their format.",
            },
        ],
    ))

    register(Converter(
        id="archive.create",
        label="Create archive",
        description="Bundle any set of files into a single archive.",
        inputs=frozenset(f.id for f in formats.all_formats() if f.id != "files"),
        outputs=frozenset(WRITABLE),
        run=_create,
        cardinality=MANY_TO_ONE,
        quality=70,
        options_for=lambda s, t: [
            Option(id="archive_name", label="Archive name", type="text", default="",
                   help="Leave blank to name it after the first file.", advanced=False),
            Option(id="compression_level", label="Compression", type="number", default=6,
                   help="0 stores files without compressing. 9 is smallest but slowest.",
                   minimum=0, maximum=9, step=1, advanced=False),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
        warnings_for=lambda inputs, target, options: [
            *(
                [{
                    "level": "info",
                    "title": "Already-compressed files will not shrink",
                    "detail": "JPEG, MP4, MP3 and PDF files are compressed already, so "
                              "the archive will be close to their combined size.",
                }] if any(
                    i.format_id in ("jpeg", "mp4", "mp3", "png", "webp", "mkv", "pdf")
                    for i in inputs
                ) else []
            ),
        ],
    ))
