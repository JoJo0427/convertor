"""Phase 4: optional content-aware workflows.

These go beyond changing a file's format — they read what the input actually says
and prepare it for the output. Every converter here stays unavailable until an
Anthropic API key is configured, and none of them sit on the path of an ordinary
conversion: a plain PDF → DOCX never touches this module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence

from .. import config, formats
from ..errors import ConversionError
from ..registry import Choice, Converter, InputFile, Option, RunContext, register
from .base import NAMING_OPTION, OVERWRITE_OPTION, target_path

# Read per call: the model is a live setting, not a constant.


# Roughly four characters per token; chunk well below the window so a long
# document is processed in full rather than silently truncated.
CHUNK_CHARACTERS = 40_000

LANGUAGES = (
    ("english", "English"), ("chinese_simplified", "Chinese (Simplified)"),
    ("chinese_traditional", "Chinese (Traditional)"), ("malay", "Malay"),
    ("japanese", "Japanese"), ("korean", "Korean"), ("french", "French"),
    ("german", "German"), ("spanish", "Spanish"), ("portuguese", "Portuguese"),
    ("italian", "Italian"), ("dutch", "Dutch"), ("arabic", "Arabic"),
    ("hindi", "Hindi"), ("russian", "Russian"), ("thai", "Thai"),
    ("vietnamese", "Vietnamese"), ("indonesian", "Indonesian"),
)


def available() -> bool:
    return bool(config.anthropic_api_key())


def _client():
    if not available():
        raise ConversionError(
            code="ai_not_configured",
            title="Intelligent workflows are not switched on",
            detail=(
                "These conversions read the content of your file using Anthropic's "
                "API, which needs a key. Every other conversion in this app runs "
                "entirely on this machine and is unaffected."
            ),
            suggestions=[
                "Add an Anthropic API key in Settings. It takes effect immediately.",
                "Or choose a standard conversion — OCR, text extraction and document "
                "conversion all work offline.",
            ],
        )
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise ConversionError(
            code="ai_sdk_missing",
            title="The Anthropic SDK is not installed",
            detail="Intelligent workflows need the anthropic package.",
            suggestions=["Rebuild the engine image, which installs it automatically."],
        )
    return AsyncAnthropic(api_key=config.anthropic_api_key())


def _split(text: str, size: int = CHUNK_CHARACTERS) -> list[str]:
    """Split on paragraph boundaries so no chunk cuts through a sentence."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in text.split("\n\n"):
        piece = paragraph + "\n\n"
        if length + len(piece) > size and current:
            chunks.append("".join(current))
            current, length = [], 0
        current.append(piece)
        length += len(piece)
    if current:
        chunks.append("".join(current))
    return chunks


async def _ask(client, system: str, content: list | str, *, effort: str = "high") -> str:
    """One request, streamed, with server-side refusal fallback enabled."""
    text_parts: list[str] = []
    async with client.beta.messages.stream(
        model=config.ai_model(),
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = await stream.get_final_message()

    if message.stop_reason == "refusal":
        raise ConversionError(
            code="ai_declined",
            title="The model declined to process this content",
            detail="This document was not transformed because the request was refused "
                   "on safety grounds.",
            suggestions=[
                "Use a standard conversion instead — text extraction and OCR do not "
                "inspect meaning.",
            ],
        )
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
    result = "".join(text_parts).strip()
    if not result:
        raise ConversionError(
            code="ai_empty",
            title="The model returned nothing",
            detail="The request completed but produced no output.",
            suggestions=["Try again, or use a standard conversion."],
        )
    return result


def _read_source(item: InputFile, ctx: RunContext) -> str:
    """Get text out of the input, whatever kind of document it is."""
    if item.format_id == "pdf":
        import fitz

        doc = fitz.open(str(item.path))
        text = "\n\n".join(page.get_text("text") for page in doc)
        doc.close()
        if not text.strip():
            raise ConversionError(
                code="no_text_layer",
                title="This PDF has no readable text",
                detail=f"{item.name} appears to be scanned, so there is nothing for the "
                       "intelligent workflow to read.",
                suggestions=[
                    "Run the OCR text conversion on it first, then apply this workflow "
                    "to the result.",
                ],
            )
        return text
    return item.path.read_text("utf-8", "replace")


_CLEANUP_SYSTEM = """You repair documents that have been through OCR or a lossy \
conversion, and re-express them as clean Markdown.

Rules:
- Preserve every fact, number, name and piece of content. Never summarise, never \
omit, never add information that is not in the source.
- Fix what the conversion broke: words split across line breaks, paragraphs cut into \
fragments, `rn` read as `m`, `l` read as `1`, stray hyphenation, duplicated headers \
and footers, page numbers stranded mid-sentence.
- Restore structure that is evident in the text: headings, lists, tables, block \
quotes, code blocks.
- Keep the original language. Do not translate.
- Where the source is genuinely unreadable, keep the best reading and mark it \
`[unclear: ...]` rather than inventing text.
- Output only the Markdown document. No preamble, no explanation, no code fence \
around the whole result."""


async def _cleanup(ctx: RunContext) -> list[Path]:
    client = _client()
    outputs: list[Path] = []
    total = len(ctx.inputs)

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Reading {item.name}")
        source = await asyncio.to_thread(_read_source, item, ctx)
        chunks = _split(source)
        if len(chunks) > 1:
            ctx.note(
                "Processed in sections",
                f"{item.name} was handled in {len(chunks)} sections so the whole "
                "document is covered — nothing was truncated.",
            )

        cleaned: list[str] = []
        for position, chunk in enumerate(chunks, start=1):
            ctx.progress(
                (index - 1 + position / len(chunks)) / total,
                f"Cleaning {item.name} — section {position} of {len(chunks)}",
            )
            preface = (
                "This is one section of a longer document; continue naturally and do "
                "not add a closing summary.\n\n" if len(chunks) > 1 else ""
            )
            cleaned.append(await _ask(client, _CLEANUP_SYSTEM, preface + chunk))

        body = "\n\n".join(cleaned)
        out = target_path(ctx, item.name, ctx.target, index=index)
        out.write_text(body.rstrip() + "\n", encoding="utf-8")
        ctx.warn(
            "This output was rewritten by a model",
            "The text was repaired rather than copied. Compare it against the "
            "original before relying on it for anything important.",
        )
        outputs.append(out)
        ctx.progress(index / total, f"Cleaned {item.name}")
    return outputs


_VISION_SYSTEM = """You convert an image into a faithful structured Markdown document.

First work out what the image actually is — a screenshot, a photograph of a page, a \
whiteboard, a form, a receipt, a table, a slide, a diagram, a chart — then represent \
it in the way that preserves the most meaning:
- Tables become Markdown tables with the real column headers.
- Forms become labelled field/value lines.
- Diagrams become a description of the components and the connections between them.
- Charts become a short statement of what is plotted, plus a data table if the values \
are legible.
- Pages of prose become prose with their headings and lists intact.

Rules:
- Transcribe exactly what is visible. Do not correct, complete or invent content.
- Keep the original language.
- Mark anything you genuinely cannot read as `[unclear]`.
- Output only the Markdown. No preamble."""

_MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp",
                "gif": "image/gif"}


async def _image_to_document(ctx: RunContext) -> list[Path]:
    import base64

    client = _client()
    outputs: list[Path] = []
    total = len(ctx.inputs)

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Looking at {item.name}")

        def encode() -> tuple[str, str]:
            """Normalise to PNG unless it is already a directly supported type."""
            if item.format_id in _MEDIA_TYPES:
                return (
                    base64.standard_b64encode(item.path.read_bytes()).decode(),
                    _MEDIA_TYPES[item.format_id],
                )
            from PIL import Image

            staged = ctx.workdir / f"vision-{index}.png"
            with Image.open(item.path) as img:
                frame = img.convert("RGB")
                # Keep the longest edge reasonable; detail beyond this is not read.
                frame.thumbnail((2000, 2000), Image.LANCZOS)
                frame.save(staged, "PNG")
            return base64.standard_b64encode(staged.read_bytes()).decode(), "image/png"

        data, media_type = await asyncio.to_thread(encode)
        hint = str(ctx.option("content_hint", "auto"))
        instruction = "Convert this image into a structured Markdown document."
        if hint != "auto":
            instruction += f" The image is {hint.replace('_', ' ')}."

        body = await _ask(client, _VISION_SYSTEM, [
            {"type": "image", "source": {"type": "base64",
                                         "media_type": media_type, "data": data}},
            {"type": "text", "text": instruction},
        ])
        out = target_path(ctx, item.name, ctx.target, index=index)
        out.write_text(body.rstrip() + "\n", encoding="utf-8")
        ctx.warn(
            "This output was produced by a model reading the image",
            "It is an interpretation, not a mechanical transcription. Check it against "
            "the original before relying on it.",
        )
        outputs.append(out)
        ctx.progress(index / total, f"Read {item.name}")
    return outputs


async def _translate(ctx: RunContext) -> list[Path]:
    client = _client()
    outputs: list[Path] = []
    total = len(ctx.inputs)
    language = dict(LANGUAGES).get(str(ctx.option("target_language", "english")), "English")
    register_tone = str(ctx.option("tone", "match"))

    system = (
        f"You translate documents into {language}.\n\n"
        "Rules:\n"
        "- Translate the meaning, not the words. The result must read as though it "
        f"were written in {language} originally.\n"
        "- Preserve every element of the structure exactly: headings, lists, tables, "
        "links, code blocks, emphasis. Markdown syntax is not translated.\n"
        "- Never translate code, identifiers, file paths, URLs or command names.\n"
        "- Keep proper nouns in their usual form for the target language.\n"
        "- Translate all of it. Never summarise or omit a passage.\n"
        + (
            "- Match the tone and formality of the source.\n"
            if register_tone == "match" else
            f"- Use a {register_tone} register throughout.\n"
        )
        + "- Output only the translated document. No preamble, no notes."
    )

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Reading {item.name}")
        source = await asyncio.to_thread(_read_source, item, ctx)
        chunks = _split(source)
        if len(chunks) > 1:
            ctx.note(
                "Translated in sections",
                f"{item.name} was translated in {len(chunks)} sections covering the "
                "whole document.",
            )
        translated = []
        for position, chunk in enumerate(chunks, start=1):
            ctx.progress(
                (index - 1 + position / len(chunks)) / total,
                f"Translating {item.name} — section {position} of {len(chunks)}",
            )
            translated.append(await _ask(client, system, chunk))

        out = target_path(ctx, item.name, ctx.target, index=index)
        out.write_text("\n\n".join(translated).rstrip() + "\n", encoding="utf-8")
        ctx.warn(
            "Machine translation",
            f"This {language} text was produced by a model. Have it checked before "
            "using it in anything binding.",
        )
        outputs.append(out)
        ctx.progress(index / total, f"Translated {item.name}")
    return outputs


async def _brief(ctx: RunContext) -> list[Path]:
    client = _client()
    outputs: list[Path] = []
    total = len(ctx.inputs)
    depth = str(ctx.option("brief_depth", "standard"))
    shape = {
        "short": "a single tight paragraph followed by at most five bullet points",
        "standard": "a two-paragraph overview, then the key points as bullets, then "
                    "any decisions, numbers or dates that matter",
        "detailed": "a section-by-section breakdown that follows the document's own "
                    "structure, keeping every figure and named entity",
    }[depth]

    system = (
        "You turn a document into a clear, well-organised Markdown brief.\n\n"
        f"Produce {shape}.\n\n"
        "Rules:\n"
        "- Every claim must come from the document. Never add outside knowledge.\n"
        "- Keep exact figures, dates, names and quantities as they appear.\n"
        "- If the document contradicts itself or leaves something unresolved, say so.\n"
        "- Keep the original language of the document.\n"
        "- Output only the Markdown brief. No preamble."
    )

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Reading {item.name}")
        source = await asyncio.to_thread(_read_source, item, ctx)
        chunks = _split(source)

        if len(chunks) == 1:
            body = await _ask(client, system, chunks[0])
        else:
            ctx.note(
                "Summarised in two passes",
                f"{item.name} is long, so each of its {len(chunks)} sections was "
                "summarised and the results were then combined.",
            )
            partials = []
            for position, chunk in enumerate(chunks, start=1):
                ctx.progress(
                    (index - 1 + 0.7 * position / len(chunks)) / total,
                    f"Reading section {position} of {len(chunks)}",
                )
                partials.append(await _ask(
                    client,
                    "Summarise this section of a longer document, keeping every figure, "
                    "date and name. Output Markdown only.",
                    chunk,
                    effort="medium",
                ))
            ctx.progress((index - 0.1) / total, "Combining sections")
            body = await _ask(client, system,
                              "Section summaries of one document:\n\n"
                              + "\n\n---\n\n".join(partials))

        out = target_path(ctx, item.name, ctx.target, index=index, suffix="-brief")
        out.write_text(body.rstrip() + "\n", encoding="utf-8")
        ctx.warn(
            "This is a generated summary, not the document",
            "Detail is deliberately dropped. Keep the original for anything that "
            "depends on the exact wording.",
        )
        outputs.append(out)
        ctx.progress(index / total, f"Summarised {item.name}")
    return outputs


def _ai_warnings(inputs: Sequence[InputFile], target: str, options: dict) -> list[dict]:
    notices = [{
        "level": "warning",
        "title": "This conversion sends your file to Anthropic's API",
        "detail": "Unlike every other conversion in this app, an intelligent workflow "
                  "transmits the file's contents off this machine to be read by a "
                  "model. Do not use it on material that must not leave your control.",
    }]
    if not available():
        notices.append({
            "level": "error",
            "title": "No API key is configured",
            "detail": "Add an Anthropic API key in Settings to enable "
                      "these workflows.",
        })
    return notices


def register_all() -> None:
    register(Converter(
        id="ai.cleanup",
        label="Repair and restructure a document",
        description="Fix what OCR or a lossy conversion broke, and rebuild the "
                    "document's structure as clean Markdown.",
        inputs=frozenset(("txt", "md", "html", "pdf")),
        outputs=frozenset(("md",)),
        run=_cleanup,
        cardinality="one_to_one",
        # Far below the mechanical routes: this must never be chosen automatically.
        quality=5,
        options_for=lambda s, t: [NAMING_OPTION, OVERWRITE_OPTION],
        warnings_for=_ai_warnings,
    ))

    register(Converter(
        id="ai.image_to_document",
        label="Turn an image into a document",
        description="Read a screenshot, photo, whiteboard or form and rebuild it as a "
                    "structured document rather than a flat block of text.",
        inputs=frozenset(("png", "jpeg", "webp", "gif", "tiff", "bmp", "heic")),
        outputs=frozenset(("md",)),
        run=_image_to_document,
        cardinality="one_to_one",
        quality=5,
        options_for=lambda s, t: [
            Option(id="content_hint", label="What is in the image", type="select",
                   default="auto",
                   help="Telling the reader what it is looking at improves the result.",
                   choices=(
                       Choice("auto", "Work it out automatically"),
                       Choice("a_table", "A table"),
                       Choice("a_form", "A form or receipt"),
                       Choice("a_page_of_text", "A page of text"),
                       Choice("a_diagram", "A diagram or flowchart"),
                       Choice("a_chart", "A chart or graph"),
                       Choice("a_whiteboard", "Handwriting or a whiteboard"),
                       Choice("a_screenshot", "An application screenshot"),
                   ), advanced=False),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
        warnings_for=_ai_warnings,
    ))

    register(Converter(
        id="ai.translate",
        label="Translate while converting",
        description="Translate a document into another language, keeping its structure "
                    "and formatting intact.",
        inputs=frozenset(("txt", "md", "html", "pdf")),
        outputs=frozenset(("md",)),
        run=_translate,
        cardinality="one_to_one",
        quality=5,
        options_for=lambda s, t: [
            Option(id="target_language", label="Translate into", type="select",
                   default="english", help="",
                   choices=tuple(Choice(code, name) for code, name in LANGUAGES),
                   advanced=False),
            Option(id="tone", label="Register", type="select", default="match",
                   help="", choices=(
                       Choice("match", "Match the original"),
                       Choice("formal", "Formal"),
                       Choice("neutral", "Neutral"),
                       Choice("conversational", "Conversational"),
                   ), advanced=False),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
        warnings_for=_ai_warnings,
    ))

    register(Converter(
        id="ai.brief",
        label="Condense into a brief",
        description="Turn a long document into an organised summary that keeps the "
                    "figures, dates and decisions.",
        inputs=frozenset(("txt", "md", "html", "pdf")),
        outputs=frozenset(("md",)),
        run=_brief,
        cardinality="one_to_one",
        quality=5,
        options_for=lambda s, t: [
            Option(id="brief_depth", label="How much detail", type="select",
                   default="standard", help="",
                   choices=(
                       Choice("short", "Short", "A paragraph and a handful of bullets"),
                       Choice("standard", "Standard", "Overview, key points, and the "
                                                      "numbers that matter"),
                       Choice("detailed", "Detailed", "Follows the document's own "
                                                      "structure section by section"),
                   ), advanced=False),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
        warnings_for=_ai_warnings,
    ))
