# Ayre Scanner — Design & Implementation Specification
### The authoritative brief for the next version of the product

---

## 0. How to Use This Document

This is an implementation brief, not a mood board. It's written against the existing Flutter codebase (`ayre-scanner-app`, `lib/`) and names existing files and components (`AppColorTokens`, `AppMotion`, `PremiumCard`, `_FloatingPillNav`, `home_shell.dart`, etc.) deliberately, because the underlying architecture — token system, state management, navigation mechanism, information architecture — is a genuinely solid foundation and is being **extended**, not rewritten.

**Treat the current UI as a skeleton, not a visual constraint.** The existing screens, components, and layouts tell you *what the product does and how it's organized* — keep that. They should not be treated as a starting aesthetic to preserve, soften, or incrementally polish. Every visual surface named in this document (palette, typography, ornament, card material, the navigation bar, the sentiment gauge, the sparkline) is being substantially rebuilt, not reskinned at the margins. If an instruction below reads as a bigger change than you expected for a given component, implement the bigger change — that's the intent, not an overstatement.

No exact hex values, pixel measurements, or spacing tokens are prescribed. What's specified is **design intent**: hierarchy, roles, relationships, and behavior. Use sensible concrete values consistent with the direction described; where a decision is genuinely open, pick the option that best serves the product and move on. No onboarding flow is proposed — out of scope.

---

## 1. Executive Summary & Direction

Ayre Scanner's engineering foundation stays exactly as it is: a real token system (`AppColorTokens`, `AppSpacing`, `AppRadius`, `AppMotion`, `AppTypo`), an 8pt grid, a documented no-bounce motion policy, `IndexedStack`-based tab state preservation, and calm, well-reasoned empty/error states. What renders on top of that foundation — the palette, the typography, the ornament, the shape language, the way data is drawn, the material and finish of every surface including the navigation bar — is a genuine visual reinvention. Today's surface reads as a well-executed but interchangeable member of the fintech-dashboard category: a trust-blue-or-teal hue, soft glow-blob ornaments, glassy translucent cards, one default sans-serif doing every job, and a navigation bar that looks materially close to default system tab bars. None of that carries forward as-is.

**The organizing idea: Ayre Scanner is an instrument, not a dashboard.** The product's whole job is turning market noise into a small number of readable signals — the same job a precision instrument does with pressure, drift, or bearing. Every decision below is a specific, load-bearing expression of that one idea:

- **A palette built around warm brass and warm ink**, replacing the blue-or-teal-on-cool-neutral formula nearly every competing product uses. Brass/gold is the sole, always-on brand hue, on a base that is warm at every step in both themes — genuinely warm parchment in light mode, warm ink/graphite (never blue-tinted black) in dark mode.
- **Data drawn like instrument readings, not chart-library defaults.** The sentiment gauge becomes a real needle-and-dial pressure gauge with engraved tick marks; the sparkline becomes a fine hairline trace, never a filled-gradient area chart.
- **An engraved, hairline ornament vocabulary — tick marks, contour lines, a bearing/compass mark — fully replacing every soft glow-blob and gradient-sweep in the app today**, including the splash screen's rotating gradient ring.
- **A three-role typographic system**: a characterful display serif for a small, named set of hero/brand moments; a monospaced, tabular instrument-readout face for every live data figure; and Inter (or equivalent) for body copy and UI chrome. See §4.2 for the exact, non-negotiable rules governing which role applies where.
- **A redesigned navigation pill** — not the current nav bar, but still a pill: the floating, edge-to-edge, rounded silhouette and its expand-on-select mechanic are genuinely good and stay, while its material, active-state signature, and finish are completely rebuilt around the instrument identity (§5). This is a full re-skin of the app's most-touched component, not a color or icon swap.
- **Motion that stays exactly as disciplined as today — no bounce, no overshoot, ever — but is upgraded to a genuinely richer, physically-based system**: critically-damped springs for gesture-driven interactions, a needle-sweep for the gauge, a mechanical digit-roll for changing numbers, a sliding needle-mark for the nav's active state, and one reusable "live data" pulse signature.

None of this is decoration for its own sake — every choice is traceable to legibility, brand differentiation, or reinforcing what the product does. The instrument idea is a **structuring principle for shape, mark-making, and typography** — flat, precise, high-contrast, digitally native — never a literal skeuomorphic reproduction (no chrome bezels, faux-metal textures, drop-shadowed "physical" dials, or cluttered multi-dial panels). Where a recommendation risks tipping toward costume rather than idea, the flatter, more restrained reading is correct.

**What is not changing:** the information architecture, the screen inventory, the navigation *mechanism* (tab-based, `IndexedStack`, state preserved across tabs), the accessibility bar, and the underlying interaction discipline (press feedback, haptics, no-bounce motion) are already correct and carry forward. The reinvention is scoped to how the product looks, how its data is drawn, and what its surfaces are made of — not to what it does or how it's organized.

---

## 2. What to Keep Exactly As-Is

Engineering and interaction decisions independent of visual style. Nothing about the new identity requires touching these — call this out explicitly during implementation so nobody "improves" something that was already correct:

- **Token architecture as a mechanism.** Keep `AppColorTokens`, `AppSpacing`, `AppRadius`, `AppMotion`, `AppTypo` as the extension points — only most of their *values*, and a few of their described roles, change.
- **The no-bounce motion policy.** Correct today, stays correct — nothing in this document asks for bounce, overshoot, or elastic curves anywhere.
- **`PressableScale` press feedback** (0.97 scale, no overshoot) — the right interaction weight; extend it to every tappable surface that currently lacks it.
- **The existing selective haptic feedback pattern** — extend it consistently rather than replacing it (§4.6).
- **`IndexedStack`-based tab state preservation** in `HomeShell` — any navigation rework must preserve this exactly, including for the new Profile tab.
- **The `AnimatedEntrance` staggered-entrance pattern** — keep the mechanism (restrained, single-play, index-delayed, doesn't replay on tab re-visit); only the surfaces it animates are visually rebuilt.
- **The calm, static Insights error-state behavior** — a failed fetch is low-stakes and shouldn't be dramatized. Extend this exact restraint to every other error state; only the icon/surface dressing changes.
- **The instinct toward bespoke, custom-drawn data visuals** over generic chart-library defaults — correct, and pushed further (§7.3), not reversed.
- **Semantic separation of gain/loss/brand/legacy tokens** — keep the underlying separation; finish retiring the legacy decorative palette (cream/ivory/peach/coral/mint/lavender/violet/sage/gold) as already planned.
- **The floating navigation pill's silhouette and core interaction mechanic** (§5) — kept and rebuilt in place, not replaced with a different shape.

**Visual specifics being fully rebuilt, not preserved or "turned up":** the teal/blue-forward brand hue; every soft glow/blob/gradient-sweep ornament, including Splash's rotating ring; the single-typeface (Inter-only) system; the glassmorphism-adjacent translucent card and nav material; the sentiment gauge's soft gradient-arc rendering; the sparkline's filled-gradient area-chart rendering; the nav bar's glassy fill and expanding-tint active state.

---

## 3. Key Problems This Document Fixes

| # | Problem | Where | Severity |
|---|---|---|---|
| 1 | Profile/Settings only reachable from Home tab's avatar; unreachable from Signals, Insights, Learn | `home_tab.dart`, `home_shell.dart` | High — IA break |
| 2 | Profile menu sheet contains non-functional tiles (Profile, Settings, Saved) and non-functional session rows (Help, Sign Out) | `profile_menu_sheet.dart` | High — dead affordances |
| 3 | Notification bell in Home header has no `onTap` | `home_tab.dart` | High — dead affordance |
| 4 | Placeholder/debug copy shipped in user-facing settings ("Keep auth disabled for now") | `profile_menu_sheet.dart` | High — unfinished feel |
| 5 | Splash screen uses `easeOutBack` (overshoot), contradicting the app-wide no-bounce rule | `splash_screen.dart` | Medium — inconsistency |
| 6 | The entire current visual language reads as an interchangeable fintech-dashboard: teal-on-neutral, glow-blob ornament, glassy cards and nav, single typeface | app-wide | High — brand differentiation |
| 7 | `textTertiary` fails WCAG AA contrast in both themes | `app_theme.dart` | Medium — accessibility |
| 8 | Home shows two simultaneous refresh indicators (native `RefreshIndicator` + custom `_RefreshRibbon`); other tabs show one | `home_tab.dart` vs. other tabs | Medium — inconsistent feedback |
| 9 | Learn's empty state is unstyled plain text while Signals/Insights have designed empty states | `learn_tab.dart` | Medium — inconsistency |
| 10 | Legacy decorative color tokens remain wired despite being marked for phase-out | `app_theme.dart` | Low–Medium — tech debt |
| 11 | No responsive/breakpoint handling despite shipping macOS/Windows/Linux/Web build targets | app-wide | Low–Medium — latent gap |
| 12 | Bottom nav's translucent, glassy material and generic expanding-fill active state read as a default system/competitor tab bar, independent of its pill shape | `home_shell.dart` | Medium — brand risk |
| 13 | Arbitrary, non-semantic use of accent tokens to differentiate cards | `home_tab.dart`, `insights_tab.dart` | Low — clarity |

---

## 4. Design System

### 4.1 Color — Palette, Roles, Usage

**Direction.** A disciplined, high-contrast neutral base plus one confident, narrowly-used accent is the right mechanism for a financial product — but the specific hue nearly every competitor reaches for (blue or teal "trust," on a cool near-black) is a category default, not a differentiator. Result: **warm brass/gold as the sole primary brand hue**, on a base that is **warm at every step, in both themes** — genuinely warm parchment in light mode, warm ink/graphite (never blue-tinted black) in dark mode.

| Role | Token | Purpose | Direction |
|---|---|---|---|
| Background | `background` | App canvas | Warm parchment/paper (light) · warm ink-graphite, never blue-black (dark) |
| Elevation scale | `surface` / `surfaceAlt` / `surfaceRaised` | Card, grouped-section, sheet/overlay tiers | Three to four warm-neutral lightness steps per theme; elevation reads through lightness and tint, never shadow, especially in dark mode |
| Text hierarchy | `textPrimary` / `textSecondary` / `textTertiary` | Body, secondary, caption/eyebrow text | Warm-toned ink / warm off-white — never pure black/white. `textTertiary` must clear 4.5:1 contrast (AA, normal text) against its typical surface in both themes — the current value does not, and must be replaced (§9) |
| Primary / structural | `primary` | Nav needle-mark, primary buttons, section accents, hero surfaces, the gauge needle, the sparkline stroke, wayfinding marks | Warm brass/gold — confident, never neon or pastel. The one always-on brand hue, doing both "brand" and "the instrument's mechanism" work |
| Secondary / informational | `accentCool` | Informational badges, secondary/outline buttons, the neutral-trend chart line | A deep, cartographic slate-blue-gray — reads as "ink," never as a second brand color competing with brass |
| Signature accent | `accentWarm` ("ember") | The one deliberately vivid, narrowly-used hue; doubles as the warning/stale-data/attention semantic | A confident burnt-orange/vermillion — distinct from brass, distinct from `negative`, never mistaken for a gain/loss signal |
| New / fresh marker | `accentMint` | Small-area "new" dots/badges only, never a large surface | A hue outside the teal/green family entirely, so it never drifts back toward "a paler brand color" |
| Positive / negative | `positive` / `negative` | Gain/loss, success/destructive | Deliberate, ink-toned verdigris-green and vermillion-rust-red — not stock-app defaults — always paired with a `+`/`–` sign and directional glyph, never color alone |

**Ember usage rule (the single most important rule in this section).** `accentWarm`/ember is used **only** for: (1) the momentum-score ring/indicator on Home's hero card when a reading is genuinely notable, (2) a "featured"/"hot signal" badge on at most one or two list cards at a time, (3) time-sensitive/attention-needed states (alert badges, stale-data indicators, destructive-confirmation weight), and (4) one clearly load-bearing CTA per screen with a single primary action. Never a decorative fill, a rotated "third card color," or a gradient base.

**Light and dark as intentional counterparts, not an inversion.** Tune each theme separately against its own background using the same semantic roles and the same underlying hue identity — not the same numeric values. Test: does each theme, viewed alone, look designed by someone looking only at that background — not whether the two match each other numerically.

**Chart and data-visualization colors:**
- Gain/loss lines and fills use `positive`/`negative` exclusively — never repurposed for anything that isn't a literal gain/loss signal.
- A flat/neutral trend renders in `accentCool` — never plain gray, never a dimmed green/red.
- The **default single-index sparkline stroke is `primary` (brass)**, not a gain/loss color, even when the underlying trend is up or down — the sparkline is the instrument's trace first and a directional signal second; direction is carried redundantly by the accompanying `+`/`–` figure and glyph. A signal card's headline delta figure and its arrow glyph still use `positive`/`negative` — this rule is specifically about the line stroke.
- Any multi-series comparison chart (e.g., NIFTY vs. SENSEX) distinguishes series using `primary` / `accentCool` / `accentWarm` — deliberately never red/green — so "which line is which index" and "is this index up or down" stay two separate visual questions.
- Redundant encoding is mandatory everywhere (§9): every gain/loss figure carries a sign in the number and a directional glyph; color is always a confirming second channel, never the only one.

### 4.2 Typography — Three Roles, Applied Without Exception

The current single-typeface system (Inter throughout, including headings and live numerals) is replaced by three roles, each with an exclusive job. **These rules are strict — a screen may not introduce a fourth typographic treatment for numerals, and may not fall back to a role's typeface outside its defined job.**

1. **Instrument-readout monospace — mandatory for every live market figure, without exception.** A highly legible monospaced, tabular-figure face (e.g. **IBM Plex Mono** or **JetBrains Mono**) renders: every stock/index price, every absolute and percentage change, every volume/traded-value figure, the sentiment gauge's numeric reading, and any timestamp tied to live data (chart axis labels, "as of"/"updated Xm ago" freshness markers). This applies identically to every row in the new Top Gainers, Top Losers, and Most Active Equities sections (§8) — no numeral in those sections ever renders in Inter or the display serif. Fixed-width digits also mean a live-updating number doesn't reflow its neighbors on refresh.
2. **Display serif — reserved for exactly two hero moments, nowhere else.** A characterful, slightly high-contrast, engraved-feeling serif (directional references: **Fraunces** at a sturdier setting, **Canela**, or a technical serif like **Spectral**/**Guyot**) is used **only** for the momentum score on Home's hero card and the splash screen's wordmark. It also carries section eyebrows, screen titles, and headings as a typographic voice — but **never a numeral outside the two named hero moments.** A section total, a card's headline figure, or any other "big number" elsewhere in the app uses the instrument monospace like every other live figure, not the serif.
3. **Inter (or equivalent grotesk) — body copy and UI chrome.** Card descriptions, list-row labels, navigation labels, button text, and non-numeric UI chrome. Reserve all-caps eyebrow/micro-labels for true categorical labels ("NIFTY 50," "SUBJECTS") in Inter — never for a live figure.

**Quick reference — what renders in which role:**

| Content | Role |
|---|---|
| Stock/index price, change, % change, volume | Instrument monospace |
| Sentiment gauge numeric reading | Instrument monospace |
| Chart timestamps, "as of"/"updated" freshness figures | Instrument monospace |
| Home hero momentum score | Display serif (the one numeral exception) |
| Splash wordmark | Display serif |
| Screen titles, section eyebrows, headings | Display serif |
| Symbol, company name, card copy, labels, buttons | Inter |

Audit line-height on multi-line body copy at larger accessibility text sizes to confirm no clipping.

### 4.3 Shape Language — Rectilinear and Chamfered, With One Sanctioned Exception

Keep the 8pt spacing grid unchanged. Change the shape system's default character:

- **Reduce the default corner radius** across cards, sheets, and containers from today's fully-rounded treatment toward a smaller, more consistent radius that reads as machined rather than bubbly — roughly half the current default is a reasonable starting point.
- **Introduce a chamfer (a single flat cut across one or two corners) as the shape system's second controlled "voice,"** used for instrument-coded elements: section-header accent marks and the sentiment gauge's dial housing. Cards and general containers keep the smaller rounded-corner treatment as the first voice; a screen never uses more than these two voices at once.
- **The full-pill/stadium shape is retired as the default for cards, sheets, badges beyond small chips, and containers generally.** It remains appropriate for genuinely small, discrete content (a status chip, a small badge).
- **The one sanctioned exception is the bottom navigation bar**, which keeps its floating pill/capsule silhouette by design (§5) — a single, deliberate reserved use of the shape, not a sign that "pill everything" survives elsewhere. The nav differentiates itself from generic pill navs through material and active-state signature, not through abandoning a genuinely good interaction shape.

### 4.4 Motion System

The substance of the existing motion policy is correct and stays exactly as strict:

- **Keep the no-bounce policy exactly as strict as today.** Nothing below asks for bounce, overshoot, or elastic curves anywhere.
- **Add physically-based, critically-damped spring infrastructure** (damping ratio ≈ 1.0 — resolves directly to target, zero overshoot) alongside the existing duration/curve tiers, for interruptible, gesture-driven interactions: sheet drags, fling-scroll settling, chart/gauge redraws mid-animation, the nav's needle-mark sliding between tabs. A critically-damped spring settles exactly like the existing `easeOutCubic` curves — the difference shows up when an animation is interrupted mid-flight and needs to re-target smoothly instead of resetting, which is the actual mechanism behind motion "feeling alive." Simple tap-triggered fades/slides can stay on the existing system.
- **Retain the existing duration tiers** (`instant`/`fast`/`medium`/`slow`); **add one new tier, `AppMotion.choreographed`**, for large multi-element sequenced transitions, sized so a 3–5-element staggered sequence completes within roughly 500–650ms total.
- **Add one named, reusable motion signature — a "live data" pulse** (a slow, subtle single sweep or band across a surface, echoing the engraved ornament vocabulary in §4.5) used specifically and only to indicate freshly-landed live data. Play once on the triggering update, never on a loop — this is a status signal, not ambient decoration, and it collapses to a static state-color change under reduced motion.
- **The gauge needle sweeps** from its previous reading to its new one on data update, using the spring infrastructure above, rather than snapping or cross-fading.
- **The nav's active-state needle-mark slides** from the previous tab's position to the new one (§5.3) using the same spring infrastructure, rather than cross-fading or teleporting.
- **Numeric values roll** through their intermediate digits to a new value (a mechanical digit-roll, like an odometer settling) rather than cross-fading or swapping instantly — subtle, a settle rather than a flourish, skipped on first load.
- **Fix the splash screen's `easeOutBack` overshoot**, replacing it with the standard no-bounce curve (or its critically-damped spring equivalent) at the existing duration.
- **Document a reduced-motion path** covering every category above, including spring-based and digit-roll/needle-sweep/needle-mark treatments, which collapse to an instant value or position swap.

### 4.5 Ornament & Surface Vocabulary

1. **Every soft glow/blob/gradient-sweep ornament is retired outright — not reduced, replaced.** This includes the translucent circle blob behind Home, Signals, Learn, and Login's hero cards, and Splash's rotating gradient ring. Nothing carries forward in any form, including a "toned down" version.
2. **The replacement is a small, specific engraved line-work vocabulary**, flat and hairline, never soft or blurred:
   - **Tick marks** — short parallel hairlines evoking a dial's calibration ticks, used as a restrained edge treatment on hero cards, section dividers, and the nav bar's edge (§5).
   - **A bearing/compass mark** — a single small circular mark with one or two fine radiating lines, used as Ayre's brand-mark equivalent to the old radar-sweep motif. Where movement is warranted (Splash), express it as one slow-rotating needle — critically damped, no overshoot — rather than a sweeping gradient wash.
   - **Contour lines** — thin, gently curved parallel hairlines (isobar/depth-contour in spirit), used sparingly as background texture on at most one hero surface per screen.
   - Used **at most once per screen**, on the single most prominent hero surface: **Home's hero keeps an ornament**; **Signals and Learn carry no ornament**, relying on the flat/bordered surface treatment below; **Login and Splash share the bearing/compass motif**, since both are one-time, sequential screens.
3. **Card and surface separation follows a strict restraint hierarchy: whitespace first, then a background-tint/lightness shift, then a hairline border only if the first two don't read clearly** — never a soft drop-shadow-plus-blur "glass" treatment as the default. `PremiumCard`'s current frosted/translucent material is retired as the default finish; a flat, tinted surface with an optional hairline edge is the new default. Materials stay matte and opaque — no true glassmorphism (specular highlights, heavy blur) anywhere, including sheets and the nav bar.
4. **Gradients are used only where they represent actual data or a genuine one-time brand moment** — shading a dial face for depth, filling a data-driven indicator — never as decorative atmosphere behind a hero.
5. **Every interactive-looking element must be interactive**, or visually demoted so it doesn't imply an action.
6. **No placeholder/debug copy ships to a user-facing surface**, ever.
7. **Copy audit.** Pass every user-facing string in Settings, Profile, and empty/error states against the overused-word list (*unlock, empower, seamless, leverage, streamline, robust, cutting-edge, elevate, harness, delve*, and similar) — if more than one appears in a short passage, rewrite toward something concrete and specific to the product (e.g., "Price Alerts — notify me when a saved signal crosses my threshold").
8. **The sentiment gauge and sparkline are redrawn in the instrument style, not just recolored** — see §7.3.

### 4.6 Haptics & Interaction Feedback

Extend the app's existing, already-correct selective haptic pattern consistently rather than replacing it. Haptic weight should match the weight of the action:

- **Light selection haptic** — tab switches on the nav (including the new Profile tab), any single-select toggle or filter chip.
- **Confirmation-weight haptic** — completing a toggle change in Settings, saving an Edit Profile change, a pull-to-refresh that successfully lands new data.
- **Stronger, warning-weight haptic** — opening the sign-out confirmation sheet, and confirming a destructive action (Sign Out itself).
- **No haptic** — passive state changes the user didn't directly trigger (a background price update, a chart redraw on a tab the user isn't viewing), and every visual-only press-feedback interaction (`PressableScale`) that doesn't represent a discrete completed action.
- Every primary action across Profile, Settings, and the market-data sections should have haptic feedback audited against this list before ship — today's coverage is real but partial, and gaps read as inconsistency once the rest of the interaction system is this considered.

### 4.7 Avoiding a Generic / AI-Generated Result

A compact, checkable list to hold every screen against before it ships:

- No soft glow/blob/gradient-sweep ornament anywhere, under any name (§4.5 #1).
- No card, sheet, or badge defaults to a flat border + soft drop-shadow as its only separation method — tint and hairline come first (§4.5 #3).
- No default reach for glassmorphism (blur, specular highlight, frosted translucency) on any surface, including the nav and modal sheets.
- No bounce, spring-overshoot, or elastic easing anywhere, on any interaction.
- No numeral renders outside the three-role system in §4.2 — check every screen against the quick-reference table.
- No generic, high-energy copy — run every new string through the overused-word audit (§4.5 #7).
- No dead, non-functional, or merely decorative interactive-looking elements (§4.5 #5).
- No screen should be recognizable as "a fintech dashboard" if you removed the wordmark — if a screen would look at home in any other trading app with a palette swap, it hasn't actually adopted the instrument identity yet. Every technique used (a tint, a gradient, an animation) should be traceable to a specific reason tied to this product, not reached for by default.

---

## 5. Bottom Navigation — A Redesigned Pill, Not the Current One

### 5.1 What stays: the pill's shape and core mechanics

The nav bar keeps its **floating, edge-to-edge, rounded (pill/capsule) silhouette** and its **expand-on-select label reveal** (icon-only when inactive, icon+label when active, animated width change) — both are good, well-tested interaction decisions worth keeping. Also unchanged: full-width placement (not an inset capsule with gaps to the screen edges), touch target sizing (52px item height), and safe-area/bottom-padding handling — recompute segment widths for the new fifth tab, but don't touch that plumbing.

**This is explicitly not "keep the current nav bar."** The pill silhouette and interaction mechanic survive; everything about its material, finish, and active-state signature is rebuilt from scratch per §5.2.

### 5.2 What is fully rebuilt: material and active-state signature

- **Surface material:** retire the current ~90%-opacity translucent/glassy fill entirely. Replace it with a flat, opaque, matte instrument-panel surface (a `surfaceRaised`-tier fill from the elevation scale in §4.1) — no blur, no specular highlight, no soft drop shadow. This single change is what actually separates Ayre's nav from Apple's and competitors' glassy floating capsules; the pill silhouette can stay because the material it's made of no longer reads as the same thing.
- **Edge treatment:** a fine hairline top edge marked with short brass tick marks at each segment boundary — a ruler/calibration-strip detail drawn from the tick-mark ornament vocabulary (§4.5), giving the bar a measuring-instrument character even at a glance.
- **Active-state signature:** replace the current tinted-fill expanding segment with a **sliding needle-mark** — a short, fine `primary` (brass) index line that moves along the bar's edge to sit under the active segment, sliding smoothly from its previous position to its new one on tab change (§4.4) rather than cross-fading or teleporting. This is shown together with the existing icon+label expand mechanic, not instead of it — the label reveal confirms *what* is selected, the needle-mark confirms *where*, in the same idiom as the sentiment gauge's needle.
- **Profile tab icon:** the user's own avatar (initials-in-circle, matching the Profile screen's identity block) instead of a generic person glyph. The Profile tab receives the same active-state treatment as the other four (label reveal, needle-mark); it's a special case only in its icon source.
- **Add Profile as a fifth, true tab.** Final order: **Home · Signals · Insights · Learn · Profile.** Recompute segment proportions for five items and verify no label truncation at narrow mobile widths.
- **Iconography** stays on a rounded, flat, line-based set consistent with the rest of the app — avoid anything that reads as a system-default glyph set.

### 5.3 Interaction & motion detail for the bar

- **Tab select:** `fast` duration, standard no-overshoot curve — the underlying timing is already correct, keep it.
- **Needle-mark slide:** spring-driven (§4.4), sequenced to begin roughly 15–20ms after the label reveal starts — label first, needle-mark confirms, not simultaneous.
- **Haptic:** light selection haptic on every tab change, extended to the new Profile tab (§4.6).
- **Press feedback:** every segment wrapped in `PressableScale`, including a re-tap on an already-active tab.

### 5.4 Responsive behavior

- **Mobile (primary target):** as specified above.
- **Tablet/larger phones in landscape:** cap the bar's maximum width and center it rather than stretching five segments edge-to-edge across a much wider viewport.
- **Desktop/Web:** at wider window widths, transform into a floating **left-side vertical rail** — same material, tick-edge, and needle-mark logic, avatar-as-icon for Profile — rather than stretching a mobile bottom bar across a desktop window. Items stack vertically, the needle-mark moves along the rail's left edge, labels reveal to the right of the icon on selection.

---

## 6. Information Architecture: Profile & Settings Become Real Screens

### 6.0 Navigation model

- **Home, Signals, Insights, Learn, and the new Profile screen are five peer destinations** in the same `IndexedStack` mechanism `HomeShell` already uses — Profile is not a modal, not a separate navigator stack, not visually different in kind from the other four tabs.
- **Settings and Edit Profile are pushed routes, not tabs**, reached only from within the Profile tab. Pushing either hides the bottom nav for the duration: a plain top app bar with a back affordance, standard push transition, bottom nav reappears on return.
- **Because Profile is now always reachable via the tab bar, no other tab gets its own avatar/profile entry point.** Home's header avatar simply selects the Profile tab; Signals/Insights/Learn don't get one added.

### 6.1 The problem today

`profile_menu_sheet.dart` is a single modal doing five jobs at once — identity view, navigation menu, notifications panel, appearance panel, session management — reachable from only one of four tabs, with roughly half its tappable elements doing nothing.

### 6.2 Recommended structure: two dedicated screens

**Profile screen** (full screen, reached via the Profile tab):
- **Identity block** at top: avatar, display name, an email/handle or account-tier label if one genuinely exists in the backend.
- **Edit Profile** row pushing a dedicated screen (back/cancel left, "Save" right, disabled until a field changes). Limit editable fields to what the backend genuinely supports.
- **A menu into other destinations** replacing the non-functional 3-tile grid: **Settings**, and **Saved/Watchlist** only if it's a real, existing/planned feature — remove the tile rather than keep a dead one.
- **Session block at the bottom**, visually separated (a divider or distinct surface-tint band) from the routine menu above: Help/Support (a real destination, even a `mailto:` or support URL) and **Sign Out**, wired to the existing sign-out path. Sign Out opens a confirmation bottom sheet (Cancel / Sign Out), the destructive action styled with `negative`, Cancel as the visually primary/default action, warning-weight haptic on open (§4.6).

**Settings screen** (full screen, reached from Profile's menu). Before building any row, confirm it maps to a real, currently-working piece of app/backend behavior — if it doesn't yet, leave that row or section out entirely rather than ship a non-functional control.
- **Notifications** — migrate the existing toggles verbatim; add a one-line consequence description under each.
- **Appearance** — migrate the dark/light toggle; add a third **System** option as a three-way selector.
- **Account & Security** — only what maps to real capability today, at minimum a read-only sign-in identifier.
- **Data & Privacy** — only if there's something real to control; omit entirely if not.
- **About/Support** — app version, Help/Support link, legal links if they exist elsewhere.
- **Sign Out stays on Profile, not Settings** — it's an identity/session action, not a preference.
- Consistent disclosure affordance on every row: a chevron for navigable rows, a switch for toggles.

**Why two screens, not one longer one.** A single long scroll would still conflate "who I am" with "how the app behaves," and would just make the crowded sheet longer without resolving the core problem.

---

## 7. Screen-by-Screen Specifications

### 7.1 Home (`home_tab.dart`)

What works and stays: the greeting header, hero board card (momentum score, live-market pill, sparkline), signal-readiness card, and the `AnimatedEntrance` stagger already form a clear, confident landing hierarchy.

What changes:
- Wire the **notification bell** to a minimal, real notifications screen (a reverse-chronological list the existing alert/digest settings already generate) — only fall back to removing the icon if even that isn't feasible.
- Standardize refresh feedback on the native `RefreshIndicator` alone (tinted to `primary`); retire the bespoke `_RefreshRibbon` entirely.
- Avatar tap navigates to the Profile tab instead of opening a modal sheet.
- The hero card's ornament becomes the tick-mark/contour treatment from §4.5 — Home's hero is the one surface per screen permitted an ornament.
- Add the three new market-data sections — see §8.

### 7.2 Signals (`signals_tab.dart`)

What works and stays: the hero-plus-card-list structure, and per-signal sparklines as a genuine visual signature.

What changes: hero carries no ornament (per §4.5, only Home's does); sparklines redrawn in the instrument style (§7.3), applied everywhere the component is used; the already-well-structured empty state gets updated visual dressing (icon treatment, surface) to match §4.5 — it's the template Learn's empty state is brought up to match.

### 7.3 Insights (`insights_tab.dart`) — the sentiment gauge, rebuilt as a real instrument

The sentiment gauge with weekly/monthly toggle is Ayre's strongest single visual signature, and the deliberately calm, static error state is genuinely well-reasoned — **don't add animation to the error state.**

- **Rebuild the gauge, don't recolor it.** Replace the soft gradient-arc rendering entirely with a **needle-and-dial gauge**: a semicircular (or near-semicircular) dial face with fine engraved tick marks at regular intervals, a thin needle pointing to the current reading, and the numeric value set in the instrument-readout monospace (§4.2) at the dial's center or below it. The dial face is a flat fill (a surface-scale tone, never a gradient); tick marks in a muted ink tone; the needle in `primary` (brass) or the semantic color appropriate to the reading — a narrow, sanctioned exception to "primary is brand-only," since the needle here is genuinely both the brand mark and the data indicator.
- The needle **sweeps** from its previous reading to its new one on data update (§4.4) — the single most visible expression of the instrument idea in the app.
- What stays unchanged: the weekly/monthly toggle mechanism, the underlying data/calculation, and the calm/static error-state treatment — only the gauge's *rendering* changes.
- **Sparkline, everywhere it appears:** redrawn as a fine hairline trace with a tick baseline — in the spirit of a barograph strip chart — never a filled-gradient area chart. On data update it redraws progressively (draw-in or path-morph), never an instant replace or cross-fade.
- Add a low-key empty state (successful fetch, zero insights) matching the calm register of the existing error state — there isn't one today.
- Accent-color usage on metric bubbles follows §4.1's semantic assignment rather than rotating decoratively.

### 7.4 Learn (`learn_tab.dart`)

What works: the lesson-card structure (icon, eyebrow, title, description, arrow affordance) is clean and scannable — keep it.

What changes: bring the empty state (currently unstyled plain text) up to the shared standard — a flat, non-gradient icon in a simple bordered circle, a short heading in the display serif, one line of supporting copy in the calm register matching Insights. Hero carries no ornament, same reasoning as Signals.

### 7.5 Login (`login_screen.dart`)

Currently dark-launched, not part of the live journey today, but brought to spec regardless.

- Keep the constrained max-width card layout — the one place that already anticipates non-mobile viewports correctly; use it as the model for §10.
- Login and Splash share the bearing/compass motif (§4.5) — a static or single-needle version, not the retired rotating gradient sweep — positioned behind the login card.

### 7.6 Splash (`splash_screen.dart`)

- Fix the motion inconsistency: replace `Curves.easeOutBack` with the standard no-bounce curve (§4.4).
- Replace the rotating gradient-sweep ring with the bearing/compass motif: a static compass mark with a single slow-rotating needle (critically damped, no overshoot), paired with the nav's needle-mark treatment as one coherent motif across the app.

### 7.7 Profile & Settings (new screens)

See §6.2 in full.

---

## 8. Home Tab — New Market Data Sections (Top Gainers, Top Losers, Most Active Equities)

### 8.1 Architecture requirement

No widget may call a specific market-data backend, HTTP client, or hardcoded URL directly. Introduce a dedicated `MarketMoversService` interface (or equivalent name matching the codebase's conventions) with `getTopGainers()`, `getTopLosers()`, and `getMostActiveEquities()` — the only thing the new Home-tab widgets depend on. The concrete implementation (an NSE community-library wrapper, a commercial market-data API client, a backend proxy, or a temporary development source) stays swappable behind that interface without touching any widget.

### 8.2 Expected data shape (per stock/row)

| Field | Type | Notes |
|---|---|---|
| `symbol` | string | Required — stable row identity |
| `companyName` | string | Fall back to `symbol` if unavailable |
| `lastPrice` | numeric | Required |
| `change` | numeric | Absolute change, can be negative — required, drives the sign |
| `percentChange` | numeric | Required — headline figure for ranking/display |
| `volumeOrValue` | numeric | Required for Most Active; optional elsewhere |
| `asOf` | timestamp | Required — drives the freshness/staleness indicator |

If a provider is missing a non-required field, degrade that piece of UI gracefully rather than blocking the section. A row missing any always-required field falls back to the section's error/empty treatment instead of rendering a broken card.

### 8.3 Placement and presentation

**Placement:** after the existing NIFTY/SENSEX tiles, before the bottom of the current `ListView` — header → hero board → signal-readiness card → index tiles → the three new sections.

- Each section gets its own eyebrow-style header (display serif) and shows a fixed 3–5 rows by default, not a long scrollable list. A "see all" affordance, if wanted, pushes to a dedicated full-list screen.
- Each row reuses Ayre's existing card/list-row grammar: symbol + company name in Inter, and **last price, absolute change, percentage change, and volume all in the instrument monospace role — no exceptions, per §4.2.** A per-row micro-sparkline in the instrument style is a reasonable enhancement if the data source provides enough points — not required for v1.
- Sorting: Top Gainers descending by `percentChange`; Top Losers ascending (most negative first); Most Active descending by `volumeOrValue`.
- Gain/loss color and redundant encoding follow §4.1/§9 exactly.
- A small "as of [time]" freshness marker per section, computed from `asOf`, with its numeric/time portion in the instrument monospace.
- Tapping a row is a reasonable addition only if there's somewhere real for it to lead — don't invent a stock-detail or trading capability that doesn't exist elsewhere merely to give the row a destination. If there's nowhere to go, the row is non-interactive.

### 8.4 State handling

| State | When | Treatment |
|---|---|---|
| Loading | First load / not yet returned | Shape-matched skeleton rows, not the app-wide loader |
| Empty | Succeeded, zero rows (e.g. outside market hours) | Shared empty-state template, section-specific copy, static |
| Error | Request failed | Insights' calm, static error template at the section level — a failed "Most Active" fetch shouldn't block the rest of Home |
| Stale | `asOf` older than the provider's normal cadence | Keep last-known data visible; flag with the ember token and explicit copy ("Data may be delayed") |
| Source unavailable | Provider down/unreachable | Same user-facing treatment as Error — the distinction matters for logging/retry behind the interface, not a fourth visible state |
| Refresh | Pull-to-refresh on Home | Part of the single app-wide `RefreshIndicator` — no second, section-local control; apply the numeric-roll/sparkline-redraw treatments to any row whose values changed |

This section does not specify a chosen data provider, does not assume a specific backend contract beyond the shape above, and does not add trading, watchlist, alerting, or per-stock detail capability.

---

## 9. Accessibility

- **Contrast.** `textTertiary` must clear 4.5:1 (AA, normal text) against its typical surface in both themes. Verify every other brand/semantic token clears at least 4.5:1 against its typical surface pairing; where a hue is used as a large-area fill rather than text (e.g., a filled button), a 3:1 large-text/UI-component threshold is acceptable, but use the darker/more-saturated reading wherever the same hue appears as small text or a thin line. Re-verify dark-theme pairings using the APCA method as a supplement to WCAG before shipping.
- **Color is never the only channel.** Positive/negative price movement always carries a `+`/`–` sign in the number itself plus a directional glyph — extend this to any bare numeric delta that doesn't currently carry one. Hard requirement, applies identically to the new market-data sections from their first shipped version.
- **Touch targets** are already comfortably compliant — confirm new Profile-tab and Settings-row targets match the existing standard.
- **Dynamic type.** Verify multi-line card copy doesn't clip or overlap at larger system font sizes.
- **Reduced motion.** Add an explicit path (OS-level setting) substituting cross-fades for every motion category in §4.4, including spring-based, digit-roll, needle-sweep, and needle-mark treatments, which collapse to an instant value or position swap. Reduced motion changes *how* something communicates, never removes the communication.

---

## 10. Responsive Behavior

The repository ships build targets for macOS, Windows, Linux, and Web alongside mobile, with no breakpoint/adaptive-layout logic anywhere in `lib/` today.

- **Content max-width.** Apply the principle Login already gets right — constrain scrollable content to a centered column once the viewport exceeds a comfortable reading width.
- **Nav → side rail** at wide viewports, per §5.4.
- **Grid opportunities at tablet+ widths:** a two-column layout for **Signals** and **Learn** once the viewport comfortably supports two comfortable-width cards side by side — both are lists of self-contained, independently-scannable items. **Insights and Home stay single-column at every width** — both read as a linear narrative a grid would break apart.
- **Orientation:** the app currently locks to portrait; reasonable for the mobile-first product today, revisit only if tablet support becomes a real target.
- **Long content / data variability:** verify card layouts don't break with longer stock/index names or larger price figures than current sample data.

---

## 11. Animation & Micro-Interaction Reference

| Interaction | Treatment | Notes |
|---|---|---|
| Tab switch | Label reveal + sliding needle-mark | Needle-mark begins ~15–20ms after the label reveal (§5.3) |
| Any button/card press | `PressableScale` (0.97) | Apply universally |
| List/card entrance on first load | `AnimatedEntrance` stagger | Don't replay on tab re-visit |
| Screen-to-screen push (Profile → Settings, → Edit Profile, any future pushed screen) | Horizontal slide-in + subtle fade | Standing convention for any future pushed screen |
| Tab content transition (`IndexedStack` swap) | Restrained cross-fade of the incoming tab's already-built content, opacity-only | Additive to the nav's own label/needle-mark motion |
| Expanding/collapsing content | Height/opacity expand via critically-damped spring | A rapid double-tap re-targets smoothly instead of restarting |
| Modal/bottom sheet presentation | Slide-up with scrim fade-in; spring-based drag-to-dismiss tracking the finger | Drag-to-dismiss is the clearest case for spring-based motion |
| Sentiment-gauge needle sweep | Critically-damped spring from old reading to new | The app's clearest expression of the instrument identity |
| Sparkline redraw on data update | Progressive draw-in / path-morph, never instant replace | The shape change itself is the information |
| Numeric value changes (price, %, momentum score) | Mechanical digit-roll, old value to new | Subtle — a settle, not a flourish; skip on first load |
| Refresh (pull-to-refresh, all tabs) | Single `RefreshIndicator`; chart/needle/numeric-roll treatments play once new data lands | Indicator does its job, then content visibly settles |
| Success feedback | Inline checkmark swap (single preference change) or toast-style banner (whole action) | Confirm, don't celebrate |
| Empty/error state appearance | No entrance animation | Matches Insights' existing, deliberate choice |
| Loading states (skeleton-to-content) | Brief cross-fade, not a hard cut | |
| Hover states (desktop/web only) | Subtle background-tint shift, no scale change | Only where a cursor is actually present |
| "Live data" pulse | Plays once on a triggering data update, never loops | Reduces to a static color change under reduced motion |

**What stays instantaneous:** text input focus/blur, scroll position changes, and any state change the user didn't directly trigger — including a chart/needle/number update landing on a tab the user isn't currently viewing. Play the update animation only the next time that content is actually on screen and the value differs from what was last shown.

**Sequencing:** where more than one thing changes at once, stagger by roughly 15–25ms per element rather than firing simultaneously; for `choreographed`-tier sequences of three or more elements, cap the total stagger budget so the last element starts within roughly 100ms of the first.

---

## 12. States & Feedback Matrix

| State | Target treatment |
|---|---|
| Loading (tab-level) | Keep the existing loader for short/typical loads, re-dressed in the flat instrument register; consider shape-matched skeletons only if real-world load times regularly exceed ~1–2s. Market-data sections (§8) use shape-matched skeletons from the start. |
| Empty (no data, request succeeded) | One shared visual template (icon, heading, one line of copy, static, no entrance animation) across Signals, Insights, and Learn, re-dressed per §4.5. Market-data sections reuse this per-section. |
| Error (request failed) | Reuse Insights' calm, static template app-wide, re-dressed per §4.5, including per-section on market-data sections. |
| Stale data | Flag with the ember token and explicit copy rather than hiding or blanking last-known values. |
| Success confirmation | Add a lightweight, consistent confirmation pattern per §11. |
| Destructive/consequential action | A bottom sheet confirmation (Cancel / Sign Out) before any destructive path executes, with warning-weight haptic on open (§4.6). |

---

## 13. Component-Level Checklist

**This is a rebuild checklist, not a polish checklist.** `PremiumCard`, the navigation bar, the sentiment gauge, and the sparkline should each be treated as due for a full visual re-implementation against the rules in §4 — their underlying widget structure, state, and data flow stay the same, but their rendering code should not survive as an incremental tweak of what exists today.

- **`PressableScale` coverage audit** — confirm every tappable surface uses it, including Profile menu rows, Settings toggle rows, and every nav segment.
- **Haptic coverage audit** — confirm every action in §4.6's list actually fires the correct weight of feedback; today's coverage is partial.
- **`PremiumCard` default treatment** — flat, tinted surface with an optional hairline border is now the default; the ornament (tick/contour marks) is an explicit, at-most-once-per-screen modifier, not a default; the old frosted/glass material is retired as the default finish.
- **Typography enforcement** — audit every screen against the §4.2 quick-reference table; no numeral should render outside the instrument monospace / display serif (two moments only) / Inter system.
- **Icon-only affordances** (notification bell, any future icon-only buttons) must either act or be removed.
- **Toggle switches** — consistent sizing, on/off color mapping using `positive`/neutral tokens, and an adjacent text label describing what it does.
- **Divider/section-separator consistency** — one hairline divider treatment, consistent inset, ink-toned, reused across Profile, Settings, and any future grouped screens.
- **Introduce a shared numeral component** enforcing the monospace/tabular treatment everywhere live data renders, so no screen can accidentally fall back to the body typeface for a price or percentage.

---

## 14. Prioritized Implementation Roadmap

### P0 — Structural fixes and the core identity (do first)

1. **Apply the new brass-and-ink palette app-wide** — full light/dark values, the light/dark-as-counterparts principle, the elevation scale.
2. **Retire every soft glow/blob/gradient-sweep ornament** and replace with the engraved line-work / bearing-compass system (§4.5).
3. **Rebuild the sentiment gauge as a needle-and-dial instrument and the sparkline as a hairline trace** (§7.3).
4. **Adopt the three-role typographic system** (§4.2) — display serif (two hero moments only), instrument-readout monospace (every live figure), Inter for body/UI.
5. **Rebuild the navigation bar's material and active-state signature** (§5) — keep the pill silhouette and expand-on-select mechanic, replace the glassy fill with an opaque instrument-panel surface, a ruler-tick edge, and a sliding needle-mark active indicator.
6. **Promote Profile to a fifth nav tab**; retire the avatar-only entry point (§5.2, §6).
7. **Split the profile sheet into dedicated Profile and Settings screens**; wire every menu row to a real destination or remove it (§6.2).
8. **Wire or remove** the notification bell and every other currently-dead affordance.
9. Replace placeholder/debug copy in the session section with real, finished copy; run the generic-phrasing audit on all new Settings/Profile/empty-state copy.
10. **Fix the splash screen's overshoot curve** to match the app-wide no-bounce system.
11. **Fix the `textTertiary` contrast** issue per §9.
12. **Build the `MarketMoversService` abstraction and the three new Home-tab sections** per §8, including all listed states and the strict monospace-numeral rule.

### P1 — Consistency and finish quality

13. Standardize refresh feedback to a single indicator app-wide; retire `_RefreshRibbon`.
14. Bring Learn's empty state up to the shared standard.
15. Move the radius system toward the smaller, chamfer-inclusive language app-wide (nav bar excepted, per §4.3).
16. Finish the legacy color-token deprecation.
17. Add content max-width constraints for wide/desktop viewports.
18. Build out the richer motion system: critically-damped spring infrastructure, the `choreographed` tier, the needle-sweep/digit-roll/needle-mark treatments, the live-data pulse signature, hover states on desktop/web.
19. Extend reduced-motion support to every category introduced by item 18.
20. Complete the haptic-feedback audit against §4.6.

### P2 — Optional polish

21. Nav → side rail at desktop widths.
22. Add the three-way System/Light/Dark appearance selector in Settings.
23. Two-column layouts for Signals and Learn at tablet+ widths.
24. Skeleton-style loading placeholders elsewhere, if real-world load times warrant it.
25. Sequencing refinement (15–25ms stagger) between simultaneous motion elements.
26. Per-row micro-sparklines on market-mover rows, if the data source supports it.
27. A "view more"/full-list screen for each market-mover section, if the fixed 3–5-row summary proves too limiting.
28. Re-verify the palette's dark-theme text/background pairings using the APCA method as a supplement to the WCAG check.

---

## 15. Closing Note

Ayre Scanner's engineering discipline — a real token system, a considered no-bounce motion policy, state preservation across tabs, well-reasoned calm error states — stays exactly as it is. Everything the app's surface currently has in common with a generic fintech dashboard is replaced: the teal-or-blue trust palette becomes warm brass and ink; the soft glow-blob ornament becomes engraved tick marks and a bearing motif; the glassy frosted cards and nav become flat, tinted, hairline-edged surfaces; the single default typeface becomes a three-role system where live data has its own unmistakable typographic identity; the sentiment gauge and sparkline become genuine drawn instruments instead of chart-library defaults. The navigation bar keeps the pill shape and interaction mechanic that already work, rebuilt in an opaque instrument-panel material with a ruler-tick edge and a sliding needle-mark — a full re-skin of the app's most-touched component, not a shape change and not a color swap. Every choice here is traceable to legibility, to differentiation from a crowded category, or to reinforcing what the product actually does: an instrument, built digitally, for reading the market.
