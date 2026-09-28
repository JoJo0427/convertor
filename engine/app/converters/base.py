"""Shared helpers for converter modules: output naming, presets, common options."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import formats
from ..registry import Choice, InputFile, Option, Preset, RunContext

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

NAMING_OPTION = Option(
    id="naming",
    label="Output file names",
    type="text",
    default="{name}.{ext}",
    help=(
        "Tokens: {name} original name, {ext} new extension, {format} output format, "
        "{index} position in the batch, {date} today's date."
    ),
    advanced=True,
)

OVERWRITE_OPTION = Option(
    id="on_conflict",
    label="If a file with that name already exists",
    type="select",
    default="number",
    help="How to handle a name collision in the output folder.",
    choices=(
        Choice("number", "Add a number", "report.pdf becomes report (2).pdf"),
        Choice("overwrite", "Replace it", "The existing file is overwritten"),
        Choice("skip", "Skip the file", "Leave the existing file untouched"),
    ),
    advanced=True,
)

METADATA_OPTION = Option(
    id="keep_metadata",
    label="Keep metadata",
    type="boolean",
    default=True,
    help="Preserve author, timestamps and camera information where the output format supports it.",
    advanced=True,
)


def safe_stem(name: str) -> str:
    stem = Path(name).name
    for ext in sorted(
        (e for f in formats.all_formats() for e in f.extensions), key=len, reverse=True
    ):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    stem = _UNSAFE.sub("_", stem).strip(" .")
    return stem or "output"


def resolve_name(
    ctx: RunContext,
    source_name: str,
    target: str,
    *,
    index: int = 1,
    suffix: str = "",
) -> str:
    pattern = str(ctx.option("naming", "{name}.{ext}") or "{name}.{ext}")
    ext = formats.extension_for(target).lstrip(".")
    stem = safe_stem(source_name)
    if suffix:
        stem = f"{stem}{suffix}"
    rendered = (
        pattern.replace("{name}", stem)
        .replace("{ext}", ext)
        .replace("{format}", target)
        .replace("{index}", str(index))
        .replace("{date}", datetime.now().strftime("%Y-%m-%d"))
    )
    rendered = _UNSAFE.sub("_", rendered).strip()
    if not rendered:
        rendered = f"{stem}.{ext}"
    if not rendered.lower().endswith(f".{ext.lower()}"):
        rendered = f"{rendered}.{ext}"
    return rendered


def target_path(
    ctx: RunContext,
    source_name: str,
    target: str,
    *,
    index: int = 1,
    suffix: str = "",
) -> Path:
    """Resolve the final output path, honouring the naming and conflict rules."""
    name = resolve_name(ctx, source_name, target, index=index, suffix=suffix)
    path = ctx.outdir / name
    policy = str(ctx.option("on_conflict", "number"))
    if not path.exists() or policy == "overwrite":
        return path
    if policy == "skip":
        ctx.note(
            "An existing file was kept",
            f"{name} already exists in the output folder and was left untouched.",
        )
        return path
    stem, dot, ext = name.rpartition(".")
    counter = 2
    while path.exists():
        path = ctx.outdir / f"{stem} ({counter}){dot}{ext}"
        counter += 1
    return path


def single_input(ctx: RunContext) -> InputFile:
    return ctx.inputs[0]


def quality_presets(kind: str) -> list[Preset]:
    """The plain-language presets shared by the quality-bearing converters."""
    if kind == "image":
        return [
            Preset("best", "Best quality",
                   "Largest files, closest to the original. Use when the image will be edited again.",
                   {"quality": 100, "resize_mode": "none", "keep_metadata": True}),
            Preset("balanced", "Balanced",
                   "A good default: clearly sharp, noticeably smaller than the original.",
                   {"quality": 85, "resize_mode": "none", "keep_metadata": True}),
            Preset("web", "Web optimised",
                   "Sized and compressed for fast web pages, metadata stripped.",
                   {"quality": 78, "resize_mode": "fit", "max_width": 1920,
                    "max_height": 1920, "keep_metadata": False}),
            Preset("small", "Smallest file size",
                   "Aggressive compression. Fine for previews, visible on close inspection.",
                   {"quality": 60, "resize_mode": "fit", "max_width": 1280,
                    "max_height": 1280, "keep_metadata": False}),
            Preset("print", "Print optimised",
                   "Full resolution at 300 DPI, metadata preserved.",
                   {"quality": 96, "resize_mode": "none", "dpi": 300,
                    "keep_metadata": True}),
        ]
    if kind == "media":
        return [
            Preset("best", "Best quality",
                   "Near-transparent quality at a larger file size.",
                   {"quality_mode": "high", "keep_metadata": True}),
            Preset("balanced", "Balanced",
                   "The usual choice: good quality, reasonable size.",
                   {"quality_mode": "balanced", "keep_metadata": True}),
            Preset("small", "Smallest file size",
                   "Heavier compression for sharing and storage.",
                   {"quality_mode": "small", "keep_metadata": False}),
            Preset("compatible", "Maximum compatibility",
                   "Widely supported codecs, so the file plays almost anywhere.",
                   {"quality_mode": "balanced", "compatibility": True}),
        ]
    if kind == "document":
        return [
            Preset("layout", "Preserve original layout",
                   "Keep pages looking as close to the source as possible.",
                   {"fidelity": "layout", "keep_metadata": True}),
            Preset("editable", "Editable output",
                   "Favour clean, editable structure over pixel-exact layout.",
                   {"fidelity": "structure", "keep_metadata": True}),
            Preset("archive", "Archive quality",
                   "Maximum fidelity and embedded fonts for long-term storage.",
                   {"fidelity": "layout", "keep_metadata": True, "embed_fonts": True}),
            Preset("small", "Smallest file size",
                   "Downsample images inside the document to reduce size.",
                   {"fidelity": "layout", "compress_images": True, "keep_metadata": False}),
        ]
    return []
