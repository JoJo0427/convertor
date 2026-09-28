"""OCR: reading text out of images and scanned documents."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence

from .. import engines, formats
from ..errors import ConversionError
from ..registry import Choice, Converter, InputFile, Option, RunContext, register
from .base import NAMING_OPTION, OVERWRITE_OPTION, target_path
from .images import parse_page_range

# Language packs installed in the engine image, with plain-language names.
LANGUAGES = (
    ("eng", "English"), ("chi_sim", "Chinese (Simplified)"),
    ("chi_tra", "Chinese (Traditional)"), ("msa", "Malay"),
    ("fra", "French"), ("deu", "German"), ("spa", "Spanish"),
    ("por", "Portuguese"), ("ita", "Italian"), ("nld", "Dutch"),
    ("jpn", "Japanese"), ("kor", "Korean"), ("ara", "Arabic"),
    ("hin", "Hindi"), ("rus", "Russian"), ("tha", "Thai"), ("vie", "Vietnamese"),
)

OCR_INPUTS = ("png", "jpeg", "webp", "tiff", "bmp", "gif", "heic", "pdf")


def _ocr_options(source: str, target: str) -> list[Option]:
    opts = [
        Option(id="ocr_language", label="Language of the text", type="select",
               default="eng",
               help="Choosing the right language markedly improves accuracy.",
               choices=tuple(Choice(code, name) for code, name in LANGUAGES),
               advanced=False),
        Option(id="ocr_mode", label="Page layout", type="select", default="auto",
               help="Tell the reader what shape the text is in.",
               choices=(
                   Choice("auto", "Detect automatically", "Works for most documents"),
                   Choice("single_column", "One column of text"),
                   Choice("single_block", "A single block", "Signs, labels, screenshots"),
                   Choice("single_line", "One line of text"),
                   Choice("sparse", "Scattered text", "Photos with text here and there"),
               )),
        Option(id="preprocess", label="Clean up the image first", type="boolean",
               default=True,
               help="Straightens, sharpens and increases contrast before reading. "
                    "Usually improves results on photos and poor scans."),
    ]
    if source == "pdf":
        opts.append(Option(
            id="pages", label="Pages", type="text", default="",
            help="Leave blank for the whole document, or give a range like 1-10.",
            advanced=False,
        ))
        opts.append(Option(
            id="ocr_dpi", label="Scan resolution", type="number", default=300,
            help="300 is the sweet spot for OCR. Higher is slower with little gain.",
            minimum=150, maximum=600, step=50, unit="DPI",
        ))
    return opts + [NAMING_OPTION, OVERWRITE_OPTION]


_PSM = {"auto": "3", "single_column": "4", "single_block": "6",
        "single_line": "7", "sparse": "11"}


def _prepare(source: Path, workdir: Path, index: int) -> Path:
    """Grayscale, autocontrast and upscale small images before recognition."""
    from PIL import Image, ImageFilter, ImageOps

    out = workdir / f"ocr-prep-{index}.png"
    with Image.open(source) as img:
        frame = img.convert("L")
        frame = ImageOps.autocontrast(frame, cutoff=1)
        if min(frame.size) < 1000:
            factor = max(2, 1000 // max(1, min(frame.size)))
            frame = frame.resize(
                (frame.width * factor, frame.height * factor), Image.LANCZOS
            )
        frame = frame.filter(ImageFilter.SHARPEN)
        frame.save(out, "PNG")
    return out


async def _tesseract(ctx: RunContext, image: Path, name: str, out_base: Path,
                     output_type: str) -> str:
    args = [
        str(image), str(out_base),
        "-l", str(ctx.option("ocr_language", "eng")),
        "--psm", _PSM.get(str(ctx.option("ocr_mode", "auto")), "3"),
    ]
    if output_type == "pdf":
        args.append("pdf")
    await engines.run("tesseract", args, timeout=600, input_name=name)
    produced = out_base.with_suffix(".pdf" if output_type == "pdf" else ".txt")
    if not produced.exists():
        raise ConversionError(
            code="ocr_failed",
            title="No text could be recognised",
            detail=f"The reader ran on {name} but produced nothing.",
            suggestions=[
                "Check that the language setting matches the text in the image.",
                "Very low-resolution or heavily skewed scans often fail; try a "
                "higher-quality source.",
            ],
        )
    return "" if output_type == "pdf" else produced.read_text("utf-8", "replace")


async def _ocr_images(ctx: RunContext) -> list[Path]:
    engines.require("tesseract")
    outputs: list[Path] = []
    total = len(ctx.inputs)

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Reading text in {item.name}")
        image = item.path
        if ctx.option("preprocess", True) and item.format_id != "pdf":
            image = await asyncio.to_thread(_prepare, item.path, ctx.workdir, index)

        base = ctx.workdir / f"ocr-{index}"
        if ctx.target == "pdf":
            await _tesseract(ctx, image, item.name, base, "pdf")
            out = target_path(ctx, item.name, "pdf", index=index, suffix="-searchable")
            base.with_suffix(".pdf").replace(out)
            ctx.note("Searchable PDF created",
                     "The original picture is kept, with recognised text layered "
                     "invisibly on top so it can be selected and searched.")
        else:
            text = await _tesseract(ctx, image, item.name, base, "txt")
            words = len(text.split())
            if words == 0:
                ctx.warn(
                    f"No text was found in {item.name}",
                    "The image was read successfully but contained no recognisable "
                    "words. Check the language setting if the text is not English.",
                )
            out = target_path(ctx, item.name, ctx.target, index=index)
            out.write_text(text.strip() + "\n", encoding="utf-8")
            ctx.note(f"Read {item.name}", f"{words:,} words recognised.")
        outputs.append(out)
        ctx.progress(index / total, f"Read {item.name}")
    return outputs


async def _ocr_pdf(ctx: RunContext) -> list[Path]:
    """Render each page to an image, then recognise it."""
    import fitz

    engines.require("tesseract")
    outputs: list[Path] = []
    dpi = int(ctx.option("ocr_dpi", 300))

    for index, item in enumerate(ctx.inputs, start=1):
        if item.metadata.get("readable") is False:
            from ..errors import corrupt_file

            raise corrupt_file(
                item.name,
                "Its page structure could not be read when the file was added.",
            )
        doc = fitz.open(str(item.path))
        if doc.needs_pass:
            from ..errors import password_protected

            raise password_protected(item.name)
        wanted = parse_page_range(str(ctx.option("pages", "")), doc.page_count)
        chunks: list[str] = []

        for position, page_number in enumerate(wanted, start=1):
            ctx.progress(
                (index - 1 + position / len(wanted)) / len(ctx.inputs),
                f"Reading page {page_number} of {item.name}",
            )
            page_image = ctx.workdir / f"ocr-page-{index}-{page_number}.png"
            await asyncio.to_thread(
                lambda: doc.load_page(page_number - 1)
                .get_pixmap(dpi=dpi)
                .save(str(page_image))
            )
            if ctx.option("preprocess", True):
                page_image = await asyncio.to_thread(
                    _prepare, page_image, ctx.workdir, page_number
                )
            base = ctx.workdir / f"ocr-out-{index}-{page_number}"
            text = await _tesseract(ctx, page_image, item.name, base, "txt")
            if text.strip():
                chunks.append(text.strip())
        doc.close()

        body = "\n\n".join(chunks)
        if not body.strip():
            raise ConversionError(
                code="ocr_empty",
                title="No text could be read from this document",
                detail=f"{item.name} was rendered and scanned but no words were "
                       "recognised on any page.",
                suggestions=[
                    "Confirm the language setting matches the document.",
                    "Increase the scan resolution in advanced settings.",
                ],
            )
        out = target_path(ctx, item.name, ctx.target, index=index)
        out.write_text(body + "\n", encoding="utf-8")
        ctx.note(
            f"Read {item.name}",
            f"{len(body.split()):,} words recognised across {len(wanted)} pages.",
        )
        outputs.append(out)
    return outputs


def _ocr_warnings(inputs: Sequence[InputFile], target: str, options: dict) -> list[dict]:
    notices = [{
        "level": "info",
        "title": "Recognised text is a best effort",
        "detail": "OCR reads shapes, not characters, so unusual fonts, handwriting "
                  "and poor scans produce mistakes. Proofread anything important.",
    }]
    if target != "pdf":
        notices.append({
            "level": "warning",
            "title": "Layout and images are not kept",
            "detail": "Columns, tables and pictures become plain text in reading order.",
        })
    if any(i.metadata.get("hasTextLayer") for i in inputs):
        notices.append({
            "level": "info",
            "title": "This PDF already contains real text",
            "detail": "Direct text extraction will be faster and perfectly accurate. "
                      "OCR is only needed for scans.",
        })
    language = options.get("ocr_language", "eng")
    if language != "eng":
        name = dict(LANGUAGES).get(language, language)
        notices.append({
            "level": "info",
            "title": f"Reading as {name}",
            "detail": "Accuracy drops sharply if the document is in a different language.",
        })
    return notices


def register_all() -> None:
    register(Converter(
        id="ocr.image",
        label="Read text from images",
        description="Recognise the words in a picture or screenshot.",
        inputs=frozenset(f for f in OCR_INPUTS if f != "pdf"),
        outputs=frozenset(("txt", "md", "pdf")),
        run=_ocr_images,
        engines=("tesseract",),
        cardinality="one_to_one",
        # Below image.to_pdf so a plain image→PDF stays a plain image→PDF.
        quality=65,
        options_for=_ocr_options,
        warnings_for=_ocr_warnings,
    ))

    register(Converter(
        id="ocr.pdf",
        label="Read text from a scanned PDF",
        description="Recognise the words on scanned pages that hold no real text.",
        inputs=frozenset(("pdf",)),
        outputs=frozenset(("txt", "md")),
        run=_ocr_pdf,
        engines=("tesseract",),
        cardinality="one_to_one",
        # Below pdf.extract_text: a PDF with a text layer should never need OCR.
        quality=40,
        options_for=_ocr_options,
        warnings_for=_ocr_warnings,
    ))
