"""Document conversion: office files, markup, and PDF page operations."""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from functools import lru_cache
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
    Preset,
    RunContext,
    register,
)
from .base import (
    METADATA_OPTION,
    NAMING_OPTION,
    OVERWRITE_OPTION,
    quality_presets,
    safe_stem,
    target_path,
)
from .images import parse_page_range

OFFICE_INPUTS = ("docx", "doc", "odt", "rtf", "pptx", "ppt", "odp",
                 "xlsx", "xls", "ods", "html", "txt")
OFFICE_OUTPUTS = ("pdf", "docx", "doc", "odt", "rtf", "txt", "html",
                  "xlsx", "xls", "ods", "pptx", "ppt", "odp")

MARKUP = ("md", "html", "rst", "tex", "epub", "docx", "txt", "odt")

# LibreOffice's `--convert-to` filter names, where they differ from the extension.
_LO_FILTERS = {
    ("html", "pdf"): "pdf:writer_web_pdf_Export",
    ("txt", "pdf"): "pdf:writer_pdf_Export",
    ("xlsx", "pdf"): "pdf:calc_pdf_Export",
    ("xls", "pdf"): "pdf:calc_pdf_Export",
    ("ods", "pdf"): "pdf:calc_pdf_Export",
    ("pptx", "pdf"): "pdf:impress_pdf_Export",
    ("docx", "txt"): "txt:Text (encoded):UTF8",
    ("odt", "txt"): "txt:Text (encoded):UTF8",
    ("docx", "html"): "html:HTML (StarWriter)",
}

# Formats whose extension is not the filter name. LibreOffice needs the filter
# spelled out for every legacy target, or it writes the modern format instead.
_LO_TARGET_FILTERS = {
    "doc": "doc:MS Word 97",
    "xls": "xls:MS Excel 97",
    "ppt": "ppt:MS PowerPoint 97",
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "odp": "odp:impress8",
    "odt": "odt:writer8",
    "ods": "ods:calc8",
}

# Which application owns each source, so a spreadsheet is not handed to Writer.
_LO_FAMILY = {
    "xlsx": "calc", "xls": "calc", "ods": "calc", "csv": "calc",
    "pptx": "impress", "ppt": "impress", "odp": "impress",
}

PAGE_SIZES = {
    "a4": "A4", "letter": "Letter", "legal": "Legal", "a5": "A5", "a3": "A3",
}


def _page_options(include_margins: bool = True) -> list[Option]:
    opts = [
        Option(id="page_size", label="Page size", type="select", default="a4",
               help="", choices=tuple(
                   Choice(key, label) for key, label in
                   (("a4", "A4"), ("letter", "US Letter"), ("legal", "US Legal"),
                    ("a5", "A5"), ("a3", "A3"))
               ), advanced=False),
        Option(id="orientation", label="Orientation", type="select", default="portrait",
               help="", choices=(Choice("portrait", "Portrait"),
                                 Choice("landscape", "Landscape")), advanced=False),
    ]
    if include_margins:
        opts.append(Option(
            id="margin", label="Page margin", type="number", default=20,
            help="Blank space around the content on every side.",
            minimum=0, maximum=60, step=1, unit="mm",
        ))
    return opts


# ------------------------------------------------------------------ LibreOffice


async def _libreoffice(ctx: RunContext) -> list[Path]:
    outputs: list[Path] = []
    total = len(ctx.inputs)
    # A private profile per run: LibreOffice refuses to start twice against one.
    profile = ctx.workdir / f"lo-profile-{uuid.uuid4().hex[:8]}"
    profile.mkdir(parents=True, exist_ok=True)

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Converting {item.name}")
        stage = ctx.workdir / f"lo-out-{index}"
        stage.mkdir(parents=True, exist_ok=True)

        target_filter = _LO_FILTERS.get(
            (item.format_id, ctx.target),
            _LO_TARGET_FILTERS.get(
                ctx.target, formats.extension_for(ctx.target).lstrip(".")
            ),
        )
        if ctx.target == "pdf" and ctx.option("fidelity", "layout") == "layout":
            if (item.format_id, ctx.target) not in _LO_FILTERS:
                target_filter = "pdf:writer_pdf_Export"

        args = [
            f"-env:UserInstallation=file://{profile}",
            "--headless", "--norestore", "--nolockcheck", "--nodefault",
            "--convert-to", target_filter,
            "--outdir", str(stage),
            str(item.path),
        ]
        await engines.run("libreoffice", args, timeout=600, input_name=item.name)

        produced = sorted(stage.glob("*"))
        if not produced:
            raise ConversionError(
                code="no_output",
                title="LibreOffice produced no output",
                detail=f"{item.name} was opened but nothing was written. The file may "
                       "use features this converter cannot read.",
                suggestions=[
                    "Open the file in its original application and re-save it.",
                    "Or try converting to PDF first, then to your target format.",
                ],
            )
        out = target_path(ctx, item.name, ctx.target, index=index)
        shutil.move(str(produced[0]), out)
        outputs.append(out)
        ctx.progress(index / total, f"Converted {item.name}")

    if ctx.target == "pdf" and ctx.option("compress_images", False):
        outputs = [await _compress_pdf(ctx, path) for path in outputs]
    return outputs


async def _compress_pdf(ctx: RunContext, path: Path) -> Path:
    if not engines.available("ghostscript"):
        ctx.note("Compression was skipped",
                 "Ghostscript is not installed, so the PDF was left at full size.")
        return path
    compressed = path.with_name(path.stem + "-compressed.pdf")
    await engines.run("ghostscript", [
        "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
        "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={compressed}", str(path),
    ], input_name=path.name)
    before, after = path.stat().st_size, compressed.stat().st_size
    if after < before:
        path.unlink()
        compressed.rename(path)
        ctx.note("Images inside the PDF were downsampled",
                 f"File size went from {before // 1024:,} KB to {after // 1024:,} KB.")
    else:
        compressed.unlink(missing_ok=True)
    return path


# ----------------------------------------------------------------------- Pandoc


async def _pandoc(ctx: RunContext) -> list[Path]:
    outputs: list[Path] = []
    total = len(ctx.inputs)
    reader = {"md": "markdown", "html": "html", "rst": "rst", "tex": "latex",
              "epub": "epub", "docx": "docx", "txt": "markdown", "odt": "odt"}
    writer = {"md": "gfm", "html": "html5", "rst": "rst", "tex": "latex",
              "epub": "epub3", "docx": "docx", "txt": "plain", "odt": "odt"}

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Converting {item.name}")
        out = target_path(ctx, item.name, ctx.target, index=index)
        args = [
            "--from", reader.get(item.format_id, item.format_id),
            "--to", writer.get(ctx.target, ctx.target),
            "--output", str(out),
        ]
        if ctx.option("standalone", True) and ctx.target in ("html", "epub", "tex"):
            args.append("--standalone")
        if ctx.option("toc", False):
            args += ["--toc", "--toc-depth", "3"]
        if ctx.target == "html" and ctx.option("embed_resources", True):
            args.append("--embed-resources")
        if ctx.option("wrap_text", "auto") == "none":
            args += ["--wrap", "none"]
        args.append(str(item.path))
        await engines.run("pandoc", args, timeout=300, input_name=item.name)
        outputs.append(out)
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


_PRINT_CSS = """
@page { size: {size} {orientation}; margin: {margin}mm; }
html { font-size: {font}pt; }
body { font-family: {family}; line-height: 1.55; color: #18181b; }
h1, h2, h3, h4 { line-height: 1.25; margin: 1.4em 0 0.5em; page-break-after: avoid; }
h1 { font-size: 1.9em; } h2 { font-size: 1.45em; } h3 { font-size: 1.2em; }
p, li { orphans: 3; widows: 3; }
code, pre { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.88em; background: #f4f4f5; }
code { padding: 0.1em 0.3em; border-radius: 3px; }
pre { padding: 0.8em 1em; border-radius: 6px; overflow-wrap: break-word;
  white-space: pre-wrap; page-break-inside: avoid; }
blockquote { margin: 1em 0; padding-left: 1em; border-left: 3px solid #d4d4d8;
  color: #52525b; }
table { border-collapse: collapse; width: 100%; margin: 1em 0;
  page-break-inside: avoid; font-size: 0.94em; }
th, td { border: 1px solid #d4d4d8; padding: 6px 9px; text-align: left; }
th { background: #f4f4f5; }
img { max-width: 100%; }
a { color: #1d4ed8; text-decoration: none; }
hr { border: none; border-top: 1px solid #e4e4e7; margin: 2em 0; }
"""


@lru_cache(maxsize=1)
def html_pdf_renderer() -> str:
    """Which HTML-to-PDF renderer this install can actually use.

    Returns "weasyprint", "pymupdf", or "" when neither works.

    WeasyPrint is a Python package that loads GTK — libgobject, pango, cairo,
    harfbuzz — through cffi at *call* time. Those are apt packages in the Docker
    image and are not in the macOS app bundle, so `import weasyprint` succeeds and
    rendering then dies with `OSError: cannot load library 'libgobject-2.0-0'`.
    An import check is therefore not enough: the only honest test is to build a
    tiny document, which is what this does once and caches.

    PyMuPDF's Story renders a practical subset of HTML and CSS with no native
    dependency beyond PyMuPDF itself, which is already in the base install. It is
    the fallback rather than the default because WeasyPrint's CSS support is far
    broader where it is available.
    """
    import contextlib
    import io

    try:
        # WeasyPrint prints a multi-line installation notice to stderr when its
        # libraries are missing. That is advice for someone installing it, not a
        # diagnosis of this app, and it lands in the engine log looking like an
        # error. The probe's job is to answer a question, not to narrate.
        with contextlib.redirect_stderr(io.StringIO()):
            from weasyprint import HTML as _WeasyHTML

            _WeasyHTML(string="<p>x</p>").write_pdf()
        return "weasyprint"
    except Exception:  # noqa: BLE001 - any failure means "cannot use it"
        pass
    try:
        import pymupdf

        if hasattr(pymupdf, "Story") and hasattr(pymupdf, "DocumentWriter"):
            return "pymupdf"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _render_with_pymupdf(html_path: Path, out: Path, css_text: str,
                         page_size: str, orientation: str, margin_mm: int) -> None:
    """Paginate HTML into a PDF with PyMuPDF's Story.

    Story has no page concept of its own: it is asked to place content into a
    rectangle and reports whether anything is left over, so pagination is this
    loop. The guard on page count is not paranoia — a `place` that fits nothing
    returns "more" forever, and an unbounded loop would write until the disk
    filled rather than failing.
    """
    import pymupdf

    rect = pymupdf.paper_rect(f"{page_size}-l" if orientation == "landscape"
                              else page_size)
    inset = margin_mm * 72 / 25.4
    area = rect + (inset, inset, -inset, -inset)

    html = html_path.read_text(encoding="utf-8", errors="replace")
    # Story resolves images and fonts relative to an archive, not a base URL.
    archive = pymupdf.Archive(str(html_path.parent))
    story = pymupdf.Story(html=html, user_css=css_text, archive=archive)

    writer = pymupdf.DocumentWriter(str(out))
    more, pages = 1, 0
    while more:
        device = writer.begin_page(rect)
        more, _ = story.place(area)
        story.draw(device)
        writer.end_page()
        pages += 1
        if pages > 2000:
            raise ConversionError(
                code="layout_failed",
                title="This document could not be paginated",
                detail=f"{html_path.name} did not finish laying out after 2000 "
                       "pages, which usually means an element is wider than the "
                       "page and cannot be broken.",
                suggestions=[
                    "Try a larger page size, or landscape orientation.",
                    "Very wide tables often need to be split.",
                ],
            )
    writer.close()


async def _markup_to_pdf(ctx: RunContext) -> list[Path]:
    """Markdown and HTML to PDF: Pandoc for structure, then a page renderer.

    Pandoc's LaTeX path needs a full TeX install and fails on unicode-heavy text;
    routing through HTML keeps the container small and the typography predictable.
    """
    renderer = html_pdf_renderer()
    if not renderer:
        raise ConversionError(
            code="engine_missing",
            title="No page renderer is available",
            detail="Laying out a PDF needs either WeasyPrint with its system "
                   "libraries or PyMuPDF, and neither could be loaded.",
            suggestions=["Reinstalling Convertor should restore PyMuPDF."],
        )

    outputs: list[Path] = []
    total = len(ctx.inputs)
    css_text = (
        _PRINT_CSS
        .replace("{size}", PAGE_SIZES.get(str(ctx.option("page_size", "a4")), "A4"))
        .replace("{orientation}", str(ctx.option("orientation", "portrait")))
        .replace("{margin}", str(int(ctx.option("margin", 20))))
        .replace("{font}", str(int(ctx.option("font_size", 11))))
        .replace("{family}", {
            "sans": '"DejaVu Sans", "Helvetica Neue", Arial, sans-serif',
            "serif": '"DejaVu Serif", Georgia, "Times New Roman", serif',
            "mono": 'ui-monospace, "DejaVu Sans Mono", Menlo, monospace',
        }.get(str(ctx.option("font_family", "sans")), "sans-serif"))
    )

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Laying out {item.name}")
        if item.format_id == "html":
            html_path = item.path
        else:
            html_path = ctx.workdir / f"{safe_stem(item.name)}-{index}.html"
            args = ["--from", "markdown", "--to", "html5", "--standalone",
                    "--embed-resources"]
            if ctx.option("toc", False):
                args += ["--toc", "--toc-depth", "3"]
            args += ["--output", str(html_path), str(item.path)]
            await engines.run("pandoc", args, timeout=300, input_name=item.name)

        out = target_path(ctx, item.name, "pdf", index=index)
        ctx.progress((index - 0.4) / total, f"Rendering {item.name}")

        page_size = str(ctx.option("page_size", "a4"))
        orientation = str(ctx.option("orientation", "portrait"))
        margin_mm = int(ctx.option("margin", 20))

        def render() -> None:
            if renderer == "weasyprint":
                from weasyprint import CSS, HTML

                HTML(filename=str(html_path),
                     base_url=str(item.path.parent)).write_pdf(
                    str(out), stylesheets=[CSS(string=css_text)]
                )
            else:
                _render_with_pymupdf(html_path, out, css_text,
                                     page_size, orientation, margin_mm)

        await asyncio.to_thread(render)
        outputs.append(out)
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


# ------------------------------------------------------------------ PDF reading


def _guard_readable(item: InputFile) -> None:
    """Honour what detection already established about a damaged file.

    A truncated PDF often still opens and simply yields no text, which would
    otherwise be reported as "this looks like a scan" — a wrong diagnosis that
    sends the user to OCR for a file that needs re-downloading.
    """
    if item.metadata.get("readable") is False:
        raise corrupt_file(
            item.name,
            "Its page structure could not be read when the file was added.",
        )


def _open_pdf(item: InputFile):
    import pypdf

    try:
        reader = pypdf.PdfReader(str(item.path))
    except Exception as exc:
        raise corrupt_file(item.name, str(exc)[:200])
    try:
        encrypted = bool(reader.is_encrypted)
    except Exception:
        encrypted = False
    if encrypted:
        # An empty password opens a PDF that is only owner-restricted; anything
        # else genuinely needs the user's password.
        try:
            opened = bool(reader.decrypt(""))
        except Exception:
            opened = False
        if not opened:
            raise password_protected(item.name)
    return reader


async def _pdf_to_text(ctx: RunContext) -> list[Path]:
    import fitz

    outputs: list[Path] = []
    total = len(ctx.inputs)
    layout = str(ctx.option("text_layout", "reading"))

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Extracting text from {item.name}")

        def work() -> Path:
            _guard_readable(item)
            doc = fitz.open(str(item.path))
            if doc.needs_pass:
                raise password_protected(item.name)
            wanted = parse_page_range(str(ctx.option("pages", "")), doc.page_count)
            chunks: list[str] = []
            for page_number in wanted:
                page = doc.load_page(page_number - 1)
                mode = "text" if layout == "reading" else "blocks"
                if mode == "text":
                    chunks.append(page.get_text("text"))
                else:
                    blocks = sorted(page.get_text("blocks"), key=lambda b: (b[1], b[0]))
                    chunks.append("\n".join(b[4] for b in blocks if b[4].strip()))
                if ctx.option("page_markers", False):
                    chunks.append(f"\n\n--- page {page_number} ---\n\n")
            doc.close()
            body = "\n\n".join(c for c in chunks if c.strip())

            if not body.strip():
                raise ConversionError(
                    code="no_text_layer",
                    title="This PDF contains no selectable text",
                    detail=f"{item.name} looks like a scan: its pages are images, so "
                           "there is nothing to extract directly.",
                    suggestions=[
                        "Choose the OCR text output instead — it reads the pages as "
                        "images and recognises the words.",
                    ],
                )
            if ctx.target == "md":
                body = _text_to_markdown(body)
            out = target_path(ctx, item.name, ctx.target, index=index)
            out.write_text(body, encoding="utf-8")
            ctx.note("Text extracted",
                     f"{len(body.split()):,} words across {len(wanted)} pages.")
            return out

        outputs.append(await asyncio.to_thread(work))
        ctx.progress(index / total, f"Extracted {item.name}")
    return outputs


def _text_to_markdown(text: str) -> str:
    """A light structural pass: short standalone lines become headings."""
    lines = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        next_blank = i + 1 >= len(lines) or not lines[i + 1].strip()
        looks_heading = (
            prev_blank and next_blank and len(stripped) < 70
            and not stripped.endswith((".", ",", ";", ":"))
            and (stripped.isupper() or stripped.istitle())
        )
        out.append(f"## {stripped}" if looks_heading else stripped)
    return "\n".join(out).strip() + "\n"


# --------------------------------------------------------------- PDF page tools


async def _pdf_merge(ctx: RunContext) -> list[Path]:
    import pypdf

    writer = pypdf.PdfWriter()
    total = len(ctx.inputs)
    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Adding {item.name}")
        reader = _open_pdf(item)
        for page in reader.pages:
            writer.add_page(page)
    if ctx.option("bookmarks", True):
        offset = 0
        writer2 = pypdf.PdfWriter()
        for item in ctx.inputs:
            reader = _open_pdf(item)
            writer2.add_outline_item(safe_stem(item.name), offset)
            for page in reader.pages:
                writer2.add_page(page)
            offset += len(reader.pages)
        writer = writer2

    out = target_path(ctx, f"{safe_stem(ctx.inputs[0].name)}-merged", "pdf")
    ctx.progress(0.9, "Writing merged PDF")
    await asyncio.to_thread(lambda: writer.write(str(out)))
    ctx.note("Documents merged",
             f"{total} files became one PDF of {len(writer.pages)} pages, in the "
             "order shown in the workspace.")
    return [out]


async def _pdf_split(ctx: RunContext) -> list[Path]:
    import pypdf

    item = ctx.inputs[0]
    reader = _open_pdf(item)
    total_pages = len(reader.pages)
    mode = str(ctx.option("split_mode", "each"))
    outputs: list[Path] = []

    if mode == "each":
        groups = [[p] for p in parse_page_range(str(ctx.option("pages", "")), total_pages)]
    elif mode == "every_n":
        size = max(1, int(ctx.option("group_size", 10)))
        pages = parse_page_range(str(ctx.option("pages", "")), total_pages)
        groups = [pages[i:i + size] for i in range(0, len(pages), size)]
    else:  # "range" — one document from the selected pages
        groups = [parse_page_range(str(ctx.option("pages", "")), total_pages)]

    for index, group in enumerate(groups, start=1):
        ctx.progress((index - 1) / len(groups), f"Writing part {index}")
        writer = pypdf.PdfWriter()
        for page_number in group:
            writer.add_page(reader.pages[page_number - 1])
        label = (
            f"-page-{group[0]:03d}" if len(group) == 1
            else f"-pages-{group[0]:03d}-{group[-1]:03d}"
        )
        out = target_path(ctx, item.name, "pdf", index=index, suffix=label)
        await asyncio.to_thread(lambda w=writer, o=out: w.write(str(o)))
        outputs.append(out)

    ctx.note("Document split",
             f"{total_pages} pages became {len(outputs)} files.")
    return outputs


# ---------------------------------------------------------------------- options


def _office_options(source: str, target: str) -> list[Option]:
    opts: list[Option] = []
    if target == "pdf":
        opts += [
            Option(id="fidelity", label="What to prioritise", type="select",
                   default="layout",
                   help="",
                   choices=(
                       Choice("layout", "Keep the layout",
                              "Pages look as close to the original as possible"),
                       Choice("structure", "Keep the structure",
                              "Favours clean headings and reflowable text"),
                   ), advanced=False),
            Option(id="compress_images", label="Reduce file size", type="boolean",
                   default=False,
                   help="Downsample images inside the document. Text stays sharp."),
            Option(id="embed_fonts", label="Embed fonts", type="boolean", default=True,
                   help="Guarantees the document looks identical on machines without "
                        "the original fonts."),
        ]
    opts += [METADATA_OPTION, NAMING_OPTION, OVERWRITE_OPTION]
    return opts


def _markup_options(source: str, target: str) -> list[Option]:
    opts: list[Option] = [
        Option(id="toc", label="Add a table of contents", type="boolean", default=False,
               help="Builds a contents list from the headings in the document.",
               advanced=False),
    ]
    if target == "pdf":
        opts += _page_options()
        opts += [
            Option(id="font_family", label="Typeface", type="select", default="sans",
                   help="", choices=(
                       Choice("sans", "Sans serif", "Clean and modern, good on screen"),
                       Choice("serif", "Serif", "Traditional, good for long reading in print"),
                       Choice("mono", "Monospace", "Fixed width, suits code"),
                   )),
            Option(id="font_size", label="Body text size", type="number", default=11,
                   help="", minimum=7, maximum=18, step=1, unit="pt"),
        ]
    if target in ("html", "epub", "tex"):
        opts.append(Option(
            id="standalone", label="Complete document", type="boolean", default=True,
            help="Include the surrounding document structure rather than a bare fragment.",
        ))
    if target == "html":
        opts.append(Option(
            id="embed_resources", label="Embed images and styles", type="boolean",
            default=True,
            help="Produces one self-contained file that works without its folder.",
        ))
    if target in ("md", "txt"):
        opts.append(Option(
            id="wrap_text", label="Line wrapping", type="select", default="auto",
            help="", choices=(Choice("auto", "Wrap at a sensible width"),
                              Choice("none", "One line per paragraph")),
        ))
    opts += [NAMING_OPTION, OVERWRITE_OPTION]
    return opts


def _pdf_text_options(source: str, target: str) -> list[Option]:
    return [
        Option(id="pages", label="Pages", type="text", default="",
               help="Leave blank for the whole document, or give a range like 2-8, 12.",
               advanced=False),
        Option(id="text_layout", label="Reading order", type="select", default="reading",
               help="How to handle multi-column pages.",
               choices=(
                   Choice("reading", "Natural reading order", "Best for normal documents"),
                   Choice("blocks", "By position on the page",
                          "Better for columns, tables and forms"),
               )),
        Option(id="page_markers", label="Mark page boundaries", type="boolean",
               default=False, help="Insert a --- page N --- line between pages."),
        NAMING_OPTION, OVERWRITE_OPTION,
    ]


def _office_warnings(inputs: Sequence[InputFile], target: str, options: dict) -> list[dict]:
    notices: list[dict] = []
    sources = {i.format_id for i in inputs}

    if target == "docx" and "pdf" in sources:
        notices.append({
            "level": "warning",
            "title": "The layout is reconstructed, not recovered",
            "detail": "A PDF stores positioned text rather than paragraphs. The result "
                      "is editable, but spacing, columns and fonts can shift.",
        })
    if target == "txt":
        notices.append({
            "level": "warning",
            "title": "All formatting is discarded",
            "detail": "Images, tables, styles and layout are removed; only the words remain.",
        })
    if "pptx" in sources and target == "pdf":
        notices.append({
            "level": "info",
            "title": "Animations and transitions are not kept",
            "detail": "Each slide becomes one static page.",
        })
    if target in ("docx", "odt", "rtf") and "pdf" not in sources:
        notices.append({
            "level": "info",
            "title": "Fonts must exist on the opening machine",
            "detail": "If a font is missing, the reader substitutes a similar one and "
                      "line breaks may move.",
        })
    if options.get("compress_images"):
        notices.append({
            "level": "warning",
            "title": "Images inside the document will lose quality",
            "detail": "Downsampling reduces file size at the cost of image detail. Text "
                      "is unaffected.",
        })
    return notices


# ------------------------------------------------- PDF to editable, with layout


async def _pdf_to_docx(ctx: RunContext) -> list[Path]:
    """Rebuild a PDF as a Word document, keeping tables, images and layout.

    The obvious implementation — pull the text out and wrap it in a DOCX — throws
    away every table and every picture, which is exactly what a person converting
    a report to Word does not want. pdf2docx reconstructs the page structure
    instead, so this delegates to it rather than reimplementing the analysis.
    """
    from pdf2docx import Converter as Pdf2DocxConverter

    outputs: list[Path] = []
    total = len(ctx.inputs)

    for index, item in enumerate(ctx.inputs, start=1):
        _guard_readable(item)
        ctx.progress((index - 1) / total, f"Analysing the layout of {item.name}")
        _open_pdf(item)  # raises the right error for encrypted or damaged files

        out = target_path(ctx, item.name, ctx.target, index=index)
        pages = str(ctx.option("pages", "")).strip()

        def work() -> None:
            converter = Pdf2DocxConverter(str(item.path))
            try:
                if pages:
                    total_pages = len(converter.fitz_doc)
                    wanted = parse_page_range(pages, total_pages)
                    # pdf2docx takes a zero-based half-open range.
                    converter.convert(str(out), start=wanted[0] - 1, end=wanted[-1])
                else:
                    converter.convert(str(out))
            finally:
                converter.close()

        await asyncio.to_thread(work)

        if not out.exists() or out.stat().st_size == 0:
            raise ConversionError(
                code="reconstruction_failed",
                title="This PDF could not be rebuilt as a document",
                detail=f"{item.name} was read, but no page structure could be "
                       "recovered from it. This happens with heavily graphical or "
                       "scanned pages.",
                suggestions=[
                    "If the pages are scans, run an OCR conversion first.",
                    "Or convert to plain text, which does not need layout analysis.",
                ],
            )

        # Report honestly what actually survived.
        import zipfile

        try:
            with zipfile.ZipFile(out) as archive:
                names = archive.namelist()
                images = sum(1 for n in names if n.startswith("word/media/"))
                body = archive.read("word/document.xml").decode("utf-8", "replace")
                tables = body.count("<w:tbl>")
            kept = []
            if tables:
                kept.append(f"{tables} table{'s' if tables != 1 else ''}")
            if images:
                kept.append(f"{images} image{'s' if images != 1 else ''}")
            ctx.note(
                "Layout reconstructed",
                ("Kept " + " and ".join(kept) + ", plus the text and its headings.")
                if kept else "The text and its headings were kept; this document had "
                             "no tables or images.",
            )
        except Exception:
            pass

        outputs.append(out)
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


async def _pdf_to_markdown(ctx: RunContext) -> list[Path]:
    """PDF to Markdown with real tables, rather than flattened text."""
    import pymupdf4llm

    outputs: list[Path] = []
    total = len(ctx.inputs)
    handling = str(ctx.option("images", "extract"))
    read_pictures = bool(ctx.option("read_text_in_images", False))

    for index, item in enumerate(ctx.inputs, start=1):
        _guard_readable(item)
        ctx.progress((index - 1) / total, f"Reading {item.name}")
        _open_pdf(item)

        stem = safe_stem(item.name)
        image_dir = ctx.outdir / f"{stem}-images"
        produced: list[Path] = []

        def work() -> str:
            kwargs: dict = {"use_ocr": read_pictures}
            if handling == "extract":
                image_dir.mkdir(parents=True, exist_ok=True)
                kwargs.update({
                    "write_images": True,
                    "image_path": str(image_dir),
                    "image_format": "png",
                })
            if str(ctx.option("pages", "")).strip():
                import pymupdf

                with pymupdf.open(str(item.path)) as doc:
                    wanted = parse_page_range(str(ctx.option("pages", "")), doc.page_count)
                kwargs["pages"] = [p - 1 for p in wanted]
            return pymupdf4llm.to_markdown(str(item.path), **kwargs)

        try:
            body = await asyncio.to_thread(work)
        except Exception as exc:
            raise ConversionError(
                code="markdown_failed",
                title="This PDF could not be read as Markdown",
                detail=f"{item.name} could not be analysed for structure: "
                       f"{type(exc).__name__}.",
                suggestions=[
                    "Convert it to plain text instead, which needs no layout analysis.",
                ],
            )

        if not body.strip():
            raise ConversionError(
                code="no_text_layer",
                title="This PDF contains no selectable text",
                detail=f"{item.name} looks like a scan: its pages are images, so "
                       "there is nothing to read directly.",
                suggestions=[
                    "Choose the OCR text output, which reads the pages as pictures.",
                ],
            )

        out = target_path(ctx, item.name, ctx.target, index=index)
        out.write_text(body.rstrip() + "\n", encoding="utf-8")
        produced.append(out)

        if handling == "extract" and image_dir.exists():
            images = sorted(p for p in image_dir.iterdir() if p.is_file())
            produced.extend(images)
            if images:
                ctx.note(
                    "Images extracted alongside",
                    f"{len(images)} image{'s' if len(images) != 1 else ''} written to "
                    f"{image_dir.name}/ and linked from the Markdown.",
                )
            else:
                image_dir.rmdir()
        elif handling == "ignore":
            ctx.warn(
                "Images were dropped",
                "Only the text and tables were kept. Set images to Extract to keep "
                "the pictures.",
            )

        # Count separator *rows*, not separator cells: a three-column table has
        # one separator row and three "---|" occurrences.
        tables = sum(
            1 for line in body.splitlines()
            if re.fullmatch(r"\|[\s\-:|]+\|", line.strip()) and "-" in line
        )
        if tables:
            ctx.note("Tables preserved",
                     f"{tables} table{'s' if tables != 1 else ''} kept as Markdown "
                     "tables rather than flattened into text.")

        outputs.extend(produced)
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


def _pdf_structure_options(source: str, target: str) -> list[Option]:
    opts = [
        Option(id="pages", label="Pages", type="text", default="",
               help="Blank converts the whole document. Otherwise a range like 2-8, 12.",
               advanced=False),
    ]
    if target == "md":
        opts += [
            Option(id="images", label="Images", type="select", default="extract",
                   help="",
                   choices=(
                       Choice("extract", "Save alongside the Markdown",
                              "Written to a folder and linked from the text"),
                       Choice("ignore", "Leave them out", "Text and tables only"),
                   ), advanced=False),
            Option(id="read_text_in_images", label="Read text inside pictures",
                   type="boolean", default=False,
                   help="Runs OCR on embedded images too. Slower, and only useful "
                        "when diagrams or screenshots contain words you need."),
        ]
    opts += [NAMING_OPTION, OVERWRITE_OPTION]
    return opts



# Installing a pack changes what this answers, so it is cleared with the
# engine lookups rather than living until the next restart.
engines.on_refresh(html_pdf_renderer.cache_clear)


def register_all() -> None:
    register(Converter(
        id="doc.office",
        label="Office document converter",
        description="Convert Word, OpenDocument, PowerPoint and spreadsheet files.",
        inputs=frozenset(OFFICE_INPUTS),
        outputs=frozenset(OFFICE_OUTPUTS),
        run=_libreoffice,
        engines=("libreoffice",),
        cardinality="one_to_one",
        quality=85,
        options_for=_office_options,
        presets_for=lambda s, t: quality_presets("document"),
        warnings_for=_office_warnings,
        # Markup routes belong to Pandoc, which keeps structure far better.
        # Cross-application routes are excluded too: LibreOffice will happily
        # "convert" a spreadsheet into a slide deck and the result is nonsense.
        excluded_routes=frozenset(
            {("txt", "html"), ("html", "txt")}
            | {
                (source, target)
                for source in OFFICE_INPUTS
                for target in OFFICE_OUTPUTS
                if _LO_FAMILY.get(source, "writer") != _LO_FAMILY.get(target, "writer")
                and target != "pdf"
            }
        ),
    ))

    register(Converter(
        id="doc.markup",
        label="Markup converter",
        description="Convert between Markdown, HTML, reStructuredText, LaTeX, EPUB and Word.",
        inputs=frozenset(MARKUP),
        outputs=frozenset(("md", "html", "rst", "tex", "epub", "docx", "txt", "odt")),
        run=_pandoc,
        engines=("pandoc",),
        cardinality="one_to_one",
        quality=90,
        options_for=_markup_options,
        warnings_for=lambda inputs, target, options: [
            *(
                [{
                    "level": "warning",
                    "title": "Complex Word features are simplified",
                    "detail": "Comments, tracked changes and text boxes do not survive "
                              "the conversion to markup.",
                }] if any(i.format_id == "docx" for i in inputs) and target != "docx" else []
            ),
            *(
                [{
                    "level": "info",
                    "title": "EPUB text reflows",
                    "detail": "E-books have no fixed pages, so the original page breaks "
                              "are not preserved.",
                }] if target == "epub" else []
            ),
        ],
    ))

    register(Converter(
        id="doc.markup_to_pdf",
        label="Markdown to PDF",
        description="Typeset Markdown as a print-ready PDF with proper page layout.",
        inputs=frozenset(("md",)),
        outputs=frozenset(("pdf",)),
        run=_markup_to_pdf,
        # Pandoc converts Markdown to HTML first; HTML input skips it entirely.
        # It is declared because a route has one availability answer for all its
        # inputs, and Markdown is the input that needs it.
        engines=("pandoc",),
        # The page renderer is a separate capability from Pandoc, and the one that
        # was silently missing on macOS.
        requires=lambda: "" if html_pdf_renderer() else "a PDF page renderer",
        cardinality="one_to_one",
        quality=95,
        options_for=_markup_options,
        presets_for=lambda s, t: [
            Preset("screen", "Balanced",
                   "A4 with comfortable margins, sans-serif text. Good for sharing.",
                   {"page_size": "a4", "margin": 20, "font_family": "sans", "font_size": 11}),
            Preset("print", "Print optimised",
                   "Serif type at a readable size with generous margins for binding.",
                   {"page_size": "a4", "margin": 25, "font_family": "serif", "font_size": 11}),
            Preset("compact", "Compact",
                   "Smaller type and tighter margins to fit more on each page.",
                   {"page_size": "a4", "margin": 12, "font_family": "sans", "font_size": 9}),
            Preset("report", "Report with contents",
                   "Adds a table of contents built from the headings.",
                   {"toc": True, "page_size": "a4", "margin": 20, "font_size": 11}),
        ],
        warnings_for=lambda inputs, target, options: [{
            "level": "info",
            "title": "Layout is generated, not copied",
            "detail": "Markup has no pages of its own, so page breaks are decided by "
                      "the page size and margins you choose here.",
        }],
    ))

    # HTML gets its own converter rather than sharing Markdown's.
    #
    # They were one converter declaring `engines=("pandoc",)`, which is true for
    # Markdown and false for HTML — HTML goes straight to the page renderer. The
    # effect was that the single most ordinary document conversion in the app,
    # HTML to PDF, demanded a 41 MB download it would never use, and on a base
    # install reported itself unavailable for no real reason.
    register(Converter(
        id="doc.html_to_pdf",
        label="HTML to PDF",
        description="Render a web page or HTML document as a print-ready PDF.",
        inputs=frozenset(("html",)),
        outputs=frozenset(("pdf",)),
        run=_markup_to_pdf,
        requires=lambda: "" if html_pdf_renderer() else "a PDF page renderer",
        cardinality="one_to_one",
        # Above doc.office (which routes HTML through LibreOffice) so the base
        # install's own renderer wins when both are present.
        quality=96,
        options_for=_markup_options,
        presets_for=lambda s, t: [
            Preset("screen", "Balanced",
                   "A4 with comfortable margins, sans-serif text. Good for sharing.",
                   {"page_size": "a4", "margin": 20, "font_family": "sans",
                    "font_size": 11}),
            Preset("print", "Print optimised",
                   "Serif type at a readable size with generous margins for binding.",
                   {"page_size": "a4", "margin": 25, "font_family": "serif",
                    "font_size": 11}),
            Preset("compact", "Compact",
                   "Narrow margins and smaller type to use fewer pages.",
                   {"page_size": "a4", "margin": 12, "font_family": "sans",
                    "font_size": 9}),
        ],
    ))

    register(Converter(
        id="pdf.extract_text",
        label="PDF text extraction",
        description="Pull the text out of a PDF as plain text or Markdown.",
        inputs=frozenset(("pdf",)),
        outputs=frozenset(("txt", "md")),
        run=_pdf_to_text,
        cardinality="one_to_one",
        # Below the structure-preserving routes: flattening is right for plain
        # text and wrong for anything that can hold a table.
        quality=55,
        options_for=_pdf_text_options,
        warnings_for=lambda inputs, target, options: [
            *(
                [{
                    "level": "warning",
                    "title": "This document has no text layer",
                    "detail": "It appears to be scanned. Direct extraction will find "
                              "nothing — use the OCR output instead.",
                }] if any(i.metadata.get("hasTextLayer") is False for i in inputs) else []
            ),
            {
                "level": "info",
                "title": "Layout is not preserved",
                "detail": "Columns, tables and headers become plain lines of text.",
            },
        ],
    ))

    register(Converter(
        id="pdf.to_docx",
        label="PDF to Word (layout preserving)",
        description="Rebuild a PDF as an editable Word document, keeping tables, "
                    "images and page layout.",
        inputs=frozenset(("pdf",)),
        outputs=frozenset(("docx",)),
        run=_pdf_to_docx,
        modules=("pdf2docx",),
        cardinality="one_to_one",
        # Above every text-extraction route, so a PDF never reaches Word as a
        # flat wall of words when its structure can be recovered.
        quality=98,
        options_for=_pdf_structure_options,
        presets_for=lambda s, t: quality_presets("document"),
        warnings_for=lambda inputs, target, options: [
            {
                "level": "info",
                "title": "Tables, images and layout are reconstructed",
                "detail": "A PDF stores positioned marks rather than paragraphs, so "
                          "the structure is inferred. Expect the content to be right "
                          "and the spacing to shift slightly.",
            },
            *(
                [{
                    "level": "warning",
                    "title": "This document has no text layer",
                    "detail": "It appears to be scanned, so there is no structure to "
                              "recover. Run an OCR conversion instead.",
                }] if any(i.metadata.get("hasTextLayer") is False for i in inputs) else []
            ),
        ],
    ))

    register(Converter(
        id="pdf.to_markdown",
        label="PDF to Markdown (with tables)",
        description="Convert a PDF to Markdown, keeping tables as tables and saving "
                    "the images alongside.",
        inputs=frozenset(("pdf",)),
        outputs=frozenset(("md",)),
        run=_pdf_to_markdown,
        modules=("pymupdf4llm",),
        cardinality="one_to_one",
        quality=98,
        options_for=_pdf_structure_options,
        warnings_for=lambda inputs, target, options: [{
            "level": "info",
            "title": "Structure is inferred from the page",
            "detail": "Headings and tables are detected from layout, so an unusual "
                      "design can be read differently than it looks.",
        }],
    ))

    register(Converter(
        id="pdf.merge",
        label="Merge PDFs",
        description="Join several PDF documents into one, in the order you choose.",
        inputs=frozenset(("pdf",)),
        outputs=frozenset(("pdf",)),
        run=_pdf_merge,
        cardinality=MANY_TO_ONE,
        quality=95,
        options_for=lambda s, t: [
            Option(id="bookmarks", label="Add bookmarks for each document",
                   type="boolean", default=True,
                   help="Creates a navigation entry per source file in the merged PDF.",
                   advanced=False),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
    ))

    register(Converter(
        id="pdf.split",
        label="Split PDF",
        description="Break one PDF into separate documents by page or group.",
        inputs=frozenset(("pdf",)),
        outputs=frozenset(("pdf",)),
        run=_pdf_split,
        cardinality=ONE_TO_MANY,
        quality=60,
        options_for=lambda s, t: [
            Option(id="split_mode", label="How to split", type="select", default="each",
                   help="", choices=(
                       Choice("each", "One file per page"),
                       Choice("every_n", "Fixed-size groups"),
                       Choice("range", "A single range as one file"),
                   ), advanced=False),
            Option(id="group_size", label="Pages per file", type="number", default=10,
                   help="", minimum=1, maximum=500, step=1,
                   depends_on=("split_mode", ("every_n",)), advanced=False),
            Option(id="pages", label="Pages to include", type="text", default="",
                   help="Leave blank for the whole document, or give a range like 1-20.",
                   advanced=False),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
    ))
