"""Media conversion, driven entirely by what the installed FFmpeg can do.

Nothing here enumerates a fixed list of formats. The converter's inputs and
outputs are computed from the muxers, demuxers, encoders and filters FFmpeg
reports at startup, so this module offers exactly the capability the container
actually has — several hundred formats rather than the handful a hand-written
list would name.

FFmpeg is also the first choice, not a fallback: any conversion it can perform
natively is routed here rather than reimplemented.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import Sequence

from .. import capabilities, engines, formats
from ..capabilities import CAPS
from ..errors import ConversionError, tool_failed
from ..registry import (
    MANY_TO_ONE,
    ONE_TO_MANY,
    Choice,
    Converter,
    InputFile,
    Option,
    Preset,
    RunContext,
    register,
)
from .base import NAMING_OPTION, OVERWRITE_OPTION, safe_stem, target_path

# Preference order per codec: the best-quality encoder first, so a container
# gets libx264 rather than the built-in h264 stub when both exist.
_ENCODER_PREFERENCE = {
    "h264": ["libx264", "h264"],
    "hevc": ["libx265", "hevc"],
    "vp9": ["libvpx-vp9"],
    "vp8": ["libvpx"],
    "av1": ["libsvtav1", "libaom-av1", "librav1e"],
    "mp3": ["libmp3lame", "mp3"],
    "aac": ["libfdk_aac", "aac"],
    "opus": ["libopus", "opus"],
    "vorbis": ["libvorbis", "vorbis"],
    "flac": ["flac"],
    "alac": ["alac"],
}

# Constant-quality values per quality mode. Lower CRF is better quality.
_CRF = {"high": 18, "balanced": 23, "small": 30}
_AUDIO_BITRATE = {"high": "256k", "balanced": "160k", "small": "96k"}
_IMAGE_QSCALE = {"high": "2", "balanced": "5", "small": "9"}


def _fmt(format_id: str) -> formats.Format | None:
    return formats.get(format_id)


def _muxer_for(format_id: str) -> str | None:
    fmt = _fmt(format_id)
    if fmt is None:
        return None
    return fmt.ffmpeg or (fmt.id if CAPS.can_write(fmt.id) else None)


def _demuxable(format_id: str) -> bool:
    fmt = _fmt(format_id)
    if fmt is None:
        return False
    name = fmt.ffmpeg or fmt.id
    return CAPS.can_read(name)


def _category(format_id: str) -> str:
    fmt = _fmt(format_id)
    return fmt.category if fmt else ""


# ------------------------------------------------------- capability discovery


def media_inputs() -> frozenset[str]:
    """Every catalog format FFmpeg can demux."""
    return frozenset(
        f.id for f in formats.all_formats()
        if f.category in (formats.AUDIO, formats.VIDEO) and _demuxable(f.id)
    )


def media_outputs(kind: str | None = None) -> frozenset[str]:
    """Every catalog format FFmpeg can mux, optionally limited to one kind."""
    wanted = (
        (formats.AUDIO, formats.VIDEO) if kind is None
        else ((formats.AUDIO,) if kind == "audio" else (formats.VIDEO,))
    )
    out = set()
    for f in formats.all_formats():
        if f.category not in wanted:
            continue
        muxer = _muxer_for(f.id)
        if muxer and CAPS.can_write(muxer):
            out.add(f.id)
    return frozenset(out)


def register_discovered_formats() -> int:
    """Add every FFmpeg container the catalog does not already cover.

    Curated formats are registered first and keep their extensions, so this only
    ever widens the catalog — it never overrides a hand-written description.
    """
    if not CAPS.available:
        return 0

    added = 0
    category_of = {
        "video": formats.VIDEO, "audio": formats.AUDIO,
        "subtitle": formats.SUBTITLE,
    }

    for name, media in sorted(CAPS.formats.items()):
        if media.kind not in category_of:
            continue
        if not media.extensions:
            continue
        # An extension already claimed by a curated format means the catalog
        # already describes this container, better than a generated line would.
        if formats.extension_is_claimed(media.extensions):
            continue
        if name in formats.BY_ID:
            continue

        readable = "read" if media.demux else ""
        writable = "written" if media.mux else ""
        both = " and ".join(p for p in (readable, writable) if p) or "handled"

        formats.register(formats.Format(
            id=name,
            label=media.description or name,
            category=category_of[media.kind],
            extensions=tuple(media.extensions),
            mime=media.mime or "application/octet-stream",
            summary=(
                f"A {category_of[media.kind]} container that this FFmpeg build "
                f"can be {both}."
            ),
            common_uses=(),
            notes=(
                "Discovered from the installed FFmpeg rather than curated, so "
                "there is no hand-written guidance for it.",
            ),
            ffmpeg=name,
            discovered=True,
        ))
        added += 1
    return added


# ------------------------------------------------------------ argument building


def _time_args(ctx: RunContext) -> list[str]:
    args: list[str] = []
    start = str(ctx.option("trim_start", "")).strip()
    end = str(ctx.option("trim_end", "")).strip()
    if start:
        args += ["-ss", start]
    if end:
        args += ["-to", end]
    return args


def _video_filters(ctx: RunContext, item: InputFile | None = None) -> list[str]:
    """Build the -vf chain from whatever filters this FFmpeg actually has."""
    chain: list[str] = []

    crop = str(ctx.option("crop", "none"))
    if crop != "none" and CAPS.has_filter("crop"):
        ratios = {"16:9": (16, 9), "4:3": (4, 3), "1:1": (1, 1), "9:16": (9, 16)}
        source_w = int((item.metadata.get("width") or 0) if item else 0)
        source_h = int((item.metadata.get("height") or 0) if item else 0)
        if crop in ratios and source_w and source_h:
            rw, rh = ratios[crop]
            if source_w * rh > source_h * rw:
                new_h = source_h
                new_w = int(source_h * rw / rh)
            else:
                new_w = source_w
                new_h = int(source_w * rh / rw)
            # Odd dimensions break most encoders.
            new_w -= new_w % 2
            new_h -= new_h % 2
            x = (source_w - new_w) // 2
            y = (source_h - new_h) // 2
            chain.append(f"crop={new_w}:{new_h}:{x}:{y}")
        elif crop in ratios:
            ctx.note(
                "Crop was skipped",
                "The source dimensions could not be read, so the picture was left "
                "at its original shape.",
            )

    resolution = str(ctx.option("resolution", "source"))
    if resolution != "source" and CAPS.has_filter("scale"):
        chain.append(f"scale=-2:{resolution}:flags=lanczos")

    rotate = str(ctx.option("rotate", "0"))
    if rotate in ("90", "270") and CAPS.has_filter("transpose"):
        chain.append("transpose=1" if rotate == "90" else "transpose=2")
    elif rotate == "180" and CAPS.has_filter("hflip") and CAPS.has_filter("vflip"):
        chain.append("hflip,vflip")

    flip = str(ctx.option("flip", "none"))
    if flip == "horizontal" and CAPS.has_filter("hflip"):
        chain.append("hflip")
    elif flip == "vertical" and CAPS.has_filter("vflip"):
        chain.append("vflip")

    if ctx.option("denoise", False) and CAPS.has_filter("hqdn3d"):
        chain.append("hqdn3d")

    return chain


def _audio_filters(ctx: RunContext) -> list[str]:
    chain: list[str] = []
    if ctx.option("normalize", False) and CAPS.has_filter("loudnorm"):
        chain.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    gain = float(ctx.option("volume", 0) or 0)
    if gain and CAPS.has_filter("volume"):
        chain.append(f"volume={gain}dB")
    fade = float(ctx.option("fade", 0) or 0)
    if fade and CAPS.has_filter("afade"):
        chain.append(f"afade=t=in:st=0:d={fade}")
    return chain


def _pick_encoder(ctx: RunContext, option_id: str, default_codec: str | None) -> str | None:
    """Honour an explicit encoder choice, else the best one for the codec."""
    chosen = str(ctx.option(option_id, "auto"))
    if chosen and chosen != "auto":
        return chosen if CAPS.has_encoder(chosen) else None
    if not default_codec:
        return None
    return CAPS.best_encoder(default_codec, _ENCODER_PREFERENCE.get(default_codec))


# Containers that accept a wide range of codecs, where copying whatever the source
# already has is both legal and the right default. Everything not listed is treated
# as strict: its codec set is narrow enough that muxing the wrong thing produces a
# file that opens and does not play.
_PERMISSIVE_MUXERS = frozenset({
    "mp4", "mov", "matroska", "webm", "nut", "mpegts", "flv", "3gp", "3g2",
    "ismv", "f4v", "mxf", "avi", "asf_stream", "ogg",
})


def _codec_family(codec: str) -> str:
    """Group codecs that a strict container treats as interchangeable."""
    if codec.startswith("pcm_"):
        return "pcm"
    if codec in ("wmav1", "wmav2", "wmapro", "wmalossless"):
        return "wma"
    if codec in ("wmv1", "wmv2", "wmv3", "msmpeg4v2", "msmpeg4v3", "vc1"):
        return "wmv"
    # MPEG-1 and MPEG-2 video are deliberately NOT grouped. They look like one
    # family and are not interchangeable where it matters: a DVD or SVCD requires
    # MPEG-2, and copying MPEG-1 into one produces a disc image that no player
    # will read.
    return codec


def copy_is_sane(item: InputFile, target: str) -> bool:
    """Whether copying the source's streams into this container would really work.

    FFmpeg does not refuse most illegal combinations — it writes the file and exits
    zero. So "attempt a copy and fall back if it errors" silently produced a .wav
    holding AAC and a .wmv holding MPEG-1 video: valid containers that no ordinary
    player will play. The failure had no error to fall back from.

    For a permissive container, copying is still the right default. For a strict one
    the source codec must belong to the family that container is built around,
    which is taken from the muxer's own declared defaults rather than a guess.
    """
    muxer = _muxer_for(target)
    if not muxer:
        return False
    if muxer in _PERMISSIVE_MUXERS:
        return True

    media = CAPS.formats.get(muxer)
    if media is None:
        return False

    target_category = _category(target)
    for kind, default in (("videoCodec", media.default_video),
                          ("audioCodec", media.default_audio)):
        if kind == "videoCodec" and target_category != formats.VIDEO:
            continue
        source = item.metadata.get(kind)
        if not source:
            continue
        if not default:
            # The container declares no default for this stream type, so it
            # probably cannot carry one. Re-encoding will drop or convert it.
            return False
        if _codec_family(str(source)) != _codec_family(default):
            return False
    return True


def build_args(
    ctx: RunContext,
    item: InputFile,
    target: str,
    out: Path,
    *,
    copy_streams: bool,
) -> list[str]:
    """The full FFmpeg argument vector for one conversion.

    Shared by execution and by the expert plan preview, so what the user is shown
    is exactly what runs.
    """
    muxer = _muxer_for(target)
    media = CAPS.formats.get(muxer or "")
    target_category = _category(target)
    source_category = _category(item.format_id)

    args: list[str] = ["-nostdin", "-y", *_time_args(ctx), "-i", str(item.path)]

    want_video = target_category == formats.VIDEO and not ctx.option("drop_video", False)
    want_audio = not ctx.option("drop_audio", False)
    if target_category == formats.AUDIO:
        want_video = False

    if source_category == formats.AUDIO and target_category == formats.VIDEO:
        raise ConversionError(
            code="no_video_stream",
            title="This file has no picture to convert",
            detail=f"{item.name} is audio only, so it cannot become a video file.",
            suggestions=["Choose an audio output format instead."],
        )

    if copy_streams:
        # Container change with no re-encode: fastest possible and lossless.
        args += ["-c", "copy"]
        if not want_audio:
            args.append("-an")
        if not want_video and target_category == formats.VIDEO:
            args.append("-vn")
    else:
        if want_video:
            encoder = _pick_encoder(ctx, "video_encoder", media.default_video if media else None)
            if encoder:
                args += ["-c:v", encoder]
                mode = str(ctx.option("quality_mode", "balanced"))
                if encoder in ("libx264", "libx265"):
                    args += ["-crf", str(_CRF.get(mode, 23)),
                             "-preset", "slow" if mode == "high" else "medium",
                             "-pix_fmt", "yuv420p"]
                    if ctx.option("compatibility", False):
                        args += ["-profile:v", "baseline", "-level", "3.1"]
                elif encoder.startswith("libvpx"):
                    args += ["-crf", str(_CRF.get(mode, 31)), "-b:v", "0", "-row-mt", "1"]
                elif encoder in ("libsvtav1", "libaom-av1"):
                    args += ["-crf", str(_CRF.get(mode, 30))]
                else:
                    args += ["-qscale:v", _IMAGE_QSCALE.get(mode, "5")]
            bitrate = str(ctx.option("video_bitrate", "")).strip()
            if bitrate:
                args += ["-b:v", bitrate]
            chain = _video_filters(ctx, item)
            if chain:
                args += ["-vf", ",".join(chain)]
            fps = str(ctx.option("fps", "source"))
            if fps != "source":
                args += ["-r", fps]
        else:
            args.append("-vn")

        if want_audio:
            encoder = _pick_encoder(ctx, "audio_encoder", media.default_audio if media else None)
            if encoder:
                args += ["-c:a", encoder]
                if encoder not in ("flac", "alac") and not encoder.startswith("pcm"):
                    args += ["-b:a", _AUDIO_BITRATE.get(
                        str(ctx.option("quality_mode", "balanced")), "160k")]
                elif encoder == "flac":
                    args += ["-compression_level", "8"]
            rate = str(ctx.option("sample_rate", "source"))
            if rate != "source":
                args += ["-ar", rate]
            channels = str(ctx.option("channels", "source"))
            if channels != "source":
                args += ["-ac", channels]
            chain = _audio_filters(ctx)
            if chain:
                args += ["-af", ",".join(chain)]
        else:
            args.append("-an")

    if not ctx.option("keep_metadata", True):
        args += ["-map_metadata", "-1"]
    if str(ctx.option("subtitle_handling", "keep")) == "drop":
        args.append("-sn")

    if target == "mp4" or muxer == "mp4":
        args += ["-movflags", "+faststart"]
    if muxer:
        args += ["-f", muxer]
    args.append(str(out))
    return args


def _preview_context(options: dict) -> RunContext:
    """A context that can build arguments but can never run anything."""
    return RunContext(
        inputs=[], target="", options=options,
        workdir=Path("/tmp"), outdir=Path("/tmp"),
        progress=lambda fraction, message: None,
    )


def plan(inputs: Sequence[InputFile], target: str, options: dict) -> list[dict]:
    """The exact command this conversion will run, for the expert view."""
    if not inputs:
        return []
    item = inputs[0]
    ctx = _preview_context(options)
    out = Path(f"{safe_stem(item.name)}{formats.extension_for(target)}")
    mode = str(options.get("reencode", "auto"))
    untouched = not any(
        options.get(key) not in (None, "", "source", "none", False, 0)
        for key in ("resolution", "fps", "crop", "rotate", "flip", "denoise",
                    "normalize", "volume", "fade", "sample_rate", "channels",
                    "video_bitrate", "video_encoder", "audio_encoder")
    )
    copying = mode == "never" or (mode == "auto" and untouched)
    if copying and mode != "never" and not copy_is_sane(item, target):
        copying = False
    try:
        args = build_args(ctx, item, target, out, copy_streams=copying)
    except ConversionError as exc:
        return [{"detail": exc.detail, "command": ""}]
    note = (
        "Streams are copied, not re-encoded — instant and lossless."
        if copying else "Streams are re-encoded so every setting applies."
    )
    return [{
        "detail": note,
        "command": "ffmpeg " + " ".join(shlex.quote(a) for a in args),
    }]


# ------------------------------------------------------------------- execution


async def _run_ffmpeg(
    ctx: RunContext, item: InputFile, args: list[str],
    duration: float, base: float, span: float,
) -> tuple[int, str]:
    """Run FFmpeg, translating its progress output into job progress."""
    cmd = [engines.binary_for("ffmpeg"), "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    # Both pipes are drained here directly. communicate() would read them too,
    # and two readers on one stream raise at runtime.
    errors: list[bytes] = []

    async def read_progress() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_ms=") and duration > 0:
                try:
                    seconds = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                fraction = min(0.99, seconds / duration)
                ctx.progress(base + fraction * span,
                             f"{item.name} — {int(fraction * 100)}%")

    async def read_errors() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            errors.append(raw)

    try:
        await asyncio.gather(read_progress(), read_errors())
    finally:
        await proc.wait()

    return proc.returncode or 0, b"".join(errors).decode("utf-8", "replace")


async def _transcode(ctx: RunContext) -> list[Path]:
    engines.require("ffmpeg")
    outputs: list[Path] = []
    total = len(ctx.inputs)
    mode = str(ctx.option("reencode", "auto"))

    for index, item in enumerate(ctx.inputs, start=1):
        base, span = (index - 1) / total, 1 / total
        ctx.progress(base, f"Preparing {item.name}")
        out = target_path(ctx, item.name, ctx.target, index=index)
        duration = float(item.metadata.get("duration") or 0)

        # A container change with compatible streams needs no re-encode at all:
        # it is near-instant and bit-exact. Try it first unless told otherwise.
        untouched = not any(
            ctx.options.get(key) not in (None, "", "source", "none", False, 0)
            for key in ("resolution", "fps", "crop", "rotate", "flip", "denoise",
                        "normalize", "volume", "fade", "sample_rate", "channels",
                        "video_bitrate", "video_encoder", "audio_encoder")
        )
        attempt_copy = mode == "never" or (mode == "auto" and untouched)
        # A copy into a container that cannot legally hold these streams is not
        # worth attempting: ffmpeg would succeed and produce an unplayable file.
        if attempt_copy and mode != "never" and not copy_is_sane(item, ctx.target):
            attempt_copy = False

        code, stderr = 1, ""
        if attempt_copy:
            args = build_args(ctx, item, ctx.target, out, copy_streams=True)
            code, stderr = await _run_ffmpeg(ctx, item, args, duration, base, span)
            if code == 0:
                ctx.note(
                    "Copied without re-encoding",
                    f"{item.name} changed container only, so the streams were copied "
                    "across untouched — no quality was lost and it took seconds.",
                )
            elif mode == "never":
                raise ConversionError(
                    code="copy_impossible",
                    title=f"{formats.label(ctx.target)} cannot hold these streams as they are",
                    detail=f"{item.name} would have to be re-encoded to fit this "
                           "container, and re-encoding was switched off.",
                    suggestions=[
                        "Set re-encoding to Automatic in advanced settings.",
                        "Or choose a container that accepts the existing codecs.",
                    ],
                )

        if code != 0:
            if attempt_copy:
                ctx.note(
                    "Re-encoded rather than copied",
                    "The existing streams are not valid inside "
                    f"{formats.label(ctx.target)}, so they were re-encoded.",
                )
            args = build_args(ctx, item, ctx.target, out, copy_streams=False)
            code, stderr = await _run_ffmpeg(ctx, item, args, duration, base, span)
            if code != 0:
                raise tool_failed("FFmpeg", item.name, stderr)

        outputs.append(out)
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


async def _extract_frames(ctx: RunContext) -> list[Path]:
    engines.require("ffmpeg")
    item = ctx.inputs[0]
    every = str(ctx.option("frame_selection", "fps"))
    rate = float(ctx.option("frame_rate", 1) or 1)
    limit = int(ctx.option("frame_limit", 0) or 0)

    stem = safe_stem(item.name)
    pattern = ctx.outdir / f"{stem}-%04d{formats.extension_for(ctx.target)}"
    args = ["-nostdin", "-y", *_time_args(ctx), "-i", str(item.path)]

    if every == "fps":
        args += ["-vf", f"fps={rate}"]
    elif every == "keyframes":
        args += ["-vf", "select='eq(pict_type,I)'", "-vsync", "vfr"]
    if limit:
        args += ["-frames:v", str(limit)]
    if ctx.target in ("jpeg", "webp"):
        args += ["-qscale:v", _IMAGE_QSCALE.get(str(ctx.option("quality_mode", "balanced")), "5")]
    args.append(str(pattern))

    duration = float(item.metadata.get("duration") or 0)
    code, stderr = await _run_ffmpeg(ctx, item, args, duration, 0.0, 1.0)
    if code != 0:
        raise tool_failed("FFmpeg", item.name, stderr)

    produced = sorted(ctx.outdir.glob(f"{stem}-*{formats.extension_for(ctx.target)}"))
    if not produced:
        raise ConversionError(
            code="no_frames",
            title="No frames were produced",
            detail=f"FFmpeg read {item.name} but wrote no images. The selected time "
                   "range may fall outside the clip.",
            suggestions=["Clear the start and stop times, or widen the range."],
        )
    ctx.note("Frames extracted",
             f"{len(produced)} images written at {rate} per second."
             if every == "fps" else f"{len(produced)} keyframes written.")
    return produced


async def _thumbnail(ctx: RunContext) -> list[Path]:
    engines.require("ffmpeg")
    outputs: list[Path] = []
    total = len(ctx.inputs)
    at = str(ctx.option("thumbnail_at", "")).strip()
    smart = ctx.option("thumbnail_smart", True) and CAPS.has_filter("thumbnail")

    for index, item in enumerate(ctx.inputs, start=1):
        out = target_path(ctx, item.name, ctx.target, index=index, suffix="-thumb")
        args = ["-nostdin", "-y"]
        if at:
            args += ["-ss", at]
        elif not smart:
            duration = float(item.metadata.get("duration") or 0)
            if duration:
                args += ["-ss", str(round(duration * 0.1, 2))]
        args += ["-i", str(item.path)]
        chain = []
        if smart and not at:
            chain.append("thumbnail")
        width = int(ctx.option("thumbnail_width", 0) or 0)
        if width and CAPS.has_filter("scale"):
            chain.append(f"scale={width}:-2:flags=lanczos")
        if chain:
            args += ["-vf", ",".join(chain)]
        args += ["-frames:v", "1", str(out)]

        code, stderr = await _run_ffmpeg(ctx, item, args, 0.0, (index - 1) / total, 1 / total)
        if code != 0:
            raise tool_failed("FFmpeg", item.name, stderr)
        outputs.append(out)
        ctx.progress(index / total, f"Captured {item.name}")
    return outputs


async def _images_to_video(ctx: RunContext) -> list[Path]:
    """Assemble an ordered set of images into a video or animation."""
    engines.require("ffmpeg")
    staging = ctx.workdir / "sequence"
    staging.mkdir(parents=True, exist_ok=True)

    # FFmpeg's image demuxer needs a numbered sequence; the queue order is the
    # order the user arranged, so it is preserved exactly.
    import shutil as _shutil

    suffix = Path(ctx.inputs[0].name).suffix or ".png"
    for position, item in enumerate(ctx.inputs, start=1):
        _shutil.copy(item.path, staging / f"frame-{position:05d}{suffix}")

    rate = float(ctx.option("frame_rate", 2) or 2)
    out = target_path(ctx, f"{safe_stem(ctx.inputs[0].name)}-sequence", ctx.target)
    args = [
        "-nostdin", "-y", "-framerate", str(rate),
        "-i", str(staging / f"frame-%05d{suffix}"),
    ]
    muxer = _muxer_for(ctx.target)
    media = CAPS.formats.get(muxer or "")
    encoder = _pick_encoder(ctx, "video_encoder", media.default_video if media else "h264")
    if encoder:
        args += ["-c:v", encoder]
        if encoder in ("libx264", "libx265"):
            args += ["-crf", str(_CRF.get(str(ctx.option("quality_mode", "balanced")), 23)),
                     "-pix_fmt", "yuv420p"]
    if CAPS.has_filter("scale"):
        # Odd dimensions break most video encoders; round to even.
        args += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    if muxer:
        args += ["-f", muxer]
    args.append(str(out))

    code, stderr = await _run_ffmpeg(ctx, ctx.inputs[0], args, 0.0, 0.0, 1.0)
    if code != 0:
        raise tool_failed("FFmpeg", ctx.inputs[0].name, stderr)
    ctx.note("Sequence assembled",
             f"{len(ctx.inputs)} images at {rate} frames per second — "
             f"{len(ctx.inputs) / rate:.1f} seconds of video, in queue order.")
    return [out]


async def _extract_subtitles(ctx: RunContext) -> list[Path]:
    engines.require("ffmpeg")
    outputs: list[Path] = []
    for index, item in enumerate(ctx.inputs, start=1):
        out = target_path(ctx, item.name, ctx.target, index=index)
        track = int(ctx.option("subtitle_track", 0) or 0)
        args = ["-nostdin", "-y", "-i", str(item.path),
                "-map", f"0:s:{track}", str(out)]
        code, stderr = await _run_ffmpeg(ctx, item, args, 0.0, (index - 1) / len(ctx.inputs), 1 / len(ctx.inputs))
        if code != 0:
            raise ConversionError(
                code="no_subtitle_stream",
                title="This file has no subtitle track to extract",
                detail=f"{item.name} contains no embedded subtitles"
                       + (f" at track {track}." if track else "."),
                suggestions=[
                    "Check the media information in the queue for its stream list.",
                    "If the subtitles are burned into the picture, only OCR can read them.",
                ],
            )
        outputs.append(out)
    return outputs


async def _video_to_gif(ctx: RunContext) -> list[Path]:
    """Two-pass palette generation; one pass produces badly dithered output."""
    engines.require("ffmpeg")
    outputs: list[Path] = []
    total = len(ctx.inputs)
    fps = int(ctx.option("frame_rate", 12) or 12)
    width = int(ctx.option("gif_width", 480) or 480)

    for index, item in enumerate(ctx.inputs, start=1):
        base = (index - 1) / total
        ctx.progress(base, f"Analysing colours in {item.name}")
        palette = ctx.workdir / f"palette-{index}.png"
        chain = f"fps={fps},scale={width}:-1:flags=lanczos"

        await engines.run("ffmpeg", [
            "-hide_banner", "-loglevel", "error", "-y", *_time_args(ctx),
            "-i", str(item.path), "-vf", f"{chain},palettegen=stats_mode=diff",
            str(palette),
        ], input_name=item.name)

        out = target_path(ctx, item.name, "gif", index=index)
        ctx.progress(base + 0.5 / total, f"Building {item.name}")
        await engines.run("ffmpeg", [
            "-hide_banner", "-loglevel", "error", "-y", *_time_args(ctx),
            "-i", str(item.path), "-i", str(palette),
            "-lavfi", f"{chain}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3",
            "-loop", "0", str(out),
        ], input_name=item.name)

        size_mb = out.stat().st_size / 1_048_576
        if size_mb > 8:
            ctx.warn(
                "This GIF is large",
                f"{out.name} is {size_mb:.1f} MB. Lower the frame rate or width, "
                "or trim the clip.",
            )
        outputs.append(out)
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


# --------------------------------------------------------------------- options


def _encoder_choices(kind: str, container: str | None) -> tuple[Choice, ...]:
    """Only encoders this FFmpeg actually has, best first."""
    media = CAPS.formats.get(container or "")
    default = (media.default_video if kind == "video" else media.default_audio) if media else None
    options = [Choice("auto", f"Automatic ({default or 'best available'})",
                      "Chooses the best encoder installed for this container")]
    seen = set()
    for preferred in _ENCODER_PREFERENCE.get(default or "", []):
        if preferred in CAPS.encoders and preferred not in seen:
            seen.add(preferred)
            options.append(Choice(preferred, preferred, CAPS.encoders[preferred].description[:70]))
    for codec in CAPS.encoders.values():
        if codec.kind != kind or codec.name in seen:
            continue
        seen.add(codec.name)
        options.append(Choice(codec.name, codec.name, codec.description[:70]))
    return tuple(options[:40])


def _transcode_options(source: str, target: str) -> list[Option]:
    target_category = _category(target)
    is_video = target_category == formats.VIDEO
    container = _muxer_for(target)

    opts: list[Option] = [
        Option(id="quality_mode", label="Quality", type="select", default="balanced",
               help="Sets the compression level for this conversion.",
               choices=(
                   Choice("high", "High", "Close to the source, larger file"),
                   Choice("balanced", "Balanced", "The usual choice"),
                   Choice("small", "Small", "Noticeably compressed, easy to share"),
               ), advanced=False),
        Option(id="trim_start", label="Start at", type="text", default="",
               help="Blank starts at the beginning. Use a time like 00:01:30.",
               advanced=False),
        Option(id="trim_end", label="Stop at", type="text", default="",
               help="Blank runs to the end. Use a time like 00:04:15.",
               advanced=False),
    ]

    if is_video:
        opts += [
            Option(id="resolution", label="Resolution", type="select", default="source",
                   help="Scaling down is the most effective way to shrink a video.",
                   choices=(
                       Choice("source", "Keep original"), Choice("2160", "4K — 2160p"),
                       Choice("1440", "1440p"), Choice("1080", "Full HD — 1080p"),
                       Choice("720", "HD — 720p"), Choice("480", "SD — 480p"),
                       Choice("360", "360p"),
                   ), advanced=False),
            Option(id="fps", label="Frame rate", type="select", default="source",
                   help="", choices=(
                       Choice("source", "Keep original"), Choice("60", "60 fps"),
                       Choice("30", "30 fps"), Choice("25", "25 fps"),
                       Choice("24", "24 fps"), Choice("15", "15 fps"),
                   )),
            Option(id="crop", label="Crop to aspect ratio", type="select", default="none",
                   help="Trims the edges to fit a shape, keeping the centre.",
                   choices=(
                       Choice("none", "No crop"), Choice("16:9", "16:9 widescreen"),
                       Choice("4:3", "4:3"), Choice("1:1", "1:1 square"),
                       Choice("9:16", "9:16 vertical"),
                   )),
            Option(id="rotate", label="Rotate", type="select", default="0",
                   help="", choices=(
                       Choice("0", "None"), Choice("90", "90° clockwise"),
                       Choice("180", "180°"), Choice("270", "90° anticlockwise"),
                   )),
            Option(id="flip", label="Flip", type="select", default="none",
                   help="", choices=(
                       Choice("none", "None"), Choice("horizontal", "Horizontally"),
                       Choice("vertical", "Vertically"),
                   )),
            Option(id="video_encoder", label="Video encoder", type="select",
                   default="auto",
                   help="Only encoders installed in this container are listed.",
                   choices=_encoder_choices("video", container)),
            Option(id="video_bitrate", label="Video bitrate", type="text", default="",
                   help="Blank uses constant quality, which is usually better. "
                        "Otherwise give a rate like 2M or 800k."),
            Option(id="denoise", label="Reduce noise", type="boolean", default=False,
                   help="Smooths grain in low-light footage. Slower to encode."),
            Option(id="drop_audio", label="Remove the audio track", type="boolean",
                   default=False, help="Produces a silent video."),
            Option(id="compatibility", label="Maximise device compatibility",
                   type="boolean", default=False,
                   help="Uses widely supported settings so the file plays on older "
                        "devices, at some cost in efficiency."),
        ]
    else:
        opts += [
            Option(id="audio_encoder", label="Audio encoder", type="select",
                   default="auto",
                   help="Only encoders installed in this container are listed.",
                   choices=_encoder_choices("audio", container)),
            Option(id="sample_rate", label="Sample rate", type="select", default="source",
                   help="", choices=(
                       Choice("source", "Keep original"), Choice("48000", "48 kHz"),
                       Choice("44100", "44.1 kHz — CD quality"),
                       Choice("22050", "22.05 kHz"), Choice("16000", "16 kHz — speech"),
                   )),
            Option(id="channels", label="Channels", type="select", default="source",
                   help="", choices=(
                       Choice("source", "Keep original"), Choice("2", "Stereo"),
                       Choice("1", "Mono", "Halves the size of speech recordings"),
                   )),
            Option(id="normalize", label="Even out the volume", type="boolean",
                   default=False,
                   help="Brings quiet and loud sections closer together."),
            Option(id="volume", label="Volume adjustment", type="number", default=0,
                   help="Positive is louder, negative quieter.",
                   minimum=-30, maximum=30, step=1, unit="dB"),
        ]

    opts += [
        Option(id="reencode", label="Re-encoding", type="select", default="auto",
               help="Copying streams is instant and lossless, but only works when "
                    "the target container accepts the existing codecs.",
               choices=(
                   Choice("auto", "Automatic", "Copy when possible, re-encode when not"),
                   Choice("always", "Always re-encode", "Applies every setting exactly"),
                   Choice("never", "Copy only", "Fails rather than re-encoding"),
               )),
        Option(id="subtitle_handling", label="Embedded subtitles", type="select",
               default="keep", help="",
               choices=(Choice("keep", "Keep if the container supports them"),
                        Choice("drop", "Remove"))),
        Option(id="keep_metadata", label="Keep metadata", type="boolean", default=True,
               help="Preserves title, artist, creation date and similar tags."),
        NAMING_OPTION, OVERWRITE_OPTION,
    ]
    return opts


def _media_warnings(inputs: Sequence[InputFile], target: str, options: dict) -> list[dict]:
    notices: list[dict] = []
    target_fmt = _fmt(target)
    sources = {i.format_id for i in inputs}
    from_video = any(_category(s) == formats.VIDEO for s in sources)
    to_audio = target_fmt is not None and target_fmt.category == formats.AUDIO
    lossy_sources = sources & {"mp3", "aac", "ogg", "opus", "wma"}

    if from_video and to_audio:
        notices.append({
            "level": "info",
            "title": "Only the sound is kept",
            "detail": "The picture is discarded and the audio track is extracted.",
        })
    if lossy_sources and target in ("flac", "wav", "alac"):
        notices.append({
            "level": "warning",
            "title": "This will not restore lost quality",
            "detail": f"The source is already compressed, so converting to "
                      f"{formats.label(target)} produces a much larger file containing "
                      "exactly the same audio.",
        })
    if target in ("mp3", "aac", "ogg", "opus") and lossy_sources:
        notices.append({
            "level": "warning",
            "title": "Re-compressing already compressed audio",
            "detail": "Some quality is lost each time. Use High if this file will be "
                      "converted again later.",
        })
    if options.get("drop_audio"):
        notices.append({
            "level": "warning",
            "title": "The audio track will be removed",
            "detail": "The output will be silent.",
        })
    if target_fmt and target_fmt.discovered:
        notices.append({
            "level": "info",
            "title": f"{target_fmt.label} was discovered from FFmpeg",
            "detail": "It is fully supported by the installed engine, but there is no "
                      "hand-written guidance about its trade-offs.",
        })
    if str(options.get("reencode", "auto")) == "never":
        notices.append({
            "level": "info",
            "title": "Streams will be copied, not re-encoded",
            "detail": "Quality settings are ignored in this mode, and the conversion "
                      "fails if the container cannot hold the existing codecs.",
        })
    return notices


def _media_presets(source: str, target: str) -> list[Preset]:
    is_video = _category(target) == formats.VIDEO
    presets = [
        Preset("fast", "Fast — no re-encode",
               "Changes the container only. Instant and lossless where possible.",
               {"reencode": "auto", "quality_mode": "high"}),
        Preset("best", "Best quality",
               "Near-transparent quality at a larger file size.",
               {"quality_mode": "high", "reencode": "always", "keep_metadata": True}),
        Preset("balanced", "Balanced",
               "The usual choice: good quality, reasonable size.",
               {"quality_mode": "balanced", "reencode": "always"}),
        Preset("small", "Smallest file",
               "Heavier compression for sharing and storage.",
               {"quality_mode": "small", "reencode": "always", "keep_metadata": False}),
    ]
    if is_video:
        presets += [
            Preset("web", "Web optimised",
                   "1080p, fast-start, widely playable in browsers.",
                   {"quality_mode": "balanced", "resolution": "1080",
                    "reencode": "always", "compatibility": False}),
            Preset("compatible", "Maximum compatibility",
                   "Baseline settings so the file plays on old devices.",
                   {"quality_mode": "balanced", "compatibility": True,
                    "reencode": "always", "resolution": "720"}),
            Preset("social", "Vertical for social",
                   "Cropped to 9:16 at 1080p.",
                   {"crop": "9:16", "resolution": "1080", "quality_mode": "balanced",
                    "reencode": "always"}),
        ]
    else:
        presets += [
            Preset("speech", "Speech / podcast",
                   "Mono at 16 kHz with levels evened out. Much smaller.",
                   {"channels": "1", "sample_rate": "16000", "normalize": True,
                    "quality_mode": "small", "reencode": "always"}),
        ]
    return presets


def register_all() -> None:
    """Register the media converters against discovered capability."""
    if not CAPS.available:
        return

    readable = media_inputs()
    audio_out = media_outputs("audio")
    video_out = media_outputs("video")
    image_ids = frozenset(
        f.id for f in formats.all_formats() if f.category == formats.IMAGE
    )

    if readable and (audio_out or video_out):
        register(Converter(
            id="media.transcode",
            label="Media converter (FFmpeg)",
            description=(
                "Convert between any audio or video format this FFmpeg supports, "
                "with trimming, scaling, rotation and stream control."
            ),
            inputs=readable,
            outputs=audio_out | video_out,
            run=_transcode,
            engines=("ffmpeg",),
            cardinality="one_to_one",
            quality=90,
            options_for=_transcode_options,
            presets_for=_media_presets,
            warnings_for=_media_warnings,
            plan_for=plan,
        ))

    if readable and image_ids:
        register(Converter(
            id="media.frames",
            label="Extract frames (FFmpeg)",
            description="Pull still images out of a video at a chosen rate.",
            inputs=frozenset(f for f in readable if _category(f) == formats.VIDEO),
            outputs=frozenset({"png", "jpeg", "webp"}) & image_ids,
            run=_extract_frames,
            engines=("ffmpeg",),
            cardinality=ONE_TO_MANY,
            quality=85,
            options_for=lambda s, t: [
                Option(id="frame_selection", label="Which frames", type="select",
                       default="fps", help="",
                       choices=(
                           Choice("fps", "At a fixed rate"),
                           Choice("keyframes", "Keyframes only",
                                  "Scene changes and cuts — far fewer images"),
                       ), advanced=False),
                Option(id="frame_rate", label="Frames per second", type="number",
                       default=1, help="1 gives one image per second of video.",
                       minimum=0.1, maximum=60, step=0.1,
                       depends_on=("frame_selection", ("fps",)), advanced=False),
                Option(id="frame_limit", label="Stop after", type="number", default=0,
                       help="0 extracts every matching frame.",
                       minimum=0, maximum=10000, step=1, unit="images", advanced=False),
                Option(id="trim_start", label="Start at", type="text", default="",
                       help="A time like 00:00:30.", advanced=False),
                Option(id="trim_end", label="Stop at", type="text", default="", help=""),
                Option(id="quality_mode", label="Image quality", type="select",
                       default="balanced", help="",
                       choices=(Choice("high", "High"), Choice("balanced", "Balanced"),
                                Choice("small", "Small"))),
                NAMING_OPTION, OVERWRITE_OPTION,
            ],
            warnings_for=lambda inputs, target, options: [{
                "level": "info",
                "title": "This can produce a great many files",
                "detail": "A ten-minute clip at one frame per second is 600 images. "
                          "Use keyframes, or set a limit.",
            }],
        ))

        register(Converter(
            id="media.thumbnail",
            label="Video thumbnail (FFmpeg)",
            description="Capture one representative still from a video.",
            inputs=frozenset(f for f in readable if _category(f) == formats.VIDEO),
            outputs=frozenset({"png", "jpeg", "webp"}) & image_ids,
            run=_thumbnail,
            engines=("ffmpeg",),
            cardinality="one_to_one",
            quality=70,
            options_for=lambda s, t: [
                Option(id="thumbnail_smart", label="Pick the frame automatically",
                       type="boolean", default=True,
                       help="Chooses a representative frame rather than the first one, "
                            "which is often black.", advanced=False),
                Option(id="thumbnail_at", label="Or take it at", type="text", default="",
                       help="A time like 00:01:05. Overrides automatic selection.",
                       advanced=False),
                Option(id="thumbnail_width", label="Width", type="number", default=0,
                       help="0 keeps the video's own width.",
                       minimum=0, maximum=4000, step=10, unit="px", advanced=False),
                NAMING_OPTION, OVERWRITE_OPTION,
            ],
        ))

        register(Converter(
            id="media.sequence",
            label="Images to video (FFmpeg)",
            description="Assemble a set of images into a video or animation, in order.",
            inputs=image_ids,
            outputs=video_out,
            run=_images_to_video,
            engines=("ffmpeg",),
            cardinality=MANY_TO_ONE,
            quality=80,
            options_for=lambda s, t: [
                Option(id="frame_rate", label="Frames per second", type="number",
                       default=2, help="2 shows each image for half a second.",
                       minimum=0.1, maximum=60, step=0.1, advanced=False),
                Option(id="quality_mode", label="Quality", type="select",
                       default="balanced", help="",
                       choices=(Choice("high", "High"), Choice("balanced", "Balanced"),
                                Choice("small", "Small")), advanced=False),
                Option(id="video_encoder", label="Video encoder", type="select",
                       default="auto", help="",
                       choices=_encoder_choices("video", None)),
                NAMING_OPTION, OVERWRITE_OPTION,
            ],
            warnings_for=lambda inputs, target, options: [{
                "level": "info",
                "title": "Order follows the queue",
                "detail": "The images are used in the order shown on the left. Drag "
                          "them to change it.",
            }],
        ))

    subtitle_out = frozenset(
        f.id for f in formats.all_formats()
        if f.category == formats.SUBTITLE and CAPS.can_write(f.ffmpeg or f.id)
    )
    if readable and subtitle_out:
        register(Converter(
            id="media.subtitles",
            label="Extract subtitles (FFmpeg)",
            description="Pull an embedded subtitle track out of a video file.",
            inputs=frozenset(f for f in readable if _category(f) == formats.VIDEO),
            outputs=subtitle_out,
            run=_extract_subtitles,
            engines=("ffmpeg",),
            cardinality="one_to_one",
            quality=80,
            options_for=lambda s, t: [
                Option(id="subtitle_track", label="Track number", type="number",
                       default=0, help="0 is the first subtitle track.",
                       minimum=0, maximum=32, step=1, advanced=False),
                NAMING_OPTION, OVERWRITE_OPTION,
            ],
        ))

    if readable and "gif" in image_ids:
        register(Converter(
            id="media.gif",
            label="Video to GIF (FFmpeg)",
            description="Turn a short clip into an animated GIF with an optimised palette.",
            inputs=frozenset(f for f in readable if _category(f) == formats.VIDEO),
            outputs=frozenset({"gif"}),
            run=_video_to_gif,
            engines=("ffmpeg",),
            quality=95,
            options_for=lambda s, t: [
                Option(id="frame_rate", label="Frame rate", type="number", default=12,
                       help="10-15 looks smooth enough for most clips and stays small.",
                       minimum=5, maximum=30, step=1, unit="fps", advanced=False),
                Option(id="gif_width", label="Width", type="number", default=480,
                       help="Height follows to keep the proportions.",
                       minimum=120, maximum=1280, step=10, unit="px", advanced=False),
                Option(id="trim_start", label="Start at", type="text", default="",
                       help="A time like 00:00:05.", advanced=False),
                Option(id="trim_end", label="Stop at", type="text", default="",
                       help="Keep GIFs short.", advanced=False),
                NAMING_OPTION, OVERWRITE_OPTION,
            ],
            warnings_for=lambda inputs, target, options: [{
                "level": "warning",
                "title": "GIF is large and limited to 256 colours",
                "detail": "File size grows quickly with length and width.",
            }],
        ))
