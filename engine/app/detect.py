"""Format detection and input inspection.

Detection reads the file's actual bytes first and falls back to the extension,
so a mislabelled file is still understood. Whatever it learns along the way
(page counts, image dimensions, media duration, table shape) is returned as
metadata and reused for previews, recommendations and warnings.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from . import formats

# Magic-number signatures, checked in order. Longest and most specific first.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"\x00\x00\x01\x00", "ico"),
    (b"7z\xbc\xaf\x27\x1c", "sevenzip"),
    (b"Rar!\x1a\x07", "rar"),
    (b"\x1f\x8b", "targz"),
    (b"BZh", "tarbz2"),
    (b"\xfd7zXZ\x00", "tarxz"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "doc"),  # OLE2: doc/xls/ppt
    (b"ID3", "mp3"),
    (b"OggS", "ogg"),
    (b"fLaC", "flac"),
    (b"\x1aE\xdf\xa3", "mkv"),
)

# Inside a ZIP container, these entry names identify the real format.
_ZIP_MARKERS: tuple[tuple[str, str], ...] = (
    ("word/document.xml", "docx"),
    ("xl/workbook.xml", "xlsx"),
    ("ppt/presentation.xml", "pptx"),
    ("mimetypeapplication/vnd.oasis.opendocument.text", "odt"),
    ("mimetypeapplication/vnd.oasis.opendocument.spreadsheet", "ods"),
    ("mimetypeapplication/vnd.oasis.opendocument.presentation", "odp"),
    ("mimetypeapplication/epub+zip", "epub"),
)


@dataclass
class Detection:
    format_id: str | None
    confidence: str  # "certain" | "likely" | "uncertain" | "unknown"
    metadata: dict = field(default_factory=dict)
    notices: list[dict] = field(default_factory=list)
    preview: dict | None = None

    def notice(self, level: str, title: str, detail: str) -> None:
        self.notices.append({"level": level, "title": title, "detail": detail})


def _sniff_container(path: Path, head: bytes) -> str | None:
    """ZIP-based formats all start with PK; look inside to tell them apart."""
    if not head.startswith(b"PK\x03\x04"):
        return None
    try:
        import zipfile

        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for marker, fmt in _ZIP_MARKERS:
                if marker.startswith("mimetype"):
                    if "mimetype" in names:
                        declared = zf.read("mimetype").decode("utf-8", "replace").strip()
                        if marker == "mimetype" + declared:
                            return fmt
                elif marker in names:
                    return fmt
    except Exception:
        return "zip"
    return "zip"


def _sniff_text(path: Path, head: bytes) -> str | None:
    """Distinguish the text-shaped formats by structure, not by extension."""
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return None
    stripped = text.lstrip()
    if not stripped:
        return "txt"
    if stripped.startswith("<?xml"):
        if "<svg" in text[:2000]:
            return "svg"
        return "xml"
    if stripped.startswith("<svg"):
        return "svg"
    if stripped[:1] in "{[":
        try:
            json.loads(path.read_text("utf-8"))
            return "json"
        except Exception:
            # One object per line is JSON Lines rather than malformed JSON.
            first = stripped.splitlines()[0]
            try:
                json.loads(first)
                return "jsonl"
            except Exception:
                return None
    lowered = stripped[:400].lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        return "html"
    if stripped.startswith("WEBVTT"):
        return "vtt"
    if "-->" in text and any(line.strip().isdigit() for line in text.splitlines()[:4]):
        return "srt"
    return None


async def _probe_media(path: Path) -> dict | None:
    from . import engines

    if not engines.available("ffmpeg"):
        return None
    ffprobe = engines.probe_binary()
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    try:
        return json.loads(stdout)
    except Exception:
        return None


def _inspect_image(path: Path, det: Detection) -> None:
    try:
        from PIL import Image

        with Image.open(path) as img:
            det.metadata.update({
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
                "hasAlpha": img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info,
                "frames": getattr(img, "n_frames", 1),
                "megapixels": round(img.width * img.height / 1_000_000, 2),
            })
            if getattr(img, "n_frames", 1) > 1:
                det.notice("info", "Animated image",
                           f"This image has {img.n_frames} frames.")
    except Exception as exc:
        det.notice("warning", "Image could not be fully read", str(exc)[:200])


def _inspect_pdf(path: Path, det: Detection) -> None:
    import pypdf

    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as exc:
        det.notice(
            "error", "This PDF appears corrupted or incomplete",
            "Its structure could not be read, which usually means the file was "
            "truncated during a download or copy. "
            f"({type(exc).__name__})",
        )
        det.metadata["readable"] = False
        return

    # Encryption is checked before anything else is attempted: a locked file
    # produces library errors that would otherwise mask the real reason.
    try:
        encrypted = bool(reader.is_encrypted)
    except Exception:
        encrypted = False
    det.metadata["encrypted"] = encrypted

    if encrypted:
        opened = False
        try:
            opened = bool(reader.decrypt(""))
        except Exception:
            opened = False
        if not opened:
            det.notice(
                "error", "This PDF is password protected",
                "Its pages cannot be read until an unprotected copy is provided. "
                "Open it in a PDF reader with the password and save a copy without one.",
            )
            return

    try:
        pages = len(reader.pages)
    except Exception:
        det.notice(
            "error", "This PDF appears corrupted or incomplete",
            "The file starts like a PDF but its page list could not be read.",
        )
        det.metadata["readable"] = False
        return

    det.metadata["pages"] = pages
    sample = ""
    unreadable = 0
    for page in reader.pages[:3]:
        try:
            sample += page.extract_text() or ""
        except Exception:
            unreadable += 1

    if unreadable and not sample.strip():
        det.notice(
            "warning", "Some pages could not be read",
            f"{unreadable} of the first pages are damaged. Conversion may produce "
            "incomplete output.",
        )

    has_text = len(sample.strip()) > 40
    det.metadata["hasTextLayer"] = has_text
    det.preview = {"kind": "text", "text": sample[:1500]}
    if not has_text and not unreadable:
        det.notice(
            "info", "This looks like a scanned PDF",
            "No selectable text was found, so text output needs OCR. Choose an OCR "
            "output and the engine will read the pages as images.",
        )


def _inspect_tabular(path: Path, fmt: str, det: Detection) -> None:
    try:
        from .converters import data as data_conv

        frame = data_conv.read_table(path, fmt, nrows=50)
        det.metadata.update({
            "columns": [str(c) for c in frame.columns][:60],
            "columnCount": int(frame.shape[1]),
            "sampledRows": int(frame.shape[0]),
        })
        det.preview = {
            "kind": "table",
            "columns": [str(c) for c in frame.columns][:12],
            "rows": [
                [("" if v is None else str(v))[:80] for v in row[:12]]
                for row in frame.head(8).values.tolist()
            ],
        }
    except Exception as exc:
        det.notice("warning", "Data could not be parsed as a table", str(exc)[:200])


def _inspect_text(path: Path, det: Detection) -> None:
    try:
        raw = path.read_bytes()[:200_000]
        text = raw.decode("utf-8", "replace")
        det.metadata.update({
            "lines": text.count("\n") + 1,
            "characters": len(text),
            "words": len(text.split()),
        })
        det.preview = {"kind": "text", "text": text[:1500]}
    except Exception:
        pass


def _inspect_archive(path: Path, fmt: str, det: Detection) -> None:
    entries: list[str] = []
    try:
        if fmt == "zip":
            import zipfile

            with zipfile.ZipFile(path) as zf:
                entries = [i.filename for i in zf.infolist() if not i.is_dir()]
        elif fmt in ("tar", "targz", "tarbz2", "tarxz"):
            import tarfile

            with tarfile.open(path) as tf:
                entries = [m.name for m in tf.getmembers() if m.isfile()]
    except Exception as exc:
        det.notice("warning", "Archive could not be listed", str(exc)[:200])
        return
    if entries:
        det.metadata["entryCount"] = len(entries)
        det.preview = {"kind": "list", "items": entries[:25]}


async def detect(path: Path, original_name: str) -> Detection:
    """Identify a file and gather everything downstream steps will want."""
    head = b""
    try:
        with path.open("rb") as fh:
            head = fh.read(64_000)
    except OSError as exc:
        det = Detection(None, "unknown")
        det.notice("error", "This file could not be opened", str(exc)[:200])
        return det

    if not head:
        det = Detection(None, "unknown")
        det.notice(
            "error", "This file is empty",
            "It contains no data, so there is nothing to convert.",
        )
        return det

    format_id: str | None = None
    confidence = "uncertain"

    for signature, fmt in _SIGNATURES:
        if head.startswith(signature):
            format_id, confidence = fmt, "certain"
            break

    if format_id is None:
        container = _sniff_container(path, head)
        if container:
            format_id, confidence = container, "certain"

    if format_id is None and (b"ftyp" in head[:32]):
        brand = head[8:12].decode("ascii", "replace")
        format_id = "mov" if brand.startswith("qt") else ("heic" if brand in ("heic", "heix", "mif1") else "mp4")
        confidence = "certain"

    if format_id is None and head[:4] == b"RIFF":
        format_id = "wav" if head[8:12] == b"WAVE" else ("avi" if head[8:12] == b"AVI " else "webp")
        confidence = "certain"

    if format_id is None:
        sniffed = _sniff_text(path, head)
        if sniffed:
            format_id, confidence = sniffed, "likely"

    by_ext = formats.from_extension(original_name)

    if format_id is None:
        format_id = by_ext
        confidence = "likely" if by_ext else "unknown"
    elif format_id == "doc" and by_ext in ("xls", "ppt"):
        # OLE2 containers share a signature; trust the extension to tell them apart.
        format_id = by_ext
    elif by_ext and by_ext != format_id and confidence == "certain":
        alt = formats.label(by_ext)
        det_label = formats.label(format_id)
        # Text formats legitimately overlap (md is txt-shaped); don't cry wolf.
        text_ish = {"txt", "md", "csv", "tsv", "rst", "tex", "srt", "vtt", "yaml"}
        if not (format_id in text_ish and by_ext in text_ish):
            pass  # handled below once the Detection object exists

    det = Detection(format_id, confidence if format_id else "unknown")
    try:
        det.metadata["sizeBytes"] = path.stat().st_size
    except OSError:
        det.metadata["sizeBytes"] = 0

    if format_id is None:
        guessed, _ = mimetypes.guess_type(original_name)
        det.notice(
            "error", "This file type is not recognised",
            (
                f"Nothing in {original_name} matched a known format"
                + (f" (it claims to be {guessed})." if guessed else ".")
            ),
        )
        return det

    # Prefer the sniffed identity but say so when the extension disagreed.
    if by_ext and by_ext != format_id and confidence == "certain":
        text_ish = {"txt", "md", "csv", "tsv", "rst", "tex", "srt", "vtt", "yaml", "json"}
        if not (format_id in text_ish and by_ext in text_ish):
            det.notice(
                "info", "Extension and contents disagree",
                f"{original_name} is named like {formats.label(by_ext)} but its "
                f"contents are {formats.label(format_id)}. The contents win.",
            )

    fmt = formats.get(format_id)
    category = fmt.category if fmt else None

    if category == formats.IMAGE and format_id != "svg":
        _inspect_image(path, det)
    elif format_id == "pdf":
        _inspect_pdf(path, det)
    elif category == formats.DATA:
        _inspect_tabular(path, format_id, det)
    elif category in (formats.TEXT, formats.SUBTITLE) or format_id in ("html", "svg"):
        _inspect_text(path, det)
    elif category == formats.ARCHIVE:
        _inspect_archive(path, format_id, det)
    elif category in (formats.AUDIO, formats.VIDEO):
        probe = await _probe_media(path)
        if probe:
            fmt_info = probe.get("format", {})
            streams = probe.get("streams", [])
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
            det.metadata.update({
                "duration": round(float(fmt_info.get("duration", 0) or 0), 2),
                "bitrate": int(fmt_info.get("bit_rate", 0) or 0),
                "videoCodec": video.get("codec_name") if video else None,
                "audioCodec": audio.get("codec_name") if audio else None,
                "width": video.get("width") if video else None,
                "height": video.get("height") if video else None,
            })
            det.preview = {"kind": "media", "streams": [
                {"type": s.get("codec_type"), "codec": s.get("codec_name")}
                for s in streams[:6]
            ]}

    return det
