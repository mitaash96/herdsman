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
product evidence. PRODUCT.md establishes no logo, wordmark or identity constraint,
so the "HERDSMAN" mark is set in type and nothing more.

**Key Characteristics:**
- Carbon black on concrete pale; exactly one accent, reserved for load
- Archivo condensed uppercase against Chivo Mono for everything else — two voices, no third
- Hairline rules and plate tone instead of shadow; flat at every level
- Chamfered plates on two opposite corners; no radius anywhere in the system
- Values pinned to nodes on leader lines, as on a drawing
- Ash always means slack, and slack always also changes form
- One authored motion: a member taking up load

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
and never sets a line of text, because it does not clear text contrast.

**The Two-Tone Surface Rule.** There are exactly two surfaces: ground and plate. A
third tone would start a card system, which this world refuses.

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
  reads as an identifier rather than a small display heading.
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

**Responsive.** One breakpoint at 60rem. Below it the shell collapses to a single
column and the strut rotates from a vertical member into a horizontal rail: the
absolute member line is dropped and each node draws its own 1px segment across its
own width at `top: 0.55rem`, so the segments join into a continuous run even when the
rail wraps to a second line. Nodes become a two-row grid (ring above name), leaders
and purpose lines are dropped — three wrapped rows of purpose text cost more than
they say — and sheet padding drops to 1.75rem 1rem 3rem. Title block cells tighten to
6.5rem / 0.5rem 0.875rem.

### Named Rules
**The Leader-Line Rule.** A value is never a bare cell. It is pinned to a node by a
leader: a ring, a hairline out to the label, and the value beneath. This is the
system's core motif and it scales from a 4px title-block dot to the full strut.

**The Wrap-Don't-Scroll Rule.** The navigation rail wraps; it never becomes a
horizontal scroller and nothing in it is clipped. No surface in this UI introduces a
hidden horizontal scroll region.

**The Member-Runs-Through Rule.** A structural line always overshoots its last node —
down the full column height on desktop, edge to edge on each node on compact. A line
that stops exactly at content is a list rule, not a member.

## Elevation & Depth

**There are no shadows in this system.** Not one `box-shadow` is used as a shadow,
no gradient, no blur, no backdrop filter. Depth is entirely tonal and linear: the
plate tone lifted off the ground, two hairline weights, and the grain on the ground.
Anything that needs to sit "above" something else does so by being on plate, bordered
with a hairline, and chamfered.

The single `box-shadow` in the build is not elevation — it is a **locator halo**:
`0 0 0 3px var(--ground), 0 0 0 4px var(--member-line)` on the current view's node
ring, a solid concentric carbon ring punched out of the ground with a gap. It marks
where you are standing. It is carbon, never red, because red means load.

Overlap between plates never happens: the layout is a grid, and there is nothing to
stack.

### Named Rules
**The No-Shadow Rule.** A shadow anywhere in this UI is a defect. If an element needs
to separate from its ground, give it plate tone plus a hairline. If it needs to
separate from another plate, give it a 1px gap filled with the hairline colour — that
is how the readout grid is built.

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

**The Circle-Only Radius Rule.** `border-radius: 50%` is legal — it draws a node.
Any other radius value is not.

## Components

The build ships one populated view (Run) and one unavailable presentation used by the
other three. Only what exists is documented here.

### Motion

There is exactly one authored motion in the entire system, and it is a component
behaviour rather than a token group: `take-up-load` — `scaleX` from 0.94 through a
1.2% overshoot at 62% to 1, over 320ms on `cubic-bezier(0.16, 1, 0.3, 1)`,
transform-origin left. It is the moment a member takes up load: it settles into the
load rather than fading in. It has two uses — once on the loaded node's leader when a
view becomes current, and looping at 1.1s as the loading bar in AsyncField.
**Everything else in the system simply sets.** No hover transition, no page
transition, no fade. A global `prefers-reduced-motion: reduce` block collapses all
animation and transition durations to 0.001ms.

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

- **Style:** `repeat(auto-fit, minmax(11rem, 1fr))` cells on plate, separated by a
  1px `gap` filled with the hairline colour and a matching 1px border — the divider
  is the gap, not a border per cell. Chamfered as one plate. A `.wide` cell spans the
  full row.
- **Content:** `dt` is a Label, `dd` is a Value in carbon (or graphite for a
  sentence).

### Buttons

- **Shape:** chamfered (`{rounded.cut-control}`), transparent, 1px `rule-strong`
  border — the harder hairline reserved for operable edges.
- **Style:** 0.75rem tracked caps in carbon. There is one button variant in this
  system: a ghost. No filled or "primary" button exists, because a filled button
  would have to be red, and red means load.
- **Hover:** border and text both go red. No fill, no lift.
- **Disabled:** text drops to graphite, border softens to the plain hairline,
  `cursor: not-allowed`.

### Inputs

- **Style:** plate background, 1px `rule-strong` border, chamfered
  (`{rounded.cut-field}`), `font: inherit` so the field is set in Chivo Mono at body
  size.
- **Focus:** border turns red, plus the global 2px red `:focus-visible` outline at
  2px offset. `caret-color` is red.
- **Required:** stated in a 0.625rem tracked-caps note beneath the row, wired with
  `aria-describedby`. There is no red asterisk anywhere.

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
  `{colors.ink}` or `{colors.ink-2}`.
- **Do** define every new colour in all three theme blocks — `:root`, the
  `prefers-color-scheme` block, and `:root[data-theme='dark']` — or the toggle
  breaks in one direction.
- **Do** preserve the last known values and mark them stale when a refresh fails, and
  render unknown as `—`.
- **Do** wrap a rail rather than scrolling it, and let structural lines overshoot
  their last node.

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
  states set instantly.
- **Don't** draw the structure in `{colors.ash}` — ash on a member claims the whole
  diagram is slack.
- **Don't** invent a logo, wordmark or identity mark. PRODUCT.md establishes none;
  "HERDSMAN" is set in Archivo and that is the whole mark.
- **Don't** blank a readout on error, and don't render unknown as `0` or an empty
  string.
- **Don't** use a glyph or icon-font icon. The two illustrations in this build (the
  hanging cord, the broken line) are drawn SVG diagrams of the structure itself.
