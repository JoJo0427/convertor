# Convertor

A local-first universal file conversion workspace. Add files, see what they are,
pick what you want them to become, and get them back — without uploading private
documents to a conversion website.

Everything runs in two containers on your own machine or your own VM.

```bash
cp .env.example .env
docker compose up -d
```

Then open **http://localhost:3000**.

The first build takes several minutes and produces a ~2.8 GB engine image: it
bakes in LibreOffice, FFmpeg, Pandoc, Tesseract, ImageMagick, Ghostscript,
librsvg, Potrace and 7-Zip so nothing needs installing on the host and the same image runs unchanged
on a server.

---

## What it does

**Add files → it works out what they are → choose an output → adjust if you want
→ convert → review and download.**

| | |
|---|---|
| **Documents** | PDF ⇄ DOCX ⇄ ODT ⇄ RTF ⇄ HTML ⇄ Markdown ⇄ EPUB, slides ⇄ PPTX ⇄ PPT ⇄ ODP, and the legacy DOC/XLS formats both read *and* written. **PDF → Word and PDF → Markdown keep tables, images and layout**, not just the words |
| **Images** | PNG, JPEG, WebP, AVIF, GIF, TIFF, BMP, ICO, HEIC — convert, resize, recompress |
| **Vector** | SVG, EPS, PostScript and Illustrator artwork rendered to pixels or PDF at any resolution; bitmaps traced **into** SVG with Potrace |
| **Across the two** | Images → one PDF, PDF pages → images, image → text by OCR, image → searchable PDF |
| **Structured data** | CSV, TSV, XLSX, ODS, JSON, JSON Lines, XML, YAML, Parquet, Markdown tables |
| **Media** | Whatever the installed FFmpeg supports — see below. Transcode, remux, trim, scale, crop, rotate, denoise, extract frames, thumbnails, image sequences, embedded subtitles, GIF |
| **Archives** | Extract ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, 7z and RAR; create everything but RAR |
| **Subtitles** | SRT ⇄ WebVTT, and either into a plain transcript |

Three shapes of conversion, chosen at the top of the output panel:

- **Each file** — every input converted separately.
- **Combine into one** — images into one PDF, PDFs merged, datasets stacked or
  joined, any set of files into an archive.
- **Split apart** — a PDF into pages or fixed-size groups, an archive into its
  contents.

When no direct converter exists, the engine routes through an intermediate
format and says so up front rather than silently doing something surprising.

### FFmpeg is a first-class engine, not a fallback

Media capability is **discovered at startup, never hardcoded**. The engine probes
`ffmpeg -formats`, `-encoders`, `-decoders` and `-filters`, then offers exactly
what that build can do — on the shipped image that is around **210 formats
readable, 125 writable, 219 encoders and 548 filters**, against the ten formats a
hand-written list would have named. Swap the base image and the offer changes
with it; drop an encoder and it disappears from the menus rather than failing
mid-job.

Anything FFmpeg does natively is routed to FFmpeg rather than reimplemented. A
container change with compatible streams is copied rather than re-encoded —
instant and bit-exact — and the workspace says which of the two happened.

Only the encoders actually installed appear in the encoder menus, and the
**Expert** disclosure shows the literal command that will run:

```
ffmpeg -nostdin -y -i clip.mp4 -c:v libvpx-vp9 -crf 23 -b:v 0 -row-mt 1 \
  -vf scale=-2:720:flags=lanczos -c:a libopus -b:a 160k -f webm clip.webm
```

---

## Choosing an output

Three ways in, because people arrive knowing different things:

- **By goal** — "make it smaller", "make it editable", "put it on a website".
  The tool picks the format.
- **By recent use** — what you converted to last, drawn from history rather than
  kept as separate state, so clearing history clears this too.
- **By format** — the ranked recommendations, with every reachable format one
  click away in a searchable browser.

## What the workspace shows you before it runs

- **What each file actually is**, read from its bytes rather than its extension —
  with page counts, image dimensions, media duration, table shape, and a preview.
- **Which output formats are worth choosing**, ranked for the files you added,
  with the full reachable set one click away.
- **Exactly what will happen**: the route each input format takes, how many files
  you will get back, and which inputs will be skipped as incompatible.
- **What the conversion will cost you**: flattened transparency, discarded
  formatting, lost animation frames, stripped metadata, re-compressed audio.
- **Which setting affects which file**, when a mixed batch takes several routes —
  a page-size control that only applies to the queued PNG says so.

Nothing is hidden behind a progress bar you cannot interpret. A batch reports per
file, and one bad file never costs you the rest of the batch.

---

## Privacy

The **Privacy** page accounts for every byte the app has stored — added files,
converted results, temporary working files, and the local database — and lets you
delete any of it. Conversion history is local, optional, and can be switched off.

Every conversion listed above runs entirely inside the engine container. Nothing
contacts an outside service.

The one exception is opt-in and clearly marked: **intelligent workflows** (repair
a document mangled by OCR, read a screenshot or whiteboard into a structured
document, translate while converting, condense into a brief). These send file
contents to Anthropic's API. They stay unavailable and invisible unless you set
`ANTHROPIC_API_KEY` in `.env`, and the workspace warns before every such run.

---

## Running as a desktop app

There is a second way to run this that needs no Docker, no terminal and no
localhost URL: a real application you double-click.

```bash
cd desktop
python3 bundle_binaries.py ffmpeg ffprobe gs rsvg-convert potrace
CI=true npx tauri build --bundles app,dmg
```

That produces `Convertor_1.0.0_aarch64.dmg` — 178 MB to send, 508 MB installed.
Inside it are a relocatable Python, the engine unchanged, the built interface, and
the five native engines with every library they need. The shell starts the engine
on a random free loopback port when the window opens and it stops when the window
closes; nothing listens when the app is not running.

`CI=true` is not optional on a developer machine: without it the DMG step tries to
script Finder to arrange the disk-image window, and fails with nothing useful if
macOS has not granted automation permission.

Working offline on first launch: images, vector artwork, PDF, data tables,
subtitles, and every format the bundled FFmpeg reads or writes.

Word, Excel, PowerPoint, EPUB, Parquet, 7z and PDF→Word-with-layout are **engine
packs**, installed from Settings with one button each. A pack names what it
unlocks, what it downloads and what it will take up on disk; it is fetched from
the project that makes it — The Document Foundation, pandoc, 7-zip.org, PyPI —
checked against a pinned checksum before anything is unpacked, and removable
afterwards. A cancelled or failed install leaves nothing behind. Nothing is ever
downloaded without a click.

Tesseract has no portable build to fetch, so the OCR pack states the one-line
manual install instead of offering a button that could not work. Any engine
already on your `PATH` — a LibreOffice you installed yourself — is found and used
without downloading a second copy.

Builds are **unsigned**, so the first launch needs right-click → Open rather than a
double-click. `.github/workflows/desktop.yml` builds macOS arm64, macOS x86-64 and
Windows x86-64 from one commit; the Windows half has never been run and should not
be described as working until it has. PACKAGING.md has the full picture, including
what was verified and how.

To check a build actually works rather than merely exists:

```bash
# find the port this launch chose
ps -eo command | grep 'Convertor.app.*uvicorn'
python3 tests/acceptance_desktop.py <port>
```

---

## Running on a VM

The engine is not published to the host — only the web container's port is open,
and it proxies `/api` over the private Docker network. So a server deployment is
the same two commands plus a reverse proxy:

```bash
git clone <this repo> && cd convertor
cp .env.example .env          # set CONVERTOR_PORT if 3000 is taken
docker compose up -d
```

Put nginx or Caddy in front with TLS and, since the app has no authentication of
its own, whatever access control you want. The `workspace` Docker volume holds
all state; back that up and the whole workspace moves with it.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CONVERTOR_PORT` | `3000` | Host port for the interface |
| `CONVERTOR_MAX_UPLOAD_MB` | `2048` | Largest single file that can be added |
| `CONVERTOR_CONCURRENCY` | `3` | Files converted simultaneously within one batch |
| `ANTHROPIC_API_KEY` | *(empty)* | Enables intelligent workflows. Leave blank to keep everything local |
| `CONVERTOR_AI_MODEL` | `claude-opus-5` | Model used by intelligent workflows |

---

## Adding a conversion

The registry is the extension point. A new capability is one module that builds a
`Converter` and registers it; detection, the output picker, the recommendation
ranking, the advanced settings panel, the presets and the format browser all pick
it up with no further wiring.

```python
# engine/app/converters/mine.py
from ..registry import Converter, Option, RunContext, register

async def _run(ctx: RunContext) -> list[Path]:
    ...  # write outputs into ctx.outdir, call ctx.progress(fraction, message)

def register_all() -> None:
    register(Converter(
        id="mine.thing",
        label="My converter",
        description="What it does, in plain language.",
        inputs=frozenset({"png"}),
        outputs=frozenset({"webp"}),
        run=_run,
        engines=("imagemagick",),          # declared, checked, reported if missing
        options_for=lambda src, tgt: [...], # renders itself in Advanced settings
        warnings_for=...,                   # what the user loses, shown before running
    ))
```

Then add it to the list in `engine/app/converters/__init__.py`. A new *format*
is one record in `engine/app/formats.py`, which is also what the format browser
reads.

---

## Design

The interface follows `DESIGN.md`, which is binding rather than advisory. It was
written from three real design systems (Linear for restraint and density,
Datadog for row-and-table structure, Postman for the three-pane workspace),
taking principles and rejecting their branding — including Linear's indigo, which
is exactly the accent this product must not have.

The short version: a dense hairline-ruled instrument panel. Chrome is
achromatic, colour carries only state; 28px controls, 30px rows, 12px padding;
4px radius; no shadows outside overlays; nothing larger than 17px; and a card
only around something you can actually drag.

### Themes

Four, each with a light and a dark mode, chosen from the top bar:

| | |
|---|---|
| **Formal** | The default. Achromatic and densest. |
| **Blush** | Rose tints on a deep rose-brown ink, with a rounded face (Nunito) and a type scale about 1px larger throughout. |
| **Denim** | Indigo and chambray on undyed cotton. Squarer corners. |
| **Earth** | Clay, sage and oat. |

Status colours never change with the theme — a failed conversion has to read as
failed whichever palette is on. Every theme clears its contrast targets in both
modes (body 7:1, secondary 4.5:1, tertiary 3:1, button labels 4.5:1), and a
theme that cannot is not shipped.

Nunito is served from the app itself, not from Google Fonts: a font request per
page view to a third party is not something a privacy-first local tool should
make.

## Layout

```
DESIGN.md          the binding visual specification
engine/            FastAPI conversion service (never exposed to the host)
  app/
    capabilities.py  probes FFmpeg; capability is discovered, not declared
    formats.py       curated catalog, widened at startup by what was discovered
    detect.py        identifies files by content, gathers metadata and previews
    registry.py      converters, route planning, recommendations, warnings
    jobs.py          execution, progress, cancel, retry, live event stream
    converters/      one module per capability
web/               Next.js workspace, the only container with an open port
docker-compose.yml
```

## Keyboard

| Key | Action |
|---|---|
| `⌘O` | Add files |
| `⌘↵` | Start the conversion |
| `⌘⌫` | Remove the selected file |
| `1` `2` `3` | Each file / Combine / Split |
| `↑` `↓` | Move through the queue |

---

## Development

```bash
# Engine, with reload
cd engine && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Interface, pointed at it
cd web && npm install
ENGINE_URL=http://localhost:8000 npm run dev
```

Running the engine outside Docker means the converter binaries have to exist on
your machine; whichever are missing are reported by name in the engine status
menu, and only the conversions that need them are affected.
