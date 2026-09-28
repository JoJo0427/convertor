"""Image conversion, transformation, and the image ⇄ PDF bridges."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence

from .. import engines, formats
from ..errors import ConversionError, corrupt_file
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

# Formats Pillow can write directly. HEIC and AVIF come from pillow-heif,
# which registers a save handler as well as an opener.
PILLOW_WRITE = {"png", "jpeg", "webp", "gif", "tiff", "bmp", "ico", "heic"}
PILLOW_READ = PILLOW_WRITE | {"avif"}
# Vector and PostScript artwork, rendered by a dedicated engine rather than PIL.
VECTOR_INPUTS = {"svg", "eps", "ps", "ai"}

_PIL_SAVE_NAMES = {
    "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "gif": "GIF",
    "tiff": "TIFF", "bmp": "BMP", "ico": "ICO", "avif": "AVIF",
    "heic": "HEIF",
}

LOSSY_TARGETS = {"jpeg", "webp", "avif"}
NO_ALPHA_TARGETS = {"jpeg", "bmp"}


def _image_options(source: str, target: str) -> list[Option]:
    opts: list[Option] = []
    if target in LOSSY_TARGETS:
        opts.append(Option(
            id="quality", label="Quality", type="number", default=85,
            help="Higher keeps more detail and makes a larger file. 85 is a good balance.",
            minimum=1, maximum=100, step=1, unit="%", advanced=False,
        ))
    opts.append(Option(
        id="resize_mode", label="Resize", type="select", default="none",
        help="Change the pixel dimensions of the image.",
        choices=(
            Choice("none", "Keep original size"),
            Choice("fit", "Fit within a box", "Scales down, never up, keeping proportions"),
            Choice("exact", "Exact dimensions", "Stretches to the size you give"),
            Choice("scale", "Scale by percentage"),
        ),
        advanced=False,
    ))
    opts += [
        Option(id="max_width", label="Maximum width", type="number", default=1920,
               help="", minimum=1, maximum=20000, step=1, unit="px",
               depends_on=("resize_mode", ("fit", "exact"))),
        Option(id="max_height", label="Maximum height", type="number", default=1920,
               help="", minimum=1, maximum=20000, step=1, unit="px",
               depends_on=("resize_mode", ("fit", "exact"))),
        Option(id="scale_percent", label="Scale", type="number", default=50,
               help="", minimum=1, maximum=400, step=1, unit="%",
               depends_on=("resize_mode", ("scale",))),
    ]
    if target in NO_ALPHA_TARGETS:
        opts.append(Option(
            id="background", label="Background for transparent areas", type="select",
            default="white",
            help=f"{formats.label(target)} cannot store transparency, so see-through "
                 "areas need a solid colour behind them.",
            choices=(
                Choice("white", "White"),
                Choice("black", "Black"),
                Choice("transparent_to_edge", "Nearest edge colour"),
            ),
            advanced=False,
        ))
    if target == "png":
        opts.append(Option(
            id="compression", label="Compression effort", type="number", default=6,
            help="Higher takes longer but produces a smaller file. Quality is identical either way.",
            minimum=0, maximum=9, step=1,
        ))
    if target in ("png", "jpeg", "tiff"):
        opts.append(Option(
            id="dpi", label="Resolution", type="number", default=72,
            help="Print resolution recorded in the file. 300 is standard for printing.",
            minimum=36, maximum=1200, step=1, unit="DPI",
        ))
    opts.append(Option(
        id="grayscale", label="Convert to greyscale", type="boolean", default=False,
        help="Remove all colour information.",
    ))
    opts += [METADATA_OPTION, NAMING_OPTION, OVERWRITE_OPTION]
    return opts


def _image_warnings(inputs: Sequence[InputFile], target: str, options: dict) -> list[dict]:
    notices: list[dict] = []
    target_label = formats.label(target)

    if any(i.metadata.get("hasAlpha") for i in inputs) and target in NO_ALPHA_TARGETS:
        notices.append({
            "level": "warning",
            "title": f"{target_label} does not support transparency",
            "detail": "Transparent areas will be filled with the background colour "
                      "chosen in advanced settings.",
        })
    if any(i.format_id == "svg" for i in inputs) and target != "svg":
        notices.append({
            "level": "warning",
            "title": "Vector artwork becomes a fixed-size image",
            "detail": "SVG scales to any size without losing sharpness. Once converted "
                      f"to {target_label} the resolution is locked in.",
        })
    if any((i.metadata.get("frames") or 1) > 1 for i in inputs) and target != "gif":
        notices.append({
            "level": "warning",
            "title": "Only the first frame is kept",
            "detail": f"{target_label} holds a single image, so the animation is lost.",
        })
    if target in LOSSY_TARGETS and int(options.get("quality") or 85) < 70:
        notices.append({
            "level": "warning",
            "title": "Compression artefacts will be visible",
            "detail": f"A quality below 70 makes {target_label} soften edges and "
                      "introduce blocking around fine detail.",
        })
    if target == "gif":
        notices.append({
            "level": "info",
            "title": "GIF is limited to 256 colours",
            "detail": "Photographs will show visible colour banding.",
        })
    if not options.get("keep_metadata", True):
        notices.append({
            "level": "info",
            "title": "Metadata will be removed",
            "detail": "Camera settings, location and timestamps are stripped from the output.",
        })
    return notices


def _open_image(path: Path, name: str):
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(path)
        img.load()
        return img
    except UnidentifiedImageError:
        raise corrupt_file(name, "The image data could not be decoded.")
    except OSError as exc:
        raise corrupt_file(name, str(exc)[:200])


def _apply_transforms(img, ctx: RunContext, target: str):
    from PIL import Image, ImageColor

    mode = str(ctx.option("resize_mode", "none"))
    if mode == "fit":
        img.thumbnail(
            (int(ctx.option("max_width", 1920)), int(ctx.option("max_height", 1920))),
            Image.LANCZOS,
        )
    elif mode == "exact":
        img = img.resize(
            (int(ctx.option("max_width", 1920)), int(ctx.option("max_height", 1920))),
            Image.LANCZOS,
        )
    elif mode == "scale":
        factor = float(ctx.option("scale_percent", 100)) / 100.0
        img = img.resize(
            (max(1, int(img.width * factor)), max(1, int(img.height * factor))),
            Image.LANCZOS,
        )

    if ctx.option("grayscale", False):
        img = img.convert("L")

    has_alpha = img.mode in ("RGBA", "LA", "P")
    if target in NO_ALPHA_TARGETS and has_alpha:
        rgba = img.convert("RGBA")
        choice = str(ctx.option("background", "white"))
        if choice == "transparent_to_edge":
            edge = rgba.convert("RGB").getpixel((0, 0))
        else:
            edge = ImageColor.getrgb(choice)
        canvas = Image.new("RGB", rgba.size, edge)
        canvas.paste(rgba, mask=rgba.split()[-1])
        img = canvas
        ctx.warn(
            "Transparency was flattened",
            f"{formats.label(target)} cannot store transparency, so a "
            f"{choice.replace('_', ' ')} background was applied.",
        )
    elif target in ("jpeg",) and img.mode not in ("RGB", "L", "CMYK"):
        img = img.convert("RGB")
    elif target == "png" and img.mode == "CMYK":
        img = img.convert("RGB")

    return img


def _save_kwargs(ctx: RunContext, target: str, img) -> dict:
    kwargs: dict = {}
    if target in LOSSY_TARGETS:
        kwargs["quality"] = int(ctx.option("quality", 85))
        if target == "jpeg":
            kwargs["optimize"] = True
            kwargs["progressive"] = True
            kwargs["subsampling"] = 0 if kwargs["quality"] >= 90 else 2
        if target == "webp":
            kwargs["method"] = 6
    if target == "png":
        kwargs["optimize"] = True
        kwargs["compress_level"] = int(ctx.option("compression", 6))
    if target in ("png", "jpeg", "tiff"):
        dpi = int(ctx.option("dpi", 0) or 0)
        if dpi:
            kwargs["dpi"] = (dpi, dpi)
    if target == "tiff":
        kwargs["compression"] = "tiff_lzw"
    if target == "ico":
        largest = min(max(img.width, img.height), 256)
        kwargs["sizes"] = [(s, s) for s in (16, 32, 48, 64, 128, 256) if s <= largest] or [(32, 32)]
    if not ctx.option("keep_metadata", True):
        kwargs["exif"] = b""
    return kwargs


async def _convert_images(ctx: RunContext) -> list[Path]:
    outputs: list[Path] = []
    total = len(ctx.inputs)
    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Converting {item.name}")
        out = target_path(ctx, item.name, ctx.target, index=index)

        if item.format_id not in PILLOW_READ or ctx.target not in PILLOW_WRITE:
            await _magick_convert(ctx, item, out)
        else:
            def work() -> None:
                with _open_image(item.path, item.name) as img:
                    frames = getattr(img, "n_frames", 1)
                    if frames > 1 and ctx.target == "gif":
                        img.save(out, _PIL_SAVE_NAMES[ctx.target], save_all=True,
                                 loop=0, optimize=True)
                        return
                    converted = _apply_transforms(img, ctx, ctx.target)
                    converted.save(out, _PIL_SAVE_NAMES[ctx.target],
                                   **_save_kwargs(ctx, ctx.target, converted))

            await asyncio.to_thread(work)

        outputs.append(out)
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


async def _magick_convert(ctx: RunContext, item: InputFile, out: Path) -> None:
    """Fall back to ImageMagick for formats Pillow will not read or write."""
    args: list[str] = [str(item.path)]
    mode = str(ctx.option("resize_mode", "none"))
    if mode == "fit":
        args += ["-resize", f"{int(ctx.option('max_width', 1920))}x{int(ctx.option('max_height', 1920))}>"]
    elif mode == "exact":
        args += ["-resize", f"{int(ctx.option('max_width', 1920))}x{int(ctx.option('max_height', 1920))}!"]
    elif mode == "scale":
        args += ["-resize", f"{int(ctx.option('scale_percent', 100))}%"]
    if ctx.option("grayscale", False):
        args += ["-colorspace", "Gray"]
    if ctx.target in NO_ALPHA_TARGETS:
        colour = str(ctx.option("background", "white"))
        args += ["-background", "white" if colour == "transparent_to_edge" else colour,
                 "-alpha", "remove", "-alpha", "off"]
    if ctx.target in LOSSY_TARGETS:
        args += ["-quality", str(int(ctx.option("quality", 85)))]
    if not ctx.option("keep_metadata", True):
        args += ["-strip"]
    if item.format_id == "svg":
        dpi = int(ctx.option("dpi", 0) or 192)
        args = ["-density", str(dpi), *args]
    args.append(str(out))
    await engines.run("imagemagick", args, input_name=item.name)


# ---------------------------------------------------------------- images → PDF


def _images_to_pdf_options(source: str, target: str) -> list[Option]:
    return [
        Option(id="page_size", label="Page size", type="select", default="fit",
               help="How each image is placed on the page.",
               choices=(
                   Choice("fit", "Fit the image", "Each page is exactly the image's shape"),
                   Choice("a4", "A4"),
                   Choice("letter", "US Letter"),
               ), advanced=False),
        Option(id="orientation", label="Orientation", type="select", default="auto",
               help="", choices=(
                   Choice("auto", "Match each image"),
                   Choice("portrait", "Portrait"),
                   Choice("landscape", "Landscape"),
               ), depends_on=("page_size", ("a4", "letter")), advanced=False),
        Option(id="margin", label="Margin", type="number", default=0,
               help="Blank space around each image.", minimum=0, maximum=100, step=1,
               unit="mm", depends_on=("page_size", ("a4", "letter"))),
        Option(id="dpi", label="Image resolution", type="number", default=150,
               help="Higher looks better in print and makes a larger PDF.",
               minimum=72, maximum=600, step=1, unit="DPI"),
        NAMING_OPTION, OVERWRITE_OPTION,
    ]


async def _images_to_pdf(ctx: RunContext) -> list[Path]:
    from PIL import Image

    pages: list = []
    total = len(ctx.inputs)
    size_choice = str(ctx.option("page_size", "fit"))
    dpi = int(ctx.option("dpi", 150))
    margin_mm = float(ctx.option("margin", 0))
    orientation = str(ctx.option("orientation", "auto"))

    page_dims_mm = {"a4": (210.0, 297.0), "letter": (215.9, 279.4)}

    def build() -> None:
        for index, item in enumerate(ctx.inputs, start=1):
            ctx.progress((index - 1) / total, f"Adding {item.name}")
            with _open_image(item.path, item.name) as img:
                frame = img.convert("RGB") if img.mode in ("RGBA", "P", "LA") else img.convert("RGB")
                if size_choice == "fit":
                    pages.append(frame.copy())
                    continue
                w_mm, h_mm = page_dims_mm[size_choice]
                landscape = (
                    orientation == "landscape"
                    or (orientation == "auto" and frame.width > frame.height)
                )
                if landscape:
                    w_mm, h_mm = h_mm, w_mm
                page_px = (int(w_mm / 25.4 * dpi), int(h_mm / 25.4 * dpi))
                inset = int(margin_mm / 25.4 * dpi)
                box = (max(1, page_px[0] - inset * 2), max(1, page_px[1] - inset * 2))
                scaled = frame.copy()
                scaled.thumbnail(box, Image.LANCZOS)
                canvas = Image.new("RGB", page_px, "white")
                canvas.paste(
                    scaled,
                    ((page_px[0] - scaled.width) // 2, (page_px[1] - scaled.height) // 2),
                )
                pages.append(canvas)

    await asyncio.to_thread(build)
    if not pages:
        raise ConversionError(
            code="no_pages",
            title="No images could be read",
            detail="None of the selected files produced a usable page.",
            suggestions=["Check that the images open correctly, then try again."],
        )

    stem = safe_stem(ctx.inputs[0].name)
    out = target_path(ctx, f"{stem}-combined" if len(ctx.inputs) > 1 else stem, "pdf")
    ctx.progress(0.9, "Writing PDF")
    await asyncio.to_thread(
        lambda: pages[0].save(
            out, "PDF", save_all=True, append_images=pages[1:], resolution=float(dpi)
        )
    )
    ctx.note(
        "Page order follows the input list",
        f"{len(pages)} pages were written in the order shown in the workspace.",
    )
    return [out]


# ---------------------------------------------------------------- PDF → images


def _pdf_to_images_options(source: str, target: str) -> list[Option]:
    return [
        Option(id="dpi", label="Resolution", type="number", default=150,
               help="Higher gives sharper images and larger files. 300 suits printing.",
               minimum=36, maximum=600, step=1, unit="DPI", advanced=False),
        Option(id="pages", label="Pages", type="text", default="",
               help="Leave blank for every page, or give a range like 1-5, 8, 11-13.",
               advanced=False),
        Option(id="quality", label="Quality", type="number", default=90,
               help="", minimum=1, maximum=100, step=1, unit="%",
               depends_on=("__target__", ("jpeg", "webp"))),
        NAMING_OPTION, OVERWRITE_OPTION,
    ]


def parse_page_range(spec: str, total: int) -> list[int]:
    """Parse "1-5, 8" into page numbers, clamped to what the document has."""
    spec = (spec or "").strip()
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            try:
                lo, hi = int(start), int(end or total)
            except ValueError:
                continue
            pages.extend(range(max(1, lo), min(total, hi) + 1))
        else:
            try:
                page = int(chunk)
            except ValueError:
                continue
            if 1 <= page <= total:
                pages.append(page)
    return sorted(dict.fromkeys(pages)) or list(range(1, total + 1))


async def _pdf_to_images(ctx: RunContext) -> list[Path]:
    import pypdf

    item = ctx.inputs[0]
    if item.metadata.get("readable") is False:
        raise corrupt_file(
            item.name, "Its page structure could not be read when the file was added."
        )
    try:
        reader = pypdf.PdfReader(str(item.path))
        total_pages = len(reader.pages)
    except Exception as exc:
        raise corrupt_file(item.name, str(exc)[:200])

    wanted = parse_page_range(str(ctx.option("pages", "")), total_pages)
    dpi = int(ctx.option("dpi", 150))
    outputs: list[Path] = []

    import fitz  # PyMuPDF

    doc = fitz.open(str(item.path))
    if doc.needs_pass:
        from ..errors import password_protected

        raise password_protected(item.name)

    def render(page_number: int) -> Path:
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(dpi=dpi, alpha=ctx.target == "png")
        out = target_path(
            ctx, item.name, ctx.target,
            index=page_number, suffix=f"-page-{page_number:03d}",
        )
        if ctx.target in ("png", "jpeg", "webp", "tiff"):
            from PIL import Image
            import io

            with Image.open(io.BytesIO(pix.tobytes("png"))) as img:
                frame = img.convert("RGB") if ctx.target != "png" else img
                kwargs = {}
                if ctx.target in ("jpeg", "webp"):
                    kwargs["quality"] = int(ctx.option("quality", 90))
                frame.save(out, _PIL_SAVE_NAMES[ctx.target], **kwargs)
        else:
            pix.save(str(out))
        return out

    for position, page_number in enumerate(wanted, start=1):
        ctx.progress((position - 1) / len(wanted), f"Rendering page {page_number}")
        outputs.append(await asyncio.to_thread(render, page_number))

    doc.close()
    if len(wanted) < total_pages:
        ctx.note(
            "Some pages were skipped",
            f"{len(wanted)} of {total_pages} pages were rendered, following the page "
            "range in advanced settings.",
        )
    return outputs



# --------------------------------------------------------------- vector input


async def _render_vector(ctx: RunContext) -> list[Path]:
    """Rasterise SVG, EPS, PS or AI artwork.

    SVG goes to rsvg-convert rather than ImageMagick: ImageMagick only shells
    out to it anyway, and calling it directly gives correct text and gradient
    handling plus a real error when it fails. The PostScript family goes
    straight to Ghostscript for the same reason — and because ImageMagick picks
    its own render density, which overshoots its own width limit on artwork with
    a large bounding box.
    """
    outputs: list[Path] = []
    total = len(ctx.inputs)
    dpi = int(ctx.option("dpi", 0) or 192)

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Rendering {item.name}")
        out = target_path(ctx, item.name, ctx.target, index=index)

        if item.format_id in ("eps", "ps", "ai"):
            # Ghostscript, not ImageMagick: ImageMagick shells out to gs anyway
            # but picks its own render density, which overshoots its own width
            # limit on artwork with a large bounding box.
            staged = out if ctx.target in ("png", "pdf") else (
                ctx.workdir / f"vector-{index}.png"
            )
            device = "pdfwrite" if ctx.target == "pdf" else "png16m"
            await engines.run("ghostscript", [
                "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET",
                *(["-dEPSCrop"] if item.format_id == "eps" else []),
                f"-sDEVICE={device}",
                *([f"-r{dpi}"] if device != "pdfwrite" else []),
                f"-sOutputFile={staged}", str(item.path),
            ], timeout=300, input_name=item.name)

            if staged is not out:
                staged_input = InputFile(id=item.id, name=item.name, path=staged,
                                         format_id="png", metadata=item.metadata)
                inner = RunContext(
                    inputs=[staged_input], target=ctx.target, options=ctx.options,
                    workdir=ctx.workdir, outdir=ctx.outdir, progress=ctx.progress,
                )
                produced = await _convert_images(inner)
                ctx.warnings.extend(inner.warnings)
                if not produced:
                    raise ConversionError(
                        code="render_failed",
                        title="The rendered artwork could not be converted",
                        detail=f"{item.name} rendered but the final "
                               f"{formats.label(ctx.target)} step wrote nothing.",
                        suggestions=["Try PNG or PDF, which are written directly."],
                    )
                out = produced[0]

        elif item.format_id == "svg" and engines.available("rsvg"):
            # rsvg-convert writes png, pdf, ps and svg natively; anything else
            # is rendered to PNG first and then handed to the image converter.
            direct = {"png": "png", "pdf": "pdf"}
            args: list[str] = ["--dpi-x", str(dpi), "--dpi-y", str(dpi)]

            mode = str(ctx.option("resize_mode", "none"))
            if mode in ("fit", "exact"):
                args += ["--width", str(int(ctx.option("max_width", 1920))),
                         "--height", str(int(ctx.option("max_height", 1920)))]
                if mode == "fit":
                    args.append("--keep-aspect-ratio")
            if str(ctx.option("background", "")) not in ("", "transparent_to_edge"):
                args += ["--background-color", str(ctx.option("background", "white"))]
            elif ctx.target in NO_ALPHA_TARGETS:
                args += ["--background-color", "white"]

            if ctx.target in direct:
                args += ["--format", direct[ctx.target], "-o", str(out), str(item.path)]
                await engines.run("rsvg", args, input_name=item.name)
            else:
                staged = ctx.workdir / f"vector-{index}.png"
                await engines.run(
                    "rsvg", [*args, "--format", "png", "-o", str(staged),
                             str(item.path)],
                    input_name=item.name,
                )
                staged_input = InputFile(id=item.id, name=item.name, path=staged,
                                         format_id="png", metadata=item.metadata)
                inner = RunContext(
                    inputs=[staged_input], target=ctx.target, options=ctx.options,
                    workdir=ctx.workdir, outdir=ctx.outdir, progress=ctx.progress,
                )
                # Use what the inner conversion actually wrote. Calling
                # target_path again would allocate a *new* name, because the
                # file it is checking against now exists.
                produced = await _convert_images(inner)
                ctx.warnings.extend(inner.warnings)
                if not produced:
                    raise ConversionError(
                        code="render_failed",
                        title="The rendered artwork could not be converted",
                        detail=f"{item.name} rendered to pixels but the final "
                               f"{formats.label(ctx.target)} step wrote nothing.",
                        suggestions=["Try PNG or PDF, which are written directly."],
                    )
                out = produced[0]
        else:
            await _magick_convert(ctx, item, out)

        if not out.exists() or out.stat().st_size == 0:
            raise ConversionError(
                code="render_failed",
                title=f"{formats.label(item.format_id)} artwork could not be rendered",
                detail=f"{item.name} was recognised but produced no image.",
                suggestions=[
                    "Open it in its original application and re-export it.",
                    "Very large or unusual artwork sometimes needs a lower resolution.",
                ],
            )
        outputs.append(out)
        ctx.progress(index / total, f"Rendered {item.name}")
    return outputs


def _vector_options(source: str, target: str) -> list[Option]:
    opts = [
        Option(id="dpi", label="Render resolution", type="number", default=192,
               help="Vector artwork has no fixed size; this decides how many "
                    "pixels it becomes. 192 suits screens, 300 print.",
               minimum=36, maximum=1200, step=1, unit="DPI", advanced=False),
    ]
    return opts + [o for o in _image_options(source, target) if o.id != "dpi"]


# --------------------------------------------------------------- vector output


async def _trace_to_svg(ctx: RunContext) -> list[Path]:
    """Trace a bitmap into real vector paths with Potrace."""
    engines.require("potrace")
    outputs: list[Path] = []
    total = len(ctx.inputs)
    threshold = int(ctx.option("trace_threshold", 128))
    invert = bool(ctx.option("trace_invert", False))

    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Preparing {item.name}")
        bitmap = ctx.workdir / f"trace-{index}.pbm"

        def prepare() -> None:
            from PIL import Image, ImageOps

            with _open_image(item.path, item.name) as img:
                grey = img.convert("L")
                if invert:
                    grey = ImageOps.invert(grey)
                # Potrace works on a two-tone bitmap; the threshold decides
                # which side of the line each pixel falls.
                grey.point(lambda v: 255 if v > threshold else 0, mode="1").save(bitmap)

        await asyncio.to_thread(prepare)

        out = target_path(ctx, item.name, "svg", index=index)
        ctx.progress((index - 0.4) / total, f"Tracing {item.name}")
        await engines.run("potrace", [
            "--svg",
            "--turdsize", str(int(ctx.option("trace_despeckle", 2))),
            "--alphamax", str(float(ctx.option("trace_smoothing", 1.0))),
            "--output", str(out), str(bitmap),
        ], input_name=item.name)

        body = out.read_text("utf-8", "replace") if out.exists() else ""
        paths = body.count("<path")
        if paths == 0:
            raise ConversionError(
                code="nothing_to_trace",
                title="Nothing could be traced from this image",
                detail=f"At the current threshold, {item.name} is entirely one "
                       "tone, so there are no edges to turn into shapes.",
                suggestions=[
                    "Adjust the threshold in advanced settings.",
                    "Tracing suits line art, logos and text. Photographs do not "
                    "trace usefully.",
                ],
            )
        ctx.note("Traced to vector paths",
                 f"{paths} path{'s' if paths != 1 else ''} produced. The result is "
                 "a single-colour silhouette, not a copy of the original.")
        outputs.append(out)
        ctx.progress(index / total, f"Traced {item.name}")
    return outputs



def register_all() -> None:
    image_ids = [
        f.id for f in formats.all_formats()
        if f.category == formats.IMAGE and f.id not in VECTOR_INPUTS
    ]

    register(Converter(
        id="image.transform",
        label="Image converter",
        description="Convert between image formats, resize, and control quality.",
        inputs=frozenset(image_ids),
        outputs=frozenset(PILLOW_WRITE | {"avif"}),
        run=_convert_images,
        cardinality="one_to_one",
        quality=80,
        options_for=_image_options,
        presets_for=lambda s, t: quality_presets("image"),
        warnings_for=_image_warnings,
    ))

    register(Converter(
        id="image.to_pdf",
        label="Images to PDF",
        description="Place one or more images into a single PDF document.",
        inputs=frozenset(image_ids),
        outputs=frozenset({"pdf"}),
        run=_images_to_pdf,
        cardinality=MANY_TO_ONE,
        quality=90,
        options_for=_images_to_pdf_options,
        warnings_for=lambda inputs, target, options: (
            [{
                "level": "info",
                "title": "Images become pages, not editable text",
                "detail": "The PDF will contain pictures. Use an OCR output if you need "
                          "searchable text.",
            }]
        ),
    ))

    # A single image to PDF is the same operation with one page; register it as a
    # one_to_one route too so it shows up for single-file conversions.
    register(Converter(
        id="image.to_pdf_single",
        label="Image to PDF",
        description="Wrap a single image in a PDF page.",
        inputs=frozenset(image_ids),
        outputs=frozenset({"pdf"}),
        run=_images_to_pdf,
        cardinality="one_to_one",
        quality=70,
        options_for=_images_to_pdf_options,
    ))

    register(Converter(
        id="pdf.to_images",
        label="PDF to images",
        description="Render each page of a PDF as a separate image.",
        inputs=frozenset({"pdf"}),
        outputs=frozenset({"png", "jpeg", "webp", "tiff"}),
        run=_pdf_to_images,
        cardinality=ONE_TO_MANY,
        quality=90,
        options_for=_pdf_to_images_options,
        warnings_for=lambda inputs, target, options: [{
            "level": "info",
            "title": "One image per page",
            "detail": "A "
                      + str(next((i.metadata.get("pages") for i in inputs if i.metadata.get("pages")), "multi"))
                      + "-page PDF produces that many image files.",
        }],
    ))

    register(Converter(
        id="image.render_vector",
        label="Vector artwork renderer",
        description="Render SVG, EPS, PostScript or Illustrator artwork to pixels "
                    "at a chosen resolution.",
        inputs=frozenset(VECTOR_INPUTS),
        outputs=frozenset(PILLOW_WRITE | {"pdf", "avif"}),
        run=_render_vector,
        # rsvg renders SVG; Ghostscript renders the PostScript family. Between
        # them they cover every format in VECTOR_INPUTS, so those two are what
        # the route requires. ImageMagick was declared here once as a third
        # engine and is not called by this converter at all — declaring it made
        # the entire route unavailable wherever ImageMagick was absent, which
        # is every packaged desktop build.
        engines=("rsvg", "ghostscript"),
        cardinality="one_to_one",
        quality=88,
        options_for=_vector_options,
        presets_for=lambda s, t: quality_presets("image"),
        warnings_for=_image_warnings,
    ))

    register(Converter(
        id="image.trace_svg",
        label="Trace image to SVG",
        description="Turn a bitmap into real vector paths that stay sharp at any "
                    "size. Suits line art, logos and lettering.",
        inputs=frozenset(PILLOW_READ),
        outputs=frozenset({"svg"}),
        run=_trace_to_svg,
        engines=("potrace",),
        cardinality="one_to_one",
        quality=80,
        options_for=lambda s, t: [
            Option(id="trace_threshold", label="Threshold", type="number",
                   default=128,
                   help="Pixels darker than this become shape, lighter become "
                        "background. Raise it to capture more.",
                   minimum=1, maximum=254, step=1, advanced=False),
            Option(id="trace_invert", label="Invert", type="boolean", default=False,
                   help="Use for light artwork on a dark background.",
                   advanced=False),
            Option(id="trace_despeckle", label="Ignore specks smaller than",
                   type="number", default=2,
                   help="Drops stray dots. Raise it for noisy scans.",
                   minimum=0, maximum=100, step=1, unit="px"),
            Option(id="trace_smoothing", label="Corner smoothing", type="number",
                   default=1.0,
                   help="0 keeps every corner sharp, 1.33 rounds them fully.",
                   minimum=0, maximum=1.33, step=0.01),
            NAMING_OPTION, OVERWRITE_OPTION,
        ],
        warnings_for=lambda inputs, target, options: [
            {
                "level": "warning",
                "title": "Tracing produces a single-colour silhouette",
                "detail": "Potrace follows the edge between light and dark, so "
                          "colour and shading are discarded. This is the right tool "
                          "for logos, line art and lettering — and the wrong one for "
                          "photographs.",
            },
            *(
                [{
                    "level": "info",
                    "title": "This image is a photograph",
                    "detail": "Expect a blotchy silhouette rather than a usable "
                              "vector. Adjust the threshold to control what survives.",
                }] if any((i.metadata.get("megapixels") or 0) > 2 for i in inputs) else []
            ),
        ],
    ))
