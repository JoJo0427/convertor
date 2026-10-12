# Convertor — Design Direction

This is the binding visual specification for the application. It exists because
"make it look professional" is not an instruction an implementer can follow, and
without a written direction the default output is a rounded-card, gradient,
oversized-heading SaaS landing page.

Every rule below overrides any habit to the contrary. When a decision is not
covered here, resolve it by asking: *what would a tool someone keeps open for
eight hours a day do?*

**This document is subordinate to `~/personal/design.md`**, the standard that
applies across every project in that tree. Where the two disagree, the standard
wins and this file is wrong. Everything below is the concrete expression of that
standard for this particular product.

---

## 1. References and what was taken from each

Three design systems were reviewed from the `design-ai` corpus. Principles were
extracted; nothing was copied, and no brand identity is reproduced.

| Reference | Taken | Explicitly rejected |
|---|---|---|
| **Linear** | Layered near-monochrome surfaces; surgical use of colour; tight letter-spacing; a narrow weight scale; dense structured layout; "quietly powerful" restraint | Its 16px card radius, pill-shaped 40px buttons, and `#5E6AD2` indigo accent — the pill/indigo combination is the exact look this product must not have |
| **Datadog** | Rows and tables as the primary structure; borders defining rows rather than cards; small muted secondary text; status conveyed by indicator rather than decoration; dark surfaces read as "working instrument" | Purple brand moments; marketing-surface treatments |
| **Postman** | Three-pane workspace; a right-hand contextual settings rail; uppercase section labels; chevron progressive disclosure | Its 20px card radius, 44px controls, `0 8px 18px` card shadows, and orange — all far too soft and too large for this density |

**Synthesis:** a dense, hairline-ruled, near-monochrome instrument panel. Linear's
restraint at Datadog's density in Postman's workspace shape.

---

## 2. Visual personality

### Conversion workflow layout

An empty workspace shows one file drop area with a short explanation. The queue,
output selector and plan appear after files are added. At widths below 1280px,
keep the conversion action visible at the bottom and allow the plan/results body
to expand above it; never hide the only action or result access. Below 768px,
stack input and output settings in a scrolling area above that action bar.
Output choices include readable format descriptions and missing-pack messages.
Pane headings use secondary ink, not tertiary ink. Result rows expose every
output through an expandable list and compare actual input/output byte counts.

The application is a **workbench**, not a product page. It should look like it
was made by people who convert files all day and got tired of everything else.

- Quiet. Nothing competes for attention except the work.
- Dense. Screen space spent on information, not on padding.
- Ruled. Structure comes from 1px lines and alignment, not from floating cards.
- Honest. Every pixel corresponds to a real fact about a real file.

---

## 3. Surfaces, and the card rule

*Revised after the first build read as too flat and too tight.* The original rule
banned depth outright, which produced an interface that was legible but cold. The
rule is now about **how many planes exist**, not whether any do.

There are exactly **three planes**, and no more:

1. **Canvas** — the background the app sits on. Nothing floats on it but panes.
2. **Pane** — one raised surface per functional area (Queue, Conversion,
   Inspector). Rounded, softly shadowed, separated by a gutter of canvas.
3. **Overlay** — menus, popovers, dialogs. Above everything.

Inside a pane, content is still **regions**: titled areas separated by 1px
hairlines, with no radius, no shadow and no fill of their own. A pane is a sheet
of paper; the things printed on it are not each in their own frame.

Controls (buttons, inputs, tiles) carry a 1px shadow so they read as pressable.
That is a surface property, not a fourth plane.

Still forbidden: a card around a single control, a single statistic or a section
heading. A card nested inside a card. A card per row.

---

## 4. Colour

The interface ships **four themes**, each with a light and a dark mode. The theme
is an attribute on the root element (`data-theme`), the mode is a second
(`data-mode`), and every rule below is expressed in tokens so a theme is nothing
but two blocks of values.

`formal` is the default and the reference. The other three exist because a tool
someone keeps open all day should be allowed to look like something they want to
look at.

### Chrome is achromatic — in the formal theme

In `formal` there is **no chromatic brand accent**. Primary buttons are solid
near-black on light, near-white on dark. This is deliberate: a coloured brand
accent applied to chrome is the single strongest signal of a generated
interface.

The other three themes are *tinted* rather than accented: their colour lives in
the surfaces and the ink, not in a highlight applied on top of a neutral UI. A
theme may use its own hue for the primary fill and the selection, and nowhere
else.

Colour appears in exactly three places:
1. **Semantic state** — queued, running, done, warning, failed.
2. **Selection** — a 1px border and a faint tint on the selected row or tile.
3. **Focus rings** — one colour per theme, for accessibility, never decorative.

Never use a gradient. Never use colour to make something look interesting.

### Tokens — the `formal` theme

Every theme defines this same set. See §11 for the other three.

```
                        light            dark
canvas                  #FCFCFC          #0B0C0D
surface                 #FFFFFF          #111213
surface-raised          #F6F6F7          #17181A     menus, popovers
surface-sunken          #F1F1F2          #0E0F10     inset wells, code
line                    #E3E3E5          #232427     hairlines, row rules
line-strong             #CFCFD3          #303236     control borders
ink                     #1A1A1C          #EDEDEF     primary text
ink-muted               #5C5C63          #9A9AA3     secondary text
ink-faint               #8A8A93          #6B6B74     tertiary, units, hints
solid                   #1A1A1C          #EDEDEF     primary button fill
solid-ink               #FFFFFF          #0B0C0D     primary button text
select                  #1A1A1C          #EDEDEF     selected border
select-wash             #F1F1F2          #1B1C1F     selected background
focus                   #2F6FEB          #4C8DFF     focus ring only
```

### Semantic state — shared by every theme

State colours do **not** change with the theme. A conversion that failed must
read as failed whichever palette is on; re-tinting status to match a scheme
makes the one thing that must be unambiguous ambiguous.

```
                        light            dark        used for
running   line/text     #1D5FD0          #6BA5FF     in-flight work
ok        line/text     #1B7A44          #4FBF7C     completed
warn      line/text     #8A5A00          #E0A33A     completed with caveats
fail      line/text     #B02A2A          #F07070     failed
idle      line/text     #8A8A93          #6B6B74     queued, skipped
```

Each state also has a wash at roughly 8% opacity for row tinting. State is shown
as a **6px dot plus a word**, not as a filled pill. Pills are reserved for format
identifiers, where the label is the content rather than decoration.

---

## 5. Typography

The type scale is tokenised (`--fs-base`, `--fs-small`, …) so a theme can move
the whole scale at once. Only `blush` does.

**IBM Plex Sans** and **IBM Plex Mono**, self-hosted, with the system stack
behind them.

### Why this family, and not another

The standard requires the choice to be reasoned from the product rather than
picked for polish, so here is the reasoning.

*What the content actually is.* Roughly a third of the text in this interface is
monospace: file names, extensions, byte counts, page counts, codec names,
output paths, and the generated FFmpeg command in the expert view. Mono and sans
are constantly adjacent — a filename inside a sentence, a size at the end of a
row, `libvpx-vp9` inside a settings label.

*What that demands.* A sans and a mono that share skeletons and vertical
metrics, so the mixture reads as one system rather than two fonts colliding. And
unambiguous `1 l I` and `0 O`, because the interface asks people to read
`libx264` and distinguish `Clover.tif` from `CIover.tif`.

*Considered:*

| Face | Why not |
|---|---|
| **Inter** | Named on the avoid list, and rightly — it has become the default signature of generated interfaces. It also has no companion mono, which is the specific thing this product needs. |
| **Source Sans 3** | Very readable, but anonymous; nothing about it answers the mono problem. |
| **Public Sans** | Utilitarian in the right way, but its mono pairing is unrelated. |
| **Atkinson Hyperlegible** | Disambiguates glyphs by design, which the content genuinely wants — but its character is too loud to carry a dense interface at 34px rows. |

*Chosen:* **IBM Plex**, because it is one superfamily. The sans and the mono are
drawn together, so the third of the interface that is monospace sits inside the
other two thirds without a seam. Its slightly drafted, engineered construction
suits a conversion workbench, and Plex Mono's `1/l/I` and `0/O` are distinct.

That is the one strong decision, per the standard's tenth principle. The rest of
the type system is restraint.

Monospace is Plex Mono on **every** theme, including Blush — a file path should
not change shape because someone chose a warmer palette.

No runtime request to a font host on any theme. See §11 for the per-theme sans.

### Scale

*Raised one step throughout in the same revision.* The earlier scale was
readable but read as cramped, which is most of what "unfriendly" meant.

| Token | Size / line-height | Weight | Use |
|---|---|---|---|
| `label` | 11px / 1 | 600 | Region titles. Uppercase, `0.05em` tracking. |
| `micro` | 11px / 1.4 | 400 | Units, counts, timings |
| `small` | 12.5px / 1.45 | 400 | Secondary row text, help text |
| `base` | 13.5px / 1.55 | 400 | Body, control text |
| `strong` | 13.5px / 1.55 | 550 | Row titles, active labels |
| `head` | 15px / 1.3 | 600 | Panel headings. `-0.01em` tracking. |
| `display` | 19px / 1.25 | 600 | Page title only. One per screen. |

Nothing is larger than 19px. There are no hero headings.

Weights are **400, 550, 600 only**. Never 700 or above.

**The `blush` theme deviates from both rules, deliberately and only there**: it
raises the whole scale by roughly 1px (base 13.5px, display 18px) and uses 600
and 700, because Nunito reads lighter than the system face at the same numeric
weight. Both deviations were asked for. No other theme may move the scale
without the same explicit reason.

**Numerals**: every number that a user compares — file size, duration, count,
percentage, dimensions — uses `font-variant-numeric: tabular-nums`.

**Monospace** is used for file paths, format ids, extensions, byte counts, codec
names, and generated commands. Never for prose.

---

## 6. Density and spacing

Spacing scale: **2, 4, 6, 8, 10, 14, 16, 20, 24, 32px**. Nothing else.

| Element | Height |
|---|---|
| Control (input, select, button) | 32px |
| Compact control | 26px |
| List row | 34px |
| File queue row | 52px (two lines) |
| Format tile | 40px |
| Pane header | 38px |
| Top bar | 48px |

- Pane padding: 14px. Never 24px.
- Gutter between panes: 10px of visible canvas.
- Gap between related controls: 8px. Between groups: 16px.
- Maximum content width: none, except reading-oriented pages, which cap at 820px.

Dense is still the goal; *cramped* is not. If a row feels like it might clip its
own text, it is one step too tight.

---

## 7. Shape and depth

- **Radius**: `--radius` (7px default) on controls and tiles, `--radius-lg`
  (10px) on panes and overlays, `0` on rows, regions and table cells. Themes may
  move both; nothing goes above 14px.
- **Shadow**: three steps, and only these.
  - `--shadow-sm` on controls, buttons and tiles — they read as pressable.
  - `--shadow-md` on panes — they read as sheets on the canvas.
  - `--shadow-lg` on overlays.
  Rows, regions, table cells and section headings still cast **nothing**.
- **Sheen**: a 1px inner top highlight on the primary button, which is how a
  physical control catches light. Nowhere else.
- **Dark mode**: a shadow on near-black is invisible, so elevation there comes
  from the surface being lighter than the canvas, plus a 1px lit top edge on
  panes. Both are required — one alone reads flat.
- **Borders**: 1px hairlines still carry structure *inside* a pane.

---

## 8. Layout

Three panes on desktop, each a region with its own header and its own scroll:

```
┌──────────────────────────────────────────────────────────────┐
│ top bar · 40px · product mark, nav, engine status, theme     │
├──────────────┬────────────────────────────────┬──────────────┤
│ QUEUE        │ CONVERSION                     │ INSPECTOR    │
│ 300px        │ fluid                          │ 320px        │
│              │                                │              │
│ input files  │ output selection               │ plan         │
│ as rows      │ contextual settings            │ warnings     │
│              │                                │ convert      │
│              │                                │ results      │
└──────────────┴────────────────────────────────┴──────────────┘
```

Panes are separated by 1px hairlines and do not float. Below 1280px the
inspector collapses to a bottom sheet; below 900px the queue becomes a drawer.

---

## 9. Components

**Button.** 28px high, 4px radius, 12.5px/550 label.
*Primary*: `solid` fill, `solid-ink` text. One per view — the one that starts
work. *Default*: transparent fill, 1px `line-strong` border. *Quiet*: no border,
no fill; hover reveals `surface-raised`. *Destructive*: `fail` text, border only
on hover.

**Input / select.** 28px high, 4px radius, 1px `line-strong` border,
`surface` fill, 12.5px text. Focus: 1px `focus` border plus a 2px ring at 25%.

**Row.** 30px (or 44px for two-line), full-bleed, 12px horizontal padding,
1px bottom rule, no radius. Hover: `surface-raised`. Selected: `select-wash`
with a 2px left bar in `select`.

**Format tile.** The one place a bordered box is allowed in the conversion pane,
because output choice is a genuine grid of options. 4px radius, 1px `line`
border, 8px padding, no shadow. Selected: 1px `select` border and `select-wash`.
Two lines maximum: format name, then one line of context.

**Section.** Uppercase `label`, 8px below, then content. No box.

**Disclosure.** A 24px header row: chevron, label, then a right-aligned count in
`ink-faint`. Rotates 90° when open. Content indents 0 — alignment carries it.

**Progress.** 3px bar, no radius, `line` track, state-coloured fill. For work
with real progress information, show the true value; never animate a fake bar.
Indeterminate work uses a 3px track with a moving 30% segment.

**Table.** Header row 24px, uppercase `label`, 1px bottom rule. Body rows 30px
with 1px rules. Numeric columns right-aligned and tabular.

---

## 10. Banned

These are the specific failure modes this document exists to prevent:

- Hero text, marketing copy, or any heading above 19px
- Gradients of any kind, especially purple or blue
- Glassmorphism, blur-behind, or translucent panels
- A card around a single control, statistic or heading; a card inside a card
- Radius above 14px
- A shadow on a row, a region, a table cell or a section heading
- More than the three planes in §3
- A coloured brand accent on chrome
- Decorative icons, sparkles, or illustrated empty states
- Fake or vanity statistics
- Pills used for anything other than a format identifier
- Padding above 12px inside a region
- Emoji anywhere in the interface
- Animated progress that does not reflect real progress

---

## 11. Themes

| id | Palette | Face | Scale | Radius |
|---|---|---|---|---|
| `formal` | Achromatic. Colour is state only. | System | base 12.5px | 4px |
| `blush` | `#FCF8F8` `#FBEFEF` `#F9DFDF` `#F5AFAF` on a deep rose-brown ink | **Nunito**, self-hosted | base 13.5px | 6px |
| `denim` | `#213448` `#547792` `#94B4C1` `#EAE0CF` — indigo on undyed cotton | System | base 12.5px | 3px |
| `earth` | `#8A7650` `#8E977D` `#ECE7D1` — clay, sage, oat | System | base 12.5px | 5px |

The supplied palettes are light-mode palettes. Each theme's dark mode is derived
in the same hue family rather than invented in a new one, so switching mode
changes the light level and not the identity.

Two values in the supplied sets could not be used literally:

- `earth` — `#8A7650` as a button fill gives a 4.3:1 label, under the 4.5
  floor. The fill is `#6F5E3F`, a darker shade of the same brown; `#8A7650`
  itself carries tertiary text and the selection.
- `blush` — the four supplied tints are all near-white, so none can serve as
  ink. Ink is `#46262E`, a deep rose-brown from the same hue, and `#F5AFAF`
  becomes the primary fill with that ink on it (7.4:1).

**Every theme, in both modes, must clear**: body text 7:1, secondary 4.5:1,
tertiary 3:1, button label 4.5:1, hairline 1.15:1 against its own surface. A
theme that cannot is not shipped. Adding a theme means adding two token blocks
and re-running that check — nothing else in the interface changes.

## 12. Motion

Transitions are 120ms `ease-out`, and only on `background-color`, `border-color`
and `opacity`. Nothing moves position. Nothing bounces. Nothing fades in on
scroll. `prefers-reduced-motion` disables all of it.

---

## 13. Interaction

Built for repetition. Every primary action has a keyboard route.

| Key | Action |
|---|---|
| `⌘O` / `Ctrl+O` | Add files |
| `⌘↵` / `Ctrl+↵` | Start conversion |
| `⌘K` / `Ctrl+K` | Format search |
| `⌘⌫` | Remove selected from queue |
| `Esc` | Close overlay, cancel search |
| `↑` `↓` | Move through the queue |
| `1`–`3` | Switch conversion mode |

Focus is always visible. Tab order follows the visual order: queue, conversion,
inspector.
