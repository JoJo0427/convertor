"""The format catalog.

One entry per format the product knows about. This is the single source of truth
for detection, the output picker, and the format discovery browser, so adding a
format means adding one record here plus a converter that claims it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

DOCUMENT = "document"
IMAGE = "image"
DATA = "data"
TEXT = "text"
AUDIO = "audio"
VIDEO = "video"
ARCHIVE = "archive"
SUBTITLE = "subtitle"

CATEGORY_LABELS = {
    DOCUMENT: "Documents",
    IMAGE: "Images",
    DATA: "Structured data",
    TEXT: "Text",
    AUDIO: "Audio",
    VIDEO: "Video",
    ARCHIVE: "Archives",
    SUBTITLE: "Subtitles",
}


@dataclass(frozen=True)
class Format:
    id: str
    label: str
    category: str
    extensions: tuple[str, ...]
    mime: str
    summary: str
    common_uses: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    # Formats that are containers of other files rather than a single document.
    multi: bool = False
    aliases: tuple[str, ...] = field(default=())
    # The name the owning engine knows this format by. For media formats this is
    # the FFmpeg muxer/demuxer name, which is often not the extension.
    ffmpeg: str = ""
    # True when this record came from probing an engine rather than the curated
    # list. Discovered formats have terser descriptions and are ranked lower.
    discovered: bool = False


_CURATED: tuple[Format, ...] = (
    # ---------------------------------------------------------------- documents
    Format(
        "pdf", "PDF", DOCUMENT, (".pdf",), "application/pdf",
        "A fixed-layout page format that looks identical everywhere it is opened.",
        ("Sharing final documents", "Printing", "Invoices and contracts", "Archiving"),
        ("Text in a scanned PDF is an image until OCR is run on it.",
         "Editing a PDF directly is limited; convert to DOCX for real editing."),
        aliases=("acrobat", "portable document format"),
    ),
    Format(
        "docx", "Word document", DOCUMENT, (".docx",),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "The modern Microsoft Word format, and the usual choice for editable documents.",
        ("Editing text", "Track changes", "Templates and letters"),
        ("Converting from PDF reconstructs the layout and can shift spacing.",),
        aliases=("word", "editable document"),
    ),
    Format(
        "doc", "Word 97-2003", DOCUMENT, (".doc",), "application/msword",
        "The legacy binary Word format, still common in older archives.",
        ("Opening older documents",),
        ("Read-only here: convert it to DOCX or PDF to work with it.",),
    ),
    Format(
        "odt", "OpenDocument text", DOCUMENT, (".odt",),
        "application/vnd.oasis.opendocument.text",
        "The open standard word processing format used by LibreOffice.",
        ("Open-source office workflows", "Long-term storage"),
    ),
    Format(
        "rtf", "Rich text", DOCUMENT, (".rtf",), "application/rtf",
        "A simple formatted text format that nearly every word processor can open.",
        ("Maximum compatibility", "Simple formatted text"),
        ("Supports basic formatting only; complex layouts are simplified.",),
    ),
    Format(
        "epub", "EPUB e-book", DOCUMENT, (".epub",), "application/epub+zip",
        "A reflowable e-book format that adapts to any screen size.",
        ("E-readers", "Long-form reading"),
        ("Reflowable by design, so fixed page layouts are not preserved.",),
    ),
    Format(
        "pptx", "PowerPoint", DOCUMENT, (".pptx",),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "Slide decks in the modern PowerPoint format.",
        ("Presentations",),
        ("Animations and transitions are lost when converting to PDF.",),
    ),
    Format(
        "ppt", "PowerPoint 97-2003", DOCUMENT, (".ppt",),
        "application/vnd.ms-powerpoint",
        "The legacy binary PowerPoint format.",
        ("Opening older decks", "Sending to very old software"),
    ),
    Format(
        "odp", "OpenDocument presentation", DOCUMENT, (".odp",),
        "application/vnd.oasis.opendocument.presentation",
        "The open standard slide format used by LibreOffice Impress.",
        ("Open-source presentation workflows",),
    ),
    Format(
        "html", "HTML", DOCUMENT, (".html", ".htm"), "text/html",
        "Web page markup, readable in any browser.",
        ("Web publishing", "Email templates", "Intermediate conversion step"),
    ),
    Format(
        "md", "Markdown", TEXT, (".md", ".markdown"), "text/markdown",
        "Plain text with lightweight formatting that stays readable as-is.",
        ("Notes and documentation", "READMEs", "Writing that converts cleanly"),
        aliases=("markdown",),
    ),
    Format(
        "txt", "Plain text", TEXT, (".txt", ".text", ".log"), "text/plain",
        "Unformatted text with no styling at all.",
        ("Maximum portability", "Extracted content", "Logs"),
        ("All formatting, images and layout are discarded.",),
    ),
    Format(
        "rst", "reStructuredText", TEXT, (".rst",), "text/x-rst",
        "A structured plain-text markup used widely in Python documentation.",
        ("Sphinx documentation",),
    ),
    Format(
        "tex", "LaTeX", TEXT, (".tex",), "application/x-tex",
        "A typesetting source format used for academic and technical writing.",
        ("Papers", "Theses", "Equation-heavy documents"),
    ),
    # ------------------------------------------------------------------- images
    Format(
        "png", "PNG", IMAGE, (".png",), "image/png",
        "A lossless image format with transparency support.",
        ("Screenshots", "Logos and icons", "Images with transparency"),
        ("Lossless, so files are larger than JPEG for photographs.",),
        aliases=("transparent image",),
    ),
    Format(
        "jpeg", "JPEG", IMAGE, (".jpg", ".jpeg", ".jpe", ".jfif"), "image/jpeg",
        "The standard lossy photo format, supported absolutely everywhere.",
        ("Photographs", "Email attachments", "Maximum compatibility"),
        ("No transparency: transparent areas become a solid background.",
         "Every re-save loses a little quality."),
        aliases=("jpg", "photo"),
    ),
    Format(
        "webp", "WebP", IMAGE, (".webp",), "image/webp",
        "A modern web image format that is markedly smaller than PNG or JPEG.",
        ("Website images", "Web performance"),
        ("Older software may not open it.",),
        aliases=("web image",),
    ),
    Format(
        "avif", "AVIF", IMAGE, (".avif",), "image/avif",
        "A very efficient modern image format with excellent compression.",
        ("Web images where the smallest file matters",),
        ("Support outside current browsers is still limited.",),
    ),
    Format(
        "gif", "GIF", IMAGE, (".gif",), "image/gif",
        "A limited-colour format best known for short looping animations.",
        ("Simple animations", "Reaction images"),
        ("Limited to 256 colours, so photographs band badly.",),
    ),
    Format(
        "tiff", "TIFF", IMAGE, (".tif", ".tiff"), "image/tiff",
        "A high-fidelity image format used in print and scanning.",
        ("Scanning", "Print production", "Archive quality"),
        ("Large files; often not viewable in browsers.",),
        aliases=("scan",),
    ),
    Format(
        "bmp", "Bitmap", IMAGE, (".bmp",), "image/bmp",
        "An uncompressed image format from the Windows ecosystem.",
        ("Legacy Windows software",),
        ("Very large files for what they contain.",),
    ),
    Format(
        "heic", "HEIC", IMAGE, (".heic", ".heif"), "image/heic",
        "The efficient photo format Apple devices capture by default.",
        ("iPhone photos",),
        ("Read-only here: convert to JPEG or PNG for wider compatibility.",),
    ),
    Format(
        "svg", "SVG", IMAGE, (".svg",), "image/svg+xml",
        "A vector image that stays sharp at any size.",
        ("Logos", "Icons", "Diagrams"),
        ("Converting to a pixel format fixes the resolution permanently.",),
        aliases=("vector",),
    ),
    Format(
        "eps", "Encapsulated PostScript", IMAGE, (".eps",), "application/postscript",
        "A vector format from the print and design world.",
        ("Print artwork", "Logos supplied by a designer"),
        ("Read only here: EPS is rendered to pixels or PDF, not written.",),
        aliases=("postscript",),
    ),
    Format(
        "ps", "PostScript", IMAGE, (".ps",), "application/postscript",
        "The page description language EPS is derived from.",
        ("Legacy print pipelines",),
        ("Read only here.",),
    ),
    Format(
        "ai", "Adobe Illustrator", IMAGE, (".ai",), "application/illustrator",
        "Illustrator artwork, which is a PDF underneath.",
        ("Design source files",),
        ("Read only here, and only the flattened artwork is recovered.",),
    ),
    Format(
        "ico", "Icon", IMAGE, (".ico",), "image/x-icon",
        "The Windows and browser favicon format.",
        ("Favicons", "Application icons"),
    ),
    # --------------------------------------------------------- structured data
    Format(
        "csv", "CSV", DATA, (".csv",), "text/csv",
        "Plain-text rows and columns that every data tool can read.",
        ("Data exchange", "Importing into other systems", "Spreadsheets"),
        ("Holds one sheet only, with no formulas, formatting or types.",),
        aliases=("comma separated",),
    ),
    Format(
        "tsv", "TSV", DATA, (".tsv", ".tab"), "text/tab-separated-values",
        "Like CSV but tab-separated, which avoids comma escaping problems.",
        ("Data with commas in values",),
    ),
    Format(
        "xlsx", "Excel workbook", DATA, (".xlsx",),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "The modern Excel format, supporting multiple sheets and formatting.",
        ("Spreadsheets", "Reports", "Sharing data with colleagues"),
        aliases=("excel", "spreadsheet"),
    ),
    Format(
        "xls", "Excel 97-2003", DATA, (".xls",), "application/vnd.ms-excel",
        "The legacy binary Excel format.",
        ("Older spreadsheets",),
        ("Read-only here: convert to XLSX or CSV.",),
    ),
    Format(
        "ods", "OpenDocument sheet", DATA, (".ods",),
        "application/vnd.oasis.opendocument.spreadsheet",
        "The open standard spreadsheet format used by LibreOffice.",
        ("Open-source office workflows",),
    ),
    Format(
        "json", "JSON", DATA, (".json",), "application/json",
        "The standard structured data format for APIs and configuration.",
        ("APIs", "Configuration", "Nested data"),
        ("Nested structures are flattened when converting to a table.",),
    ),
    Format(
        "jsonl", "JSON Lines", DATA, (".jsonl", ".ndjson"), "application/x-ndjson",
        "One JSON record per line, ideal for streaming and large datasets.",
        ("Log pipelines", "Large datasets", "Machine learning data"),
    ),
    Format(
        "xml", "XML", DATA, (".xml",), "application/xml",
        "A verbose but precise structured format common in enterprise systems.",
        ("Enterprise integrations", "Document standards"),
        ("Attributes and mixed content flatten imperfectly into tables.",),
    ),
    Format(
        "yaml", "YAML", DATA, (".yaml", ".yml"), "application/yaml",
        "A human-friendly structured format used heavily for configuration.",
        ("Configuration files", "CI pipelines"),
    ),
    Format(
        "parquet", "Parquet", DATA, (".parquet",), "application/vnd.apache.parquet",
        "A compressed columnar format built for analytical workloads.",
        ("Data warehousing", "Analytics at scale"),
        ("Not human-readable; needs a data tool to open.",),
    ),
    Format(
        "md_table", "Markdown table", DATA, (".md",), "text/markdown",
        "A table written in Markdown, ready to paste into notes or a README.",
        ("Documentation", "Pull request descriptions", "Notes"),
        ("Best for small tables; wide data becomes unreadable.",),
    ),
    # -------------------------------------------------------------------- media
    Format("mp3", "MP3", AUDIO, (".mp3",), "audio/mpeg",
           "The universally compatible lossy audio format.",
           ("Music", "Podcasts", "Sharing audio"), ffmpeg="mp3"),
    Format("wav", "WAV", AUDIO, (".wav",), "audio/wav",
           "Uncompressed audio at full quality.",
           ("Editing", "Mastering", "Transcription input"),
           ("Very large files.",), ffmpeg="wav"),
    Format("flac", "FLAC", AUDIO, (".flac",), "audio/flac",
           "Lossless compressed audio: full quality at roughly half the size of WAV.",
           ("Archiving music", "Audiophile libraries"), ffmpeg="flac"),
    Format("aac", "AAC", AUDIO, (".aac",), "audio/aac",
           "An efficient lossy format that sounds better than MP3 at the same size.",
           ("Apple devices", "Streaming"),
           aliases=("m4a",), ffmpeg="ipod"),
    Format("ogg", "Ogg Vorbis", AUDIO, (".ogg", ".oga"), "audio/ogg",
           "An open lossy audio format free of patent concerns.",
           ("Games", "Open-source projects"), ffmpeg="ogg"),
    Format("opus", "Opus", AUDIO, (".opus",), "audio/opus",
           "A modern low-latency codec that excels at speech and low bitrates.",
           ("Voice chat", "Podcasts at small sizes"), ffmpeg="opus"),
    Format("mp4", "MP4", VIDEO, (".mp4", ".m4v"), "video/mp4",
           "The default video container, playable on essentially any device.",
           ("Sharing video", "Web playback", "Mobile"), ffmpeg="mp4"),
    Format("mkv", "Matroska", VIDEO, (".mkv",), "video/x-matroska",
           "A flexible container that can hold many tracks and subtitles.",
           ("Media libraries", "Multi-audio releases"),
           ("Not natively playable in most browsers.",), ffmpeg="matroska"),
    Format("webm", "WebM", VIDEO, (".webm",), "video/webm",
           "An open video format designed for the web.",
           ("Web video", "Embedded players"), ffmpeg="webm"),
    # Containers that FFmpeg reaches through a muxer named after something else.
    #
    # Without these, a person looking for WMV, WMA, VOB or M4A finds nothing they
    # recognise: `.wmv` was folded into a record labelled "ASF (Advanced / Active
    # Streaming Format)", `.m4a` into "AAC", and `.vob` into "MPEG-2 PS (SVCD)",
    # which is the wrong format. The capability was always there — FFmpeg writes
    # all four — but the product only offered them under FFmpeg's internal names,
    # so as far as the user was concerned they did not exist.
    Format("wmv", "Windows Media Video", VIDEO, (".wmv", ".asf"), "video/x-ms-wmv",
           "Microsoft's video format, common in older Windows software.",
           ("Windows Media Player", "Older PowerPoint embeds"),
           ("Written through the ASF container.",),
           aliases=("asf", "windows media"), ffmpeg="asf"),
    Format("wma", "Windows Media Audio", AUDIO, (".wma",), "audio/x-ms-wma",
           "Microsoft's audio format, common on older Windows machines.",
           ("Windows Media Player", "Ripped CDs from Windows XP"),
           aliases=("windows media audio",), ffmpeg="asf"),
    Format("m4a", "M4A audio", AUDIO, (".m4a",), "audio/mp4",
           "Apple's audio container, what iTunes and Voice Memos produce.",
           ("Apple Music", "Voice recordings", "Audiobooks"),
           aliases=("aac", "itunes", "apple audio"), ffmpeg="ipod"),
    Format("vob", "DVD Video", VIDEO, (".vob",), "video/mpeg",
           "The video format found on DVDs.",
           ("Ripping DVDs", "Playing disc backups"),
           aliases=("dvd",), ffmpeg="dvd"),
    Format("mov", "QuickTime", VIDEO, (".mov",), "video/quicktime",
           "Apple's video container, produced by iPhones and editing software.",
           ("Apple ecosystem", "Video editing"), ffmpeg="mov"),
    Format("avi", "AVI", VIDEO, (".avi",), "video/x-msvideo",
           "A legacy Windows video container.",
           ("Old video files",),
           ("Inefficient by modern standards.",), ffmpeg="avi"),
    # ----------------------------------------------------------------- archives
    Format("zip", "ZIP archive", ARCHIVE, (".zip",), "application/zip",
           "The most widely supported way to bundle files together.",
           ("Sending multiple files", "Backups"), multi=True),
    Format("tar", "TAR archive", ARCHIVE, (".tar",), "application/x-tar",
           "An uncompressed bundle of files, standard on Unix systems.",
           ("Unix tooling",), multi=True),
    Format("targz", "TAR.GZ archive", ARCHIVE, (".tar.gz", ".tgz"), "application/gzip",
           "A compressed TAR bundle, the Unix default for distribution.",
           ("Source releases", "Server backups"), multi=True),
    Format("tarbz2", "TAR.BZ2 archive", ARCHIVE, (".tar.bz2", ".tbz2"),
           "application/x-bzip2",
           "A TAR bundle compressed with bzip2 — smaller than gzip, slower.",
           ("Source releases", "Long-term storage"), multi=True),
    Format("tarxz", "TAR.XZ archive", ARCHIVE, (".tar.xz", ".txz"),
           "application/x-xz",
           "A TAR bundle compressed with xz — the smallest of the three, slowest.",
           ("Distribution images", "Archival"), multi=True),
    Format("sevenzip", "7z archive", ARCHIVE, (".7z",), "application/x-7z-compressed",
           "A high-compression archive format.",
           ("Smallest archive size",), multi=True),
    Format("rar", "RAR archive", ARCHIVE, (".rar",), "application/vnd.rar",
           "A proprietary archive format.",
           ("Opening downloads",),
           ("Extract only: creating RAR archives is not supported.",), multi=True),
    Format("files", "Extracted files", ARCHIVE, (), "application/octet-stream",
           "The individual files held inside an archive.",
           ("Unpacking a download", "Getting at one file inside a bundle"),
           ("Each file keeps its original format once extracted.",), multi=True,
           aliases=("extract", "unzip", "unpack")),
    # ---------------------------------------------------------------- subtitles
    Format("srt", "SubRip subtitles", SUBTITLE, (".srt",), "application/x-subrip",
           "The most common subtitle format, plain text with timestamps.",
           ("Video subtitles",), ffmpeg="srt"),
    Format("vtt", "WebVTT subtitles", SUBTITLE, (".vtt",), "text/vtt",
           "The subtitle format used by HTML5 video players.",
           ("Web video captions",), ffmpeg="webvtt"),
)

BY_ID: dict[str, Format] = {}
_BY_EXTENSION: dict[str, str] = {}


def register(fmt: Format) -> Format:
    """Add a format to the catalog. Curated records are registered first, so a
    discovered format never steals an extension from a hand-written one."""
    BY_ID[fmt.id] = fmt
    for ext in fmt.extensions:
        # First registration wins, so .md maps to Markdown rather than md_table.
        _BY_EXTENSION.setdefault(ext, fmt.id)
    return fmt


def extension_is_claimed(extensions: Iterable[str]) -> bool:
    return any(ext in _BY_EXTENSION for ext in extensions)


for _fmt in _CURATED:
    register(_fmt)


def all_formats() -> tuple[Format, ...]:
    return tuple(BY_ID.values())


def curated() -> tuple[Format, ...]:
    return _CURATED


def get(format_id: str) -> Format | None:
    return BY_ID.get(format_id)


def label(format_id: str) -> str:
    fmt = BY_ID.get(format_id)
    return fmt.label if fmt else format_id.upper()


def from_extension(name: str) -> str | None:
    lowered = name.lower()
    # Longest extension first so .tar.gz beats .gz-style single suffixes.
    for ext in sorted(_BY_EXTENSION, key=len, reverse=True):
        if lowered.endswith(ext):
            return _BY_EXTENSION[ext]
    return None


def extension_for(format_id: str) -> str:
    fmt = BY_ID.get(format_id)
    return fmt.extensions[0] if fmt else f".{format_id}"


def search(query: str) -> list[Format]:
    """Match a format by id, label, category, use case or plain-language alias."""
    q = query.strip().lower()
    if not q:
        return list(all_formats())
    scored: list[tuple[int, Format]] = []
    for fmt in all_formats():
        haystacks: Iterable[tuple[str, int]] = (
            (fmt.id, 100),
            (fmt.label.lower(), 90),
            (" ".join(fmt.aliases), 80),
            (" ".join(fmt.extensions), 70),
            (CATEGORY_LABELS[fmt.category].lower(), 40),
            (fmt.summary.lower(), 30),
            (" ".join(fmt.common_uses).lower(), 25),
        )
        best = 0
        for text, weight in haystacks:
            if q == text:
                best = max(best, weight + 10)
            elif q in text:
                best = max(best, weight)
        if best:
            scored.append((best, fmt))
    scored.sort(key=lambda pair: (-pair[0], pair[1].label))
    return [fmt for _, fmt in scored]
