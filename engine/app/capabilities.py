"""Discovery of what the installed conversion engines can actually do.

The product rule is that capability is *discovered*, never hardcoded: if the
FFmpeg in this container can mux Matroska and encode VP9, the application offers
that, and if a future image drops an encoder the offer disappears with it. A
hand-maintained list of ten media formats would misrepresent an engine that
supports several hundred.

Discovery runs once at startup and is cached in the workspace against the engine
version string, so a restart is instant and an upgraded FFmpeg is re-probed
automatically.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

# Muxers and demuxers that are protocols, devices or pipes rather than files.
# Offering "convert to RTSP" in a file converter would be noise, not capability.
_NOT_A_FILE = {
    "rtp", "rtp_mpegts", "rtsp", "sdp", "sap", "rtmp", "hls", "dash", "smoothstreaming",
    "webm_dash_manifest", "webm_chunk", "ffmetadata", "tee", "fifo", "fifo_test",
    "null", "pipe", "image2pipe", "mpegtsraw", "prompeg", "uncodedframecrc",
    "framecrc", "framehash", "framemd5", "hash", "md5", "crc", "streamhash",
    "chromaprint", "hds", "ico",  # ico is handled by the image converters
    "oss", "alsa", "sndio", "pulse", "jack", "fbdev", "kmsgrab", "lavfi",
    "openal", "sdl", "sdl2", "video4linux2", "v4l2", "xv", "x11grab", "decklink",
    "avfoundation", "libndi_newtek", "iec61883", "dshow", "gdigrab", "vfwcap",
}

# Formats FFmpeg can technically write but which are better served by the
# dedicated image converters, or which produce output nobody wants from a
# file-conversion tool.
_CEDE_TO_OTHER_ENGINES = {
    "image2", "png_pipe", "jpeg_pipe", "webp_pipe", "gif_pipe", "tiff_pipe",
    "bmp_pipe", "svg_pipe", "psd_pipe", "jpegls_pipe", "j2k_pipe", "pam_pipe",
    "pbm_pipe", "pgm_pipe", "ppm_pipe", "sgi_pipe", "qdraw_pipe", "dds_pipe",
    "xbm_pipe", "xpm_pipe", "xwd_pipe", "photocd_pipe", "pcx_pipe", "gdv",
    "alias_pix", "brender_pix", "cdg", "cdxl", "vobsub",
}

_EXT_RE = re.compile(r"Common extensions:\s*([^.]+)\.")
_MIME_RE = re.compile(r"Mime type:\s*([^.]+)\.")
_DEFV_RE = re.compile(r"Default video codec:\s*(\S+?)\.")
_DEFA_RE = re.compile(r"Default audio codec:\s*(\S+?)\.")


@dataclass
class MediaFormat:
    name: str
    description: str
    demux: bool = False
    mux: bool = False
    extensions: list[str] = field(default_factory=list)
    mime: str = ""
    default_video: str | None = None
    default_audio: str | None = None
    kind: str = "unknown"  # video | audio | subtitle | unknown


@dataclass
class Codec:
    name: str
    kind: str  # video | audio | subtitle
    description: str
    codec: str  # the codec this encoder implements


@dataclass
class FFmpegCapabilities:
    available: bool = False
    version: str = ""
    formats: dict[str, MediaFormat] = field(default_factory=dict)
    encoders: dict[str, Codec] = field(default_factory=dict)
    decoders: dict[str, Codec] = field(default_factory=dict)
    filters: set[str] = field(default_factory=set)

    # ------------------------------------------------------------- queries

    def can_read(self, name: str) -> bool:
        fmt = self.formats.get(name)
        return bool(fmt and fmt.demux)

    def can_write(self, name: str) -> bool:
        fmt = self.formats.get(name)
        return bool(fmt and fmt.mux)

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    def best_encoder(self, codec: str, preferred: list[str] | None = None) -> str | None:
        """The best available encoder for a codec, honouring a preference order."""
        for candidate in preferred or []:
            if candidate in self.encoders:
                return candidate
        matches = [e.name for e in self.encoders.values() if e.codec == codec]
        if codec in self.encoders:
            return codec
        return matches[0] if matches else None

    def readable(self) -> list[MediaFormat]:
        return [f for f in self.formats.values() if f.demux]

    def writable(self) -> list[MediaFormat]:
        return [f for f in self.formats.values() if f.mux]

    def summary(self) -> dict:
        return {
            "available": self.available,
            "version": self.version,
            "readableFormats": sum(1 for f in self.formats.values() if f.demux),
            "writableFormats": sum(1 for f in self.formats.values() if f.mux),
            "encoders": len(self.encoders),
            "decoders": len(self.decoders),
            "filters": len(self.filters),
        }


CAPS = FFmpegCapabilities()


# ------------------------------------------------------------------- probing


async def _run(*args: str, timeout: float = 30.0) -> str:
    """Run the FFmpeg this install owns.

    Resolved through `engines`, not `shutil.which`. A bare `which` only finds a
    copy on PATH, and a macOS app launched from Finder gets a minimal PATH that
    contains no Homebrew — so in a packaged build discovery found nothing, and
    every format FFmpeg would have contributed silently vanished from the catalog.
    The app still worked, still listed its curated formats, and reported no error:
    it simply claimed to support a fraction of what it did.
    """
    from . import engines

    if not engines.available("ffmpeg"):
        return ""
    binary = engines.binary_for("ffmpeg")
    proc = await asyncio.create_subprocess_exec(
        binary, "-hide_banner", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    return stdout.decode("utf-8", "replace")


def _parse_formats(text: str) -> dict[str, MediaFormat]:
    """Parse `-formats`: ` DE  matroska,webm  Matroska / WebM`."""
    out: dict[str, MediaFormat] = {}
    started = False
    for line in text.splitlines():
        if not started:
            if line.strip().startswith("--"):
                started = True
            continue
        if len(line) < 5:
            continue
        # Fixed columns, not whitespace splitting: a mux-only row reads
        # "  E  mp4", whose leading space would otherwise be taken as the flags
        # and "E" as the format name.
        flags = line[1:4]
        rest = line[4:].strip()
        if not rest:
            continue
        names, _, description = rest.partition(" ")
        # A device is hardware, not a file format.
        if "d" in flags:
            continue
        for name in names.split(","):
            name = name.strip()
            if not name or name in _NOT_A_FILE or name in _CEDE_TO_OTHER_ENGINES:
                continue
            # The same name appears in more than one row: mp4 is demuxed by
            # the "mov,mp4,m4a,3gp,3g2,mj2" group and muxed by its own "E mp4"
            # row. Merge the capabilities instead of letting the later row win.
            existing = out.get(name)
            if existing is None:
                out[name] = MediaFormat(
                    name=name,
                    description=description.strip() or name,
                    demux="D" in flags,
                    mux="E" in flags,
                )
            else:
                existing.demux = existing.demux or "D" in flags
                existing.mux = existing.mux or "E" in flags
                if len(description.strip()) > len(existing.description):
                    existing.description = description.strip()
    return out


def _parse_codecs(text: str, header_lines: int) -> dict[str, Codec]:
    """Parse `-encoders` / `-decoders`: ` V....D libx264  desc (codec h264)`."""
    out: dict[str, Codec] = {}
    started = False
    kinds = {"V": "video", "A": "audio", "S": "subtitle"}
    for line in text.splitlines():
        if not started:
            if line.strip().startswith("--"):
                started = True
            continue
        if len(line) < 9:
            continue
        flags = line[1:7]
        rest = line[8:].strip()
        if not rest:
            continue
        name, _, description = rest.partition(" ")
        kind = kinds.get(flags[0])
        if kind is None or not name:
            continue
        match = re.search(r"\(codec (\S+?)\)", description)
        out[name] = Codec(
            name=name, kind=kind,
            description=description.strip(),
            codec=match.group(1) if match else name,
        )
    return out


# `-filters` rows are matched on the "in->out" column rather than the flag
# column. The flag column is three characters on FFmpeg 7 ("TSC") and two on
# FFmpeg 8, which dropped command support — a fixed-width match silently finds
# nothing on one of them, disabling every video filter without an error.
def _parse_filters(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        # flags, name, in->out, description...
        if len(parts) >= 3 and "->" in parts[2]:
            found.add(parts[1])
    return found


async def _detail(name: str, muxer: bool) -> tuple[str, list[str], str, str | None, str | None]:
    """Ask FFmpeg for one format's real extensions and mime type."""
    flag = f"muxer={name}" if muxer else f"demuxer={name}"
    text = await _run("-h", flag, timeout=15.0)
    ext_match = _EXT_RE.search(text)
    extensions = (
        [f".{e.strip()}" for e in ext_match.group(1).split(",") if e.strip()]
        if ext_match else []
    )
    mime_match = _MIME_RE.search(text)
    video = _DEFV_RE.search(text)
    audio = _DEFA_RE.search(text)
    return (
        name,
        extensions,
        mime_match.group(1).strip() if mime_match else "",
        video.group(1) if video else None,
        audio.group(1) if audio else None,
    )


# Audio containers advertise a still-image video codec for embedded cover art.
# Treating that as a video stream makes MP3, FLAC and AIFF look like video
# formats, and then refuses to convert audio into them.
_COVER_ART_CODECS = {"png", "mjpeg", "bmp", "gif", "jpeg", "jpegls", "webp"}


def _classify(fmt: MediaFormat) -> str:
    if fmt.default_video in _COVER_ART_CODECS and fmt.default_audio:
        return "audio"
    if fmt.default_video and fmt.default_audio:
        return "video"
    if fmt.default_audio and not fmt.default_video:
        return "audio"
    if fmt.default_video and not fmt.default_audio:
        return "video"
    text = f"{fmt.name} {fmt.description}".lower()
    if any(word in text for word in ("subtitle", "caption", "subrip", "webvtt", "ass/ssa")):
        return "subtitle"
    if any(word in text for word in ("audio", "sound", "wave", "pcm", "music", "codec2")):
        return "audio"
    if any(word in text for word in ("video", "movie", "mpeg", "avi", "quicktime")):
        return "video"
    return "unknown"


async def discover(*, force: bool = False) -> FFmpegCapabilities:
    """Probe FFmpeg, using the workspace cache when the version has not changed."""
    global CAPS

    version_text = await _run("-version", timeout=10.0)
    if not version_text:
        CAPS = FFmpegCapabilities(available=False)
        return CAPS
    version = version_text.splitlines()[0].strip()

    cache_path = config.WORKSPACE_ROOT / "capabilities.json"
    if not force and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text("utf-8"))
            if cached.get("version") == version:
                CAPS = _from_cache(cached)
                return CAPS
        except Exception:
            pass  # a damaged cache is re-probed, never fatal

    formats_text, encoders_text, decoders_text, filters_text = await asyncio.gather(
        _run("-formats"), _run("-encoders"), _run("-decoders"), _run("-filters"),
    )

    formats = _parse_formats(formats_text)

    # Extensions come from FFmpeg itself rather than a guess. Probing every
    # format serially would take a minute; bounded concurrency makes it fast.
    limiter = asyncio.Semaphore(24)

    async def fill(name: str, fmt: MediaFormat) -> None:
        async with limiter:
            # Prefer the muxer's answer, since writing is what needs an extension.
            _, extensions, mime, video, audio = await _detail(name, muxer=fmt.mux)
            if not extensions and fmt.demux and fmt.mux:
                _, extensions, mime, video, audio = await _detail(name, muxer=False)
        fmt.extensions = extensions
        fmt.mime = mime
        fmt.default_video = video
        fmt.default_audio = audio
        fmt.kind = _classify(fmt)

    await asyncio.gather(*(fill(name, fmt) for name, fmt in formats.items()))

    # A format with no file extension cannot be selected as an output.
    formats = {n: f for n, f in formats.items() if f.extensions}

    CAPS = FFmpegCapabilities(
        available=True,
        version=version,
        formats=formats,
        encoders=_parse_codecs(encoders_text, 10),
        decoders=_parse_codecs(decoders_text, 10),
        filters=_parse_filters(filters_text),
    )

    # FFmpeg always reports far more than this. Zero or near-zero means the
    # output format changed and a parser stopped matching — which disables a
    # whole feature class without raising anything.
    for what, count, floor in (
        ("filters", len(CAPS.filters), 50),
        ("encoders", len(CAPS.encoders), 20),
        ("writable formats", sum(1 for f in formats.values() if f.mux), 10),
    ):
        if count < floor:
            print(
                f"[capabilities] WARNING: only {count} {what} discovered from "
                f"{version}. Expected at least {floor}; the parser has probably "
                "stopped matching this FFmpeg's output format.",
                flush=True,
            )

    try:
        cache_path.write_text(json.dumps({
            "version": version,
            "formats": {n: asdict(f) for n, f in CAPS.formats.items()},
            "encoders": {n: asdict(c) for n, c in CAPS.encoders.items()},
            "decoders": {n: asdict(c) for n, c in CAPS.decoders.items()},
            "filters": sorted(CAPS.filters),
        }), encoding="utf-8")
    except OSError:
        pass  # a read-only workspace just means re-probing next time

    return CAPS


def _from_cache(data: dict) -> FFmpegCapabilities:
    return FFmpegCapabilities(
        available=True,
        version=data["version"],
        formats={n: MediaFormat(**f) for n, f in data["formats"].items()},
        encoders={n: Codec(**c) for n, c in data["encoders"].items()},
        decoders={n: Codec(**c) for n, c in data["decoders"].items()},
        filters=set(data.get("filters", [])),
    )
