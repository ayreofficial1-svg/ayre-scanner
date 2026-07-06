# Ayre Scanner — Design System

A premium market-intelligence companion app (not a trading/order platform). Four core sections — **Home**, **Signals**, **Insights**, **Learn** — plus **Splash**, **Login**, and **Profile/Settings**. This document is the full visual spec: hand it to a designer or developer (Flutter, SwiftUI, React Native, etc.) to implement pixel-for-pixel or adapt.

**Design language in one line:** soft, layered, gradient-driven "premium consumer app" — rounded cards, floating capsule nav, a recurring glowing "aura halo" behind every key metric — never flat white/black, never sharp corners.

---

## 1. Brand concept

- **Name / meaning:** Ayre ≈ "air" — the app reads the atmosphere of the market.
- **Signature element — the Aura Halo:** every hero metric (mood index, signal count, sentiment score, lesson progress) sits in front of a soft, blurred, radial-gradient glow. Its hue shifts with context — mint/teal for bullish or calm, coral/gold for neutral or attention, muted red only for genuinely negative states. This is the one motif that should appear on every screen and nowhere else in generic form — do not add halos to purely decorative elements.
- **Personality per section:**
  | Section | Mood | Job |
  |---|---|---|
  | Home | Warm dawn (peach/coral/cream) | Command center — market direction understood in seconds |
  | Signals | Focused (mint/teal) | Actionable, scannable list |
  | Insights | Analytical (lavender/sage/violet) | Depth, data, narrative |
  | Learn | Warm & welcoming (cream/gold) | Encouraging, low-pressure |
  | Profile | Neutral (charcoal/cream) | Calm, administrative |

---

## 2. Color tokens

Never use flat white or flat black backgrounds. Every screen background is a **layered gradient** (see §5) plus 1–2 blurred decorative color "blobs" for depth.

### Light mode

| Token | Hex | Use |
|---|---|---|
| `cream` | `#FBF2E4` | Base background tone |
| `ivory` | `#FFF8EC` | Alt light surface / note cards |
| `peach` | `#FFD6AE` | Secondary surface, Home mood |
| `coral` | `#FF8A65` | Accent, warm hero gradients |
| `mint` | `#D9F2E6` | Secondary surface, Signals mood |
| `mint-deep` | `#9FDCC9` | Aura glow, chart accents |
| `teal` | `#1F6F72` | Primary hero gradient (trust, calm) |
| `teal-2` | `#3FA66B` | Hero gradient pair w/ teal, positive accents |
| `lavender` | `#EAE3FB` | Secondary surface, Insights mood |
| `violet` | `#6456A6` | Insights hero gradient |
| `sage` | `#DCE8D4` | Secondary surface, analytical accents |
| `gold` | `#FFC857` | Learn hero gradient, highlight borders |
| `charcoal` | `#2A241D` | Primary text |
| `charcoal-soft` | `#5C5347` | Secondary/muted text |
| `positive` | `#2C7A54` | Text on positive pill |
| `positive-bg` | `rgba(62,156,110,0.16)` | Positive pill background |
| `negative` | `#C1453A` | Text on negative pill |
| `negative-bg` | `rgba(228,104,93,0.16)` | Negative pill background |
| `paper` | `rgba(255,255,255,0.55)` | Translucent frosted surface |
| `paper-strong` | `rgba(255,255,255,0.78)` | Translucent frosted surface (more opaque) |

### Dark mode

Dark mode is **warm charcoal**, never pure black. Same hero-gradient hues, boosted slightly for contrast; surfaces become deep tinted tones (not gray) matching each section's mood.

| Token | Hex | Use |
|---|---|---|
| `d-bg-base` | `#1E1A16` | Base dark background tone |
| `d-text` | `#F5EFE2` | Primary text (warm off-white, never pure white) |
| `d-text-soft` | `#B7AA96` | Secondary/muted text |
| `d-surface` | `#2A2420` | Default card surface |
| `d-surface-2` | `#332B24` | Alt card surface |
| `d-border` | `rgba(255,255,255,0.08)` | Card hairline / separator |
| `d-mint` | `#6FD9B0` | Positive accent, brighter for dark contrast |
| `d-teal` | `#2FA6A0` | Primary accent |
| `d-coral` | `#FF9770` | Warm accent |
| `d-gold` | `#FFD27A` | Highlight accent |
| `d-lavender` | `#A597E0` | Insights accent |
| `d-violet` | `#8B7BC9` | Insights hero accent |
| `d-sage` | `#8FBF7D` | Analytical accent |
| `d-positive` | `#5FCB8F` | Positive text |
| `d-positive-bg` | `rgba(95,203,143,0.18)` | Positive pill bg |
| `d-negative` | `#FF7A6E` | Negative text |
| `d-negative-bg` | `rgba(255,122,110,0.18)` | Negative pill bg |

**Tinted surfaces per section (dark mode)** — cards should not all share one gray; tint toward the section's hue at low saturation, e.g.:
- Home / warm: `#3A2A1C` (peach-tinted), `#1F3A32` (mint-tinted)
- Signals: `#1F3A32`, deeper teal-black gradient background
- Insights: `#2A2340` (lavender-tinted), `#1F3A32`
- Learn: `#3A2A1C`, `#2A2340`
- Profile: neutral warm charcoal only, no strong tint

---

## 3. Typography

| Role | Typeface | Notes |
|---|---|---|
| Display / headings | **Fraunces** (serif, variable) | Weights 460–650. Used for page titles, hero values, big numbers where warmth matters. Gives the "premium consumer app" feel instead of a typical fintech grotesk. |
| UI / body text | **Plus Jakarta Sans** | Weights 400–800. All labels, buttons, paragraph copy, nav. Rounded terminals match the soft/capsule visual language. |
| Data / numerals | **Space Grotesk** | Weights 400–700. Used *only* for numeric tickers, prices, percentages, timestamps — gives quantitative precision that contrasts intentionally with the softer Fraunces/Jakarta pairing. |

**Scale (approx, mobile):**
- Hero value: 40–44px / Fraunces 560–650
- Page title: 24–26px / Fraunces 560
- Card title: 14–16px / Jakarta 700
- Body / secondary: 12.5–14px / Jakarta 400–500, `charcoal-soft`
- Eyebrow / label: 11–13px / Jakarta 600–700, letter-spacing 0.04–0.06em, uppercase for section labels

---

## 4. Shape, elevation & spacing

- **Corner radii:** cards 24–32px · hero cards 32px · buttons/tags/pills fully rounded (capsule, 100px) · nav bar 33px (fully rounded capsule) · avatar/icon blobs 16–50% (circle) · **never sharp corners, anywhere.**
- **Shadows:** soft, diffuse, warm-tinted (not pure black) — e.g. `0 20px 40px -12px rgba(0,0,0,0.28)` for hero cards, `0 12px 26px -14px rgba(60,40,20,0.18)` for standard cards. Dark mode shadows deepen toward `rgba(0,0,0,0.6)`.
- **Borders:** 1px hairline, translucent white (`rgba(255,255,255,0.5)` light / `rgba(255,255,255,0.08)` dark) — never a hard solid-color border.
- **Spacing:** generous. Screen horizontal padding 20–22px. Card internal padding 18–26px. Vertical rhythm between cards 12–18px.
- **Decorative blobs:** 1–2 per screen, large (200–260px), heavily blurred (`blur(35–40px)`), low opacity (0.2–0.7), placed at screen corners/edges for depth — never centered over content.

---

## 5. Backgrounds (critical — read before building)

No screen uses a flat white or flat black/gray page background. Every screen background is a **165°-angled 3-stop linear gradient**, tinted to that section's mood, e.g.:

- Home (light): `linear-gradient(165deg, #FDF3E7 0%, #FFDCC2 40%, #FFB199 100%)`
- Signals (light): `linear-gradient(165deg, #EAF7F1 0%, #CDEEE0 45%, #9FDCC9 100%)`
- Insights (light): `linear-gradient(165deg, #F1EDFB 0%, #E3DBFA 45%, #C9D9C0 100%)`
- Learn (light): `linear-gradient(165deg, #FFF8EC 0%, #FFE9B8 45%, #FFCB7D 100%)`
- Profile (light): `linear-gradient(165deg, #F5F0E8 0%, #E7DFD2 55%, #D8CDBB 100%)`
- Home (dark): `linear-gradient(165deg, #221C17 0%, #2B2119 45%, #201814 100%)`
- Signals (dark): `linear-gradient(165deg, #182420 0%, #173430 50%, #0F2C28 100%)`
- Insights (dark): `linear-gradient(165deg, #1E1B29 0%, #241F35 50%, #1B2A20 100%)`
- Learn (dark): `linear-gradient(165deg, #241D14 0%, #2E2414 50%, #33240F 100%)`
- Profile (dark): `linear-gradient(165deg, #1D1A16 0%, #242019 55%, #2A241C 100%)`

Layer 1–2 blurred decorative blobs (see §4) on top of the gradient, behind all content.

---

## 6. Components

**Hero card** — the focal point of every screen. Rich two-stop gradient (see §5 for per-section pairs, e.g. teal→mint for Home, coral→gold for Signals, violet→lavender for Insights, gold→coral for Learn), white text, contains the **Aura Halo** glow plus the section's primary message: on Home this is the single-word **market direction** (Bullish/Bearish/Neutral) with the halo's hue encoding it; elsewhere it's the section's key metric (signal count, sentiment donut, lesson progress ring).

**Standard card** — pastel or tinted-dark surface, 1px translucent border, soft shadow, rounded 24–26px. Used for watchlist items, signal rows, insight modules, lesson cards, settings rows.

**Pill / tag** — fully rounded, small caps or sentence case, used for: status filters (Signals tabs), positive/negative % change (green/red tinted, never harsh saturated red/green — always the muted `positive`/`negative` tokens), streak badges, membership badge.

**Progress ring** — circular SVG stroke, rounded linecap, used for mood index, signal confidence, sentiment %, lesson completion. Track color is a low-opacity version of the foreground.

**Floating capsule navigation** — persistent bottom nav bar, floating above the screen edge (20px margin all sides), fully rounded capsule track, translucent frosted background with blur, soft shadow, 4 tabs (Home / Signals / Insights / Learn).
- **Inactive tabs:** icon only, muted color (`charcoal-soft` light / `d-text-soft` dark), fixed circular tap target (~44px).
- **Active tab:** smoothly expands (width animates, ~250–300ms spring/ease-out) into a filled rounded capsule containing the icon *and* its label side by side. Capsule fill uses the app's own gradient/accent tokens (e.g. a soft teal→mint or charcoal-on-cream fill) — never the raw colors from any external reference image, layout/interaction only is borrowed.
- Only one tab is expanded at a time; switching tabs collapses the previous capsule back to icon-only while the new tab expands — this pair of animations should feel like one continuous glide, not two separate cuts.
- The bar itself never resizes — only the active tab's width changes, so the capsule appears to "travel" along the track as the user switches sections.

**Icon blobs** — 44×44px rounded-square (16px radius) colored containers behind list-row icons, tinted to match the row's category, never plain gray.

---

## 7. Motion (guidance, not literal code)

- Card entrance: staggered fade + slight upward slide (~200–300ms, ease-out), 40–60ms stagger between cards.
- Aura halo: slow ambient pulse — scale 1 → 1.08, opacity 0.55 → 0.75, ~5s ease-in-out loop, infinite.
- Nav transitions: the active tab's capsule expands/collapses on tap (width + label opacity animate together, spring/ease-out, ~250–300ms) as described in §6; page content crossfades/slides underneath, no hard cuts.
- Buttons: subtle scale-down (0.97) on press, spring back.
- Respect reduced-motion settings — disable ambient pulse and large transitions, keep only opacity fades.

---

## 8. Screen-by-screen content spec

1. **Splash** — centered wordmark "Ayre" (Fraunces), circular gradient logomark, tagline "Market intelligence, made calm.", aura halo glowing behind mark, 3-dot loading indicator.
2. **Login** — greeting headline ("Welcome back, trader."), email/password fields (rounded frosted fields), primary gradient "Continue" button, secondary ghost buttons for Apple/Google, footer link to create account.
3. **Home** — the command-center screen; should communicate today's market within seconds.
   - Header: personalized greeting ("Good morning, Maya") + circular profile picture (real photo or avatar), top-right.
   - Hero: shows **only the overall market direction** — one word, large and confident: **Bullish**, **Bearish**, or **Neutral** — plus a one-line supporting note ("Broad-based buying since open"). No numeric index clutters this hero; the aura halo's hue *is* the visual encoding of direction (mint/teal glow = bullish, coral/red-tinted glow = bearish, gold/neutral glow = neutral). Keep this card uncluttered — direction is the entire message.
   - Below the hero: three live index cards for **NIFTY 50**, **SENSEX**, and **BANK NIFTY** — each showing index name, current level, point/percent change, and a small sparkline, styled as a horizontal row of three compact cards (or a horizontal-scroll set on narrow screens) rather than a plain table.
   - Optional lower section: watchlist / top movers, as supporting content beneath the three index cards — not the focal point.
4. **Signals** — where users discover scanner-generated setups. Header; optional hero summarizing today's signal count. Filter tag row (All / Breakouts / Reversals / Volume). Each signal card must clearly surface: **stock name**, **current price**, **price movement** (% pill), a **confidence** indicator (ring or score), and a short **supporting rationale** (one line explaining *why* the signal fired, e.g. "Cleared 200D resistance on volume"). Organize as a clean, scannable vertical list — every card should be understandable in under two seconds.
5. **Insights** — explains the market, not individual stocks; should feel intelligent and highly visual. Header. Hero: overall **market sentiment** visualization (donut/gauge) plus a plain-language **market climate** headline ("Tech leads, breadth improving"). Supporting cards: volatility index with progress bar, sector rotation / correlation heatmap, and an **analyst note** card that gives a short narrative answer to *why* the market is behaving this way (gold left-border accent, quote-like treatment) — this "why" narrative is the section's core job, not just charts.
6. **Learn** — the educational hub; should feel like an inviting library to explore, not a flat article list. Header + optional streak badge. Hero: "continue where you left off" card with progress ring. Below: browsable categories/collections covering **trading, investing, technical analysis, psychology, and risk management** — presented as colorful, icon-led course/collection cards (varied pastel surfaces per category) rather than a bare list of links, encouraging exploration through visual variety and clear category identity.
7. **Profile & Settings** — kept minimal and elegant, not feature-heavy. Avatar, name, membership pill. Grouped rows for: **theme** (light/dark, and any accent options), **notifications**, **account** details/preferences, **data & privacy**, and **help**; each row uses a tinted icon blob + chevron. **Sign out** presented as a clearly separated, outlined (not filled) destructive row at the bottom — deliberately understated rather than alarming.

---

## 9. What to avoid

- Plain white or plain black/near-black page backgrounds.
- Sharp corners, thin hard borders, standard Material defaults.
- Neon green / pure red fintech colors — always route positive/negative through the muted `positive`/`negative` tokens.
- Cards that are all the same white/gray tone — vary pastel/tinted surfaces per card.
- Flat bottom nav bar — it must be a floating capsule with margin on all sides.
- Overusing the aura halo on non-hero elements — it signals "this is the key number," so it loses meaning if it's everywhere.
