"""Downloading media from a URL, with yt-dlp.

This is deliberately not a converter. Everything else in the engine takes a file
that already exists on the machine and produces another one; this takes a URL and
reaches out to the network. Those are different enough that folding it into the
converter registry would mean special-casing "inputs" everywhere, and would put a
network operation behind a UI built on the promise that nothing leaves the
machine. It gets its own module, its own endpoints and its own screen.

Two rules follow from that separation:

1. **Nothing is fetched until a button is pressed.** Probing a URL for its title
   and available qualities is itself a network request, so it is a separate,
   explicit action rather than something that happens as you type.
2. **The user chooses where files land**, and the default is a real folder they
   can open — not a workspace subdirectory they would have to go hunting for.

Downloads run in a worker thread. yt-dlp is synchronous and long-running, and the
API has to stay responsive to report its progress.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, engines
from .errors import ConversionError

# What the user picks, expressed as the yt-dlp format selector it means. Kept
# small on purpose: "best quality", "1080p", "just the audio" is the whole of
# what most people want, and every extra row is one more decision to make.
QUALITY_PRESETS: dict[str, dict] = {
    "best": {
        "label": "Best available",
        "detail": "Highest quality video and audio, merged into one file.",
        "format": "bestvideo*+bestaudio/best",
    },
    "1080p": {
        "label": "Up to 1080p",
        "detail": "Full HD. Smaller than the best available, still sharp.",
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    },
    "720p": {
        "label": "Up to 720p",
        "detail": "HD. A good balance of size and quality.",
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    },
    "audio": {
        "label": "Audio only",
        "detail": "Extracts the sound as an MP3. No video.",
        "format": "bestaudio/best",
        "audio_only": True,
    },
}


def available() -> bool:
    import importlib.util

    return importlib.util.find_spec("yt_dlp") is not None


def default_directory() -> Path:
    """Where downloads go unless the user says otherwise.

    A real folder in the user's home, not a workspace subdirectory. Someone who
    downloads a video expects to find it where they keep things, and "it is in the
    app's data directory" is not an answer.
    """
    home = Path.home()
    for candidate in (home / "Downloads", home / "Documents"):
        if candidate.is_dir():
            return candidate / "Convertor"
    return config.OUTPUT_DIR


@dataclass
class Download:
    id: str
    url: str
    title: str = ""
    status: str = "running"      # running | completed | failed | cancelled
    phase: str = "starting"
    progress: float | None = None
    downloaded: int = 0
    total: int = 0
    speed: str = ""
    eta: str = ""
    files: list[str] = field(default_factory=list)
    directory: str = ""
    error: dict | None = None
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["elapsed"] = round((self.finished or time.time()) - self.started, 1)
        return data


_downloads: dict[str, Download] = {}
_cancelled: set[str] = set()
_lock = threading.Lock()


class _Cancelled(Exception):
    pass


def state(download_id: str) -> dict | None:
    with _lock:
        item = _downloads.get(download_id)
        return item.to_dict() if item else None


def recent(limit: int = 20) -> list[dict]:
    with _lock:
        items = sorted(_downloads.values(), key=lambda d: d.started, reverse=True)
        return [d.to_dict() for d in items[:limit]]


def _set(download_id: str, **fields) -> None:
    with _lock:
        item = _downloads.get(download_id)
        if item is None:
            return
        for key, value in fields.items():
            setattr(item, key, value)


def _require() -> None:
    if not available():
        raise ConversionError(
            code="downloader_missing",
            title="The downloader is not installed",
            detail="yt-dlp ships with Convertor but could not be imported.",
            suggestions=["Reinstalling Convertor should restore it."],
        )


# --------------------------------------------------------------------- probing


def probe(url: str) -> dict:
    """Look up what is at a URL without downloading it.

    Metadata only — `download=False` — so pressing this costs a page fetch, not a
    video. It exists so the user can confirm they pasted the right link before
    committing to a few hundred megabytes.
    """
    _require()
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "skip_download": True,
        "ffmpeg_location": _ffmpeg_dir(),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as error:  # noqa: BLE001 - yt-dlp raises many types
        raise _readable_error(url, error) from error

    if info is None:
        raise ConversionError(
            code="nothing_found",
            title="Nothing was found at that link",
            detail=f"{url} did not return any media.",
            suggestions=["Check the link opens in a browser."],
        )

    entries = info.get("entries")
    if entries is not None:
        items = [e for e in entries if e]
        return {
            "kind": "playlist",
            "title": info.get("title") or "Playlist",
            "count": len(items),
            "duration": None,
            "thumbnail": (items[0].get("thumbnail") if items else None),
            "uploader": info.get("uploader") or "",
        }

    return {
        "kind": "single",
        "title": info.get("title") or "Untitled",
        "count": 1,
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader") or "",
        "extension": info.get("ext") or "",
    }


def _ffmpeg_dir() -> str | None:
    """The directory holding the FFmpeg yt-dlp should use for merging.

    yt-dlp looks for ffmpeg on PATH, which in a packaged macOS app is nearly
    empty. Without this, every "best quality" download silently falls back to a
    single pre-merged stream at lower quality, because merging was impossible.
    """
    if not engines.available("ffmpeg"):
        return None
    return str(Path(engines.binary_for("ffmpeg")).parent)


def _readable_error(url: str, error: Exception) -> ConversionError:
    text = str(error)
    low = text.lower()
    if "unsupported url" in low or "no video formats" in low:
        return ConversionError(
            code="unsupported_site",
            title="This link is not something the downloader understands",
            detail=f"{url} is not a media page it can read.",
            suggestions=[
                "Use the link to the video's own page, not a search or share page.",
                "Some sites are not supported at all.",
            ],
        )
    if "private" in low or "sign in" in low or "log in" in low or "members-only" in low:
        return ConversionError(
            code="requires_sign_in",
            title="This video is not public",
            detail="The site is asking for a sign-in before it will serve this.",
            suggestions=["Only public videos can be downloaded."],
        )
    if "age" in low and "confirm" in low:
        return ConversionError(
            code="age_restricted",
            title="This video is age-restricted",
            detail="The site will not serve it without a signed-in account.",
            suggestions=["Only videos that play without signing in can be fetched."],
        )
    if "unavailable" in low or "removed" in low or "404" in low:
        return ConversionError(
            code="not_available",
            title="That video is not available",
            detail="The site reports it as removed, private or region-blocked.",
            suggestions=["Check the link still opens in a browser."],
        )
    # Last line first: yt-dlp puts the cause there and a banner above it.
    tail = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return ConversionError(
        code="download_failed",
        title="The download could not be completed",
        detail=tail[-1][:300] if tail else "yt-dlp reported no reason.",
        suggestions=[
            "Try again — many failures here are temporary.",
            "Check the link opens in a browser.",
        ],
    )


# ------------------------------------------------------------------- downloading


def _run(item: Download, quality: str, directory: Path, subtitles: bool) -> None:
    import yt_dlp

    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["best"])
    produced: list[str] = []

    def hook(status: dict) -> None:
        if item.id in _cancelled:
            raise _Cancelled()
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            done = status.get("downloaded_bytes") or 0
            _set(item.id,
                 phase=f"Downloading {status.get('info_dict', {}).get('title', '')}".strip()
                       or "Downloading",
                 downloaded=done, total=int(total),
                 progress=(done / total) if total else None,
                 speed=(status.get("_speed_str") or "").strip(),
                 eta=(status.get("_eta_str") or "").strip())
        elif status.get("status") == "finished":
            _set(item.id, phase="Merging streams", progress=None)
            name = status.get("filename")
            if name:
                produced.append(name)

    def after(status: dict) -> None:
        if status.get("status") == "finished":
            name = status.get("info_dict", {}).get("filepath") or status.get("filename")
            if name:
                produced.append(name)

    directory.mkdir(parents=True, exist_ok=True)
    # What was already there. The only reliable way to know what this download
    # produced is to diff the folder, because yt-dlp's hooks report the name it
    # was writing *before* postprocessing, and extracting audio renames the file.
    existing = {p for p in directory.iterdir() if p.is_file()}
    options: dict = {
        "outtmpl": str(directory / "%(title)s.%(ext)s"),
        "format": preset["format"],
        "progress_hooks": [hook],
        "postprocessor_hooks": [after],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ffmpeg_location": _ffmpeg_dir(),
        # Without this a single failed entry aborts an entire playlist.
        "ignoreerrors": "only_download",
        "retries": 3,
    }
    if preset.get("audio_only"):
        options["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # Prefer a container that plays everywhere once the streams are merged.
        options["merge_output_format"] = "mp4"
    if subtitles:
        options["writesubtitles"] = True
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = ["en"]

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(item.url, download=True)
        title = ""
        if isinstance(info, dict):
            title = info.get("title") or ""
        # Anything in the folder that was not there before, plus any hook-reported
        # path that still exists. Never a bare listing of the folder: the previous
        # fallback did exactly that, so a download whose file had been renamed by
        # postprocessing reported unrelated files the user already had as its own
        # output.
        appeared = {p for p in directory.iterdir() if p.is_file()} - existing
        appeared |= {Path(p) for p in produced if Path(p).exists()}
        final = sorted(str(p) for p in appeared if not p.name.startswith("."))
        _set(item.id, status="completed", phase="Finished", progress=1.0,
             title=title or item.title, files=final, finished=time.time())
    except _Cancelled:
        _set(item.id, status="cancelled", phase="Cancelled", finished=time.time())
    except Exception as error:  # noqa: BLE001 - reported, never swallowed
        # yt-dlp catches what a progress hook raises and re-raises its own type,
        # so a cancel can arrive here wearing the wrong coat. Reporting it as a
        # failure would put an error in front of someone who pressed Cancel.
        if item.id in _cancelled or isinstance(error.__cause__, _Cancelled):
            _set(item.id, status="cancelled", phase="Cancelled",
                 finished=time.time())
            return
        readable = _readable_error(item.url, error)
        _set(item.id, status="failed", phase="Failed", finished=time.time(),
             error=readable.to_dict())
    finally:
        _cancelled.discard(item.id)


def start(url: str, quality: str = "best", directory: str = "",
          subtitles: bool = False) -> dict:
    _require()
    url = (url or "").strip()
    if not url:
        raise ConversionError(
            code="no_url",
            title="No link was given",
            detail="Paste the address of the page the video is on.",
            suggestions=[],
        )
    if not url.lower().startswith(("http://", "https://")):
        raise ConversionError(
            code="not_a_url",
            title="That does not look like a link",
            detail=f"{url[:120]!r} is not a web address.",
            suggestions=["Copy the address from your browser's address bar."],
        )

    target = Path(directory).expanduser() if directory else default_directory()
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe_file = target / f".convertor-write-test-{uuid.uuid4().hex[:6]}"
        probe_file.write_text("")
        probe_file.unlink()
    except OSError as error:
        raise ConversionError(
            code="cannot_write",
            title="That folder cannot be written to",
            detail=f"{target}: {error}",
            suggestions=["Choose a different folder."],
        ) from error

    item = Download(id=uuid.uuid4().hex[:12], url=url, directory=str(target))
    with _lock:
        _downloads[item.id] = item
    threading.Thread(target=_run, args=(item, quality, target, subtitles),
                     daemon=True, name=f"download-{item.id}").start()
    return item.to_dict()


def cancel(download_id: str) -> dict | None:
    with _lock:
        item = _downloads.get(download_id)
        if item is None or item.status != "running":
            return item.to_dict() if item else None
    _cancelled.add(download_id)
    return state(download_id)


async def wait_for_change(download_id: str, previous: dict | None,
                          timeout: float = 1.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = state(download_id)
        if current != previous:
            return current
        await asyncio.sleep(0.15)
    return previous


def summary() -> dict:
    return {
        "available": available(),
        "defaultDirectory": str(default_directory()),
        "canMerge": _ffmpeg_dir() is not None,
        "qualities": [
            {"id": key, "label": value["label"], "detail": value["detail"]}
            for key, value in QUALITY_PRESETS.items()
        ],
        "downloads": recent(),
    }
