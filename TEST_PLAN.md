# Convertor — Test Plan

## Why this document exists

Every defect found in this project so far shared one property: **the software
reported success, or reported the wrong failure.** Not one was a crash.

- `PDF → DOCX` produced a valid Word file containing zero tables and zero
  images. Exit code 0.
- FFmpeg filter discovery silently returned 0 filters, so *every* crop, scale
  and rotate option became a no-op. Exit code 0.
- Every SVG conversion failed, and the error blamed the file.
- A large DOCX → PDF failure was reported as
  `failed to launch javaldx — java may not function correctly`, which is a
  harmless warning LibreOffice prints on every run.

So this plan is built on two rules:

> **Rule 1 — "It produced a file" is not a pass.**
> Every conversion assertion must inspect the *content* of the output: table
> count, image count, row count, pixel dimensions, stream codecs, page count.
>
> **Rule 2 — "It didn't error" is not a pass.**
> Silent degradation is the dominant failure mode here. Options that do nothing,
> capabilities that quietly vanish, and content that quietly disappears must be
> asserted against positively.

A third rule follows from the javaldx bug:

> **Rule 3 — an error message is a feature and gets its own tests.**
> Wrong diagnosis is worse than no diagnosis, because it sends the user to fix
> the wrong thing.

---

## How to run

```bash
# Whole suite against the running stack
python tests/run.py

# One category
python tests/run.py --only T4          # the conversion matrix
python tests/run.py --only R           # regressions only
python tests/run.py --case R14          # a single case

# Rebuild fixtures from scratch (needed after an engine image change)
python tests/fixtures.py --rebuild
```

Every test targets **http://localhost:3000** — the proxy, not the engine
directly. Half the bugs found were in the proxy, so testing the engine in
isolation would have missed them. A small number of cases marked **[engine]**
additionally hit the engine directly to isolate a layer.

Cases marked **[manual]** need a human eye (visual, keyboard, contrast). Cases
marked **[slow]** take over 30 seconds and are excluded from the fast run.

---

## 1. Fixture corpus

Fixtures are generated, never committed as binaries, so they can be rebuilt when
the engine image changes. `tests/fixtures.py` builds all of them.

### 1.1 Well-formed, small

| Fixture | Built by | Purpose |
|---|---|---|
| `notes.md` | literal | headings, list, table, emphasis |
| `table.csv` | literal, 5 rows × 3 cols | known row/column count |
| `data.json` | literal, nested objects + arrays | flattening behaviour |
| `photo.png` | ffmpeg `testsrc2` 640×400 | known dimensions |
| `alpha.png` | PIL, RGBA with real transparency | alpha handling |
| `animated.gif` | ffmpeg, 12 frames | frame-count preservation |
| `logo.svg` | literal, shapes + text | vector rendering |
| `lineart.png` | rendered from `lineart.svg` | tracing input |
| `vector.eps` | literal PostScript | PostScript family |
| `tone.wav` | ffmpeg `sine`, 3 s | known duration |
| `clip.mp4` | ffmpeg `testsrc2` + `sine`, 6 s, 640×480, h264/aac | known streams |
| `deck.odp` | zipfile, 2 slides | presentation routes |
| `sheet.xlsx` | openpyxl, 3 sheets, formulas | multi-sheet loss |
| `subs.srt` | literal, 5 cues | cue-count preservation |
| `bundle.zip` | zipfile, 6 nested entries | archive extraction |

### 1.2 The rich document — the most important fixture

`rich.pdf`: one page containing a **heading**, a **paragraph**, a **3×4
table with a header row**, an **embedded PNG chart**, and a **closing
paragraph**.

This single fixture is the assertion target for every document route, because it
is the fixture that caught the flattening bug. Its known-good content is:

```
headings   1
paragraphs 2
tables     1  (4 rows including header, 3 columns)
images     1
words      ~40
```

Any document conversion of `rich.pdf` asserts against these numbers.

### 1.3 Hostile and edge-case

| Fixture | How | Catches |
|---|---|---|
| `empty.txt` | zero bytes | empty-input handling |
| `truncated.pdf` | first 400 bytes of a real PDF | corrupt detection |
| `locked.pdf` | AES-256 encrypted, user password | password path |
| `owner-locked.pdf` | owner password only, empty user password | must *succeed* |
| `scanned.pdf` | image-only pages, no text layer | OCR routing |
| `mystery.dat` | 5 KB of `/dev/urandom` | unrecognised type |
| `photo.txt` | a PNG named `.txt` | content-over-extension |
| `notes.png` | Markdown named `.png` | content-over-extension |
| `no-extension` | a PDF with no suffix at all | extensionless detection |
| `üñïçø∂é 名前.pdf` | unicode filename | path and header safety |
| `spaces and 'quotes'.png` | shell-hostile filename | argv construction |
| `../escape.zip` | zip with `../` entry paths | path traversal |
| `bomb.zip` | 100 nested small files | extraction limits |
| `silent.mp4` | video with no audio stream | missing-stream handling |
| `audio-only.mp4` | MP4 with no video stream | missing-stream handling |
| `cover-art.mp3` | MP3 with embedded PNG artwork | cover-art misclassification |

### 1.4 Large — the category that is currently failing

Built only when `--large` is passed, because they are slow.

| Fixture | Size | Built by |
|---|---|---|
| `big-text.docx` | ~200 pages, ~8 MB | python-docx, repeated content |
| `big-images.docx` | 60 embedded photos, ~40 MB | python-docx |
| `big-mixed.docx` | 120 pages, 30 tables, 40 images | python-docx |
| `big-sheet.xlsx` | 100k rows × 12 cols | openpyxl write_only |
| `long.pdf` | 500 pages | WeasyPrint |
| `long.mp4` | 10 minutes, 1080p | ffmpeg |
| `huge.png` | 10000 × 10000 | PIL |
| `many/` | 500 small mixed files | generated |

> **Note.** `big-*.docx` and `long.pdf` are the fixtures that reproduce the
> currently-open LibreOffice failure. They are the acceptance gate for it.

---

## 2. T1 — Detection

Detection runs before anything else, so a detection bug misroutes everything
downstream.

| ID | Case | Pass criteria |
|---|---|---|
| T1.01 | Every curated format detects as itself from a valid sample | `formatId` matches exactly; `confidence` is `certain` or `likely` |
| T1.02 | `photo.txt` (PNG named .txt) | detects `png`, emits an "extension and contents disagree" notice |
| T1.03 | `notes.png` (Markdown named .png) | detects `md`; no false disagreement notice for text-family overlap |
| T1.04 | `no-extension` | detects `pdf` from magic bytes alone |
| T1.05 | `empty.txt` | `formatId` null, **error** notice "This file is empty" |
| T1.06 | `mystery.dat` | `formatId` null, **error** notice naming the file |
| T1.07 | `truncated.pdf` | detects `pdf`, **error** notice "corrupted or incomplete", `metadata.readable == false` |
| T1.08 | `locked.pdf` | **error** notice "password protected"; **no library text** in the detail |
| T1.09 | `owner-locked.pdf` | detects normally, **no** password error, page count present |
| T1.10 | `scanned.pdf` | `metadata.hasTextLayer == false`, info notice suggesting OCR |
| T1.11 | `alpha.png` | `metadata.hasAlpha == true` |
| T1.12 | `animated.gif` | `metadata.frames == 12` |
| T1.13 | `clip.mp4` | duration ≈ 6, width 640, height 480, videoCodec h264, audioCodec aac |
| T1.14 | `silent.mp4` | `audioCodec` null, no crash |
| T1.15 | `cover-art.mp3` | category **audio**, not video |
| T1.16 | `bundle.zip` | `metadata.entryCount == 6`, preview lists entries |
| T1.17 | `deck.odp` | detects `odp`, **not** `zip` |
| T1.18 | `sheet.xlsx` / `.docx` / `.pptx` | each detects itself, not `zip` |
| T1.19 | `üñïçø∂é 名前.pdf` | uploads, detects, name preserved byte-for-byte in the response |
| T1.20 | `huge.png` **[slow]** | detects without exhausting memory; dimensions correct |
| T1.21 | Every *discovered* FFmpeg format with a sample | detects as that format, not as a neighbour sharing a container |
| T1.22 | Detection is pure | running detection twice on the same bytes yields identical output |

---

## 3. T2 — Routing and planning

| ID | Case | Pass criteria |
|---|---|---|
| T2.01 | Reachability audit | For every curated format, record routes in/out. Compare against a committed snapshot; any change must be intentional and update the snapshot |
| T2.02 | Deliberate dead-ends | `eps`, `ps`, `ai`, `rar` have no inbound route; `files` has no outbound. Any *other* dead-end fails the test |
| T2.03 | No phantom chains | Assert `png → xlsx`, `png → json`, `png → md_table`, `jpeg → csv` have **no route** |
| T2.04 | Chain honesty | Every multi-hop route offered must actually run — execute all of them (`T4.99`) |
| T2.05 | Cardinality: single file | `csv → xlsx` in `each` mode uses `data.table`, **not** `data.combine` |
| T2.06 | Cardinality: combine | `png+pdf → pdf` in `combined` mode is **rejected before running**, with an actionable message |
| T2.07 | Cardinality: split | `pdf → pdf` in `split` mode uses `pdf.split` |
| T2.08 | Cross-application block | `xlsx → pptx`, `docx → xlsx` have no route |
| T2.09 | Route is per input format | Mixed batch returns one route per distinct source format, all correct |
| T2.10 | Option scoping | In a mixed batch, `appliesTo` lists only the formats a setting affects |
| T2.11 | Option union | Every option offered belongs to at least one route in the batch |
| T2.12 | Engine-gated routes | With an engine removed, routes needing it are marked unavailable, **not** hidden and not silently broken |
| T2.13 | Intents resolve | Every intent's `best` target has a working route from the given inputs |
| T2.14 | Recently-used validity | Every entry is reachable from the current queue |
| T2.15 | Preset validity | Every preset key corresponds to a real option id for that route |
| T2.16 | Plan preview truthfulness | The command shown in the expert view is byte-identical to the one executed (assert by capturing the real argv) |

---

## 4. T3 — Capability discovery

The 0-filters bug lived here and disabled a whole feature class silently.

| ID | Case | Pass criteria |
|---|---|---|
| T3.01 | Parser sanity | `readableFormats`, `writableFormats`, `encoders`, `decoders`, `filters` are **all non-zero** |
| T3.02 | Plausibility floors | filters ≥ 100, encoders ≥ 50, writable formats ≥ 30. A parser that silently matches nothing fails |
| T3.03 | Known filters present | `scale`, `crop`, `transpose`, `hflip`, `vflip`, `hqdn3d`, `loudnorm`, `thumbnail`, `palettegen` all `true` |
| T3.04 | Known encoders present | `libx264`, `libvpx-vp9`, `libmp3lame`, `libopus`, `aac`, `flac` |
| T3.05 | Mux-only rows | `mp4`, `ipod`, `webm` are writable (they appear as ` E name` rows) |
| T3.06 | Merged rows | `mp4` is **both** readable and writable (two separate rows in `-formats`) |
| T3.07 | Cover-art classification | `mp3`, `flac`, `aiff` classify as **audio** despite advertising a PNG video codec |
| T3.08 | Device exclusion | No `alsa`, `v4l2`, `x11grab` in the catalog |
| T3.09 | Protocol exclusion | No `rtp`, `rtsp`, `hls`, `dash` in the catalog |
| T3.10 | Extension required | Every discovered format has at least one file extension |
| T3.11 | Curated wins | No discovered format steals an extension from a curated one |
| T3.12 | Cache correctness | Second startup reads the cache; the cached summary equals the fresh-probe summary |
| T3.13 | Cache invalidation | Changing the recorded version string forces a re-probe |
| T3.14 | Damaged cache | A truncated `capabilities.json` re-probes rather than crashing startup |
| T3.15 | Engine absent | With `ffmpeg` removed from PATH, startup succeeds, media converters do not register, and the UI reports FFmpeg missing by name |

---

## 5. T4 — Conversion matrix

Every case asserts **content**, per Rule 1.

### 5.1 Documents

| ID | Route | Content assertion |
|---|---|---|
| T4.01 | `rich.pdf → docx` | tables ≥ 1, images ≥ 1, table rows ≥ 4, word count within 10% of source |
| T4.02 | `rich.pdf → md` | contains a pipe-table separator row; image files written alongside; heading present |
| T4.03 | `rich.pdf → txt` | word count within 10%; no image references |
| T4.04 | `rich.pdf → png` | one PNG per page, dimensions match page × dpi within 2px |
| T4.05 | `notes.md → pdf` | page count ≥ 1, extractable text matches source words, table survives |
| T4.06 | `notes.md → docx` | heading count, list count and table count all match source |
| T4.07 | `notes.md → epub` | valid EPUB zip, contains the heading text |
| T4.08 | `notes.md → html` | contains `<table>`, `<h1>`, `<li>` |
| T4.09 | `docx → pdf` | page count > 0, extracted text matches, **images preserved** |
| T4.10 | `docx → doc` and back | text round-trips |
| T4.11 | `xlsx → xls`, `xlsx → csv` | row and column counts preserved; multi-sheet loss warned |
| T4.12 | `odp → pptx`, `odp → ppt`, `odp → pdf` | slide count preserved (2) |
| T4.13 | `scanned.pdf → txt` | fails with `no_text_layer` and suggests OCR — **must not** return empty success |
| T4.14 | `scanned.pdf → txt` via OCR route | non-empty text, word count > 0 |

### 5.2 Images and vector

| ID | Route | Content assertion |
|---|---|---|
| T4.20 | `png → jpeg/webp/avif/tiff/bmp/gif/heic` | opens, dimensions unchanged, correct format signature |
| T4.21 | `alpha.png → jpeg` | opens as RGB, background applied, warning emitted about transparency |
| T4.22 | `alpha.png → webp` | alpha channel **preserved** |
| T4.23 | `animated.gif → gif` (resize) | frame count still 12 |
| T4.24 | `animated.gif → png` | 1 frame, warning about losing animation |
| T4.25 | `png` resize modes | `fit` never upscales; `exact` matches exactly; `scale` matches percentage ±1px |
| T4.26 | `png → ico` | contains multiple icon sizes |
| T4.27 | `logo.svg → png/jpeg/webp/pdf/tiff/heic/avif/gif/bmp` | all produce non-empty output with expected dimensions at the requested DPI |
| T4.28 | `logo.svg` at 96 vs 300 dpi | output dimensions scale proportionally |
| T4.29 | `vector.eps → png/pdf/jpeg` | non-empty, correct dimensions |
| T4.30 | `lineart.png → svg` | ≥ 1 `<path>`, valid XML, warning that it is a silhouette |
| T4.31 | `photo.png → svg` | succeeds but warns it is unsuitable for photographs |
| T4.32 | `png → pdf` page sizes | A4/Letter/fit produce the stated page dimensions |
| T4.33 | Multiple images → one PDF | page count equals image count, **in queue order** |

### 5.3 Structured data

| ID | Route | Content assertion |
|---|---|---|
| T4.40 | `csv → xlsx/json/jsonl/xml/yaml/parquet/md_table/ods/tsv` | row count and column count preserved in every target |
| T4.41 | `data.json → csv` | nested keys flattened to dotted columns; no data lost |
| T4.42 | `csv` delimiter option | output actually uses the chosen separator |
| T4.43 | `csv` encoding option | a Latin-1 source with accents round-trips correctly |
| T4.44 | `json` shape option | `records` / `columns` / `values` produce structurally different, valid JSON |
| T4.45 | Combine: stack | output rows equal the sum of input rows |
| T4.46 | Combine: merge on key | join produces the expected row count; missing key gives an actionable error |
| T4.47 | `big-sheet.xlsx → csv` **[slow]** | 100k rows all present |

### 5.4 Media

| ID | Route | Content assertion |
|---|---|---|
| T4.60 | `mp4 → mkv` (no settings) | note says **copied without re-encoding**; video codec still h264; duration unchanged |
| T4.61 | `mp4 → webm` | note says re-encoded; codec is vp9/opus; duration unchanged ±0.1s |
| T4.62 | `mp4 → mp3` | audio-only, duration preserved |
| T4.63 | `resolution` option | output height exactly as requested, width even |
| T4.64 | `crop` option | output aspect ratio matches the request exactly |
| T4.65 | `rotate` 90/180/270 | dimensions swap for 90/270, unchanged for 180 |
| T4.66 | `fps` option | output frame rate matches |
| T4.67 | `trim` options | output duration equals the requested span ±0.2s |
| T4.68 | `drop_audio` | no audio stream in output |
| T4.69 | `reencode=never` on an incompatible pair | fails with `copy_impossible`, **not** a silent re-encode |
| T4.70 | `mp4 → gif` | valid GIF, frame rate as requested |
| T4.71 | `mp4 → frames` | file count matches fps × duration (or the limit) |
| T4.72 | `mp4 → thumbnail` | exactly one image |
| T4.73 | images → video | duration equals count ÷ fps |
| T4.74 | `audio-only.mp4 → mp4` | actionable `no_video_stream` error |
| T4.75 | `wav → flac/opus/aac/ogg/aiff` | duration preserved; sample rate as requested |
| T4.76 | Every discovered writable container, sampled | produces a file ffprobe can read back |
| T4.77 | `long.mp4 → webm` **[slow]** | completes, progress reached 100%, duration preserved |

### 5.5 Archives and subtitles

| ID | Route | Content assertion |
|---|---|---|
| T4.80 | `zip/tar/targz/tarbz2/tarxz/7z → files` | extracted file count and names match the archive index |
| T4.81 | `../escape.zip` | traversal entries skipped, warning emitted, **nothing written outside the output folder** |
| T4.82 | files → each archive format | archive opens; entry count matches input count |
| T4.83 | `srt ↔ vtt` | cue count preserved (5); timings preserved |
| T4.84 | `srt → txt` | line count equals de-duplicated cue count |
| T4.85 | subtitle time offset | every timestamp shifted by exactly the offset |

---

## 6. T5 — Scale and limits

**This is the category the current bug lives in, and the thinnest today.**

| ID | Case | Pass criteria |
|---|---|---|
| T5.01 | `big-text.docx → pdf` **[slow]** | completes; page count > 100 |
| T5.02 | `big-images.docx → pdf` **[slow]** | completes; every image present in the PDF |
| T5.03 | `big-mixed.docx → pdf` **[slow]** | completes; table and image counts preserved |
| T5.04 | `long.pdf (500pp) → docx` **[slow]** | completes or fails with an accurate, actionable message — never with a masked warning |
| T5.05 | `long.pdf → png` | one image per page; no memory exhaustion |
| T5.06 | `huge.png → jpeg` **[slow]** | completes; dimensions preserved |
| T5.07 | Upload at the size limit | accepted |
| T5.08 | Upload one byte over the limit | rejected with `too_large`, naming the limit |
| T5.09 | 500-file batch **[slow]** | all items reported; none lost; progress monotonic |
| T5.10 | Timeout path | a deliberately slow conversion hits the timeout and reports `timeout` with the elapsed minutes — not a generic failure |
| T5.11 | Memory ceiling | large conversions do not OOM-kill the container; if they must fail, the error says so |
| T5.12 | Disk exhaustion | with a full workspace volume, writes fail with an actionable message, not a traceback |
| T5.13 | Output size sanity | `mp4 → gif` on a long clip warns before producing a file over 8 MB |

---

## 7. T6 — Failure modes and error quality

Per Rule 3. **Every** case here asserts on the message, not just the failure.

| ID | Case | Pass criteria |
|---|---|---|
| T6.01 | Universal error shape | Every error has non-empty `code`, `title`, `detail`, and ≥ 1 `suggestions` entry |
| T6.02 | **No library text leaks** | No error `detail` matches `/Traceback|Errno|0x[0-9a-f]{6}|site-packages|\.py:\d+/` |
| T6.03 | **No benign warning quoted as a cause** | No error `detail` contains a known-benign engine warning. Maintain a blocklist: `javaldx`, `Fontconfig warning`, `libpng warning`, `deprecated`, `GLib-GObject` |
| T6.04 | Diagnostic line selection | Given synthetic stderr where the real error precedes a trailing warning, the reported detail quotes the **error**, not the warning |
| T6.05 | Corrupt PDF | diagnosed as corrupt, **not** as "scanned" |
| T6.06 | Password PDF | diagnosed as password-protected on every route (`txt`, `docx`, `png`, `md`, OCR) |
| T6.07 | Unsupported route via API | rejected before any work starts |
| T6.08 | Missing engine | names the engine and gives an install hint |
| T6.09 | Empty result | a converter producing a zero-byte output fails rather than reporting success |
| T6.10 | Partial batch failure | successes are preserved and downloadable; job status is `completed_with_warnings` |
| T6.11 | Incompatible file in batch | marked `skipped` with a reason, rest proceed |
| T6.12 | Cancel mid-job | remaining items `cancelled`, completed items keep their outputs |
| T6.13 | Retry after failure | only failed items re-run; successful outputs untouched |
| T6.14 | Engine restart mid-job | job reports failure honestly; UI recovers on reload |
| T6.15 | Error suggestions are actionable | each suggestion names a concrete next step, not "try again" alone |

---

## 8. T7 — Concurrency

| ID | Case | Pass criteria |
|---|---|---|
| T7.01 | Parallel LibreOffice | 5 simultaneous office conversions all succeed (each needs its own profile directory) |
| T7.02 | Parallel FFmpeg | 5 simultaneous transcodes all succeed; progress reported per item |
| T7.03 | Two jobs at once | both complete; outputs land in separate folders |
| T7.04 | Same file in two jobs | both succeed; no shared-temp collision |
| T7.05 | Output name collision | `on_conflict` policies `number`/`overwrite`/`skip` each behave as documented |
| T7.06 | Concurrency limit respected | never more than `CONVERTOR_CONCURRENCY` items running |
| T7.07 | Temp isolation | each job's working directory is separate and removed afterwards |

---

## 9. T8 — Job lifecycle and progress

| ID | Case | Pass criteria |
|---|---|---|
| T8.01 | Progress monotonic | never decreases within an item |
| T8.02 | Progress reaches 1.0 | on every successful item |
| T8.03 | Real progress for media | FFmpeg progress reflects actual position, not a timer |
| T8.04 | SSE stream | emits ≥ 2 frames; final frame carries a terminal status |
| T8.05 | SSE terminal close | stream closes after the terminal frame |
| T8.06 | Polling fallback | with SSE blocked, the UI still reaches the terminal state |
| T8.07 | Summary arithmetic | completed + warnings + failed + skipped + cancelled == item count |
| T8.08 | Duration reported | every finished item has a plausible `durationMs` |
| T8.09 | Output accounting | `summary.outputs` equals the actual file count on disk |

---

## 10. T9 — API and proxy

| ID | Case | Pass criteria |
|---|---|---|
| T9.01 | Small upload | 200, correct detection |
| T9.02 | **Large upload via curl** | 1.5 MB and 25 MB both succeed (this is the `Expect: 100-continue` case) |
| T9.03 | Upload byte integrity | uploaded size equals source size exactly |
| T9.04 | Streaming download | a 100 MB output downloads with a matching checksum |
| T9.05 | Zip-all download | contains every output, names intact |
| T9.06 | Path traversal on download | `?path=/etc/passwd` returns 403 |
| T9.07 | Path outside workspace | rejected |
| T9.08 | Static assets | `/fonts/*.woff2` return 200 with `font/woff2` |
| T9.09 | Engine restarted under the proxy | first request may fail, subsequent requests recover |
| T9.10 | Engine down | proxy returns the friendly `engine_unreachable` error, and **logs the underlying cause** |
| T9.11 | Unicode filenames | survive upload → convert → download intact |
| T9.12 | Every endpoint has a schema test | request/response shape asserted for all routes |

---

## 11. T10 — Interface **[manual unless noted]**

| ID | Case | Pass criteria |
|---|---|---|
| T10.01 | No console errors | zero `pageerror` on every route (automatable) |
| T10.02 | All four themes × both modes render | no missing tokens, no unstyled flashes (automatable via screenshot diff) |
| T10.03 | Contrast floors | body 7:1, secondary 4.5:1, tertiary 3:1, button label 4.5:1 in all 8 combinations (automatable) |
| T10.04 | Theme persists across reload | theme and mode both restored |
| T10.05 | Keyboard shortcuts | `⌘O`, `⌘↵`, `⌘⌫`, `1`/`2`/`3`, `↑`/`↓`, `Esc` each perform their documented action |
| T10.06 | Focus visible | every interactive element shows a focus ring |
| T10.07 | Tab order | follows visual order: queue → conversion → inspector |
| T10.08 | Screen-reader labels | every icon-only button has an accessible name (automatable via axe) |
| T10.09 | Responsive | 1280px collapses the inspector; 900px collapses the queue; no horizontal page scroll at any width |
| T10.10 | Long file names | truncate with ellipsis, never overflow their row |
| T10.11 | Drag to reorder | queue order matches combined-output order |
| T10.12 | Empty states | every pane has a sensible empty state |
| T10.13 | Reduced motion | `prefers-reduced-motion` disables all animation |
| T10.14 | Select controls | render one chevron, no text overlap, in all four themes |

---

## 12. T11 — Privacy and storage

| ID | Case | Pass criteria |
|---|---|---|
| T11.01 | Storage accounting | reported sizes match `du` on the volume within 5% |
| T11.02 | Clear temp | removes temp only; inputs and outputs untouched |
| T11.03 | Clear inputs | removes inbox and the file records; outputs untouched |
| T11.04 | Clear outputs | removes outputs only |
| T11.05 | Clear history | history empty; **recently-used empties too** |
| T11.06 | History disabled | no new entries recorded; conversions still work |
| T11.07 | Auto temp cleanup | job temp folder gone after completion |
| T11.08 | Stale temp on startup | old temp folders removed |
| T11.09 | No content in the database | database contains no file bytes — inspect the schema and a populated file |
| T11.10 | Offline operation | with the container's network disabled, every non-AI conversion still works |
| T11.11 | AI gating | with no API key, intelligent converters are unavailable and never invoked |
| T11.12 | AI warning | when enabled, every AI route warns before running |

---

## 13. R — Regression suite

**One case per defect already found.** Each names the exact signature so a
recurrence is unmistakable. These run on every change.

| ID | Defect | Detection signature |
|---|---|---|
| R01 | FFmpeg deadlock — two readers on stdout | any `wav → mp3` raises `RuntimeError: read() called while another coroutine is already waiting` |
| R02 | `csv → xlsx` used the combine converter | route converter id is not `data.table`, or output named `*-combined.xlsx` |
| R03 | Phantom chain `png → text → html → xlsx` | a route exists from any image to any tabular format |
| R04 | Combined mode routed by first file only | `png + pdf → pdf` starts running instead of being rejected |
| R05 | `pdf → docx` flattened the document | output DOCX has 0 tables or 0 images from `rich.pdf` |
| R06 | Corrupt PDF misdiagnosed as scanned | `truncated.pdf → txt` error mentions "scan" |
| R07 | Broken `except` clause on password path | `locked.pdf` raises `TypeError: catching classes that do not inherit from BaseException` |
| R08 | Library text leaked to the user | any error detail matches the Rule-2 regex in T6.02 |
| R09 | `-formats` mux-only rows misparsed | `mp4`, `webm` or `matroska` missing from writable formats |
| R10 | `-formats` duplicate rows overwrote | `mp4` not both readable and writable |
| R11 | `-filters` parsed to zero | `filters == 0`, or `has_filter("scale") == false` |
| R12 | Cover art made audio look like video | `mp3`/`flac`/`aiff` category is video, or `wav → aiff` errors "no picture" |
| R13 | Crop expression split the filtergraph | crop conversion fails with `Filter not found` |
| R14 | SVG unrenderable — `rsvg-convert` missing | any SVG conversion fails with `delegate failed` |
| R15 | Staged output path re-allocated | `svg → jpeg` fails "produced no image" while `svg → png` succeeds |
| R16 | ODP detected as ZIP | `deck.odp` detects as `zip` |
| R17 | `Expect: 100-continue` broke large uploads | curl upload > 1 MB returns 503 |
| R18 | `public/` missing from the standalone build | `/fonts/inter-latin.woff2` returns 404 |
| R19 | Select chevron tiled | visual diff on any select control |
| R20 | Inspector footer unpinned | Convert button not at the pane's bottom edge |
| R21 | Duplicate format notices | two notices share a title in one plan |
| R22 | Mixed batch showed one route | `details.routes` shorter than the distinct source-format count |
| R23 | EPS exceeded ImageMagick's width limit | `eps → png` fails with `width exceeds user limit` |
| R24 | javaldx warning quoted as the cause | any error detail contains `javaldx`, `Fontconfig warning`, or another blocklisted benign warning; or `most_diagnostic_line` returns the last line of a stderr whose last line is a warning |
| R25 | ~~Large office file conversion~~ *(not a size defect — see below)* | a 568 MB DOCX → PDF does not complete; or a failure with only warnings on stderr is reported as anything other than "stopped without reporting a reason" |
| R26 | **Engine orphaned when the window dies** | after the desktop app exits by any means — quit, Force Quit, SIGKILL, logout — a `python -m uvicorn app.main` child is still running |
| R27 | `beforeBuildCommand` resolved outside the repo | `tauri build` fails with `Could not read package.json` for a path outside the project, or the build silently uses a stale `web/out-build` |
| R28 | apt hints shown in the desktop build | `/api/system` `installHint` contains `apt-get` when `CONVERTOR_BUILD != docker` |
| R29 | DMG bundling blocked on Finder automation | `tauri build --bundles dmg` fails in `bundle_dmg.sh` with no captured cause |
| R30 | **Bundled dylib reached by `@rpath` never copied** | `bundle_binaries.py` reports a clean bundle while `otool -L` on any bundled file shows an `@rpath`/`@loader_path` reference whose target is absent; at runtime, `eps → png` fails with `no such file` on a dylib |
| R31 | Stale engine declaration disabled a working route | `image.render_vector` reports unavailable on a build that has rsvg and Ghostscript |
| R32 | **A removed pack still reported as installed** | `DELETE /api/packs/<id>` on a pip pack whose modules were imported, then `GET /api/packs` still shows `installed: true` |
| R33 | Pack size understated by more than 3× | a pack's advertised megabytes differ from `diskMegabytes` after install by more than ~25% |
| R34 | Archive pack kept its whole payload | an installed archive pack contains anything beyond its declared members (a manual, a changelog) |
| R35 | A cancelled or failed install left files behind | after a cancel, a checksum mismatch or an HTTP error: the pack root exists, or `tmp/` holds a `pack-*` directory |
| R36 | **Pack installed successfully and provided nothing** | after installing a `dmg-app` pack, `bin/<exe>` is an absolute symlink, or the pack's engine does not resolve and run |
| R37 | **Pip packs' requirements lists were not in the app bundle** | installing `parquet` or `pdfword` from the packaged app fails with `pack_requirements_missing` |
| R38 | **Capability discovery used bare `shutil.which`** | with no ffmpeg on `PATH` but `CONVERTOR_BIN_DIR` set, `/api/system` reports `ffmpeg.available` false or the catalog drops to the curated count |
| R39 | **HTML → PDF crashed on WeasyPrint's native libraries** | `html → pdf` in a packaged build fails, or any error detail contains `libgobject`, `dlopen` or `cffi` |
| R40 | HTML → PDF demanded the Pandoc pack it never used | `doc.html_to_pdf` reports unavailable on a base install that has PyMuPDF |
| R41 | **Stream copy produced unplayable files** | `m4a → wav` yields a non-PCM audio stream; `→ vob` yields anything but `mpeg2video`; `→ wmv` yields a non-WMV-family video codec |
| R42 | WMV, WMA, M4A and VOB were unreachable by name | `/api/formats?q=wmv` (or wma, m4a, vob) returns no format whose id is that name |
| R43 | Adding a curated format silently unclaimed an extension | any extension in the curated list resolves to `UNCLAIMED`, or `.asf`/`.m4a` resolve to a format that is not about that container |
| R44 | Settings that needed a restart | `PATCH /api/settings` with a new `maxUploadMb`, then `GET /api/system`, and `limits.maxUploadBytes` still reports the old value |
| R45 | **A pack told the user to run a shell command** | any pack's `manualHint` or `unavailableReason` contains `brew`, `apt-get`, `choco` or `pip install` as the only route to a capability |
| R46 | A pack reported missing while its capability worked | the OCR pack reports `installed: false` on a build where `tesseract` is available and OCR conversions succeed |
| R47 | Downloader could not merge streams | `GET /api/download` reports `canMerge: false` on a build that has FFmpeg, which silently caps every download at a single pre-merged stream |
| R48 | **A setting was stored, displayed, and ignored** | `PATCH /api/settings` with a nonsense `maxUploadMb`, then compare the stored value with `system.limits.maxUploadBytes` — they must agree |
| R49 | **The API key was returned in plain text** | `GET /api/settings` contains the key rather than a mask, or writing the mask back clears the stored key |
| R50 | **A download reported files it did not create** | download into a folder containing an unrelated file; that file appears in the job's `files`, or an audio-only download reports no file at all |
| R51 | Cancelling a download was reported as a failure | cancelling mid-download ends with `status: failed` instead of `cancelled` |
| R52 | Searchable PDF sized in pixels instead of points | OCR of a 4000px image produces a PDF page far larger than the paper it represents |
| R53 | **A pack installed and its route stayed dead** | with a capability cached by `lru_cache`, install the pack: `engines.refresh()` runs, the pack reports installed, and the route is still unavailable until the process restarts |
| R54 | **A whole control group rendered as nothing** | load the download screen with the engine unreachable: the "What to save" heading must still be followed by its four choices |
| R55 | A two-line control used a single-row class | any `.tile` whose content is more than one line, or a label whose `scrollWidth` exceeds its box |
| R56 | **A download reported success having produced nothing** | start a download whose URL resolves but yields no media: it must end `failed` with a reason, never `completed` with an empty `files` |
| R57 | yt-dlp's error text was discarded | a failed download's detail is generic where yt-dlp logged a specific cause |
| R58 | HTML → PDF emits ligatures that break text search | search the produced PDF for a word containing "fi", "fl" or "ffi" — it must be findable, not stored as U+FB01 |

---

## 14. Assertion helpers

`tests/assertions.py` provides the content inspectors, so a test reads as an
assertion about the document rather than about bytes.

```python
docx_stats(path)   -> {tables, rows, images, paragraphs, words, headings}
pdf_stats(path)    -> {pages, words, images, has_text_layer, encrypted}
xlsx_stats(path)   -> {sheets, rows, cols, formulas}
image_stats(path)  -> {width, height, mode, has_alpha, frames, format}
media_stats(path)  -> {duration, width, height, vcodec, acodec, fps, streams}
table_stats(path)  -> {rows, cols, columns}
svg_stats(path)    -> {paths, width, height, valid_xml}
archive_stats(path)-> {entries, names, total_size}
subtitle_stats(p)  -> {cues, first_start, last_end}
```

Plus:

```python
assert_error_quality(error)     # Rule 3: shape, no library text, no benign warning
assert_no_silent_loss(src, out) # content counts survived, or a warning explains why
assert_progress_sane(frames)    # monotonic, terminal, reaches 1.0
```

---

## 15. What this plan deliberately does not cover

Stated so the gaps are known rather than assumed:

- **Visual fidelity** of a conversion — whether a converted PDF *looks* right is
  not asserted, only whether its structural content survived. Perceptual diffing
  is out of scope.
- **Every one of the 123 discovered FFmpeg formats** — a representative sample
  per container family is tested (T4.76). Exhaustive coverage would be slow and
  low-value.
- **OCR accuracy** — asserted as "non-empty and plausible word count", not
  against a ground-truth transcription.
- **AI workflow output quality** — only that they run, warn, and stay gated.
- **Multi-user or auth behaviour** — the app has none by design.
- **Browser matrix** — tested in Chromium only.

---

## 16. Running order and gates

| Stage | Contents | Gate |
|---|---|---|
| **Fast** (< 2 min) | T1, T2, T3, T6, R01–R58 | Must pass before any commit |
| **Standard** (< 15 min) | + T4, T7, T8, T9, T11 | Must pass before a merge |
| **Full** (< 90 min) | + T5, all `[slow]`, T10 automatable | Must pass before a release, and after any engine-image change |
| **Manual** | T10 `[manual]` | Before a release |

An **engine image rebuild invalidates the Full stage**: capability discovery,
engine versions and delegate availability all change with it. That is exactly
how the SVG and filter bugs got in.

### R25, and why it is no longer "large files fail"

R25 was filed from a user report: a large office file failed, and the message
quoted `Warning: failed to launch javaldx`. Fixing R24 changed what that report
means, because the quoted line turned out to be present on **successful** runs
too — LibreOffice prints it on every invocation, and it is always the last line
of stderr, so the old code quoted it as the cause of every LibreOffice failure
regardless of the actual cause.

Size was then tested directly. A 568 MB DOCX — 300 sections, 3,600 table cells,
300 unique images — converted to a 166 MB PDF in 39 seconds through the full
API, upload included. **Size alone does not cause this failure.** The original
report's real cause is still unknown, and is now the only thing the error message
will name when it recurs.

The lesson is the one this whole document is built on: the defect was not the
failure, it was the *reporting* of the failure, and it cost a session of
investigation pointed at the wrong suspect.

### R30, and the verifier that certified a broken app

`bundle_binaries.py` existed specifically to stop a Homebrew-linked binary from
shipping with references to paths that exist only on the build machine. It ended
with a verification pass, and that pass printed *"every reference resolves inside
the bundle"* over a bundle that was missing a library.

The hole: it treated any `@`-prefixed install name as already fine. `@rpath/…`
*looks* relative and therefore safe, but a library reached through `@rpath` is no
more present in the bundle than one named by absolute path — it still has to be
copied. `libwebp` refers to `libsharpyuv` that way. The collector skipped it, so
it was never copied; the verifier skipped it too, so nothing complained; and
Ghostscript died on first use in the packaged app.

Two changes, and the second matters more than the first:

1. `@rpath`, `@loader_path` and `@executable_path` are now resolved — against the
   *original* file's directory, not the copy's, since that is what they were
   written relative to.
2. The verifier **follows** every relative reference and requires the target to
   exist on disk. A check that trusts a reference it has not followed is not a
   check.

This is Rule 1 of this document applied to the build system rather than to a
conversion: *"it produced a file" is not a pass.* The bundler produced a bundle,
and said so, and was wrong.

### R32, and why `find_spec` cannot answer "is it installed"

Removing a pack deletes its files. The panel then asked
`importlib.util.find_spec("pdf2docx")` and was told yes — because `find_spec`
consults `sys.modules` first, and any module imported once keeps a valid spec
long after its files are gone. So a user could press Remove, watch 421 MB
disappear from disk, and be told the pack was still installed.

The fix is to drop the stale `sys.modules` entries whose `__file__` lived under the
removed directory. A loaded extension module stays mapped into the process either
way and that cannot be undone; what can be undone is the *claim* that it is
available, after which the next import fails honestly on the missing file and the
route reports as needing a pack again.

Verified the only way it can be: import from the pack, remove it, and check that
both `find_spec` and the API now say no.

### R35, and the three ways an install can end

Every install runs in a staging directory under `tmp/` and is moved into place by
a single rename as the last step. That makes the three endings symmetrical, and
each is tested rather than assumed:

| Ending | Assertion |
|---|---|
| Cancelled 48 MB into a 299 MB download | pack root absent, `tmp/` empty, `installed` false |
| Checksum mismatch (forced) | structured error naming both digests, pack root absent, `tmp/` empty |
| HTTP error / unreachable host | structured error naming the URL and status, nothing written |

The point is not that a failure is handled. It is that a failure leaves the app in
exactly the state it was in before the click — so retrying is safe, and a
half-installed pack is not a state the rest of the code has to reason about.

### R36 — the same defect as R30, one layer up

A pack downloaded, matched its checksum, unpacked, and was renamed into place
without a single error. It reported **installed**. It provided nothing.

The `.app` is assembled in a staging directory and moved into place at the end.
The symlink exposing `soffice` was written during staging as an *absolute* path —
so the moment the install succeeded, the path it pointed at stopped existing.
Every step returned success. The feature was dead.

This is R30 again at a different layer, and the fix is the same shape:

1. The link is relative, so it survives the move that makes the install real.
2. The installer **verifies before reporting success**: every engine the pack
   claims must resolve to a file that exists and is executable, and every module
   must import. If not, the pack is removed and the failure says what was
   missing — rather than leaving an "installed" pack whose conversions fail one
   at a time, which is the worst of both outcomes.

Tested by reintroducing the bug: `installer._expose` is monkeypatched back to
writing an absolute symlink, and the install must fail with
`pack_install_incomplete` and leave nothing behind.

The recurring lesson, now written down twice: **a step that completes without
error is not a step that worked.** Every install path in this codebase has to
end by checking the thing it was supposed to produce.

### R38 — the app quietly supported a fraction of what it claimed

`capabilities._run` located FFmpeg with `shutil.which("ffmpeg")`. Everything else
in the engine resolves binaries through `engines`, which checks the bundled
`CONVERTOR_BIN_DIR` first; this one function did not.

A macOS application launched from Finder does not inherit a shell `PATH` — it gets
roughly `/usr/bin:/bin`, with no Homebrew in it. So in a packaged build FFmpeg was
present, bundled, and working for conversions, while *discovery* could not find it:
`-formats` was never run, the discovered catalog was empty, and the app fell back
to its ~59 curated formats. No error, no warning, nothing in the log. It simply
offered a fraction of what it could do, and MPEG was one of the casualties.

This is the same defect as the `probe_binary()` bug fixed earlier for ffprobe, in
a different function — the pattern is *one code path that resolves a binary its
own way*. The regression test runs discovery with `PATH` reduced to `/usr/bin:/bin`
and asserts the catalog is the full size.

### R41 — the copy that ffmpeg was happy to make

Media conversion tried a stream copy first and fell back to re-encoding if FFmpeg
errored. For most containers that is right and near-free. For a strict container it
was silently wrong, because **FFmpeg does not refuse most illegal combinations** —
it writes the file and exits zero:

| Conversion | What it produced | What that means |
|---|---|---|
| `m4a → wav` | a `.wav` containing an AAC stream | opens in nothing |
| `mpeg → vob` | a DVD image containing MPEG-1 | no player reads it |
| `mpeg → wmv` | a `.wmv` containing MPEG-1 | Windows Media Player refuses it |

There was no error to fall back from. `copy_is_sane()` now decides up front:
permissive containers still copy anything, and a strict one requires the source
codec to belong to the family that container's own muxer defaults declare.

MPEG-1 and MPEG-2 video are deliberately not treated as one family. They look
interchangeable and are not — that grouping was the first fix attempt, and it left
`→ vob` still broken.

The counter-case matters too: `mpeg → mkv` re-encodes to H.264, which looks like a
regression and is correct. FFmpeg genuinely cannot mux MPEG-1 video into Matroska
(`Error writing trailer: Invalid argument`), so the fallback is doing its job. Two
of the three "failures" in the first run of this test were the assertions being
wrong rather than the code — worth recording, because a test that asserts the wrong
codec is indistinguishable from a bug until you check what the tool itself will do.

### R45 — a pack that could not install itself

The OCR pack's install button did not install anything. It displayed a line
telling the user to run `brew install tesseract tesseract-lang`.

That was a reasonable engineering answer — Tesseract is a system binary with no
portable macOS build — and a useless product answer. The whole point of packing
this app for non-technical users is that they never open a terminal, and a pack
whose install instruction is a shell command is not a pack, it is a note.

The fix was to stop requiring Tesseract. RapidOCR is an ONNX model distributed on
PyPI, so the pack installs the same way every other pip pack does. Tesseract is
still preferred when present because it is better on dense text, and the converter
chooses at runtime.

That change introduced R46 immediately: the Docker image has Tesseract but not
RapidOCR, so the pack began reporting itself missing while OCR worked perfectly.
`Pack.satisfied_by` now expresses "this capability is already here by another
route" — a pack describes a *capability*, not a particular implementation of one.

### R48–R50, found by asking what happens when the input is wrong

Three defects in one sweep of the new code, all the same shape: the system
accepted something, reported success, and did something else.

**R48.** Settings were validated when *read*, not when written. Typing `abc` into
the upload limit stored `abc`, displayed `abc` back, and silently used the old
value. `-5` stored `-5` and used 1 MB. The number on screen and the number in
force were different, and nothing anywhere said so. Validation moved to the write
path, so what is stored is what is used — the test asserts exactly that equality
rather than any particular clamping rule.

**R49.** `GET /api/settings` returned the Anthropic API key in full: in the page,
in any log of that response, in any screenshot of the settings screen. For an app
whose entire argument is that your data stays on your machine, handing a secret
back out on every read is the wrong default. The key is write-only now, masked on
read, with a `hasAnthropicKey` flag for the interface — and the write path ignores
a masked value, because a form that round-trips its own display value would
otherwise overwrite the real key with its own mask.

**R50.** When a download finished, the app listed what it had produced by taking
yt-dlp's hook-reported filenames and, if none of them existed, **listing the
folder**. Audio extraction renames the file, so that fallback fired routinely —
and reported whatever the user already had in their downloads folder as the
result of this download. Now the folder is snapshotted before and diffed after.

The audio-only case is what proves it: the hook reports a `.mp4`, the file on disk
is a `.mp3`, and the old code could only have answered with unrelated files.

### R53 — the cache that outlived the thing it described

`ocr_backend()` and `html_pdf_renderer()` answer "what can this install actually
do". Both were wrapped in `lru_cache`, and nothing cleared them.

So installing the OCR pack went like this: the download succeeded, the checksum
matched, the files landed, `engines.refresh()` ran, the installer's own
verification passed (it asks `find_spec`, which is not cached), the pack reported
itself installed — and the OCR route stayed unavailable, because the one function
that decides it was still returning the cached answer from before the install.
95 MB, a success message, and nothing worked until the app was restarted.

`engines.refresh()` now clears registered capability caches as well as its own
binary lookups, and each module registers its cache with `engines.on_refresh`.
Verified in both directions — a simulated install makes the route available, a
simulated removal takes it away — because a cache that is only cleared one way is
half a fix.

The general rule this earns: **anything that caches an answer to "can this install
do X" has to be invalidated where X can change.** Both of these were written in
this same session, which is how recent a decision has to be before it is worth
re-checking.

### R54 — the layout probe that could not see an empty screen

A headless probe measured every page for clipped text, overlapping boxes and
overflow, and reported the download screen clean. It was not clean. The four
quality buttons were **absent**: they came from `GET /api/download`, so until that
resolved — and forever if it failed — "What to save" was a heading with nothing
under it, and "Save to" was an empty box.

The probe reported zero findings because there was nothing on screen to clash.
Every check it ran was a check on rendered elements, and the bug was elements that
never rendered. It took a screenshot to see it, and the screenshot took ten
seconds.

The options are four fixed strings that never change at runtime. The engine had no
say in them, so fetching them bought nothing and risked exactly this. They are
defined client-side now and always render; the engine is asked only for what it
alone knows — the default folder, whether yt-dlp loaded, whether FFmpeg can merge.

Two lessons, and the second is the expensive one:

1. **Never make a control's existence depend on a network round trip** when the
   control's content is a constant.
2. **A measurement that only inspects what rendered cannot tell you what failed to
   render.** An assertion suite and a look at the thing are not substitutes for
   each other. R55 — a two-line control using a fixed-height single-row class —
   *was* caught by measurement, because that bug produced something to measure.

### R56 — "no exception was raised" is not success

A user downloaded an hour-long video. It finished in three seconds, said
**Finished**, and produced no file.

Two decisions combined to make that possible:

1. `ignoreerrors: "only_download"` was passed for every download. It exists so one
   bad entry does not abort a playlist. For a single video it converts a failure
   into a silent no-op — yt-dlp logs the reason, returns normally, and the caller
   cannot tell a finished download from one that never started.
2. The worker then marked the job `completed` unconditionally. Success was
   inferred from the absence of an exception, which after (1) means almost
   nothing.

The fix is two lines of principle. **Producing nothing is a failure, whatever the
library returned** — the run now checks that a file actually appeared and raises
if not. And `ignoreerrors` is set only for a URL that looks like a playlist, where
it is the behaviour actually wanted.

`.part` and `.ytdl` files are excluded from what counts as produced, so an
interrupted transfer cannot masquerade as a result.

R57 came with it: yt-dlp writes failures to a logger rather than raising, so the
reason was going nowhere. A collecting logger keeps the last error and it becomes
the `detail` of the reported failure, instead of a generic sentence.

**Verified in all three directions**, because a check that only fires one way is
not a check: a real download still reports `completed` with its file; a URL that
yields nothing reports `failed`; an unreachable host reports `failed` carrying
yt-dlp's own words.

### R58 — a PDF that reads correctly and cannot be searched

The PyMuPDF renderer substitutes typographic ligatures, so "fixed" is stored as
"ﬁxed" (U+FB01). The page looks right and copy-paste and Ctrl-F both fail on any
word containing fi, fl or ffi.

Found by accident: an assertion checking the generated report contained the word
"fixed" failed against a PDF where it plainly appeared. The assertion was right and
the renderer was wrong, which is the opposite of the usual case and the reason it is
worth writing down.

Low severity — the document is readable and printable — but a report nobody can
search is diminished, and the fix is a renderer font option rather than anything
structural.

## Workspace usability regression checks (2026-09-30)

- With an empty queue, show one file drop area; keep paste input and upload errors accessible.
- At 1440px, show all three panes. At 900px and 375px, keep the conversion/result action visible and allow plan details to expand above it. The page must not overflow horizontally.
- Change quality or resize settings: keep the focused input mounted while the plan refreshes, and disable both Convert and Cmd/Ctrl+Enter until that exact plan is ready.
- A missing engine, blocking warning, empty queue, running job, or existing result must prevent keyboard submission just as it prevents button submission. Repeated shortcuts must not submit duplicate jobs.
- Expand warning details automatically on narrow screens; do not hide information about data loss or external AI processing behind the collapsed panel.
- Convert a PNG to WebP: show actual input/output sizes and load both images in Compare images.
- Extract a ZIP containing five files: initially show three outputs, then reveal all five with Show all 5 files. Each output must retain its download link.
- Select a less-common format in Browse all: retain that selected format in the main picker with its description and pack requirements.
- Verify Media downloader remains separate from converted-file downloads.

Automated submission regressions: `cd web && npm test`.
Manual checks completed against an isolated engine workspace: PNG→WebP, repeated
keyboard submission, image previews, five-file ZIP extraction, and responsive
layouts at 1440px, 900px and 375px. Both preview images loaded at their actual
800px source width; the 375px viewport had no horizontal overflow and the action
footer remained inside the viewport.

## Media preview return path (2026-09-30)

- Desktop output filenames and preview buttons must open a modal viewer, never navigate the app webview to `/api/outputs`.
- Convert WAV→MP3 and MOV→MP4, open each preview, then use Back to conversion and Escape. The URL, queue, settings and results must stay intact; closing removes the player and restores focus to its preview trigger.
- Unsupported playback must show an error inside the viewer while Back remains available.
- Desktop Show file uses the Tauri bridge; browser downloads retain attachment behavior. Keep `app.withGlobalTauri` enabled to match `web/lib/desktop.ts`.
- HTML/document previews remain inside a sandboxed frame and cannot navigate the parent workspace.

Verified WAV→MP3 playback, Back and Escape; MOV→MP4 decoded at 320×240 with
readyState 4 and returned to the same results. Automated checks: `cd web && npm test`
and `desktop/runtime/bin/python3 tests/test_preview_metadata.py`.
