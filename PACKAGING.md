# Packaging Convertor as a desktop app

*Target: someone who has never opened a terminal double-clicks an installer and
converts a file. macOS and Windows.*

---

## The decision

**A Tauri desktop app with a small base install and engine packs fetched on
first use.**

Docker was rejected as the distribution mechanism. Docker Desktop is a 600 MB
prerequisite that needs admin rights, runs a VM on macOS, and is not free for
larger organisations. Wrapping it does not remove the technical setup — it
relocates it into a prerequisite, which is worse, because the failure happens
before our app is even involved.

Signing is deferred. Unsigned builds are usable internally (macOS: right-click →
Open; Windows: SmartScreen → More info → Run anyway) but are **not** acceptable
for the non-technical audience this is for. See [Signing](#7-signing) for what
turning it on requires.

---

## 1. Why tiering, and why it is nearly free

Measured from the running engine image:

| Component | Size | Needed for |
|---|---:|---|
| Python libraries | 743 MB | everything |
| LibreOffice | 277 MB | DOCX, XLSX, PPTX, ODT, ODP, DOC, XLS, PPT |
| Pandoc | 203 MB | EPUB, RST, LaTeX, Markdown↔Word |
| Tesseract + 17 languages | 49 MB | OCR of scans and images |
| librsvg, Ghostscript, potrace, 7-Zip | ~65 MB | vector, PostScript, archives |
| FFmpeg | ~80 MB with codecs | all audio and video |

Two Python packages are avoidable in the base: **pyarrow (132 MB)**, required by
nothing and used only for Parquet, and **onnxruntime (58 MB)**, pulled in by a
layout extra whose OCR path is off by default.

The application was already built to run with engines missing:

- `engines.py` resolves each binary at runtime and reports availability by name.
- `Converter.engines` gates every route; unavailable routes are marked, not
  hidden, and never fail mid-job.
- The interface already renders "engine not installed" with an install hint.

So tiering is not a compromise bolted on for packaging. It is the shipping form
of a decision already made. The work is to turn an install *hint* into an
install *button*.

---

## 2. What ships in the base

**~450 MB installed** — measured, not estimated: a relocatable CPython 3.12
plus the base library set comes to 387 MB, and the bundled engine binaries and
shell add the rest. Compressed in a `.dmg` that is roughly 180–200 MB to
download.

The three heavyweights in the base are PyMuPDF (114 MB), pandas (73 MB) and
NumPy (68 MB). All three are load-bearing for PDF and data conversion, so they
stay.

- Tauri shell and the built web interface
- Python runtime and the trimmed library set
- FFmpeg, ffprobe, Ghostscript, librsvg, potrace

Working from the first launch, with no network:

| | |
|---|---|
| Images | PNG, JPEG, WebP, AVIF, GIF, TIFF, BMP, ICO, HEIC — convert, resize, recompress |
| Vector | SVG, EPS, PostScript, Illustrator → pixels or PDF; bitmap → SVG tracing |
| PDF | ↔ images, → text, → Markdown, merge, split, page ranges |
| Data | CSV, TSV, XLSX, ODS, JSON, JSON Lines, XML, YAML, Markdown tables |
| Media | everything the bundled FFmpeg supports — several hundred formats |
| Archives | ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ |
| Subtitles | SRT ↔ WebVTT ↔ transcript |

That is the majority of ordinary use, available immediately.

## 3. Engine packs

Fetched on demand, with progress, and removable afterwards.

Sizes below are **measured**, not estimated — download first, then what it
actually occupies once installed, because for a pip pack those differ by more
than three times and the second number is the one the user pays.

| Pack | Download | On disk | Unlocks | Source |
|---|---:|---:|---|---|
| `office` | 298 MB | 802 MB | DOCX, XLSX, PPTX, ODT, ODP, legacy DOC/XLS/PPT | LibreOffice, from The Document Foundation |
| `markup` | 41 MB | 42 MB | EPUB, RST, LaTeX, Markdown ↔ Word | Pandoc, from its GitHub release |
| `pdfword` | 128 MB | 421 MB | PDF → Word with tables, images and layout | pdf2docx + PyMuPDF layout, from PyPI |
| `ocr` | — | — | reading scans and pictures of text | **no automatic source** — see below |
| `archives` | 2 MB | 6 MB | 7z and RAR | 7-Zip, from 7-zip.org |
| `parquet` | 36 MB | 115 MB | Apache Parquet | pyarrow, from PyPI |

**Packs come from the projects that make them**, not from a mirror of ours. There
is no build of ours in the trust path, and every URL in `engine/app/packs.py` can
be checked against the project's own download page. Each artefact is pinned by
version; LibreOffice's checksums are the ones The Document Foundation publishes,
and pandoc and 7-Zip publish none, so those were computed once from the pinned
release and recorded. That distinction is stated in the code rather than glossed:
a computed checksum catches a corrupted or changed download, and does not claim
to be an upstream signature.

**`ocr` has no button, and that is deliberate.** Tesseract publishes no portable
macOS build, and the usual Windows installer is third-party. Rather than download
something that cannot be pointed at an official release, the pack states the
one-line manual route and the engine picks Tesseract up from `PATH` like any
other engine. A button that cannot honestly work is worse than no button.

**Rules.**

1. A pack is offered at the moment it is needed, naming what it unlocks and what
   it costs — never as an upsell, never on first launch.
2. A route needing an absent pack stays **visible and marked**, not hidden. The
   user learns the capability exists.
3. Downloads verify a checksum before install and are atomic: a failed or
   cancelled download leaves the app exactly as it was.
4. Packs are removable, and the storage page accounts for them like everything
   else.
5. **The app never downloads anything on its own.** Every fetch is a direct
   response to a click. This matters more than usual for a tool sold on privacy.

---

## 4. Product changes desktop requires

These are not packaging work; the app is currently wrong for a desktop context
and would be wrong however it were shipped.

| Now | Needs to be |
|---|---|
| Output at `/data/output/<job-id>/` | A real folder — default `~/Documents/Convertor`, configurable, with "save beside the original" as an option |
| Output path shown as text | **Reveal in Finder / Show in Explorer** button |
| Workspace inside a Docker volume | Platform app-data directory, and an uninstall that can actually find it |
| Downloads through the browser | Files are already local; "Download" becomes "Reveal" or "Save a copy" |
| Engine is a long-running service | Starts with the window, stops with it; no orphan process, no terminal |

---

## 5. Shape of the build

```
convertor/
  engine/            unchanged FastAPI service
  web/               unchanged interface, built to static assets
  desktop/
    src-tauri/
      tauri.conf.json
      src/main.rs           window, sidecar lifecycle, native dialogs
      binaries/             per-platform engine binaries, base tier only
    packs/
      manifest.json         pack ids, sizes, URLs, checksums per platform
```

The engine runs as a **Tauri sidecar** — the same FastAPI app, bound to
`127.0.0.1` on a random free port, started when the window opens and killed when
it closes. The interface talks to it exactly as it does today, so the proxy, the
SSE stream and every API contract are unchanged.

Python is embedded as a **relocatable CPython 3.12** from
python-build-standalone, with the base library set installed into it. PyInstaller
was the original plan and is not used: the interpreter tree is already
relocatable, so freezing adds a build step, an import hook and a class of
hard-to-diagnose missing-module failures in exchange for nothing. The engine
sources ship as plain `.py` files under `Contents/Resources/app`, which also means
a broken build can be inspected in place.

## 6. Platform notes

**macOS.** Engine binaries live in `Contents/Resources/binaries`, with their
libraries in `binaries/lib`. `bundle_binaries.py` copies each binary with every
transitive non-system library, rewrites the install names to `@executable_path`
and `@loader_path`, ad-hoc signs everything (Gatekeeper rejects a dylib whose
signature no longer matches after `install_name_tool` edits its header), and then
verifies that every reference resolves to a file that is present. That last step
has to follow `@rpath` references rather than assume they are fine — see R30 in
TEST_PLAN.md for what happens when it does not.
LibreOffice is not relocatable in the usual sense — the `office` pack installs
`LibreOffice.app` into application support and the engine is pointed at that
`soffice` path rather than assuming `PATH`.

**Windows.** WebView2 is present on Windows 11 and installable on 10; the Tauri
bootstrapper handles absence. Engine binaries sit beside the executable. Watch
`PATH` length and spaces in paths — the engine already builds argv as a list
rather than a shell string, which avoids the usual quoting failures.

**Both.** No terminal window. `CREATE_NO_WINDOW` on Windows subprocesses;
LaunchServices handles it on macOS.

## 7. Signing

Currently **off**. What it costs to turn on:

| | Cost | Effect |
|---|---|---|
| Apple Developer | $99 / year | `codesign` + `notarytool` + stapling → clean double-click on macOS |
| Windows OV certificate | ~$200–400 / year | `signtool` → no SmartScreen warning after reputation builds |
| Windows EV certificate | more | immediate SmartScreen trust |

Until then: macOS shows *"cannot be opened because Apple cannot check it"* and
needs right-click → Open once; Windows shows a SmartScreen panel needing
*More info → Run anyway*. Both are fine for you and colleagues. Neither is
acceptable for the audience this exists for, so signing is the last gate before
handing it to anyone else.

## 8. Order of work

| # | Step | State |
|---|---|---|
| 1 | Split base from packs; `pyarrow` and the layout extra out of base | **done** |
| 2 | Pack model: definitions, detection, engine *and* module gating | **done** |
| 3 | Interface names the pack and its size instead of a binary | **done** |
| 4 | Relocatable Python 3.12 runtime, base tier installed | **done** |
| 5 | Tauri shell: window, sidecar lifecycle, free-port injection, reveal-in-Finder | **done** |
| 6 | Static export build for the shell | **done** |
| 7 | Pack *download and install* — pinned sources, checksums, progress, removal | **done, verified** |
| 8 | Bundle engine binaries (FFmpeg, ffprobe, Ghostscript, rsvg, potrace) | **done, macOS** |
| 9 | `.dmg` bundling and an end-to-end launch | **done, verified** |
| 10 | Windows `.msi` | **written, unverified** |
| 11 | Auto-update | not started |
| 12 | Signing and notarisation | deferred by choice |

### What "verified" means for step 9

Installed from the `.dmg` into `/Applications` and exercised as a user would:

`tests/acceptance_desktop.py` runs against the installed app over HTTP, the way
the interface does, so it exercises the bundled interpreter, the bundled engine
binaries and the real workspace rather than the source tree. **16 of 16 pass.**
Nothing in it is satisfied by a job reaching "completed" — every output is
reopened and checked for what the conversion was supposed to preserve:

| | |
|---|---|
| Media | MP4 → GIF (24 real frames), → MP3 (audio present), → WebM (video stream is VP9) |
| Vector | SVG → PNG, PDF via rsvg; **EPS → PNG, PDF via Ghostscript**; PNG → SVG with real `<path>` data via potrace |
| Data | CSV → XLSX (a workbook with a sheet, not a renamed CSV), → JSON (all rows) |
| PDF | SVG → PDF → PNG round trip |
| Errors | a corrupt PDF gets a structured error with suggestions, quoting no benign warning |
| Gating | no `apt-get` hints; available engines carry no hint; `build: desktop` |

Plus the lifecycle checks, which no conversion test would catch:

| Check | Result |
|---|---|
| Engine starts on a free loopback port; window never opens on a dead API | ✓ |
| Engine dies on graceful quit | ✓ |
| Engine dies on Force Quit / `SIGKILL` of the window | ✓ within ~3 s |
| Every bundled binary runs with an empty environment (`env -i`) | ✓ all five |

Installed size 508 MB; `.dmg` 178 MB.

Three defects were found *by* packaging and would not have shown up any other
way — R26 (the engine outliving its window), R30 (a dylib reached by `@rpath`
that was never copied, certified clean by its own verifier) and R31 (a stale
engine declaration that disabled every vector route in any build without
ImageMagick). All three are fixed and in the table above; see TEST_PLAN.md.

### Step 10, and why it is not "done"

`.github/workflows/desktop.yml` builds macOS (arm64 and Intel) and Windows
x86-64 from one commit, fetching the Python runtime and FFmpeg per platform and
uploading the `.dmg` and `.msi` as artifacts. The macOS half of it does what this
machine just did by hand. **The Windows half has never run.** It is written from
documentation, and until a runner executes it nobody should claim a Windows
build exists.

> **Verification limit.** This was built on macOS (arm64). The Windows
> configuration **cannot be built or tested here** — it needs a Windows machine
> or a CI runner first.

---

## 9. Handing the app to someone

**macOS.** Send them `Convertor_1.0.0_aarch64.dmg` (178 MB). They open it and
drag Convertor to Applications. Because the build is unsigned, the *first* launch
must be **right-click → Open**, then *Open* in the dialog that appears —
double-clicking shows "Convertor cannot be opened because Apple cannot check it
for malicious software" and nothing else. Every later launch is a normal
double-click. An Apple Silicon Mac needs the `aarch64` build and an Intel Mac the
`x86_64` one; they are not interchangeable.

**Windows.** Send them the `.msi` from the CI run. SmartScreen will interrupt
with an unsigned-publisher panel; they choose *More info → Run anyway*. WebView2
installs itself if missing.

**What they get on first launch, offline:** images, vector artwork, PDF, data
tables, subtitles, and every format the bundled FFmpeg supports.

**Everything else is one button.** Word, Excel, PowerPoint, EPUB, Parquet, 7z and
PDF→Word-with-layout arrive as packs from Settings, each naming what it unlocks,
what it downloads and what it will occupy. A route that needs one stays visible
and marked rather than hidden, so the capability is discoverable before the
download is. OCR is the exception and says so in place of offering a button.

Nothing downloads without a click — no prefetch, no "recommended", no nagging. For
a tool whose claim is that files never leave the machine, reaching for the network
unasked would be the one unforgivable behaviour.

Both warnings above disappear only with signing, which is step 12.
