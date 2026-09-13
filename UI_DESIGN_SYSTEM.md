# VAYREN — PERMANENT GLOBAL UI DESIGN SYSTEM

**Status:** Authoritative. Single source of truth for ALL VayREN UI — current
and future, Qt retained shell and Rust+Slint target alike.
**Owns:** Visual language, tokens, components, navigation, responsive/DPI
behavior, states, tables, charts, numbers, accessibility, keyboard,
animation, reuse rules.
**Not owns:** Language ownership → `ARCHITECTURE_CONSTITUTION.md` (new native
UI is ALWAYS Rust+Slint; the Qt shell is retained only, converging to these
tokens until its migration slice arrives); module APIs/events →
`90_brain/`.
**When to read:** BEFORE touching any UI file, before adding any widget,
before inventing any style. No exceptions.
**Rule §0 — NO ONE-OFF UI:** a screen must never invent its own visual
language. Reuse first (§12). A genuinely new global pattern goes into THIS
document first, then into code. Design drift is a defect.

---

## 1. Design philosophy

Premium without decoration. Apple-level refinement (simplicity, spacing,
typography, hierarchy, restraint, consistency, clarity, interaction quality)
combined with institutional quant-terminal density. The result feels
**precise, calm, expensive, technical, professional, fast, trustworthy,
focused**.

Quality comes from typography, alignment, spacing, subtle borders, surface
hierarchy, controlled contrast, restrained accents, precise sizing — NEVER
from gradients, glow, shadows, giant rounded cards, illustrations, oversized
icons, gratuitous animation, or rainbow colors. Flat structured surfaces by
default; a card exists only when it improves information grouping.

Production UI must never look like debug tooling, raw widgets, or a
prototype — including internal analytics screens.

## 2. Information architecture

Every screen answers: *"What does a professional need to know first?"*
Weight is strictly tiered — never equal:

- **PRIMARY** — critical trading/research information. Strongest type,
  most space, first in reading order.
- **SECONDARY** — supporting context. Quiet type, compressed first when
  space shrinks (§8).
- **TERTIARY** — advanced configuration/details. Behind progressive
  disclosure (collapsed panels, tabs, drawers) — never in the primary
  workspace by default.

Decorative UI must never compete with data.

## 3. Canonical tokens (single values, both frameworks)

These values are canonical. Qt (`00_app/app/ui/lab_theme.py`) and Slint
(`rust/vayren-shell/ui/design.slint`, palette) implement THE SAME numbers —
a framework file that disagrees with this table is wrong.

### 3.1 Color

| Token | Value | Use |
|---|---|---|
| `BG0` | `#070B10` | App background, sticky bars |
| `BG1` | `#0B1017` | Tables, inputs, secondary surface |
| `BG2` | `#0F151D` | Alternate rows, pressed |
| `PANEL` | `#101720` | Cards, sections, dialogs |
| `PANEL2` | `#131B24` | Hover, menus, secondary panel |
| `PANEL3` | `#18212C` | Selection, tertiary surface |
| `BORDER` | `#202B36` | Borders, handles |
| `BORDER_SOFT` | `#1A232D` | Gridlines, hairlines |
| `ACCENT` | `#00C7B7` | Primary actions, active states, brand |
| `ACCENT_DIM` | `#00A99D` | Hover on accent, selections bg |
| `ACCENT_DEEP` | `#04211E` | Text on accent fills |
| `TEXT` | `#E6EDF3` | Primary text |
| `TEXT2` | `#8B98A7` | Secondary text |
| `MUTED` | `#596675` | Labels, captions, placeholders |
| `POS` | `#21C58B` | Profits, supported, healthy |
| `POS_DIM` | `#16493A` | Positive badge backgrounds |
| `NEG` | `#F05A67` | Losses, errors, blocked |
| `NEG_DIM` | `#4A2027` | Negative badge backgrounds |
| `WARN` | `#DDAA45` | Warnings, stale, pending |
| `WARN_DIM` | `#4A3C18` | Warning badge backgrounds |

Derived-only colors (pressed accent `#008F84`, light-on-dark text
`#1A0508` on NEG fills): derive at the use site from the row above, never
add new hex literals. **No other hex literal may appear in UI code.**
Quantity check: `grep -rE "#[0-9A-Fa-f]{6}" <ui-dir>` must return hits
only inside the token files.

### 3.2 Typography

One hierarchy, everywhere. Family: UI `Segoe UI` (Slint: system default
sans — never bundle a font for chrome); code/numbers `JetBrains Mono,
Consolas` where tabular figures matter.

| Token | px | Use |
|---|---|---|
| `FS_DISPLAY` | 24 | Screen identity / strategy name |
| `FS_HERO` | 26 | Single primary result (e.g. Net P&L) |
| `FS_TITLE` | 18 | Section titles |
| `FS_METRIC` | 19 | Level-2 metric values |
| `FS_METRIC_SM` | 16 | Level-3 metric values (decision numbers never below 16) |
| `FS_BODY` | 14 | Body / control text |
| `FS_TABLE` | 13 | Table cells (floor for table content) |
| `FS_SMALL` | 12 | Secondary text |
| `FS_LABEL` | 11 | Micro labels (ABSOLUTE FLOOR — nothing renders below 11) |

Labels are uppercase, weight 600–700, letter-spacing 0.4–1.0px. Metric
values are always materially larger than their labels (size + weight +
color, never shrinking). Financial numbers use tabular figures;
large values may step up one tier — never an ad-hoc size.

### 3.3 Spacing / geometry

Single scale (px): `SP_XS 2 / SP_SM 4 / SP_MD 8 / SP_LG 12 / SP_XL 16 /
SP_XXL 24`. Every margin, padding, gap, section gap, table padding, toolbar
gap derives from this scale. Fixed chrome heights: `H_TOPBAR 42 /
H_CONTEXT 34 / H_TABS 34 / H_STRIP 36`. Radius: `RADIUS 4 / RADIUS_SM 3`.
Table row height `ROW_HEIGHT 22` (ranking viewports may pin 26 — see
§8.4). Icons: rail 30, inline 16/22.

### 3.4 Surfaces

- App bg `BG0`; content surfaces `BG1`; grouped content `PANEL`;
  hover `PANEL2`; selection `PANEL3`.
- Borders: 1px `BORDER` (hairlines `BORDER_SOFT`). Splitter handles 1px,
  hover `ACCENT_DIM`.
- **Blank space must be intentional** (§10): insufficient content →
  useful summary, contextual info, secondary analytics, or a proper empty
  state — never an unexplained void, never filler widgets.

## 4. Layout system

- Native layouts only (Qt layouts / Slint layouts). **No fixed-pixel
  coordinates for major architecture.** Size policies + stretch factors +
  min/max constraints own the geometry.
- Shell order (both frameworks): **nav rail → context → content**.
  Qt chart shell: tools rail (40) | panel | chart container. Slint shell:
  104px nav rail | content column | 24px status bar.
- Content column: page header → context bar → scrollable page. Pages that
  can overflow use ONE page-level scroll container — children never squeeze
  each other into overlap (see incident §13.1).
- Centered max-width (~1280) scroll content for control-center pages
  (brokers, settings); full-bleed for chart/lab workstations.
- Dialogs: centered, `PANEL` surface, single primary action, Esc cancels,
  focus starts on the primary control.

## 5. Responsive desktop system

Never designed around one resolution. Must adapt to 1280×720 → ultrawide,
window restore/resize, and Windows DPI 100/125/150/175%+.

Shrink priority (in order): ① cut whitespace ② compress secondary info
③ reflow KPI grids (4→2 cols under ~620px content) ④ collapse
advanced/secondary panels ⑤ intelligent page scroll. NEVER overlap widgets,
clip text/buttons, hide critical info, or produce unusable controls.
Growth priority: larger charts, richer tables, better separation — never
uniform stretching.

DPI: logical/device-independent layout only. Never hardcode dimensions from
the developer's monitor; never assume physical == logical pixels. All
controls scale with the OS. Qt: layout-driven geometry + `WA_StyledBackground`
where QSS backgrounds are required; Slint: `px` units are logical by design.

Resize stability: maximum → medium → small → minimum-usable window must
transition gracefully. No layout explosions, no disappearing widgets, no
giant blank regions.

**Breakpoint verification matrix** (required after significant UI change):
1920×1080, 1600×900, 1440×900, 1366×768, 1280×720; maximized, restored,
manually resized. DPI spot-checks at 100/125/150% where the OS allows.

## 6. Components (§12 global set — reuse, never duplicate)

Before creating any component: ① search this system ② reuse ③ extend
④ create only with genuine reason — then document it HERE first.

Global set: Button (primary / secondary-subordinate / quiet / danger-stop /
tool-icon), Input, Search, Dropdown, Date picker, Number input, Toggle,
Tabs (workspace tab-bar + analytical strip), Sidebar/nav rail, Toolbar,
KPI/Metric tile, Table, Chart container, Status indicator, Badge, Tooltip,
Dialog, Drawer, Notification, Banner (quiet, left-edge semantic — never a
full-bleed block), Empty state, Loading state, Error state.

Rules:

- **ONE dominant action per view.** The primary run/confirm control uses
  the filled accent style; subordinate mirrors (e.g. top-bar RUN) use the
  outlined variant — never two equally dominant buttons.
- Destructive/arm states (STOP) are semantic NEG fills, never a second
  run affordance.
- Tabs: underline-accent style, checked = `TEXT` + 2px accent bar.
- Inputs: `BG1`, 1px `BORDER`, 3px radius, focus border `ACCENT`,
  disabled = `MUTED`.
- Badges/pills: text + semantic color; color is never the ONLY signal
  (pair with text/shape).
- Tooltips carry the full value wherever elision is visual-only.

## 7. States

Every important component handles: default, hover, focus, active,
selected, disabled, loading, success, warning, error, empty, stale,
running. Workflow surfaces additionally: READY, RUNNING, FINALIZING,
COMPLETED, OUTDATED, FAILED, NO DATA, INVALID CONFIGURATION,
PARTIAL RESULT. States are visually clear but restrained — not every state
is a banner. Focus is always visible (keyboard §11).

**Result ownership (trading/backtest):** displayed results must always map
to the current strategy + side/mode + universe + symbols + timeframe +
dates + capital + costs + parameters. Config change → previous results are
explicitly marked OUTDATED (quiet banner + one-click re-run); stale results
never masquerade as current. Strategy switch / empty context resets owners
— no stale leakage.

## 8. Tables (first-class)

Consistent row height (`22`, ranking 26 where viewport contracts pin it);
headers uppercase 11px `TEXT2`, 1px bottom border; numbers right-aligned
with tabular figures; `TEXT` cells on `BG1` with `BG2` alternation;
selection `PANEL3` (never low-contrast combos); hover `PANEL2`; row
selection + no-edit + right-elision with full-value tooltips; content-sized
columns with sane minimums, last column stretches; sort/filter/search where
the dataset warrants; virtualization/caps for large datasets (never 100k+
widget items on the UI thread); signature-guarded refills (no per-tick
flicker). No oversized rows to fill space.

## 9. Charts

Analytical clarity first: clear title, meaningful units, useful axes,
contextual metrics, consistent controls and interaction. Scale with space;
no dead chart deserts, no decoration. Static layers are cached (grid +
series); live overlays (header, crosshair) paint per frame. Headers show
identity + OHLC from the latest bar, independent of crosshair. Charts exist
to support decisions.

## 10. Financial number system

One formatting convention everywhere (see `ui_kit.text/money` + Slint
helpers): currency 2dp with sign for deltas; percentages 2dp; ratios 2dp;
P&L signed + semantic color; drawdown as %; volume with thousands
separators; prices per-venue precision; timestamps timeframe-aware
(intraday `Tue 04 Aug '26 13:00`, daily date-only, weekly `Week N YYYY`,
monthly `Mon YYYY`); large numbers compact only with full-value tooltip;
small numbers never rounded to zero without indication. Missing data
renders `N/A` / `—` / `Unavailable` — never zero-filled, never invented.
Positive/negative/neutral semantics are identical on every screen.

## 11. Navigation, keyboard, accessibility

One navigation grammar: permanent rail (chart shell: 40px tool rail;
Slint: 104px rail; workspaces: tab bar), clear active state (accent),
logical order, Alt+1..7 shortcuts in the Slint shell, full keyboard
reachability in Qt (visible focus, sane tab order, Esc closes
menus/dialogs, `Alt+R` resets chart view). Readable contrast on every
text/background pair; text scales with OS settings; hit areas ≥ 22px for
row actions, ≥ 28px for rail buttons; every icon button has text tooltip +
accessible label. Density and accessibility coexist — if they conflict,
density yields on the contested control.

## 12. Motion & performance

Motion is subtle, immediate, and cheap: hover/press feedback, no layout
thrashing, no animation on the critical path. UI work never blocks
calculation threads; large tables/charts stay responsive (§8 caps, chart
LOD). No unnecessary repaints, widget churn, or expensive per-frame layout.

## 13. Backend safety & verification record

UI work must NEVER change engines, calculations, state management,
persistence, APIs, or workflows. Presentation-only unless the task says
otherwise. Verify on the RENDERED app, not source: clipping, overlap,
alignment, spacing, type, scaling, scroll, state transitions, table/chart
behavior — across the §5 matrix.

### 13.1 Incidents (do not regress)

- **Lab vertical-split squeeze (2026-09-12):** a vertical splitter crammed
  ~1000px+ of content minimums into 644–824px viewports → overlap +
  clipping. Fix pattern: page-level `QScrollArea`, content flows and
  scrolls instead of squeezing. Any new page with stacked sections follows
  this pattern.
- **Stale-result masquerade (2026-09-12):** unguarded run entries emitted
  runs with no open strategy. Fix pattern: context guards + ownership
  fingerprints (§7). Every results surface keeps them.
- **QSS palette resolution:** QSS `palette()` roles resolve from the
  APPLICATION palette — set it once at startup; plain QWidgets need
  `WA_StyledBackground` for painted backgrounds.
- **Trailing stretch:** a `QVBoxLayout` without a trailing stretch spreads
  slack between items — end every vertical stack with `addStretch(1)`.

## 14. Consistency audit (definition of done)

For every touched screen: does it look like VAYREN? Same type? Same
spacing? Same components? Same navigation? Same states? Adapts correctly?
Premium yet dense? No decoration? The rendered app at two or more §5 sizes
is the authority — not the diff.

## 15. Future AI agent rule (PERMANENT)

Any future AI agent working on VAYREN UI MUST: ① read THIS file before
modifying UI ② follow it ③ search for reusable components first
④ never introduce one-off styles ⑤ preserve responsive behavior
⑥ preserve DPI independence ⑦ preserve accessibility ⑧ preserve existing
state/ownership logic ⑨ run the actual app after meaningful UI change
⑩ verify rendered UI (≥2 sizes) ⑪ document any genuinely new global
pattern HERE. No screen is ever an isolated design project. New native UI
goes to Rust+Slint per the Constitution; retained-Qt edits converge to
these tokens within already-retained files (new Qt UI files need a
per-file retention entry — validator hard-fails otherwise).
