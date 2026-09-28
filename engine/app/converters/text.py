"""Subtitles and plain-text transformations."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from ..errors import ConversionError
from ..registry import Choice, Converter, Option, RunContext, register
from .base import NAMING_OPTION, OVERWRITE_OPTION, target_path

_CUE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[.,]\d{3})"
)


def _parse_cues(text: str) -> list[tuple[str, str, str]]:
    """Read SRT or WebVTT into (start, end, text) triples."""
    cues: list[tuple[str, str, str]] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
            if not lines:
                continue
        timing_index = next((i for i, line in enumerate(lines) if _CUE.search(line)), None)
        if timing_index is None:
            continue
        match = _CUE.search(lines[timing_index])
        assert match is not None
        body = " ".join(lines[timing_index + 1:]).strip()
        if body:
            cues.append((match.group("start"), match.group("end"), body))
    return cues


def _normalise(stamp: str, style: str) -> str:
    stamp = stamp.strip()
    return stamp.replace(",", ".") if style == "vtt" else stamp.replace(".", ",")


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


async def _convert_subtitles(ctx: RunContext) -> list[Path]:
    outputs: list[Path] = []
    total = len(ctx.inputs)
    offset = float(ctx.option("time_offset", 0) or 0)

    def shift(stamp: str) -> str:
        if not offset:
            return stamp
        hours, minutes, rest = stamp.replace(",", ".").split(":")
        seconds = int(hours) * 3600 + int(minutes) * 60 + float(rest) + offset
        seconds = max(0.0, seconds)
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Converting {item.name}")

        def work() -> Path:
            cues = _parse_cues(item.path.read_text("utf-8", "replace"))
            if not cues:
                raise ConversionError(
                    code="no_cues",
                    title="No subtitles were found in this file",
                    detail=f"{item.name} contains no timed text blocks.",
                    suggestions=[
                        "Confirm the file is a subtitle track rather than a plain "
                        "transcript.",
                    ],
                )
            out = target_path(ctx, item.name, ctx.target, index=index)
            if ctx.target == "vtt":
                parts = ["WEBVTT", ""]
                for start, end, body in cues:
                    parts.append(
                        f"{_normalise(shift(start), 'vtt')} --> "
                        f"{_normalise(shift(end), 'vtt')}"
                    )
                    parts += [body, ""]
                out.write_text("\n".join(parts), encoding="utf-8")
            elif ctx.target == "srt":
                parts = []
                for number, (start, end, body) in enumerate(cues, start=1):
                    parts += [
                        str(number),
                        f"{_normalise(shift(start), 'srt')} --> "
                        f"{_normalise(shift(end), 'srt')}",
                        _strip_tags(body),
                        "",
                    ]
                out.write_text("\n".join(parts), encoding="utf-8")
            else:  # txt or md — a readable transcript
                if ctx.option("keep_timestamps", False):
                    lines = [
                        f"[{start.split('.')[0].split(',')[0]}] {_strip_tags(body)}"
                        for start, _, body in cues
                    ]
                else:
                    lines, previous = [], ""
                    for _, _, body in cues:
                        clean = _strip_tags(body).strip()
                        # Rolling captions repeat the previous line; collapse them.
                        if clean and clean != previous:
                            lines.append(clean)
                            previous = clean
                out.write_text("\n".join(lines) + "\n", encoding="utf-8")
                ctx.note("Transcript created",
                         f"{len(cues)} subtitle cues became {len(lines)} lines of text.")
            return out

        outputs.append(await asyncio.to_thread(work))
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


async def _convert_text(ctx: RunContext) -> list[Path]:
    """Re-encode or re-wrap plain text without going through a markup engine."""
    outputs: list[Path] = []
    total = len(ctx.inputs)
    source_encoding = str(ctx.option("source_encoding", "utf-8"))
    target_encoding = str(ctx.option("target_encoding", "utf-8"))
    line_ending = str(ctx.option("line_ending", "lf"))

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Converting {item.name}")

        def work() -> Path:
            raw = item.path.read_bytes()
            try:
                text = raw.decode(source_encoding)
            except (UnicodeDecodeError, LookupError):
                text = raw.decode("utf-8", "replace")
                ctx.warn(
                    "Some characters could not be decoded",
                    f"{item.name} is not valid {source_encoding}. Unreadable bytes "
                    "were replaced with a placeholder character.",
                )
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if line_ending == "crlf":
                text = text.replace("\n", "\r\n")
            out = target_path(ctx, item.name, ctx.target, index=index)
            try:
                out.write_bytes(text.encode(target_encoding, "replace"))
            except LookupError:
                out.write_text(text, encoding="utf-8")
            return out

        outputs.append(await asyncio.to_thread(work))
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


def register_all() -> None:
    register(Converter(
        id="subtitle.convert",
        label="Subtitle converter",
        description="Convert between subtitle formats or pull out a plain transcript.",
        inputs=frozenset(("srt", "vtt")),
        outputs=frozenset(("srt", "vtt", "txt", "md")),
        run=_convert_subtitles,
        cardinality="one_to_one",
        quality=95,
        options_for=lambda s, t: [
            *(
                [Option(id="keep_timestamps", label="Keep timestamps", type="boolean",
                        default=False,
                        help="Prefix each line with the time it appears.", advanced=False)]
                if t in ("txt", "md") else []
            ),
            Option(id="time_offset", label="Shift all timings", type="number", default=0,
                   help="Positive delays the subtitles, negative brings them forward.",
                   minimum=-600, maximum=600, step=0.1, unit="seconds"),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
        warnings_for=lambda inputs, target, options: (
            [{
                "level": "info",
                "title": "Timing information is discarded",
                "detail": "A transcript keeps the words in order but loses the times "
                          "each line appears.",
            }] if target in ("txt", "md") and not options.get("keep_timestamps") else []
        ),
    ))

    register(Converter(
        id="text.encoding",
        label="Plain text converter",
        description="Change a text file's encoding or line endings.",
        inputs=frozenset(("txt", "md", "rst", "csv", "json", "yaml", "xml", "html")),
        outputs=frozenset(("txt",)),
        run=_convert_text,
        cardinality="one_to_one",
        # Low priority: markup engines handle these routes far better.
        quality=20,
        options_for=lambda s, t: [
            Option(id="source_encoding", label="Encoding of the source", type="select",
                   default="utf-8", help="",
                   choices=(Choice("utf-8", "UTF-8"), Choice("latin-1", "Latin-1"),
                            Choice("cp1252", "Windows-1252"), Choice("utf-16", "UTF-16"),
                            Choice("shift_jis", "Shift-JIS")), advanced=False),
            Option(id="target_encoding", label="Encoding to write", type="select",
                   default="utf-8", help="UTF-8 is the right answer almost always.",
                   choices=(Choice("utf-8", "UTF-8"), Choice("latin-1", "Latin-1"),
                            Choice("cp1252", "Windows-1252"), Choice("utf-16", "UTF-16"))),
            Option(id="line_ending", label="Line endings", type="select", default="lf",
                   help="", choices=(
                       Choice("lf", "Unix — LF", "macOS, Linux, and most tools"),
                       Choice("crlf", "Windows — CRLF", "Windows Notepad and older software"),
                   )),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
    ))
