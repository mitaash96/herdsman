---
name: Herdsman Driver UI
description: A structural-engineering drawing for supervising agent work — carbon members, ash slack, one tension red.
colors:
  ground: "#e7e5e1"
  plate: "#f2f1ed"
  ink: "#0d0d0f"
  ink-2: "#63625c"
  ash: "#7a7974"
  rule: "#c9c7c2"
  rule-strong: "#b3b1ab"
  member-line: "#4e4d48"
  red: "#c01f1b"
  red-quiet: "#c01f1b26"
  seat: "#0d0d0f"
typography:
  display:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(3rem, 10vw, 8.5rem)"
    fontWeight: 640
    lineHeight: 0.9
    letterSpacing: "-0.02em"
    fontVariation: "'wdth' 70, 'wght' 640"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "2rem"
    fontWeight: 620
    lineHeight: 1
    letterSpacing: "-0.01em"
    fontVariation: "'wdth' 70, 'wght' 620"
  mark:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 700
    letterSpacing: "0.02em"
    fontVariation: "'wdth' 76, 'wght' 700"
  value:
    fontFamily: "Chivo Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.9375rem"
    fontWeight: 500
    letterSpacing: "-0.01em"
    fontFeature: "tabular-nums"
  body:
    fontFamily: "Chivo Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.6
    fontFeature: "tabular-nums"
  label:
    fontFamily: "Chivo Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.625rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.14em"
rounded:
  square: "0"
  cut-control: "9px"
  cut-field: "10px"
  cut-plate: "12px"
spacing:
  hair: "1px"
  xs: "0.5rem"
  sm: "0.75rem"
  md: "1.25rem"
  lg: "1.5rem"
  xl: "2.5rem"
components:
  view-node:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "0.5rem 1.25rem 0.5rem 0"
  view-node-slack:
    textColor: "{colors.ink-2}"
    typography: "{typography.body}"
  view-node-loaded:
    textColor: "{colors.red}"
    typography: "{typography.body}"
  view-node-hover:
    textColor: "{colors.red}"
  titleblock-cell:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    typography: "{typography.value}"
    rounded: "{rounded.square}"
    padding: "0.625rem 1.25rem"
  sheet:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.cut-plate}"
    padding: "2.5rem 2.75rem 3rem"
    width: "74rem"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.cut-control}"
    padding: "0.4rem 1rem"
  button-ghost-hover:
    textColor: "{colors.red}"
  button-ghost-disabled:
    textColor: "{colors.ink-2}"
  input-text:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.cut-field}"
    padding: "0.45rem 0.7rem"
  readout-cell:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.cut-plate}"
    padding: "0.75rem 1rem"
  slack-chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.square}"
    padding: "0.05rem 0.3rem"
  member-seat:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "0 0.25rem"
    size: "11px"
  member-seat-hover:
    textColor: "{colors.red}"
  member-seat-slack:
    textColor: "{colors.ink-2}"
  lane-rail-cell:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    padding: "0 0.75rem 0 0"
    width: "9rem"
  schedule-row:
    textColor: "{colors.ink-2}"
    typography: "{typography.body}"
    padding: "0.55rem 0.75rem 0.55rem 0"
  schedule-row-selected:
    backgroundColor: "{colors.red-quiet}"
    textColor: "{colors.ink}"
  drawer-sheet:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.cut-plate}"
    padding: "0"
    width: "min(30rem, 100%)"
    height: "100dvh"
  bank-entry:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.square}"
    padding: "1.5rem 0 1.25rem"
  dim-pair:
    textColor: "{colors.ink}"
    padding: "0"
  dim-pair-key:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
  list-tab:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.cut-control}"
    padding: "0.35rem 0.85rem"
  list-tab-current:
    textColor: "{colors.ink}"
  list-tab-hover:
    textColor: "{colors.red}"
  rig-column:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.value}"
    rounded: "{rounded.square}"
    padding: "0 0.25rem"
  rig-column-state:
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
  seat-mark:
    backgroundColor: "transparent"
    textColor: "{colors.ash}"
    size: "8px"
  seat-mark-supported:
    backgroundColor: "{colors.seat}"
    textColor: "{colors.seat}"
    size: "8px"
  seat-mark-unsupported:
    backgroundColor: "transparent"
    textColor: "{colors.rule-strong}"
    size: "8px"
  kind-chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    rounded: "{rounded.square}"
    padding: "0.2rem 0.5rem 0.25rem"
  kind-chip-current:
    textColor: "{colors.ink}"
  kind-chip-hover:
    textColor: "{colors.red}"
  register-entry:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.square}"
    padding: "0.3rem 0 0.35rem"
  register-entry-hover:
    textColor: "{colors.red}"
  register-entry-slack:
    textColor: "{colors.ink-2}"
  closure-link:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "0.4rem 0"
    size: "11px"
  closure-link-run:
    textColor: "{colors.ink-2}"
    typography: "{typography.value}"
  doc-heading:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    padding: "2rem 0 0.75rem"
  code-plate:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.ink}"
    rounded: "{rounded.cut-field}"
    padding: "0.75rem 0"
---

# Design System: Herdsman Driver UI

Scope: the browser driver UI under `ui/`. The Python daemon, the `herdsman` CLI and
herdr have no visual surface and nothing here applies to them.

## Overview

**Creative North Star: "The Force Diagram"**

The driver UI is drawn, not decorated. It borrows the language of a structural
engineering drawing: a carbon member running the height of the window, view-nodes
seated on it, leader lines pulling out to values pinned beside them, a title block
across the top carrying the readouts an operator checks first. The thesis is
supervision as a load path — every initiative is a member under load, and the
operator's job is tracing a stalled load back to its counterforce. The system
therefore spends its whole expressive budget on one question: what is carrying load
right now, and what is hanging slack.

The surface is flat and quiet by construction. There is no shadow anywhere, no
gradient, no rounded card, no glow. Depth comes from two hairline rules, one plate
tone lifted off the ground, and a repeating 4px grain that keeps concrete from
reading as flat fill. Colour is almost entirely absent: a carbon-black-on-concrete
neutral field with a single tension red, and that red is spent only on load. Density
is high in the chrome (a five-cell title block at 10px labels) and generous in the
field (a 68ch prose measure inside a large chamfered sheet), because the chrome is
scanned and the field is read.

Two anti-references are confirmed. The category's card grid and glowing node cloud
are refused outright — nothing here is a card, nothing glows. And `ui/schedule-view.html`,
the earlier prototype, is anti-reference for look and feel; it survives only as
product evidence. The identity is the wordmark "HERDSMAN" set in Archivo plus one
drawn mark, added 2026-09-18 at the owner's request: a plate with the two-cut
chamfer, one member running through it edge to edge and two members seated on that
run — an H built as a force diagram. It is carbon only, and there is nothing else:
no second lockup, no colour variant, no illustration.

**Key Characteristics:**
- Carbon black on concrete pale; exactly one accent, reserved for load
- Archivo condensed uppercase against Chivo Mono for everything else — two voices, no third
- Hairline rules and plate tone instead of shadow; flat at every level
- Chamfered plates on two opposite corners; no radius anywhere in the system
- Values pinned to nodes on leader lines, as on a drawing
- Ash always means slack, and slack always also changes form
- Line weight is load: a member is drawn as heavy as the load it carries
- What was declared and what was observed are never drawn in the same ink
- One authored motion: a member taking up load, and only when load really grew

## Colors

A near-monochrome field of ground concrete and carbon ink, held together by two
hairline greys, with one tension red that appears only where load is.

### Primary
- **Tension Red** (`{colors.red}`): the only chromatic value in the system, and it
  means one thing: this member is under load right now, or its load path has failed.
  It paints the loaded run of the strut, the loaded node's ring and leader, the
  hover and focus states of interactive text, the focus-visible outline, the caret
  and accent colour, and link underlines. Verified 4.83:1 on ground in light and
  5.06:1 in dark. **Red-Quiet** (`{colors.red-quiet}`, the same red at 15%) is used
  only as the selection highlight, where it sits under `{colors.ink}` text.

### Neutral
- **Concrete Pale** (`{colors.ground}`): the page ground and the title block's own
  background, carrying a repeating radial-dot grain (1px dot on a 4px grid, at 1.8%
  opacity in light and 3% in dark). Concrete is not flat, and the grain costs no
  network request.
- **Paper Ash** (`{colors.plate}`): the one lifted surface. Every plate — the drawing
  sheet, readout cells, the stale strip, inline `code`, the text input — sits on it.
  There is no third surface tone.
- **Carbon Black** (`{colors.ink}`): display type, node names, values, and anything
  the operator is meant to read first.
- **Graphite** (`{colors.ink-2}`): secondary text — labels, prose, purposes, node
  names in slack state. Verified 4.86:1 on ground in light, 6.31:1 in dark.
- **Slack Ash** (`{colors.ash}`): the material of a member carrying no load. It draws
  the hanging cord, the slack chip's dashed border, and the sheet's corner ticks, and
  it is the scrollbar thumb. Verified 3.47:1 in light — below text contrast, which is
  why it never carries text.
- **Hairline** (`{colors.rule}`) and **Hairline Strong** (`{colors.rule-strong}`):
  the two rule weights. The plain hairline divides the shell (strut edge, title block
  cells, sheet border, leader lines); the strong one is reserved for the edge of a
  control that can be operated — buttons and inputs — so an actionable edge reads
  harder than a divider.
- **Declaration Ink** (`{colors.seat}`): the ink a *claim* is drawn in. It is
  deliberately the same value as Carbon Black in both themes, and it is a separate
  token because it means a different thing: a declared capability seat is drawn in it
  so that a claim never picks up the member's observed state colour. Defined by F1,
  first consumed by the Rig Elevation's seats and their reading-panel marks.
- **Member Line** (`{colors.member-line}`): the structure itself — the strut, the
  node dot on a title-block label, the locator halo. Verified 6.73:1 in light and
  5.53:1 in dark. The structure is always carbon, never ash: ash on the strut would
  claim the whole diagram is slack.

### Themes

Light is canonical (`:root`); dark is the same materials under low light, defined
twice — once under `prefers-color-scheme: dark` guarded by `:root:not([data-theme='light'])`,
once under `:root[data-theme='dark']` so an explicit choice wins in both directions.
A pre-paint inline script in `app.html` reads the stored choice before first paint,
because a white flash beside a dark terminal at 1am is a defect. The operator cycles
system → light → dark from the title block. Dark values are in
`.impeccable/design.json` under `colorMeta[*].dark`.

### Named Rules
**The Load-Only Red Rule.** Red means load and nothing else. It is never used for
location, branding, emphasis, decoration, or a "primary" button. Location is carried
by a carbon locator halo on the node ring plus `aria-current`; a view you are standing
on that carries no load stays ash. Audit test: if you can remove the red from an
element and the sentence "this is under load" is still false, the red was wrong.

**The Ash Means Slack Rule.** Ash is a graphics-only value. It draws slack members
and never sets a line of text, because it does not clear text contrast — measured at
3.47:1 on ground and 3.86:1 on plate in light, 4.1:1 in dark. A slack *reading* — a
value, a state word, the name of a contention peer — is therefore set in
`{colors.ink-2}` and carries its state as a dashed ash rule instead: an
`underline dashed var(--ash)` at 1px, offset 0.3em, or a 1px dashed bottom border.
Graphite plus a dashed ash rule, never ash type.

**The Two-Tone Surface Rule.** There are exactly two surfaces: ground and plate. A
third tone would start a card system, which this world refuses.

**The Declaration-Is-Not-A-Finding Rule.** A thing the project *declared* and a thing
the daemon *observed* are never drawn in the same ink. Observation carries the member
state vocabulary — carbon, graphite, ash, red. A declaration is drawn in
`{colors.seat}` (or ash when undeclared) and takes no state colour at all, so a claim
cannot be read as a measurement and a failed probe cannot redden a capability nobody
checked. Audit test: if a probe failing changes the colour of something the probe
never looked at, the ink was wrong.

## Typography

**Display Font:** Archivo variable (with `ui-sans-serif`, `system-ui`, sans-serif)
**Body / Label / Value Font:** Chivo Mono variable (with `ui-monospace`, `SFMono-Regular`, Menlo, Consolas, monospace)

Both faces are self-hosted woff2 in `ui/static/fonts/`, split into latin and
latin-ext subsets with explicit `unicode-range`, and the two latin files are
preloaded. Nothing is fetched from a font network at runtime — PRODUCT.md's
fresh-machine install requirement makes that a hard constraint, not a preference.
The width and weight axes are driven explicitly through `font-variation-settings`
alongside a matching `font-weight`, so a fallback render is close rather than
arbitrary.

**Character:** Archivo held narrow and heavy (`'wdth' 70, 'wght' 640`) in uppercase
gives the monumental stencilled view name of a drawing's sheet title. Chivo Mono
carries every label, value and sentence, so numbers align in tabular columns and a
plan id reads as an identifier rather than prose. The pairing is a drawing office,
not a dashboard.

### Hierarchy
- **Display** (`{typography.display}`, `clamp(3rem, 10vw, 8.5rem)`, line-height 0.9,
  balanced wrap): the view name, once per page, at the top of the sheet. Nothing else
  is ever set at display size.
- **Headline** (`{typography.headline}`, 2rem): the in-sheet statement — currently
  the "No Load" title on a gated view. Set in Graphite, not carbon: a headline that
  announces absence should not shout.
- **Mark** (`{typography.mark}`, 0.9375rem): the "HERDSMAN" wordmark at the head of
  the strut, set slightly wider and heavier than display and positively tracked so it
  reads as an identifier rather than a small display heading. The drawn glyph sits
  before it at 18px in `currentColor`, plateless, centred on the strut's member line
  so the member drops out of its bottom edge — the mark is the head of the structure,
  and the wordmark is the name pinned beside it.
- **Value** (`{typography.value}`, 0.9375rem, tabular): every readout — daemon state,
  plan id, revision, approval, theme, gate values. Always paired with a Label above it.
- **Body** (`{typography.body}`, 0.875rem, line-height 1.6): the document default.
  Prose is capped at 68ch and set in Graphite.
- **Label** (`{typography.label}`, 0.625rem, tracked 0.14em, uppercase, Graphite):
  names the thing a value belongs to. A smaller 0.5625rem / 0.12em cut exists for the
  slack chip only.

### Named Rules
**The Two-Voice Rule.** Archivo for display, mark and in-sheet headline; Chivo Mono
for absolutely everything else. There is no third face, no italic, and no weight
above 700. A heading that is not one of the three Archivo roles is set in Chivo Mono
at 500 — that is what makes the Archivo appearances count.

**The Ridden Label Rule.** A label never stacks as a kicker above a heading. It rides
a hairline: either a leader line running out to its value (title block cells) or a
full-width rule with the state pinned at the far right (`MEMBER STATE ——— SLACK`).
A tracked-caps line floating alone above a title is not part of this system.

**The Tabular Rule.** `font-variant-numeric: tabular-nums` is set on `body`, so every
number in the interface is column-aligned by default. Never override it for a readout.

## Layout

A two-column shell: a 17rem strut and the field, `minmax(0, 1fr)` beside it, at
`min-height: 100vh`. Inside the field, a wrapping title block above a sheet that
takes the remaining height.

**The strut** is a hairline-bordered column with a 1px carbon member drawn down it at
a 1.5rem gutter, starting below the wordmark and running to the bottom edge — past
the last node, because a member that stops under the last node reads as a list that
ran out. Each view-node is a four-column grid (`3rem 1.5rem minmax(0,1fr) auto`):
ring, leader, text, slack chip. Rings and leaders are nudged onto the first text line
(`margin-top: 0.42rem` / `0.62rem`) rather than centred on the block, so they align
with the name and not the whitespace under it.

**The title block** is a wrapping flex row of cells (min-width 8.5rem, padding
0.625rem 1.25rem, hairline-divided). Each cell is Label over Value, and each label
is itself a miniature node-and-leader: a 4px carbon ring, then a hairline that
stretches to fill the cell. The theme control is a fifth cell rendered as a button,
visually identical to a readout.

**The sheet** is padded 3.25rem 2.5rem 4rem and holds a plate at `max-width: 74rem`,
`min-height: 60vh`, padded 2.5rem 2.75rem 3rem, with a hairline border, chamfered
corners, and 14px ash corner ticks at top-left and bottom-right. The ticks are what
make an undrawn area read as a sheet awaiting work rather than a page that failed to
render.

**Spacing** is a loose recurring set (`{spacing.xs}` through `{spacing.xl}`), not a
strict modular scale, and the build does not enforce one. Treat the frontmatter steps
as the values to reach for first, not as a grid every value must land on.

**Responsive.** The shell has one breakpoint, at 60rem. Below it it collapses to a single
column and the strut rotates from a vertical member into a horizontal rail: the
absolute member line is dropped and each node draws its own 1px segment across its
own width at `top: 0.55rem`, so the segments join into a continuous run even when the
rail wraps to a second line. Nodes become a two-row grid (ring above name), leaders
and purpose lines are dropped — three wrapped rows of purpose text cost more than
they say — and sheet padding drops to 1.75rem 1rem 3rem. Title block cells tighten to
6.5rem / 0.5rem 0.875rem. A second breakpoint at 48rem exists, and only the load
schedule uses it (see Components).

**Drafting a drawing to the viewport.** Where a surface's whole claim is a drawn
figure, the figure is scaled by one unitless `--scale` set on its container, from
which every dimension derives — the SVG box height, the annotation ladder's row
band, the headroom above the heads, and the column width — so the drawing, its ladder
and its columns can never drift out of register. The SVG keeps its own coordinate
system (a fixed `viewBox`, geometry written in its own units); only the box it is
drafted into grows. The scale steps up on the wide sheet, holds at 1 on the compact
one, and stops just under 1 at phone width, where the seat marks and the shaft come
within a pixel of each other in weight. An empty drawing drops back to 1 rather than
reserving a half-screen of blank sheet.

### Named Rules
**The Leader-Line Rule.** A value is never a bare cell. It is pinned to a node by a
leader: a ring, a hairline out to the label, and the value beneath. This is the
system's core motif and it scales from a 4px title-block dot to the full strut.

**The Wrap-Don't-Scroll Rule.** The navigation rail wraps; it never becomes a
horizontal scroller and nothing in it is clipped. No surface in this UI introduces a
hidden horizontal scroll region.

**The Measured-Edge Exception.** A *drawing* whose members are drawn against one shared
base line cannot wrap — a wrapped elevation is two elevations — so it may scroll along
its axis where a rail may not. It buys that with three things and is not legal without
them: every member stays reachable from the keyboard (a roving tab stop, arrows along
the axis, Home/End), the same facts exist in text beside the drawing, and the edge fade
that says "there is more" is toggled by measurement — a `ResizeObserver` comparing
`scrollWidth` to `clientWidth` — never assumed. A fade that is always on lies about
scrollable content when everything fits; one that is never on cuts a name in half with
nothing to say more exists. This narrows the rule for one stated condition and does not
loosen it: a rail still wraps, and nothing anywhere scrolls *hidden*.

**The Member-Runs-Through Rule.** A structural line always overshoots its last node —
down the full column height on desktop, edge to edge on each node on compact. A line
that stops exactly at content is a list rule, not a member.

## Elevation & Depth

**There are no shadows in this system.** Not one `box-shadow` is used as a shadow,
no gradient, no blur, no backdrop filter. Depth is entirely tonal and linear: the
plate tone lifted off the ground, two hairline weights, and the grain on the ground.
Anything that needs to sit "above" something else does so by being on plate, bordered
with a hairline, and chamfered.

The only `box-shadow` in the build is not elevation — it is a **locator halo**:
`0 0 0 3px <surface>, 0 0 0 4px var(--member-line)`, a solid concentric carbon ring
punched out with a gap. It marks where you are standing, and it is carbon, never red,
because red means load. It has three uses, and each knocks out in the surface it sits
on: the strut's current view-node in `{colors.ground}`, the Contention Field's selected
seat in `{colors.plate}`, and Home's two-list switch — the pressed tab — also in
`{colors.plate}`, because the tabs sit on the sheet. A seat or a tab knocked out in
ground would leave a halo a shade too dark on the sheet.

Overlap between plates never happens: the layout is a grid, and there is nothing to
stack.

### Named Rules
**The No-Shadow Rule.** A shadow anywhere in this UI is a defect. If an element needs
to separate from its ground, give it plate tone plus a hairline. If it needs to
separate from another plate, give it a 1px gap filled with the hairline colour — that
is how the readout grid is built.

**The No-Scrim Rule.** An overlaid layer does not dim what it covers. The detail
drawer is a non-modal `<aside>` that expands on selection: the Contention Field
stays fully legible *and* fully usable beside the open sheet, because dimming —
or inerting — the drawing you are supervising is the wrong instinct. The layer is
carried by the same two materials as every other level here — the sheet's plate
tone and a 1px hairline down its leading edge. Where a seam is too narrow to carry it, the sheet takes the whole viewport
(below 60rem) rather than reaching for a scrim; a dimming overlay is not a material
in this system, and the No-Shadow Rule already refuses the blur that usually comes
with one.

## Shapes

The form language is orthogonal and cut, never rounded. Every corner in the system is
either a hard 90° or a chamfer; `border-radius` is used only to draw circles (node
rings, the 4px label dot).

**The chamfer** is real geometry, not a fake corner: `border-radius: 0 var(--cut)`
with `corner-shape: bevel`, so the border itself follows the cut and the plate reads
as a plate with two corners sawn off. It is applied to the top-right and bottom-left
only — an asymmetric cut that gives every plate a reading direction. Three cut sizes
are in use and they track the element's weight: `{rounded.cut-plate}` for the drawing
sheet and readout cells, `{rounded.cut-field}` for the text input and the stale
strip, `{rounded.cut-control}` for buttons.

**Known limitation, carried deliberately.** `corner-shape` ships in Chromium only. A
`@supports not (corner-shape: bevel)` fallback reproduces the silhouette with
`clip-path`, but clip-path cannot carry the 1px border along the two diagonals, so in
Firefox and Safari those two edges read as open cuts rather than drawn ones. This is
recorded as a real gap, not papered over; the upgrade path is deleting the fallback
block once `corner-shape` ships more widely.

**Lines.** All rules are 1px. Structural members are 1px carbon; dividers are 1px
hairline; SVG strokes are 1.25px so a drawn diagram sits fractionally heavier than a
CSS divider, which reads as ink on the page.

### Named Rules
**The Two-Cut Rule.** Chamfer the top-right and bottom-left, never all four, and
never substitute a radius. A rounded rectangle in this UI is out of world.

**The Edge-Cut Exception.** A plate held flush against a viewport edge cuts only
the corner that faces the interior. The detail drawer is pinned to the right edge
and takes the bottom-left cut alone (`border-radius: 0 0 0 var(--cut)`, `--cut:
12px`), because a top-right chamfer landing on the browser frame reads as a notch
in the window rather than as a reading direction. This narrows the Two-Cut Rule
for one stated condition — flush to an edge — and does not loosen it: a plate with
ground on both sides still takes both cuts, and no plate ever takes four or a
radius. Overriding the shared geometry means overriding its fallback in the same
breath: the `@supports not (corner-shape: bevel)` block re-declares a five-point
`clip-path` for this sheet, because the shared fallback would otherwise still cut
both corners.

**The Circle-Only Radius Rule.** `border-radius: 50%` is legal — it draws a node.
Any other radius value is not.

## Components

The build ships four worked views — Run, drawn as the Contention Field, Home,
drawn as the Load Bank, Kitchen, drawn as the Rig Elevation, and Library, drawn as
the Closure Sheet — with no unavailable presentation remaining. Only what exists is documented here.

### Motion

There is exactly one authored motion in the entire system, and it is a component
behaviour rather than a token group: `take-up-load` — `scaleX` from 0.94 through a
1.2% overshoot at 62% to 1, over 320ms on `cubic-bezier(0.16, 1, 0.3, 1)`,
transform-origin left. It is the moment a member takes up load: it settles into the
load rather than fading in. It has three uses — once on the loaded node's leader when a
view becomes current, looping at 1.1s as the loading bar in AsyncField, and once on a
Load Bank member's loaded segments when that run's loaded share actually grew between
two reads. **Everything else in the system simply sets.** No hover transition, no page
transition, no fade. A global `prefers-reduced-motion: reduce` block collapses all
animation and transition durations to 0.001ms.

`stand-up` is the same moment on the other axis: `scaleY` from 0.94 through the same
1.2% overshoot at 62% to 1, over 340ms on the same `cubic-bezier(0.16, 1, 0.3, 1)`,
transform-origin bottom centre. It plays on a column of the Rig Elevation that a probe
actually raised between two reads. It is not a second authored motion — it is the one
moment expressed on the axis its drawing loads along, because a member that stands
vertically takes up load vertically and scaling it horizontally would be a wobble
rather than a settle.

**The One-Moment-Two-Axes Rule.** There is one authored moment, and a new drawing may
only restate it on its own load axis: the same 0.94 start, the same 1.2% overshoot at
62%, the same curve, the same ~0.34s, and the same compared-gain gate. Anything that
changes the timing, the curve or the shape is a second motion and this system does not
have one.

**The Grew-Or-Nothing Rule.** `take-up-load` fires on a state change that is genuinely
a gain in load, compared against the previous read and keyed by the thing's own
identity — never on arrival, never on a poll, never on a re-order. Home holds the
previous loaded share per plan id and plays the motion for 340ms only on the members
whose share rose. A drawing that twitches every six seconds while nothing happened is
decoration, and this system has no decoration.

### Member States

The state vocabulary is applied by putting `.member` on an element and setting
`data-state`. It sets `--member-ink` and `--member-dash`, which children consume via
`currentColor` and `stroke-dasharray`. Colour never carries a state alone: each state
also changes the member's form.

- **slack** — ash, dashed (`3 3`). No load yet: a gated view, an unaddressed plan, a
  stale readout.
- **balanced** — graphite, solid. In place, load path complete, not yet loaded.
- **loaded** — red, solid. Under load right now: the current view.
- **seated** — carbon, solid. Load transferred, permanently in the structure: the
  daemon answering, an approved plan.
- **failed** — red, gapped (`1 4`). The load path is discontinuous.

### Navigation (the strut)

- **Style:** view-nodes seated on a carbon member. Each node is ring + leader + name
  + purpose, plus a slack chip when gated. Name at 0.875rem/500 in carbon; purpose at
  0.625rem tracked caps in graphite.
- **Slack (gated):** ring border dashed, name drops to graphite, a dashed ash chip
  reading "SLACK" sits at the right. A gated view is slack whether or not you are
  standing on it — it carries no load either way.
- **Loaded (current, ungated):** the node's own length of member turns red and the
  ring fills red, so the current view shows as a run of tension on the strut. The
  leader plays `take-up-load` once.
- **Current (location):** `aria-current="page"`, a carbon locator halo on the ring,
  full-opacity leader, carbon name. Location is a separate signal from load and never
  borrows red.
- **Hover:** the name turns red. No underline, no background, no movement.

### Title Block

- **Style:** hairline-divided cells on ground, each a Label riding a leader over a
  Value. Labels take a 4px carbon ring at their left.
- **State:** the Daemon and Approval cells are members — their value inherits
  `--member-ink`, so "Answering" is carbon-seated, "Stale" is ash-slack, "Not
  answering" is red-failed. Unknown reads `—`, never `0` and never blank.
- **Theme cell:** an identical cell rendered as a `<button>` with `font: inherit`,
  transparent, no border except the shared divider; the value turns red on hover. Its
  `aria-label` states the current theme and that activating changes it.

### Sheet / Plates

- **Corner Style:** chamfered top-right and bottom-left (`{rounded.cut-plate}`).
- **Background:** plate; **Border:** 1px hairline; **Shadow:** none, per the
  No-Shadow Rule.
- **Corner ticks:** 14px ash L-brackets at top-left and bottom-right, inset -1px.
- **Internal Padding:** 2.5rem 2.75rem 3rem, tightening to 1.5rem 1.25rem 2rem below
  60rem.

### Readout Grid

- **Style:** cells on plate separated by a 1px `gap` filled with the hairline colour,
  with a matching 1px border — the divider is the gap, not a border per cell.
  Chamfered as one plate. A `.wide` cell spans the full row. Two layouts are in use
  and both are legal: `repeat(auto-fit, minmax(11rem, 1fr))` where the cells should
  stay equal, and a wrapping flex row of `1 1 11rem` cells where a long value should
  be allowed its share.
- **Content:** `dt` is a Label, `dd` is a Value in carbon (or graphite for a
  sentence). A `dd` may itself be a member — it inherits `--member-ink`, so a count of
  write conflicts reads seated at zero, failed above it, and slack when unread. A cell
  may carry a **gloss**: one 0.625rem graphite line under the value saying what the
  number means ("the most agents this plan can ever keep busy"). Glosses are dropped
  below 60rem.

### Buttons

- **Shape:** chamfered (`{rounded.cut-control}`), transparent, 1px `rule-strong`
  border — the harder hairline reserved for operable edges.
- **Style:** 0.75rem tracked caps in carbon. There is one button variant in this
  system: a ghost. No filled or "primary" button exists, because a filled button
  would have to be red, and red means load.
- **Hover:** border and text both go red. No fill, no lift.
- **Disabled:** text drops to graphite, border softens to the plain hairline,
  `cursor: not-allowed`.

### Selects

- **Style:** the input's geometry exactly — plate, 1px `rule-strong`, chamfered
  (`{rounded.cut-field}`) — with the native dropdown arrow removed
  (`appearance: none`) and replaced by a 1px hairline chevron in `{colors.ink-2}`,
  drawn by a `.pick` wrapper's `::after` at 0.4em square, rotated 45°, with
  `pointer-events: none`. The control stays a real `<select>`: the keyboard, the
  screen reader and the platform's own list are the point.
- **Content:** the first option is the prompt ("Choose a run…"), never a
  pre-selected guess. A select that depends on another is `disabled` until its
  parent is chosen and says so in its prompt ("Choose a harness first…"),
  disabled styling matching the button's: graphite text, plain hairline border.
- **Empty:** a select with nothing to offer is not rendered. The surface prints
  why there is nothing to choose, in the daemon's own words.
- **Where:** Run's plan picker and R6's reassignment (harness, model) and
  redirect (checkpoint). Anything that names a thing that already exists is one
  of these, never a text field.

### Inputs

- **Style:** plate background, 1px `rule-strong` border, chamfered
  (`{rounded.cut-field}`), `font: inherit` so the field is set in Chivo Mono at body
  size.
- **Focus:** border turns red, plus the global 2px red `:focus-visible` outline at
  2px offset. `caret-color` is red.
- **Required:** stated in a 0.625rem tracked-caps note beneath the row, wired with
  `aria-describedby`. There is no red asterisk anywhere.

### Contention Field (signature component)

The Run view's drawing: one plan's dependency graph as a ruled field, rows are lanes
(chains that can never overlap, so their count is the plan's parallelism ceiling) and
columns are dependency rank. Everything in it is structure; nothing in it is a time.

- **One grid rules the whole drawing.** A single CSS grid carries the rail, the rank
  marks, the lanes and every seat, so they cannot drift apart:
  `grid-template-columns: 9rem repeat(var(--cols), minmax(0, 11rem)) minmax(0, 1fr)`,
  rows a 1.5rem rank header over `var(--lanes)` lane rows. The lane row steps down
  3.25rem → 2.75rem → 2.25rem as the lane count passes 6 and 10. Hairline above and
  below the frame, hairline down the right edge of the rail.
- **Column pitch is capped, with a trailing filler column.** `minmax(0, 11rem)` per
  rank plus a final `minmax(0, 1fr)` that absorbs the slack, so a three-rank plan
  tightens to the left instead of stretching one dependency across the sheet.
- **The rail** names each lane: a Label riding a leader (a hairline that flexes out to
  the field edge), with a note under it — `n · critical` in carbon where the lane
  carries the critical path, `n in sequence` in graphite where it holds more than one
  member. The rank header is 0.625rem tracked graphite numerals, centred over each
  column and sitting on a hairline.
- **The cord layer** is one SVG absolutely positioned over the field's own columns
  (`grid-column: 2 / span var(--cols); grid-row: 2 / -1`, `pointer-events: none`) with
  `viewBox="0 0 {columns} {lanes}"` and `preserveAspectRatio="none"`: geometry is
  written in grid units — a member sits at `depth + 0.5`, `lane + 0.5` — and the
  browser does the layout arithmetic. A dependency is an elbow (out horizontally, then
  down into the target's column); a contention pair is a bracket dropped from the
  midpoint between the two, because it is a relation and not a flow.
- **Reading one member.** Selecting a seat drops every non-incident cord to 0.25
  opacity — the critical path excepted, because it is the plan's floor whatever you
  happen to be reading.

**Cord vocabulary.** Exact, and all of it unfilled, butt-capped and miter-joined:

- **Lane ruling** (`{colors.rule}`, 1px, dotted `1 5`): the lane's own line, drawn the
  full width of the field whether or not it carries anything.
- **Lane run** (`{colors.member-line}`, 1.25px): the chain itself, first seat to last
  with a 0.2-unit overshoot at each end — a member runs through.
- **Critical path** (`{colors.ink}`, 2.5px): the one heavy run, and the only stroke in
  the system at that weight.
- **Dependency crossing** (`{colors.rule-strong}`, 1.25px): drawn only for edges that
  leave their lane; inside a lane the run already carries the order.
- **Write conflict** (`{colors.red}`, 1.5px, gapped `1 4`): a hard limit on the
  concurrency the lanes promise, so it is always drawn.
- **Missing edge** (`{colors.ash}`, 1.25px, dashed `3 3`): advisory, and one shared
  file can suggest a dozen, so it is drawn only for the selected member. The schedule
  lists every one, always.

**Member seats** are real `<button>`s placed on the grid at their lane and rank, each
carrying `.member` and `data-state`, so they inherit the state vocabulary unchanged:

- **Ring:** 11px, 1.5px `currentColor`, `border-radius: 50%`, filled `{colors.plate}`
  so it knocks out of the run it sits on.
- **State forms:** slack dashes the border; loaded fills red; seated fills carbon;
  **balanced takes a ready pip** — a `currentColor` disc inset 2px — because a dashed
  border against a solid one is unreadable at 11px and ready is the one state an
  operator acts on; failed cuts the ring open left and right; **cancelled strikes the
  ring** with a 2px diagonal, a line through the member rather than a sixth colour.
- **Write-conflicted:** a 4px red tick under the ring, so the seat carries what the
  cord says.
- **Selected:** the inherited carbon locator halo, knocked out in plate, plus
  `aria-current`.
- **Mark:** the initiative id at 0.875rem/500 in carbon (graphite when slack, red on
  hover) on a `{colors.plate}` background, so the run knocks out behind the label.
  Marks are dropped past twelve ranks and below 60rem; the rings still carry state and
  position, and a note says the schedule names every member.

**The key** sits under the field in two parts, because a key that documents the cords
but not the members is half a key: member states as ring + tracked-caps word, then cord
kinds as a 1.75rem swatch drawn with the same stroke values it documents, `dt` over
`dd`. When the risk report did not answer, the conflict and missing-edge entries read
*unread* rather than describing a cord that was never drawn.

**Responsive.** Below 60rem the rail gives up 9rem for 4rem — every rem it gives back
widens the seats, which are the drawing's tap targets — and the lane notes, the id
marks and the seat padding drop.

### Load Schedule

The accessible equivalent of the drawing, and not a summary of it: a real `<table>`
carrying every member in the field's own order, on the field's one selection.

- **Style:** `table-layout: fixed`, no zebra and no cell borders — one hairline under
  each row, and a `{colors.rule-strong}` hairline under the tracked-caps header row.
  Cells are top-aligned graphite; the member id is carbon with its name in graphite
  beneath it.
- **Fixed widths** (34 / 6 / 6 / 12 / 12 / 30%). The contention column needs width, not
  height: stacking a peer id over its path cost 1811px of page and still broke long
  routes mid-word. Reach for column width first.
- **Critical path:** named under the member on a 2.5px carbon underline — the weight
  the critical run is drawn at, so the drawing and the table say it the same way.
- **Selected row:** `{colors.red-quiet}` across every cell, plus `aria-current="true"`.
  This is the only place red-quiet appears outside `::selection`.
- **Row control:** the member cell is a bare button (`font: inherit`, no border, no
  padding) whose id and name go red on hover, exactly as a view-node does.
- **Responsive:** below 60rem the lane and rank columns drop — both are drawn in the
  field and named in the member readout — and cells break anywhere; below 48rem the
  contention column drops too, and a note says where it went rather than dropping a
  real blocker in silence.

### Keyboard Model

The field and the schedule are one widget in two renderings, and the keyboard says so:

- **One selection**, held as an initiative id and nothing positional, so a live re-read
  that re-ranks the field cannot move what you were reading.
- **A roving tabindex in each.** Each holds exactly one tab stop and arrows move within
  it. In the drawing ←/→ move along the lane, ↑/↓ to the nearest rank in the lane above
  or below, Home/End to the ends of the lane, and **selection follows focus** — moving
  through a drawing is how you read it, and nothing here is destructive. In the
  schedule ↑/↓ move by row. A thirty-seat drawing must not be thirty tab stops.
- **The anchor is never the selected id alone.** It falls back to the first member, so
  a revision that drops the selected initiative cannot leave a widget with no tabbable
  element. That is a keyboard trap, not an empty state.

### Load Bank (signature component)

Home's drawing: the fleet as a rack of members, one entry per run, in the daemon's
own order. Nothing in it is a card and nothing in it is a chart — every figure on
screen is either a member or a dimension pinned under one.

- **The entry** is a list item on a 1px hairline top rule, padded `1.5rem 0 1.25rem`
  (`1.25rem 0 1rem` below 60rem): a ruled label carrying the run id at the left, the
  hairline between, and the run's status word at the right as a member; a two-line
  clamped brief at the 68ch measure in graphite; the member; then the dimension
  string. The run id is carbon at the tight value cut with a `{colors.rule-strong}`
  underline that turns red with the text on hover — a link drawn as an operable edge,
  not as a blue.
- **Weight is load.** A segment is 2.5px when it carries load (settled, running,
  failed), 1.25px when work is in place but not loaded (paused), and 1px when it
  carries nothing (not started, cancelled). 2.5px is the critical-path weight from the
  Contention Field, reused rather than re-invented. Colour agrees with weight and never
  carries the state alone: settled is solid carbon, running solid red, failed red on
  the failed member's own `1 4` gap, paused solid graphite, not-started ash on the
  slack member's `3 3` dash, cancelled solid ash — present, and it will never carry
  load, so it cannot be mistaken for work that has not started.
- **Order is load-left to slack-right**, fixed: settled, running, failed, paused, not
  started, cancelled. `failed` sits *inside* the loaded stretch rather than behind it,
  because a break in the load path is where the load stopped, not something queued.
  A state with no initiatives is dropped rather than drawn at zero width.
- **One fleet-wide unit.** A member's drawn length is its initiative count measured
  against the largest listed run, floored at 12% so a one-initiative run is still a
  member you can see. Equal initiative counts therefore draw equal lengths and the red
  across the whole bank compares in one pass. Drawing every member full width would
  make length mean proportion *within its own run*, which is exactly the count the
  composition exists to refuse.
- **The tail** is a 0.75rem `{colors.member-line}` hairline drawn *outside* the spanned
  run, so even a floored short member overshoots its last segment — the
  Member-Runs-Through Rule at fleet scale.
- **The fleet's own member** is drawn above the bank on the same rules and sets no
  span, because it *is* the unit. It is drawn only above more than one run: at one run
  it would be the same member twice on one screen.
- **Accessibility:** every member is `aria-hidden`. The dimension string beneath it
  carries the same figures as text, which is the Two Renderings Rule met without a
  second table.

### Dimension String

The text equivalent of a member: a wrapping baseline row of label-and-value pairs
divided by 1px `{colors.rule}` hairlines (0.9rem tall, centred), gapped
`0.3rem 0.65rem` and tightening to `0.3rem 0.5rem` below 60rem. The key is the Label
(0.625rem, tracked 0.14em, uppercase, graphite); the value is carbon at the tight
value cut (0.8125rem/500), and may itself be a member so a running count reads red and
an unknown reads graphite under a dashed ash rule. Each pair is `white-space: nowrap`,
so the row wraps between pairs and never inside one.

It is what a per-row readout grid would otherwise be. The readout grid still opens the
sheet once, where four figures describe the whole fleet; repeating a bordered plate of
cells under every one of forty runs would turn the bank into the card grid this world
refuses. The string is also where a row's own control lives: **the row control is a
bare button** (`font: inherit`, no border, no padding, Label typography, red on hover)
set as the last pair, exactly the load schedule's row-picker idiom. A bordered ghost
button repeated down a list would put forty operable edges on the sheet and make the
rarest thing in the world the most common.

### Two-List Switch

Two lists, not a filter over one: each is its own read with its own totals, and the
switch is navigation between them. A `role="group"` of two chamfered
(`{rounded.cut-control}`) plate-cut buttons, 0.75rem tracked caps in graphite on a 1px
`{colors.rule}` border, each carrying its own count in a second span that inherits the
button's colour when pressed or hovered.

- **Hover:** border and text go red, as every operable edge in this system does.
- **Current:** `aria-pressed="true"` takes carbon text, the harder
  `{colors.rule-strong}` edge, and the carbon locator halo knocked out in
  `{colors.plate}`. Which list you are reading is location, not load, so it is never
  red — the same separation the strut's current view-node makes.

### Slack Notice (signature component)

The one unavailable presentation, used by the three views whose substrate has not
landed. It is a member seated in the structure that carries no load: present, named,
and visibly slack. Structure: a `MEMBER STATE ——— SLACK` ruled label; a drawn SVG of
two seated nodes with a dashed cord hanging between them because nothing pulls it;
the headline "No Load"; a 68ch sentence naming the view and saying plainly that
nothing loads it; then a readout grid carrying the gate — what it Needs, who Owns it,
and anything else genuinely blocking. It never stands in for the view's content and
never shows a fake preview.

### Async Field (signature component)

Renders the daemon's read states in the structure's own vocabulary, from a `Resource`
whose governing rule is that **a failed refresh never erases what was last known to
be true**:

- **Loading** — a balanced member: an 8rem 1.25px bar looping `take-up-load`, with a
  Label reading "Reading {what}". `aria-busy`.
- **Broken load path** (first read, nothing to preserve) — a failed member: a drawn
  SVG of a gapped, hatched line; "The load path is broken"; the daemon's own message;
  a recovery hint naming the exact command when the daemon is unreachable; a "Read
  again" ghost button. `role="alert"`.
- **Stale** (refresh failed with data in hand) — a slack member: the last values stay
  on screen, above a chamfered plate strip carrying the Label "Stale", the time they
  were last confirmed, and "Read again". `role="status"`.
- **Empty** is distinct from all three and is written per-view as a sentence, never
  as a zero.

### Detail Drawer (signature component)

The one modal surface in the build: a right-hand sheet carrying a single
initiative, opened from a seat in the Contention Field. It is a plate like any
other, laid over the ground rather than beside it, and it neither dims nor
displaces the drawing that named it.

- **Sheet:** a non-modal `<aside>` at `min(30rem, 100%)`, `position: fixed` with
  `inset: 0 0 0 auto` so it sits against the right edge, `100dvh` tall, plate
  background, 1px hairline border, cut bottom-left only (see the Edge-Cut
  Exception). No scrim at all, per the No-Scrim Rule. Hidden — `[hidden]`, not
  unmounted — until a member is selected.
- **Structure:** a fixed header on a hairline (padding 1.5rem 1.5rem 1.25rem) over
  a scrolling body (`overflow-y: auto`, `overscroll-behavior: contain`, padding
  0 1.5rem 2.5rem). Sections are separated by 1.75rem of space and a ruled label,
  not by a divider — the label's own rule is the divider.
- **Head:** the ruled label `MEMBER ——— <id>`, the initiative name as an in-sheet
  headline (Archivo, 2rem, `'wdth' 70, 'wght' 620`, uppercase, `text-wrap: balance`,
  `overflow-wrap: anywhere`), and a ghost button at the far right. The headline is
  carbon here, not graphite: it names a thing that exists, rather than announcing an
  absence as the Slack Notice's does.
- **Openness** is the parent's state; the element's is the browser's. They are
  reconciled in one direction and `close` reports back, so Escape and the close
  control take exactly the same path.
- **Readouts and attempts** are built exactly as the Run sheet's: the readout grid
  unchanged (1px `gap` filled with the hairline colour, `flex: 1 1 10rem` cells,
  `.wide` spanning the row, Label over Value with an optional 0.625rem gloss), and
  each attempt on its own bordered plate carrying its own ruled label and grid.
- **Responsive:** below 60rem the sheet takes the full width and padding tightens
  to 1.25rem 1rem 1rem in the head and 0 1rem 2rem in the body. Nothing else drops:
  the drawer's whole content is the read.
- **Motion:** none. There is no entrance, no fade and no slide. `take-up-load`
  belongs to load, not to panels, and the drawer simply sets.

**The subtask chain.** Subtasks are the member state vocabulary applied outside a
drawing for the first time, and they are drawn as a chain rather than listed as
rows: an 11px ring per step (1.5px `currentColor`, `border-radius: 50%`, filled
`{colors.plate}`) in a three-column grid of ring / brief / state word, with a 1px
`{colors.member-line}` run down the ring column that starts at the first ring and
overshoots the last by 0.55rem. The rings knock out of that run in plate exactly as
a seat knocks out of its lane run — the Knock-Out Rule and the Member-Runs-Through
Rule, unchanged, at drawer scale. State forms are the seat's: slack dashes the
border, loaded fills red, seated fills the seated ink, and balanced takes the
inset-2px ready pip, because a dashed border against a solid one is unreadable at
11px. The state word is a 0.625rem tracked-caps label in graphite, red on a loaded
step, and carries a dashed ash underline when the step is slack or skipped.

**The blocking statement, in two registers.** The section an operator opens a
stalled member to read is stated first and is never blank. It is written in exactly
two lines of type: a lead sentence naming the break, set in `--member-ink` so it is
red only on a failed member (`This member failed.`), and the sentence that explains
it, always in `{colors.ink-2}` at the 68ch prose measure. **Red marks a break; it
never sets a paragraph.** A seated statement raises its prose to `{colors.ink}` and
carries no lead line at all, so the two registers stay one signal.

- **The one action.** Terminal focus is the only control in the drawer besides
  close, and it is the ghost button unchanged (`{rounded.cut-control}`, 1px
  `rule-strong`, red on hover, graphite and softened on disabled). Its outcome sits
  beside it as a member: `seated` in carbon with `role="status"` on success,
  `failed` in red with `role="alert"` on failure. No toast, no banner, no dialog on
  a dialog.
- **Stated absences.** Every section that has nothing to show says why in a
  sentence rather than rendering an empty list or a zero — no attempt has started,
  no pane was recorded, no subtasks were declared, activity is unread rather than
  idle. A dropped member keeps the drawer open and says the revision moved.

### Rig Elevation (signature component)

Kitchen's drawing: the local machine as an elevation. Every declared harness is a
column standing on one base line, and its height is exactly how far one bounded probe
carried it. Nothing in it is a status card and nothing in it is a tick.

- **One base line, four courses.** The drawing's container carries a 1.25px
  `{colors.member-line}` bottom border — the ground every column stands on — and four
  named courses are cut at one fixed band above it. The courses are annotated top-down
  by a ladder of 1px `{colors.rule}` dashed rules with a right-aligned Label riding each
  one, and cleared bottom-up by the columns, so the ladder's rules and the columns'
  ticks are the same four heights measured from the same line. The ladder's headroom is
  container padding, never an extra grid row: a row nothing is placed in gets
  back-filled and the whole ladder slips one course.
- **The column** is the member: a 2.25px `currentColor` shaft from the base to the
  height observed (1.25px when slack, because weight is load), a 1px `{colors.rule-strong}`
  tick at each course that turns `--member-ink` once cleared, and a
  `{colors.ash}` `3 4` dashed **ghost** continuing from the head to full height. The
  ghost is the point of the drawing: a short column is short *against the height it was
  meant to reach*, not merely small.
- **Head forms.** Seated caps the shaft with a 2.25px bar; failed draws a 1.25px
  `{colors.red}` double hatch across the shaft where it stopped. The colour never
  carries it alone — the height and the head form already do.
- **Seats: the declared half.** Declared capabilities are 8px squares bolted along the
  head the probe actually reached, never up in the ghost. Filled `{colors.seat}` is
  declared supported, an open `{colors.ash}` outline is undeclared, and a
  `{colors.rule-strong}` outline struck by a 1px **horizontal** bar is declared
  unsupported. The strike is horizontal because a diagonal would read as the failed
  head's hatch, and one drawing cannot spend the same mark on "declared unsupported"
  and "the load path broke".
- **The same marks at reading size.** The reading panel repeats the three seat marks as
  0.5rem CSS squares with identical fills and inks, so the drawing and the text are one
  vocabulary rather than a legend that only works in one of them.
- **Selection is location.** The read column takes a 2.5px `{colors.member-line}` edge
  along the base line and an underlined name — a harder edge, never red, exactly as the
  strut's current node and the two-list switch do.
- **The legend is two lines, not a key block.** `COURSE — observed` and `SEAT — declared`
  as Label over a graphite sentence. It exists because the geometry makes a distinction
  a first-time reader cannot be assumed to already hold.
- **The reading panel** is a plate beside the drawing (`minmax(22rem, 1fr)` against the
  drawing's `1.6fr`, collapsing to one column on compact): the harness name at 1.0625rem
  with its state word as a tracked-caps member chip, then sections separated by a ruled
  Label on a 1px hairline rather than by a divider. Observed facts, declared seats, an
  explicit **Not observed** section naming authentication and why it cannot be measured,
  and the daemon's own reason and next action drawn as a member.
- **Accessibility:** the strip is a `role="tablist"` of columns over one `tabpanel`;
  arrows move along the elevation and carry focus with the selection; each column's
  `aria-label` states its name, state, the course it reached and how many capabilities
  were declared, because the silhouette is not available to a screen reader.
- **Responsive:** the whole figure is drafted by one `--scale` (see Layout).

### Live Outcome Line

The result of an operator-asked action, reported in place: a member-inked row of a Label
and a sentence, `role="status"`, rendered from first paint and collapsed by `:empty`
(`height: 0; margin: 0; overflow: hidden`) until it has something to say. It is seated
on success and failed on failure, and on failure it takes focus. No toast, no banner.

**The Live-Region-From-First-Paint Rule.** A region that will announce something is in
the document before the thing happens and takes no space until it does. A live region
created at the moment of the change is not reliably announced, and a placeholder that
reserves space is a hole in the sheet.
### Register

The Library's shelf: every asset as a wrapping ruled rail grouped under kind labels —
the strut's compact idiom at shelf scale, never a scroller and never a card. It is also
the picker: no text field on this surface names an asset.

- **Group:** a `.tight` ruled label carrying the kind word, its hairline and the group's
  count in graphite; 1.25rem between groups, 2.5rem under the register.
- **Entry:** a bare button (`font: inherit`, no border, padding `0.3rem 0 0.35rem`)
  stacking the asset name at body size/500 over its dimension row, on a wrapping rail
  gapped `0.25rem 1.75rem` (`0.25rem 1.25rem` below 60rem). It carries `.member` and a
  `data-state` from the asset's status, so slack and failed drop the name to graphite.
- **Hover:** the name turns red. **Current:** carbon name under a 1px
  `{colors.member-line}` rule plus `aria-current` — which asset you are reading is
  location, so it is never red.
- **Dimension row:** 0.625rem tracked caps in graphite, wrapping: origin (with
  `override` where a project copy shadows a bundled one), token count, reference count,
  and a non-active status word carrying the dashed ash rule of a slack reading.
- **Filters** rule above the register: kind as a chip row because the set is five and
  always visible, origin and status as selects, and one `find` field. The chip is a
  borderless 0.625rem tracked-caps control in graphite, red on hover, and pressed takes
  carbon over a 1px `{colors.member-line}` bottom rule — a label ridden by a rule, not a
  filled pill. `find` narrows a list already on screen; it never names a thing.
- **Empty filter:** a sentence saying how many assets are on disk and that this is a
  filter with nothing behind it, never a zero and never a blank rail.

### Closure Sheet (signature component)

The Library's drawing: one asset's reference closure as a chain down the left with the
running context cost climbing beside it, and every document in that closure stacked to
the right in walk order. It refuses the two-pane docs browser — a sidebar of titles
facing one isolated document, with a reference as a blue word that goes somewhere else.

- **The grid:** `minmax(0, 20rem) minmax(0, 1fr)` with a 2.5rem gap, items start-aligned,
  the chain column `position: sticky` at `top: 1.5rem`. Below 60rem it collapses to one
  column at a 1.75rem gap and the chain goes `static` — a sticky index in a single column
  would sit on top of the document it indexes.
- **The chain** is the Detail Drawer's subtask chain unchanged, at shelf scale: 11px rings
  (1.5px `currentColor`, `border-radius: 50%`, filled `{colors.plate}`) on a 1px
  `{colors.member-line}` run that starts at the first ring and overshoots the last by
  0.55rem. Each link is a three-column grid of ring / ref / running total. `--depth`
  indents the ref 0.85rem per level while the ring carries the same negative margin, so
  every ring stays on one column and a nested ring still knocks out of one continuous
  member.
- **Node states** are the member vocabulary applied to a reference walk: the root is
  **loaded**, a reference that resolves is **seated**, an archived one is **slack**
  (dashed ring, graphite ref, a state word under a dashed ash rule), and one that
  resolves to nothing or closes a cycle is **failed** — the ring cut open left and right,
  the state word red. A `present` node prints no state word; the ring already says it.
- **The running total climbs beside the chain** at 0.8125rem tabular graphite: the
  closure's cost *at that ring*, never the asset's own. A node with no readable asset
  reads `—`. The readout grid beneath carries the closure's three figures unchanged, and
  the effective-context cell is itself a member — seated inside the daemon's warning
  budget, failed over it, with the budget printed as `/n` in graphite beside the count.
- **Findings** are the daemon's verdicts, not this build's arithmetic. Each is a row on a
  1px hairline top rule: a member label (red on an error, graphite under a dashed ash
  rule on a warning), the ref in carbon, the message in graphite, and an optional 0.75rem
  detail. A closure-wide finding is stated once beside the total that carries it; an
  asset's own findings repeat under that asset and nowhere else. When the validate call
  fails, the chain still draws and a lead sentence says the verdicts are unread — the
  drawing is this build's walk, the findings are not.
- **The documents** stand right of the chain, root first in walk order. Each is an
  `article` opened by a ruled `h2` — a heading for a screen reader and a ruled label for
  an eye, so the label cut wins over the UA's own h2 (0.625rem/500) — carrying the node's
  state word, its hairline, and the ref as a member. Under it the title, then a dimension
  string of kind / origin / revision / tokens / status, then the body. Blocks are divided
  by a hairline and 0.5rem above, 2rem below.
- **Anchors** run from a resolvable chain ref to its document and are keyed by position
  rather than by ref: a cycle repeats a ref a resolved node already used, and two
  elements sharing an id make every anchor to it resolve to the first.

### Document (signature component)

The reading surface, and the unit's stated reading priority. Markdown is rendered from
typed tokens into real elements — no HTML is ever produced, and anything outside the
supported subset degrades to the literal characters it contains.

- **Headings are Chivo Mono.** Archivo is the display, the mark and the in-sheet
  headline, and a document heading is none of the three. Hierarchy is scale, weight (500)
  and a hairline: 1.125rem, then 1rem on a `{colors.rule}` bottom rule, then 0.9375rem,
  then 0.875rem tracked 0.04em caps in graphite. Margin is `2rem 0 0.75rem` — more space
  above than below, so a heading belongs to what follows it — and the first heading in a
  document takes none.
- **Measure:** prose, lists, quotes and rules are held to 68ch; code blocks and tables run
  the full column. That is what the 68ch is for.
- **Code block:** a plate like any other — plate tone, 1px hairline, `{rounded.cut-field}`,
  no shadow — opened by a head of Label / hairline / line count. The `pre` scrolls
  horizontally inside itself at 0.8125rem/1.7 (0.75rem below 60rem) and never wraps. The
  gutter is sized `--gutter: <digits>ch` from the block's own line count so it cannot
  reflow mid-block: right-aligned tabular graphite numerals, `user-select: none`,
  `aria-hidden`, divided from the source by the same hairline as everything else.
- **Tables** are the Load Schedule's rules unchanged — collapsed, no zebra, no cell
  borders, one hairline under each row, a `{colors.rule-strong}` hairline under the
  tracked-caps header, top-aligned graphite cells — inside a horizontal scroller at a
  30rem minimum, so a wide table never makes the page scroll sideways. Cells break on
  words (`break-word`, not `anywhere`, which collapses a column of short identifiers to
  its narrowest breakable width) and inline code in a cell stays `nowrap`.
- **Lists:** the marker is drawn, never a glyph — a 0.5rem × 1px `{colors.member-line}`
  rule at the first line's baseline. An ordered list uses a tabular graphite counter.
  Depth indents 1.25rem per level.
- **Inline:** code is plate under a hairline; `strong` is weight 600 in carbon; a
  blockquote is a 1px `{colors.rule-strong}` left rule with graphite text; `em` is a
  dashed rule, per the No-Italic Rule.
- **Empty body:** a sentence in the caller's own words saying what having no body means
  for that kind of asset, never a blank pane.


### Named Rules
**The Real-Pixel Stroke Rule.** A drawing laid over the layout grid is written in grid
units with `preserveAspectRatio="none"`, which scales the two axes differently. Every
stroke in it therefore carries `vector-effect: non-scaling-stroke`, so a 1.25px cord is
1.25px and a `3 3` dash is `3 3` whether the plan is three ranks wide or twelve.

**The Knock-Out Rule.** Anything set over a drawn run carries that surface as its own
background — a seat's ring and its id mark are both filled `{colors.plate}` on the
sheet. A line running through a label is not a style in this system; it is what a
cancelled member is drawn as.

**The Two Renderings Rule.** A drawing that carries state owes a table that carries the
same state: same order, one shared selection, both reachable from the keyboard. The
drawing may drop names at a width where the table keeps them; it may never be the only
place a fact exists.

**The Weight-Is-Load Rule.** Where a drawn run carries load, the load is in the stroke
weight first and the colour second: 2.5px loaded, 1.25px held, 1px carrying nothing.
A reader with no colour still sees how much of the structure is carrying. Audit test:
turn the drawing greyscale — if you can no longer tell loaded from idle, the weight
was doing nothing.

**The One Unit Rule.** Members drawn beside each other are measured against one shared
unit, never each against itself. A length that means "proportion of its own row" is a
percentage wearing a drawing's clothes, and it makes three of seven and three of
twenty-seven draw opposite silhouettes for identical load.

**The No-Italic Rule.** Nothing in this system is italic. Emphasis is a dashed
`{colors.rule-strong}` underline — the same mark a slack reading carries — and `em` sets
`font-style: normal` to enforce it. Neither self-hosted face ships an italic, so a
browser asked for one would synthesise a slant nobody drew.

**The Code-Never-Wraps Rule.** The Wrap-Don't-Scroll Rule governs rails and labels, not
source. A code block keeps its own lines: it never wraps, it scrolls horizontally inside
its own plate, and it carries a hairline-divided number gutter sized to its longest line
number. Wrapped code lies about the line an operator is naming. A wide table is the same
case and scrolls inside its own region rather than making the page scroll sideways.

## Do's and Don'ts

### Do:
- **Do** reserve `{colors.red}` for load and failure. Location gets the carbon
  locator halo and `aria-current`; nothing else gets red.
- **Do** give every state a form change as well as a colour — dashed, gapped, capped,
  solid. Colour is never the sole carrier of a state.
- **Do** pin every value to a node with a leader line, at whatever scale the surface
  needs.
- **Do** set display, mark and in-sheet headline in Archivo with explicit
  `font-variation-settings`, and everything else in Chivo Mono.
- **Do** self-host any new face as subset woff2 in `ui/static/fonts/` with a
  `unicode-range`. A runtime font request breaks the fresh-machine install.
- **Do** keep `{colors.ash}` for graphics only, and keep body text at
  `{colors.ink}` or `{colors.ink-2}`. A slack *reading* is graphite carrying a dashed
  ash rule, never ash type.
- **Do** give a drawing that carries state a keyboard-navigable table beside it, in the
  same order and on the same selection.
- **Do** define every new colour in all three theme blocks — `:root`, the
  `prefers-color-scheme` block, and `:root[data-theme='dark']` — or the toggle
  breaks in one direction.
- **Do** preserve the last known values and mark them stale when a refresh fails, and
  render unknown as `—`.
- **Do** wrap a rail rather than scrolling it, and let structural lines overshoot
  their last node.
- **Do** draw any ordered chain as a chain: rings on a 1px carbon run that
  overshoots the last one, knocking out in the surface they sit on. A chain of steps
  is a member like any other, inside a drawing or outside one.
- **Do** cut only the interior-facing corner on a plate held flush to a viewport
  edge, and override the `clip-path` fallback in the same rule.
- **Do** draw load in stroke weight before colour — 2.5px loaded, 1.25px held, 1px
  carrying nothing — and measure members that sit beside each other against one
  shared unit.
- **Do** carry a repeated row's own control as a bare button inside its dimension
  string, at Label typography, red on hover.
- **Do** draw a declared claim in `{colors.seat}` and an observed fact in the member
  state vocabulary, and never let one become the other.
- **Do** drive a drawing, its annotation ladder and its members from one unitless
  `--scale` on their container, leaving the SVG its own coordinate system.
- **Do** toggle a scroll-edge fade from a measurement (`scrollWidth` against
  `clientWidth`, re-read on resize), never from an assumption.
- **Do** render a live region from first paint and collapse it with `:empty`.
- **Do** fire `take-up-load` only on a compared gain in load, keyed by the thing's own
  identity, so a poll or a re-order cannot make the page twitch.
- **Do** hold prose to the 68ch measure and let code blocks and tables run the full
  column, scrolling inside themselves.
- **Do** draw a list marker as a 1px `{colors.member-line}` rule and an ordered marker as
  a tabular counter.
- **Do** set a document heading in Chivo Mono. Archivo is the display, the mark and the
  in-sheet headline, and a document heading is none of the three.

### Don't:
- **Don't** add a shadow, gradient, glow, blur or backdrop filter. Depth is plate
  tone plus a hairline.
- **Don't** add a `border-radius` other than `50%`. Corners are square or chamfered
  top-right / bottom-left.
- **Don't** build a card. The category's card grid and glowing node cloud are the
  confirmed anti-reference, and `ui/schedule-view.html` is anti-reference for look
  and feel.
- **Don't** introduce a filled or "primary" button. The ghost button is the only
  button, because a filled accent button would spend red on something that is not
  load.
- **Don't** stack a tracked-caps label above a heading as a kicker. Labels ride a
  rule or a leader.
- **Don't** add a second authored motion. `take-up-load` is the one moment; new
  states set instantly, and the one moment plays only where load actually grew. A new
  drawing may restate that one moment on its own load axis (`stand-up`) with the timing,
  curve and overshoot unchanged — it may not invent a different one.
- **Don't** let a drawing scroll without earning it: keyboard reach along the axis, the
  same facts in text, and a fade proven by measurement, or it wraps.
- **Don't** repeat a bordered control down a list of rows. One row's own action is a
  bare button in its dimension string; the ghost button's edge stays rare enough to
  mean something.
- **Don't** draw the structure in `{colors.ash}` — ash on a member claims the whole
  diagram is slack.
- **Don't** extend the identity past the wordmark and the one drawn mark
  (`ui/static/favicon.svg`, and the same path inlined plateless in the shell). No
  second lockup, no red in it — the Load-Only Red Rule applies to the mark too — and
  no third element. The two copies of the path are kept in step by hand so the icon
  file stays self-contained.
- **Don't** ask an operator to type the identity of something that already exists.
  Plans, harnesses, models, roles, assets and versions are chosen from a
  daemon-enumerated list; free text is for briefs, prompts, nudges, answers, reasons
  and comments. Where no enumeration exists yet, the action is stated unavailable in
  the same voice as any other held rule — a text field is not the stand-in
  (`notes/ui/contract.md`).
- **Don't** blank a readout on error, and don't render unknown as `0` or an empty
  string.
- **Don't** dim a surface to make something modal. `::backdrop` stays transparent;
  the plate, the hairline seam and the focus trap carry the mode, and at a width
  where a seam cannot, the sheet takes the whole viewport.
- **Don't** set a paragraph in `{colors.red}`. Red marks the break in one lead
  sentence; the sentence that explains it is `{colors.ink-2}` prose.
- **Don't** set emphasis in italic, or ask for an italic anywhere. Emphasis is a dashed
  `{colors.rule-strong}` underline; neither self-hosted face has an italic to load.
- **Don't** wrap source to make it fit. Code keeps its own lines and scrolls inside its
  own plate, with a gutter wide enough for its longest line number.
- **Don't** use a glyph or icon-font icon. Every drawing in this build — the hanging
  cord, the broken line, the Contention Field and its cord key — is a drawn SVG of the
  structure itself.
