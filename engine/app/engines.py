"""External engine discovery and subprocess helpers.

Converters declare which engines they need; anything unavailable is reported to
the UI as a named, installable capability rather than surfacing as a failure
halfway through a job.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import ConversionError, engine_missing


@dataclass(frozen=True)
class Engine:
    id: str
    label: str
    binary: str
    purpose: str
    install_hint: str


# Engines bundled inside the desktop app, as opposed to arriving in a pack.
# Kept beside the table so the two cannot drift apart unnoticed.
BASE_TIER = frozenset({"ffmpeg", "ghostscript", "rsvg", "potrace"})

ENGINES: dict[str, Engine] = {
    e.id: e
    for e in (
        Engine("ffmpeg", "FFmpeg", "ffmpeg", "Audio and video conversion",
               "apt-get install ffmpeg"),
        Engine("libreoffice", "LibreOffice", "soffice",
               "Office documents: DOCX, ODT, PPTX, XLSX",
               "apt-get install libreoffice"),
        Engine("pandoc", "Pandoc", "pandoc",
               "Markup documents: Markdown, HTML, EPUB, RST",
               "apt-get install pandoc"),
        Engine("tesseract", "Tesseract OCR", "tesseract",
               "Reading text out of images and scans",
               "apt-get install tesseract-ocr"),
        Engine("imagemagick", "ImageMagick", "magick",
               "Extended image formats and vector rasterising",
               "apt-get install imagemagick"),
        Engine("ghostscript", "Ghostscript", "gs",
               "PDF compression and repair", "apt-get install ghostscript"),
        Engine("sevenzip", "7-Zip", "7z", "7z and RAR archives",
               "apt-get install p7zip-full p7zip-rar"),
        Engine("rsvg", "librsvg", "rsvg-convert",
               "Rendering SVG artwork to pixels",
               "apt-get install librsvg2-bin"),
        Engine("potrace", "Potrace", "potrace",
               "Tracing bitmaps into SVG vector paths",
               "apt-get install potrace"),
    )
}


# Inside the desktop app the engine binaries live beside the app, not on PATH.
# The shell passes their directory; a container leaves it unset and PATH wins.
_BUNDLED = os.environ.get("CONVERTOR_BIN_DIR", "")


def _in_dir(directory: Path, binary: str) -> str | None:
    """An executable of this name in this directory, or None."""
    candidate = directory / binary
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    if os.name == "nt":
        exe = directory / f"{binary}.exe"
        if exe.is_file():
            return str(exe)
    return None


@lru_cache(maxsize=None)
def _resolve(binary: str) -> str | None:
    """Where this engine actually lives, searched in order of trust.

    1. The binaries shipped inside the app, whose version and codec set are
       known — so a stray system build cannot change what a conversion produces.
    2. Packs the user installed through the app, which are pinned the same way.
    3. The system PATH. Last, but genuinely useful: someone who already has
       LibreOffice should not be asked to download a second copy of it.
    """
    if _BUNDLED:
        found = _in_dir(Path(_BUNDLED), binary)
        if found:
            return found

    # Imported here, not at module scope: packs imports this module.
    from . import packs

    for directory in packs.bin_dirs():
        found = _in_dir(directory, binary)
        if found:
            return found

    return shutil.which(binary)


def available(engine_id: str) -> bool:
    engine = ENGINES.get(engine_id)
    if engine is None:
        return False
    # ImageMagick 6 ships `convert` instead of `magick`.
    if engine_id == "imagemagick":
        return bool(_resolve("magick") or _resolve("convert"))
    return _resolve(engine.binary) is not None


def binary_for(engine_id: str) -> str:
    if engine_id == "imagemagick":
        return _resolve("magick") or _resolve("convert") or "magick"
    engine = ENGINES[engine_id]
    return _resolve(engine.binary) or engine.binary


def probe_binary() -> str:
    """ffprobe sits beside ffmpeg. Resolve it directly rather than rewriting the
    ffmpeg path, which produces nonsense for a bundled layout."""
    found = _resolve("ffprobe")
    if found:
        return found
    return binary_for("ffmpeg").replace("ffmpeg", "ffprobe")


def install_hint(engine_id: str) -> str:
    """How this particular install gets the engine.

    `Engine.install_hint` is an apt command, which is right for the Docker image
    and useless everywhere else — on a packaged Mac or Windows app there is no
    apt, and the user is not meant to install anything by hand: the engine
    arrives as a pack, from inside the app. Telling them to run apt-get is worse
    than telling them nothing, because it sounds authoritative and cannot work.
    """
    engine = ENGINES[engine_id]
    from .config import BUILD_KIND

    if BUILD_KIND == "docker":
        return engine.install_hint

    # An engine that is already working needs no hint, and inventing one means
    # the API states something false about itself.
    if available(engine_id):
        return ""

    from . import packs

    pack = packs.pack_for_engine(engine_id)
    if pack is not None:
        return f"Install the {pack.label} pack ({pack.megabytes} MB) in Settings."

    if engine_id in BASE_TIER:
        # Shipped inside the app, so its absence is a damaged install rather
        # than something the user forgot to add. Do not offer a fix that is not
        # the real one.
        return (
            f"{engine.label} ships with Convertor but could not be found. "
            "Reinstalling the app should restore it."
        )

    return f"{engine.label} is not included in this build."


def require(engine_id: str) -> None:
    if not available(engine_id):
        engine = ENGINES[engine_id]
        raise engine_missing(engine.label, install_hint(engine_id))


def status() -> list[dict]:
    return [
        {
            "id": e.id,
            "label": e.label,
            "purpose": e.purpose,
            "available": available(e.id),
            "installHint": install_hint(e.id),
        }
        for e in ENGINES.values()
    ]


# Caches elsewhere that answer "can this install do X". They have to be cleared
# with this one, because a pack install changes the answer and nothing else will
# ask again. Registered rather than imported, so engines.py keeps knowing nothing
# about converters.
_invalidators: list = []


def on_refresh(clear) -> None:
    """Register a cache to clear whenever engine availability changes."""
    if clear not in _invalidators:
        _invalidators.append(clear)


def refresh() -> None:
    """Forget cached lookups, so a newly installed engine is picked up.

    Anything that caches a capability decision must be cleared here. When it was
    only `_resolve`, installing the OCR pack left `ocr_backend()` returning its
    cached "nothing available": the pack downloaded, verified, reported itself
    installed, and the route stayed dead until the app was restarted — the exact
    failure the pack system exists to prevent.
    """
    _resolve.cache_clear()
    for clear in _invalidators:
        try:
            clear()
        except Exception:  # noqa: BLE001 - one bad invalidator must not block the rest
            pass


async def run(
    engine_id: str,
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: float = 900.0,
    input_name: str = "the file",
) -> str:
    """Run an engine and return stdout, raising an explainable error on failure."""
    require(engine_id)
    engine = ENGINES[engine_id]
    cmd = [binary_for(engine_id), *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise ConversionError(
            code="timeout",
            title="This conversion took too long and was stopped",
            detail=(
                f"{engine.label} was still working on {input_name} after "
                f"{int(timeout / 60)} minutes."
            ),
            suggestions=[
                "Try a faster preset, or a smaller page or time range.",
                "Very large media files convert faster at a lower resolution.",
            ],
        )

    if proc.returncode != 0:
        from .errors import tool_failed

        raise tool_failed(engine.label, input_name, stderr.decode("utf-8", "replace"))
    return stdout.decode("utf-8", "replace")
