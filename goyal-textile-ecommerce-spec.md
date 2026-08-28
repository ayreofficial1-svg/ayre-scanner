# Goyal Textile — E-Commerce Platform Specification

**Document purpose:** This is the complete, standalone specification for building a genuine e-commerce website for Goyal Textile, a suiting/shirting fabric business in Chandni Chowk, Delhi. It is written to be handed directly to a coding AI agent with the instruction "build this website according to this specification." It contains business context, research findings, UX/UI direction, technical architecture, database design, business logic, and an implementation plan. **It contains no code.** All implementation decisions (exact code, libraries' internal wiring, config files) are left to the coding agent, guided by the specifications below.

---

## 0. How to read this document — information provenance

Every substantive claim in this document is tagged so the coding agent (and the business owner) can tell fact from inference:

- **[OWNER-CONFIRMED]** — stated directly by the business owner's family member in the original brief. Treat as ground truth.
- **[RESEARCH]** — found via web research into the market, competitors, or third-party services. Cited informally; verify pricing/limits again close to build time since these change often.
- **[ASSUMPTION]** — a reasonable default this spec adopts in the absence of owner input, so the coding agent has something to build against. Explicitly flagged as changeable.
- **[NEEDS OWNER CONFIRMATION]** — a decision or fact that cannot be finalized without the shop owner. These are collected in Section 15. **Do not silently invent these values; use clearly-marked placeholder content until real data is supplied.**
- **[CONFLICTING INFO]** — external sources disagree; flagged rather than resolved.

---

## 1. Business Context

### 1.1 What we know for certain [OWNER-CONFIRMED]
- The business is **Goyal Textile**, a fabric shop physically located in the **Chandni Chowk** area of Delhi.
- It is a traditional offline shop that sells fabric off large rolls called **thaans**. A customer picks a thaan, and staff cut the exact length the customer wants.
- Product range: fabric for **shirts, pants (trousers), kurtas, coats, suiting, shirting, and gifting**.
- The family wants to take this business online as a **real e-commerce store** — browsing, product pages, quantity selection, cart, checkout, payment, order placement, and order tracking — not a brochure site, not a static catalogue, and not a WhatsApp-only ordering flow.
- Cost sensitivity is very high: infrastructure should be free wherever a free tier is genuinely sufficient, and cheapest-practical otherwise.

### 1.2 Conclusion for the coding agent regarding external listings
Do not scrape, reuse, or display any address, phone number, rating, or photo from any of the above listings on the live website. None of them should be treated as confirmed source data. The real business name (exact spelling), address, phone number, and any existing online listing must come from the owner directly (see Section 15).

---

## 2. Recommended Business Model

Three models were evaluated:

| **Hybrid: full e-commerce (cart, checkout, payment, tracking) + WhatsApp as a support/assistance channel** | **Recommended.** Customers browse, select quantity, and check out entirely on the website like a normal e-commerce store — WhatsApp is layered on top as a "Chat with us" / "Ask about this fabric" affordance for questions, swatch requests, or bulk/custom orders, never as a replacement for cart and checkout. |


---

## 3. Market Research Findings [RESEARCH]

Findings from studying Indian online fabric retailers (e.g. Fabriclore, Nalli's fabric store, and other wholesale/retail fabric platforms) and general D2C e-commerce practice, used to inform recommendations below rather than being copied wholesale:

- **Selling unit is the metre, not "a product."** Every fabric product page must show **price per metre** prominently and let the customer choose how many metres they want, with the total price recalculating live.
- **Low minimum order quantities matter.** Established platforms typically allow a minimum purchase in the 1–10 metre range with clear "enough for 1 shirt / 1 kurta / 1 suit" guidance, since customers don't always know how much fabric a garment needs.
- **Fabric-specific attributes drive trust and search:** fabric type/fibre, composition, weave/pattern, width, weight (GSM), finish, suitable use (shirt/trouser/kurta/coat/suit), season, and care instructions. Listing these consistently is what separates a "serious" fabric store from a generic clothing store.
- **Photography quality is the single biggest trust signal** for fabric bought sight-unseen: true-colour, well-lit swatch close-ups, drape/fall shots, and where possible a shot of the fabric made into a finished garment.
- **Swatch/sample ordering** (a small paid or free sample piece before committing to a full cut) is common on serious fabric platforms and reduces returns — recommended for the roadmap, not the MVP.
- **Category depth:** successful fabric sellers separate by *end-use* (Shirting, Suiting, Trouser/Pant, Kurta, Coat) as primary navigation, with *fibre/fabric type* (cotton, linen, wool, blends), *colour*, and *pattern* as filters rather than top-level categories — this keeps navigation simple while filters do the heavy lifting.
- **Checkout expectations** mirror general Indian e-commerce norms: address with PIN code-based auto-suggestion, COD as an available option in many markets, UPI as the dominant digital payment method, and clear order-tracking status pages.
- **Cut-fabric return policy is the hardest open question** across the industry — because a length cut to a customer's specification cannot usually be resold as "new," most fabric retailers apply stricter return rules for cut/custom-length pieces than for standard retail. This is addressed as an owner decision in Section 10.4.

### 3.1 General e-commerce conversion research applied to this build [RESEARCH]
Independent of the fabric-specific research above, current general e-commerce UX findings directly shape Sections 8 and 11:
- Shoppers form a purchase impression within seconds, driven by a strong hero product image, an honestly-stated price, and scannable (not paragraph-dense) specifications — reflected in the product-page guidance in Sections 8.10 and 9.3.
- Mobile now accounts for the clear majority of e-commerce transactions, reinforcing the "mobile as first-class, not an afterthought" requirement in Section 8.8.
- Unexpected costs revealed only at final checkout, and forced account creation, are the two most-cited causes of cart abandonment — both already addressed in Sections 8.10 and 11.2.1.
- Trust signals (security indication, visible policies, recognizable payment logos) matter most at the exact moment a shopper is about to pay — reflected in the checkout trust-signal guidance in 8.10.

---

## 4. Recommended Technical Architecture

### 4.1 Guiding principle
Keep the **stack small and boring**. The business is small; every extra moving part is something a non-technical owner (or their family) will eventually have to pay for, secure, and maintain. Use the fewest technologies that get a real, secure e-commerce store live for close to ₹0/month in year one.

### 4.2 Recommended stack

| Layer | Recommendation | Why |
|---|---|---|
| Frontend | **React**, via a modern meta-framework such as Next.js (bundling routing, server-rendering, and image optimization), styled with **plain CSS / a CSS framework** rather than a separate design-tool dependency | React was explicitly requested and is the right choice for a highly interactive catalogue/cart/checkout UI. A React *meta-framework* (rather than bare React + a separate router) reduces the number of separate tools the coding agent must wire together. The coding agent has latitude to choose the specific styling approach (plain CSS with CSS variables, a utility CSS framework, or CSS-in-JS) — whatever best supports the glassmorphism/pill-button/animation requirements in Section 8 while staying maintainable; HTML/CSS fundamentals still underpin all of it. |
| Backend / API | **Node.js**, using the same framework's built-in API routes/server functions rather than a separate standalone backend service | Node.js was explicitly permitted and pairs naturally with a React/Next.js frontend — one language, one repository, one deployment. A separate Python backend would add a second language, a second deployment target, and a second set of secrets to manage for no functional benefit here. |
| Python | **Not used in the core application.** [ASSUMPTION] | Python adds a second runtime/language with no functional benefit for a CRUD e-commerce store. The only place Python could add value is a one-off future automation script (e.g. bulk image compression before upload) run locally by the developer, not as part of the deployed system. Explicitly do not build a Python backend, microservice, or API layer. |
| Database | **Managed PostgreSQL via Supabase** (or an equivalent managed Postgres provider with a genuinely free tier) | Real relational database, free tier covers early-stage traffic, includes built-in authentication and row-level security, avoids self-hosting a database engine. |
| Authentication | **Supabase Auth** (or the auth system bundled with the chosen backend-as-a-service) | Avoids building password storage, session handling, and email verification from scratch — all real security surface area a small team shouldn't own. |
| File/image storage | **Cloudflare R2** or **Supabase Storage** for originals, served through an **image CDN/optimizer** (see 4.4) | Product photography is the heaviest asset class on this site; storage must be cheap at scale and paired with on-the-fly resizing. |
| Hosting/deployment (frontend + API routes) | **Cloudflare Pages** (with Cloudflare Workers/Functions for API routes) as the primary recommendation; **Vercel** or **Netlify** as acceptable alternatives | [RESEARCH, verify near build time] As of 2026, Cloudflare Pages is the only major host offering **unlimited bandwidth on its free tier**; Vercel and Netlify cap free bandwidth around 100 GB/month. For an image-heavy fabric catalogue, unmetered bandwidth materially reduces the risk of an unexpected bill. All three have generous free build minutes and integrate with GitHub for automatic deployment on every push. |
| Payments | **Razorpay** (see Section 11) | Purpose-built for Indian D2C, no setup fee, no AMC on standard pricing, native UPI/cards/netbanking/wallets support. |
| Shipping | **A shipping aggregator (e.g. Shiprocket or a comparable competitor) integrated later**, manual shipping for MVP (see Section 12) | No Indian shipping aggregator currently offers a free plan; per-shipment aggregator costs only make sense once order volume justifies them. |
| Transactional email | **Brevo** free tier (300 emails/day) or an equivalent free transactional email provider | Covers order confirmations, shipping updates, and password resets at low volume with no cost; the coding agent should keep the provider swappable, since the free daily cap will eventually need to be replaced with a paid tier. |
| Analytics | **Free web analytics** — a privacy-respecting option such as Cloudflare Web Analytics (bundled free if hosting on Cloudflare) or Google Analytics 4 (free, but heavier and less private) | Both are ₹0; the coding agent should pick one and instrument the events in Section 20. |
| Version control / CI | **GitHub** (free for a single small private/public repo) with the host's built-in CI (Cloudflare Pages / Vercel / Netlify all auto-deploy from GitHub) | No separate CI system needed. |

### 4.3 What this architecture deliberately avoids
- No self-managed servers, containers, or Kubernetes — unnecessary operational burden for this scale.
- No separate microservices — a single Next.js-style application (frontend + API routes) is sufficient.
- No second programming language "for completeness" — Python is explicitly excluded from the deployed system (see table above).
- No enterprise CMS — the admin dashboard described in Section 14 is purpose-built and simpler than a general CMS.

### 4.4 Credentials and third-party account integration — placeholders only
Several required services (Supabase, Razorpay, a shipping provider, the transactional email provider, analytics) belong to accounts that only the business owner can create and hold — the coding agent cannot sign up for these on the owner's behalf. This does **not** mean skipping the integration code:

- The coding agent **must still write the full, working integration code** for each of these services (API calls, webhook handlers, SDK usage, etc.) exactly as specified elsewhere in this document (Sections 11.3 payments, 11.4 shipping, 4.2 email, 21 analytics).
- Every credential, API key, secret, project URL, or account identifier must be read from **environment variables with clear, self-explanatory names** (e.g. `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `EMAIL_PROVIDER_API_KEY`), never hard-coded into source files.
- The coding agent must **never invent placeholder values that look like real keys** (e.g. never fabricate a string that resembles a real Razorpay or Supabase key) — use obviously-fake placeholder text (e.g. `your_razorpay_key_id_here`) in any example/template `.env` file, and leave the corresponding environment variable genuinely unset in the actual deployment until the owner supplies the real value.
- The project must include a template environment file (e.g. `.env.example`) listing every required variable with a one-line comment explaining what it is and where to obtain it, so the owner (or a developer helping them) can find and fill in every value without reading source code.
- **No secret key may ever be placed in frontend/client-side code** — only in server-side code or environment configuration that never ships to the browser. Where a service requires a public/client-safe key (e.g. a payment gateway's public checkout key) as distinct from a private secret key, the specification and the code must clearly distinguish the two.
- The application must be structured so that, once the owner supplies real credentials for a given service, that service becomes fully functional **without any code changes or redesign** — only configuration.

### 4.5 Image handling strategy
Because fabric photography will be the heaviest part of the site:
- Store original high-resolution images in object storage (R2/Supabase Storage).
- Serve them through an image-optimization layer (the chosen host's built-in image component, or a dedicated free-tier image CDN) that generates responsive sizes and modern formats (WebP/AVIF) on demand, rather than pre-generating every size manually.
- Product pages should lazy-load below-the-fold images and use a blurred/low-res placeholder while the full image loads.
- Every product photo needs, at minimum: a straight-on swatch shot in true daylight-balanced colour, a close-up texture shot, and (where available) a drape or garment shot.

---

## 5. Cost Model

### 5.1 Recurring infrastructure cost table (MVP, low traffic) [RESEARCH — verify at build time, prices change frequently]

| Service | Free tier covers | Cost at MVP scale | When it starts costing money |
|---|---|---|---|
| Hosting (Cloudflare Pages) | Unlimited bandwidth, 500 builds/month | ₹0 | Only if build-minute or Workers-request limits are exceeded, which is unlikely at this scale |
| Database (Supabase free) | 500 MB database, 50,000 monthly active users, 5 GB egress, up to 2 projects | ₹0 | If the database grows past 500 MB (unlikely for years for a single small shop's catalogue+orders) or the project is inactive 7+ days (free projects auto-pause) |
| Auth (Supabase Auth, bundled) | Included in the above | ₹0 | Same as database plan |
| File storage (R2/Supabase Storage) | Several GB free depending on provider | ₹0 initially | Once product photo library grows large; monitor and budget a few hundred ₹/month once past free allowance |
| Domain name | Not free | **~₹500–₹1,200/year** [RESEARCH, varies by registrar and TLD] | Unavoidable — a custom domain (e.g. goyaltextile.in or .com) is essential for a credible store and cannot be free |
| SSL/HTTPS | Included free by the host | ₹0 | Never — standard on all recommended hosts |
| Transactional email (Brevo free) | 300 emails/day | ₹0 | Once daily order+notification volume exceeds ~300/day |
| Payment gateway (Razorpay) | No setup fee, no AMC | **~2% + 18% GST per successful transaction** (~2.36% effective) | Always — this is a transaction-based cost, not an infrastructure cost, and is unavoidable for any online payment acceptance in India |
| Shipping | No free aggregator plan exists | **Per-shipment cost**, roughly ₹20–₹35+/500g on aggregators before COD/GST/zone surcharges, or courier-direct rates if self-managed | Unavoidable once orders actually ship; can start with a single local courier/self-delivery for very early orders to defer this cost |
| Analytics | Free tier (Cloudflare Web Analytics or GA4) | ₹0 | Effectively never, at this scale |

### 5.2 Bottom line
**Recurring technical/hosting cost target: ₹0/month.** The only truly unavoidable recurring cost is the **domain name** (~₹500–₹1,200/year). Every other line item above can realistically run at ₹0/month for a small shop's early traffic. **Per-order costs (payment gateway fee + shipping) are business costs, not hosting costs, and scale with revenue rather than existing as a fixed bill** — these should be priced into the product margin, not treated as "free vs paid infrastructure."

---

## 6. Sitemap

### 6.1 Customer-facing pages (MVP)
- Home
- Shop / Browse All Fabrics
- Category pages: Suiting, Shirting, Trouser/Pant Fabrics, Kurta Fabrics, Coat Fabrics, Gifting Fabrics
- Search results page
- Product detail page (per fabric)
- Cart
- Checkout (address → review → payment)
- Order confirmation
- Order tracking / order status (guest lookup by order ID + phone/email, and account order history for logged-in users)
- Account: login/register, profile, saved addresses, order history, wishlist
- About Us / Our Story (shop heritage, Chandni Chowk presence)
- Contact / Visit Our Store (address, phone, WhatsApp, map, hours)
- Shipping Policy
- Returns & Cancellations Policy
- Privacy Policy
- Terms of Service
- FAQ

### 6.2 Admin pages (MVP)
- Admin login (separate, protected)
- Dashboard (orders needing attention, low-stock thaans, recent sales)
- Products: list, create, edit, archive
- Categories: manage
- Inventory / Thaans: list, add new thaan, adjust remaining quantity, mark thaan sold out
- Orders: list, filter by status, order detail, update status
- Customers: list, customer detail (order history)
- Store settings (business info, shipping rules, policy text)

### 6.3 Optional / future pages (not MVP)
- Blog / styling inspiration
- Wholesale / B2B ordering portal
- Loyalty program page
- Corporate gifting landing page
- Reviews on product pages (as a dedicated moderated feature)

---

## 7. Navigation Structure

### 7.1 Desktop
- Top utility bar: store phone number / WhatsApp link, "Track Order," Account, Cart icon with item count.
- Primary nav bar: Home · Suiting · Shirting · Trouser/Pant · Kurta · Coat · Gifting · Shop All · Search bar.
- Footer: About, Contact, Shipping Policy, Returns Policy, Privacy Policy, Terms, social/WhatsApp links, store address.

### 7.2 Mobile (designed for mobile, not a squeezed desktop nav)
- Fixed bottom tab bar with 4–5 destinations: **Home, Shop (opens category picker), Search, Cart, Account.**
- Hamburger/slide-out menu from the top bar for secondary links (About, Contact, Policies, Track Order).
- Category browsing on mobile uses a full-screen or bottom-sheet category picker rather than a horizontal scroll of a desktop menu.
- Sticky "Add to Cart" bar on product pages so the primary action stays reachable while scrolling photos/description on a small screen.

---

## 8. Design & Aesthetic Direction

### 8.1 Brand feel
Premium, modern, sophisticated, polished — a serious textile brand, not a generic template and not an imitation of any specific existing brand. Apply Apple's Human Interface Guidelines as **principles, not a visual template** (Section 8.7) — clarity, strong visual hierarchy, generous whitespace, restrained and confident typography, consistency of spacing and components, thoughtful animation, and accessible contrast — while building Goyal Textile's own distinct identity around the ivory/white foundation and blue accent palette specified below (Section 8.3). **Do not make the site look like Apple's website** — no Apple typography, layout patterns, iconography, or colour system.

### 8.2 Research: what makes a website "look AI-generated," and how this spec avoids it
[RESEARCH] Design commentary through 2026 converges on a consistent list of "tells" that make a site feel like generic AI output, which this specification deliberately designs against:

- **Default/overused typography** (e.g. shipping with the framework's default font, uncustomized) — addressed in 8.4 by specifying a deliberate, non-default type pairing.
- **The purple-to-blue SaaS gradient**, used as a generic "looks professional" background — this spec uses blue/navy purposefully but as a **flat, confident accent colour and occasional subtle depth effect**, never a decorative purple-blue hero gradient copied from SaaS-landing-page conventions.
- **Uniform, undifferentiated spacing and radius** — identical border-radius and padding on every card/button regardless of context, which flattens hierarchy. This spec requires **intentional variation**: not every element should be a pill (see 8.5), and spacing should establish rhythm and grouping, not just fill space evenly.
- **Stock imagery and generic illustration** — irrelevant here, since the entire catalogue is real, specific fabric photography (Section 4.5) rather than stock or AI-generated imagery; this is one of the site's structural advantages against looking generic.
- **Motion that is either absent or a single generic fade-in on everything** — addressed by the differentiated, purpose-built animation table in 8.6 (hover states that respond, easing rather than snapping, no copy-pasted identical transitions on every element).
- **Nonstandard or novelty navigation patterns that confuse users**, and **nav items that fade/disappear on hover** — explicitly avoided; navigation (Section 7) must always remain visibly present and behave predictably.
- **Generic, interchangeable copywriting** — product and category copy must be specific to real Goyal Textile fabrics and their real attributes (Section 9.3), not generic marketing filler that could describe any fabric shop.

**Working principle for the coding/design agent:** modern techniques (glassmorphism, pill buttons, gradients, animation) are **not themselves the problem** — generic, uniform, purposeless *application* of them is. Every use of these techniques in this spec is scoped to a specific place and purpose (Section 8.5), not applied uniformly across every component.

### 8.3 Colour palette [OWNER-CONFIRMED direction, refined by this spec]
- **Base/background:** white, off-white, and subtle ivory tones — the dominant surface colour across the site, keeping the palette light, airy, and premium.
- **Primary accent:** blue, royal blue, and navy blue — used for primary calls-to-action (Add to Cart, Buy Now, Pay Now), links, active/selected states, and key brand moments (logo treatment, header accents).
- **Supporting neutrals:** charcoal/near-black for primary text (never pure black, for a softer premium feel), mid-greys for secondary text and borders.
- **Colour discipline:** product-card and gallery backgrounds must stay neutral (white/ivory) so that **fabric photography supplies the colour** — this matters more here than in most e-commerce categories because colour accuracy of the fabric itself is part of the purchase decision. The blue accent palette must never appear *behind* or *tinting* a product photo in a way that could distort perceived fabric colour.
- A single deeper navy can double as a "heritage" note (nodding to the business's Chandni Chowk roots) in restrained, non-decorative places — e.g. the footer or About page — without turning into a second competing accent colour.

### 8.4 Typography
- A clean, highly legible, **deliberately chosen (not default-framework) sans-serif** for body text and UI, paired with a slightly more distinctive display typeface for headings — enough personality to avoid the generic "Inter everywhere" AI-slop signal (Section 8.2), while staying restrained enough not to hurt readability.
- Clear, consistent type scale with real hierarchy (headings, subheadings, body, captions, labels) applied consistently across every page template, not invented ad hoc per page.

### 8.5 Where to use glassmorphism and pill buttons — intentionally, not everywhere
Per the owner's direction, glassmorphism and oversized rounded "pill" buttons are part of this site's visual identity, but must be used **selectively and purposefully**, not as the default style for every element:

- **Pill-shaped buttons:** reserve for primary, high-emphasis actions — "Add to Cart," "Buy Now," "Pay Now," category filter chips, and the main nav's active-state indicator. Secondary/tertiary actions (e.g. "View details," in-page text links, admin table row actions) should use simpler, less rounded or plain text-link styles, so the pill shape keeps its meaning as "this is a primary action" rather than becoming visual noise.
- **Glassmorphism (translucent, blurred-background panels):** reserve for a small number of deliberate moments where it reinforces premium feel and doesn't hurt legibility over photography — good candidates are the sticky header/nav bar (translucent over scrolling content), the mobile cart drawer/bottom sheet, and quick-view/modal overlays. **Never** apply glassmorphism to body text blocks, product descriptions, or anywhere reading comprehension matters, since blurred/translucent backgrounds reduce contrast and readability.
- Both techniques must maintain WCAG AA contrast (Section 8.8) in their final applied context, not just in isolation — verify contrast against whatever is actually behind a glass panel.

### 8.6 Animation & micro-interaction guidance
| Interaction | Feel | Approx. timing |
|---|---|---|
| Product card hover/tap (image swap or subtle zoom) | Smooth, physical | 150–250ms ease |
| Add to cart | Small confirmation motion (item "flies" toward cart icon or the cart icon briefly pulses/badges) | 200–400ms |
| Quantity stepper change | Instant numeric update, subtotal recalculates with a brief highlight flash | <150ms |
| Filtering/sorting results | Cross-fade of the grid, not a jarring reload | 200–300ms |
| Image gallery / zoom | Smooth crossfade between thumbnails; pinch-to-zoom or tap-to-zoom on the main image | 200ms |
| Navigation / route transitions | Subtle fade or slide, never a blank white flash | 150–250ms |
| Modals (cart drawer, quick view, glass panels) | Slide-in from the relevant edge with a soft backdrop fade | 250–350ms |
| Checkout step progression | Clear animated step indicator advancing left-to-right | 200ms |
| Loading states | Skeleton screens matching final layout, not spinners, for product grids and product pages | n/a |
| Order success | One clear, warm confirmation moment (checkmark or similar), not overdone | 400–600ms once |

Every hover/press state must give real, responsive feedback (no dead buttons, no elements that fade away on hover — a known anti-pattern), and easing must feel physical (ease-in-out style curves), never an abrupt snap. All animation must respect the OS-level "reduce motion" accessibility setting — when present, replace motion with instant state changes, no exceptions.

### 8.7 Apple Human Interface Guidelines — principles applied, not visuals copied
Apple's current design guidance is used here as a set of **principles**, explicitly not as a visual template:
- **Purpose & clarity:** every screen has one primary action, made visually obvious (per 8.5's pill-button discipline).
- **Hierarchy:** size, weight, and colour consistently indicate importance — never rely on colour alone (also an accessibility requirement, 8.8).
- **Consistency:** the same component (button, card, form field) looks and behaves the same everywhere it appears.
- **Feedback:** every user action gets an immediate, appropriate visual response (button press states, loading states, success/error confirmation).
- **Simplicity & familiarity:** use conventional, well-understood e-commerce patterns (Section 8.9) rather than novel interactions that require learning.
- **Craft & attention to detail:** consistent spacing rhythm, properly aligned grids, no rough edges — the details that separate "polished" from "AI slop" per 8.2.
- **Accessibility & flexibility:** designed to work across devices, input methods, and assistive technology from the outset (Section 8.8), not retrofitted.
None of this should visually resemble Apple's own site or products — it governs *how the design decisions are made*, not *what the design looks like*.

### 8.8 Responsive requirements — mobile and desktop as equally first-class
Mobile (both iPhone/Safari and Android/Chrome) and desktop must both be treated as **primary, first-class experiences** — this is not a "mobile-friendly desktop site," and it is not a "mobile-only" build either. Every workflow (browsing, product detail, quantity selection, cart, checkout, account, admin where relevant) must be independently designed and tested for both.
- Minimum touch target size ~44×44px for all interactive controls (quantity steppers, filter chips, pill buttons, nav icons) — verified on real iOS and Android viewport sizes, not just a browser resize.
- Product image galleries: swipeable with native-feeling momentum on mobile (iOS and Android gesture conventions both respected), thumbnail rail + large image with hover-zoom on desktop.
- Forms (checkout, account) must use correct mobile input types (numeric keypad for phone/PIN code, email keyboard for email, decimal keypad for quantity-in-metres) and support autofill on both iOS and Android.
- Respect safe-area insets on notched/Dynamic-Island iPhones and gesture-nav Android devices for any fixed bottom navigation or sticky "Add to Cart" bar.
- Desktop should not simply be a stretched mobile layout — it should use the extra space for richer product-grid density, larger imagery, and persistent (non-drawer) navigation, while mobile uses the patterns in Section 7.2.
- Tables (e.g. admin order lists) must degrade to stacked cards on narrow screens rather than shrinking columns unreadably.
- Cross-browser/cross-device verification required before launch: iOS Safari, Android Chrome, desktop Chrome/Edge, and desktop Safari/Firefox at minimum, since iOS Safari in particular renders some CSS differently from other browsers.

### 8.9 Accessibility requirements
- Colour contrast meeting WCAG AA at minimum for all text and interactive elements, including text/icons placed over glassmorphism panels or the blue accent colour — checked against the actual final palette, not assumed.
- Full keyboard navigability for browsing, filtering, cart, and checkout; visible focus states on every interactive element, including pill buttons and glass-panel controls.
- Semantic HTML heading hierarchy (one H1 per page, logically nested headings) for screen-reader and SEO benefit alike.
- All meaningful images (product photos) need descriptive alt text (e.g. "Navy blue pinstripe wool-blend suiting fabric, close-up"); purely decorative images get empty alt attributes.
- Error messages must be programmatically associated with their form fields (not colour-only) and announced to assistive tech.
- Non-colour indicators for state (e.g. "Out of Stock" as a text label/badge, not only a greyed-out swatch).

### 8.10 Conventional e-commerce UX patterns to follow [RESEARCH]
Aesthetics must always support usability and conversion, not compete with it:
- Product pages should let a shopper reach a purchase decision within seconds: a strong hero image with zoom, price stated clearly and honestly (no fake struck-through prices), and key specs scannable rather than buried in dense paragraphs.
- Show shipping cost/estimate as early as possible (ideally on the product or cart page), since unexpected costs revealed only at final checkout are a leading cause of abandonment.
- Checkout should minimize form fields, combine shipping and contact info onto as few steps as reasonably possible, show a persistent order summary, and display a simple progress indicator ("Step 1 of 3") — all consistent with the checkout flow already specified in Section 11.2.
- Trust signals belong near the point of highest anxiety (payment step): security/SSL indication, recognizable payment method icons, and a visible link to the returns/shipping policy — not only buried in the footer.
- Guest checkout must remain the default, unobstructed path (already specified in 11.2.1) — forcing account creation is a well-documented conversion killer.

### 8.4 Animation & micro-interaction guidance
| Interaction | Feel | Approx. timing |
|---|---|---|
| Product card hover/tap (image swap or subtle zoom) | Smooth, physical | 150–250ms ease |
| Add to cart | Small confirmation motion (item "flies" toward cart icon or the cart icon briefly pulses/badges) | 200–400ms |
| Quantity stepper change | Instant numeric update, subtotal recalculates with a brief highlight flash | <150ms |
| Filtering/sorting results | Cross-fade of the grid, not a jarring reload | 200–300ms |
| Image gallery / zoom | Smooth crossfade between thumbnails; pinch-to-zoom or click-to-zoom on the main image | 200ms |
| Navigation / route transitions | Subtle fade or slide, never a blank white flash | 150–250ms |
| Modals (cart drawer, quick view) | Slide-in from the relevant edge with a soft backdrop fade | 250–350ms |
| Checkout step progression | Clear animated step indicator advancing left-to-right | 200ms |
| Loading states | Skeleton screens matching final layout, not spinners, for product grids and product pages | n/a |
| Order success | One clear, warm confirmation moment (checkmark or similar), not overdone | 400–600ms once |

All animation must respect the OS-level "reduce motion" accessibility setting — when present, replace motion with instant state changes, no exceptions.

### 8.5 Responsive requirements
- Design mobile-first; a large share of Indian e-commerce traffic is mobile.
- Minimum touch target size ~44×44px for all interactive controls (quantity steppers, filter chips, nav icons).
- Product image galleries: swipeable on mobile, thumbnail rail + large image on desktop.
- Forms (checkout, account) must use appropriate mobile input types (numeric keypad for phone/PIN code, email keyboard for email) and avoid horizontal scrolling at any breakpoint.
- Tables (e.g. admin order lists) must degrade to stacked cards on narrow screens rather than shrinking columns unreadably.
- Respect safe-area insets on notched devices for fixed bottom navigation.

### 8.6 Accessibility requirements
- Colour contrast meeting WCAG AA at minimum for all text and interactive elements, checked against the actual chosen palette (not assumed).
- Full keyboard navigability for browsing, filtering, cart, and checkout; visible focus states on every interactive element.
- Semantic HTML heading hierarchy (one H1 per page, logically nested headings) for screen-reader and SEO benefit alike.
- All meaningful images (product photos) need descriptive alt text (e.g. "Navy blue pinstripe wool-blend suiting fabric, close-up"); purely decorative images get empty alt attributes.
- Error messages must be programmatically associated with their form fields (not colour-only) and announced to assistive tech.
- Non-colour indicators for state (e.g. "Out of Stock" as a text label/badge, not only a greyed-out swatch).

---

## 9. Product Catalogue

### 9.1 Navigation categories (primary, top-level) [drawn from the owner's brief + market research]
Suiting · Shirting · Trouser/Pant Fabrics · Kurta Fabrics · Coat Fabrics · Gifting Fabrics

### 9.2 Secondary structure
- **Fibre/fabric type** (cotton, linen, wool, silk, blends, etc.) — filter, not top-level nav, since the same fibre appears across multiple end-use categories.
- **Colour** — filter (swatch-style colour picker).
- **Pattern** (solid, striped, checked, printed, textured) — filter.
- **Price per metre range** — filter.
- **Season** (all-season, summer/lightweight, winter/heavy) — filter.
- A product can belong to more than one end-use category where genuinely applicable (e.g. a fabric suitable for both shirting and kurta) — this must be supported by the data model (Section 13), not forced into a single category.

### 9.3 Product attributes

**Essential (every product must have these to be published):**
- Product name
- Category/categories (end-use)
- Price per metre (₹)
- Available quantity (in metres, derived from linked thaans — see Section 10)
- At least one photograph
- Short description

**Important, strongly recommended:**
- Colour (as a filterable value, plus a free-text description like "Navy with fine grey pinstripe")
- Pattern
- Fabric type / fibre
- Suitable use (which garment types it's good for — may differ slightly from its category, e.g. a shirting fabric that also works for light kurtas)
- Full description (feel, drape, care notes)
- 3+ photographs including a texture close-up

**Optional (include only when the business actually has this information — do not invent it):**
- Composition (e.g. "80% cotton, 20% linen") — only if known/labelled
- Width (in inches/cm)
- Weight/GSM
- Finish (e.g. matte, sheen, brushed)
- Season suitability
- Country/mill of origin, if the business wants to highlight it

**Explicit instruction to the coding agent:** the product data model must support all of the above fields as *optional* except the essential set. Never auto-generate or guess composition, width, weight, or origin for a real product — leave the field empty until the business supplies it, and the product UI should simply omit a spec row rather than show a placeholder value.

---

## 10. Quantity Selection & Inventory (the core of this business)

### 10.1 Customer-facing quantity selection
- Every product page shows **price per metre** as the primary price, with a live-updating **total price** as the customer adjusts quantity.
- Quantity input: a stepper/number field, not a fixed dropdown of preset sizes (since needs vary continuously), with:
  - **Minimum quantity** — a per-product or store-wide default (e.g. 1 metre) [NEEDS OWNER CONFIRMATION for exact minimum, especially since some garments need more than 1m].
  - **Increment** — support decimal quantities (e.g. 0.5m or 0.25m steps) since fabric is commonly bought in fractional metres in Indian retail [NEEDS OWNER CONFIRMATION on what increment the business actually cuts at — many shops cut to the nearest 0.25m or 0.5m].
  - **Maximum quantity** — capped at whatever is actually available from the linked thaan(s), enforced live (see 10.3).
- Helper text near the quantity field translating metres into practical guidance, e.g. "Approx. 1.5–2m needed for a shirt, 4.5–5m for a 2-piece suit" — sourced from the business's real experience, not invented estimates; use rough placeholder ranges only until the owner confirms typical requirements per garment type.
- "Add to Cart" is disabled (with a clear reason) if the selected quantity exceeds available stock, is below the minimum, or doesn't match the required increment.

### 10.2 Inventory model — thaans as stock lots
- A **Thaan** (inventory lot) is the real-world unit of stock: a single roll of a specific fabric with a specific total length.
- **One product can be backed by one or more thaans** (e.g. the same fabric restocked over time as separate rolls) — the product's "available quantity" shown to customers is the **sum of remaining length across all active thaans linked to that product**.
- **One thaan is linked to exactly one product** (a thaan is a specific fabric; if a new, slightly different dye lot arrives, it should typically become a new product or be flagged as a new lot with a shade note, not silently merged into the old thaan's stock — this protects against colour-mismatch complaints on cut fabric).
- Each thaan record tracks: originating length, remaining length, date received, and (optionally) a cost basis and an internal lot/batch reference for staff.
- Every order line item, upon confirmed payment, **decrements the remaining length of the specific thaan(s) it was cut from**. If a product's stock spans multiple thaans, the system should deduct from the oldest/lowest-remaining thaan first (first-in-first-out) so partial thaans get used up rather than left as unsellable remnants — flagged as a recommended default, adjustable by the business.
- **Manual adjustment:** staff must be able to correct a thaan's remaining length directly in the admin (e.g. after a walk-in customer buys from the same physical roll, or after a stocktake correction), since online and offline sales draw from the same physical inventory.
- **Low-stock / sold-out handling:** when a thaan's remaining length drops below a configurable threshold, the admin dashboard flags it; when a product's total remaining length across all its thaans reaches zero, the product is automatically marked "Out of Stock" on the storefront (not deleted — history and reordering should remain possible).

### 10.3 Concurrency — preventing overselling
Because the same physical fabric might be sold in-store and online at the same time, and because two online customers could add the last few metres of the same thaan to their carts simultaneously:
- Stock is **not permanently reserved** just by being in a cart — a cart hold is soft and temporary.
- At the moment of **checkout submission** (before payment is initiated), the system must re-validate that the requested quantity is still available and place a **short-lived reservation** (e.g. 10–15 minutes) on that quantity so it isn't sold to someone else mid-payment.
- If payment fails or the reservation window expires, the held quantity is released back to available stock automatically.
- If two checkouts race for the same last few metres, whichever reserves first wins; the second is shown a clear "only X metres left, please adjust your quantity" message rather than being allowed to overbuy.
- Since offline, in-shop sales also consume the same thaans, staff need a fast, simple **manual "deduct stock" action** in the admin for walk-in sales, so the online available quantity stays accurate.

### 10.4 Custom cuts
Every online order is, by nature, a custom cut (an arbitrary length chosen by the customer) — so there is no separate "custom cut" product type; the standard quantity-selection flow described above **is** the custom-cut flow. This has direct implications for returns (Section 11.4).

---

## 11. Cart, Checkout, Payments, Shipping, Returns

### 11.1 Cart
- Persistent across a session (and ideally across visits for logged-in users — store cart contents server-side per account; for guests, persist client-side for the session at minimum).
- Each cart line shows: product photo, name, key attributes (colour/pattern), selected quantity (editable inline), unit price per metre, line subtotal, and a remove control.
- Cart summary shows: subtotal, shipping (once determinable — see 11.3), any discount, and grand total.
- **Stock re-validation on cart view and again on checkout entry** — if a previously-added quantity is no longer fully available (e.g. someone else bought part of the last thaan), the customer is shown a clear inline warning and the option to adjust quantity before proceeding.
- Empty-cart state should still be useful (link back to Shop/categories), not a dead end.

### 11.2 Checkout
Flow: **Cart → Address & Contact → Order Review → Payment → Confirmation.**
- Fields collected: full name, phone number, email (recommended but decide with owner whether to require or make optional — see 11.2.1), complete shipping address (address line(s), landmark optional, city, state, PIN code), and order notes (optional, e.g. delivery instructions).
- **PIN code-based validation** to catch obviously invalid addresses and, if feasible, to auto-suggest city/state.
- Validation rules: required-field checks, phone number format validation (10-digit Indian mobile pattern), PIN code format validation (6 digits), inline error messages tied to the specific field.
- Order review step shows the full cart, address, and total **before** payment is triggered, so the customer can go back and correct anything.
- On successful payment, an order is created in a "Confirmed" state (not before — see order lifecycle in Section 13) and a confirmation page + confirmation email/SMS is sent.
- On failed/abandoned payment, no order is created; the cart is preserved so the customer can retry.

#### 11.2.1 Guest checkout vs. account
- **Recommended: guest checkout is allowed and is the default path** — do not force account creation before purchase, since that is a well-documented conversion killer, especially for a first-time online buyer base.
- After a guest completes an order, offer an easy "create an account to track this and future orders" option (pre-filled from the order they just placed) rather than requiring it upfront.
- Logged-in accounts add genuine value: saved addresses (useful for repeat business customers, e.g. tailors who order regularly), full order history in one place, and a wishlist for fabrics someone is considering. Only build what provides real value — skip anything that exists just because "most e-commerce sites have it" (e.g. no need for social-login complexity at MVP stage; a simple email/phone + password or OTP login is enough).

### 11.3 Payments
- **Recommended gateway: Razorpay.** [RESEARCH] Standard Indian domestic pricing is a flat ~2% + 18% GST per successful transaction (~2.36% effective), with no setup fee and no annual maintenance charge on the standard plan — the most cost-predictable option for a low-volume small business, since fee-free-but-AMC-charging competitors only become cheaper at higher monthly volumes than this business is likely to see initially.
- Support the payment methods Razorpay exposes: UPI, cards, netbanking, and popular wallets. **Cash on Delivery (COD)** should be evaluated separately as a fulfilment/trust decision, not a "payment gateway" feature — flagged for owner decision in Section 15, since COD affects cash handling and courier partner choice.
- **Never store raw card details** anywhere in this system — payment gateway tokenization/hosted checkout handles that; the application only ever stores a payment reference/status returned by the gateway.
- **Order-payment relationship:** an order should only move to a "Confirmed"/paid state once the gateway confirms successful payment via its callback/webhook — never trust a client-side "payment succeeded" signal alone, since that can be spoofed; the server must independently verify payment status with the gateway.
- **Payment failure:** if payment fails, the order stays unconfirmed (or is not created at all — see 13), the customer sees a clear retry option, and the reserved stock (10.3) is released after the short hold window.
- **Duplicate payment prevention:** generate a unique order/payment reference per checkout attempt and check it server-side before creating a second order, so a customer double-clicking "Pay" or retrying after a slow network response can't be charged twice or create duplicate orders for the same cart.

### 11.4 Shipping
- **MVP approach: manual shipping**, using a single local courier or India Post/self-delivery for nearby Delhi orders, since no Indian shipping aggregator offers a free plan and per-shipment aggregator costs only make sense once volume justifies the integration effort. [RESEARCH]
- **Roadmap approach:** integrate a shipping aggregator (e.g. Shiprocket or a comparable competitor) once order volume justifies it, for automated rate calculation, label generation, and tracking-number sync back into the order.
- Checkout must still collect everything a future aggregator integration would need (full structured address, PIN code) even while shipping is handled manually, so no rework is needed later.
- Required order statuses for fulfilment (see Section 13 for the full lifecycle) must include, at minimum, a "Packed" and "Shipped" state with an optional tracking number/link field the admin can fill in per order.

### 11.5 Returns & cancellations — this needs owner decisions, not invented policy
Because most online orders are custom-cut lengths, this business cannot simply apply a generic "30-day no-questions return" policy the way a ready-made-garment store can — a length already cut for one customer is very hard to resell as fresh stock. Research into comparable fabric businesses shows this is a genuinely unsettled area industry-wide, handled differently shop to shop. **This spec deliberately does not invent a final policy.** The coding agent should build the *system* (order states, policy display, cancellation windows) to be configurable, and the website must display **clearly marked placeholder policy text** until the owner confirms real answers to:

- [NEEDS OWNER CONFIRMATION] Can a **cancellation** happen at all after payment, and if so, up to what order status (e.g. only before the fabric is actually cut/"Processing")?
- [NEEDS OWNER CONFIRMATION] Are **returns** accepted for a genuine **quality defect or wrong item shipped** (strongly recommended to say yes, since this protects customer trust and is standard consumer expectation), even though ordinary "changed my mind" returns on cut fabric are not?
- [NEEDS OWNER CONFIRMATION] Is a **refund, store credit, or exchange** offered when a return is accepted, and within what timeframe?
- [NEEDS OWNER CONFIRMATION] Who bears **return shipping cost** in a defect case?
- [NEEDS OWNER CONFIRMATION] Is there any cooling-off window (e.g. "cancel within 1 hour of ordering, before we start cutting") that IS offered, distinct from returns after fulfilment?

The policy pages (Section 6.1) and checkout order-review step must both link to and display whatever final policy the owner confirms — this is a legal/trust surface, not just a footer link, so it needs to be visible before the customer pays, not only after.

---

## 12. Accounts, Wishlist, Order Tracking

- **Guest order tracking:** allow anyone to look up an order's status via order ID + phone number or email, without requiring login — important since guest checkout is the default path.
- **Account order history:** logged-in customers see all past orders with status, items, and totals.
- **Saved addresses:** logged-in customers can save one or more delivery addresses for faster repeat checkout.
- **Wishlist:** logged-in customers can save fabrics they're considering, viewable later, with an easy "move to cart" action. Skip anything more elaborate (e.g. shareable wishlists) at MVP stage.
- **Profile:** name, phone, email, password/OTP management.

---

## 13. Order Lifecycle

| Status | Meaning | Customer sees | Admin can do |
|---|---|---|---|
| **Pending Payment** | Checkout submitted, payment not yet confirmed by the gateway | "Awaiting payment confirmation" | Nothing yet — system-only transient state |
| **Payment Failed** | Gateway reported failure or the reservation window expired | Clear retry-payment option; stock released | View for records; no fulfilment action |
| **Confirmed** | Payment verified by the gateway (server-side) | "Order confirmed" | Begin processing; this is the first real, actionable order state |
| **Processing** | Staff have started preparing the order | "We're preparing your order" | Move to Fabric Being Cut / Packed |
| **Fabric Being Cut** | Optional finer-grained status specific to this business, shown between Processing and Packed | "Your fabric is being cut to your specified length" | Mark as Packed once done |
| **Packed** | Ready for dispatch | "Packed, ready to ship" | Attach courier/tracking info, mark Shipped |
| **Shipped** | Handed to courier | Tracking number/link, expected delivery | Mark Delivered on confirmation, or handle Failed Delivery |
| **Delivered** | Courier confirms delivery (or admin marks manually if courier lacks tracking webhooks) | "Delivered" | Order effectively complete; can still process a return per policy |
| **Cancelled** | Cancelled per the confirmed cancellation policy window | "Order cancelled," refund status if applicable | Trigger refund via gateway where applicable |
| **Returned** | A return was accepted per policy and processed | Refund/exchange/credit status | Mark refund complete, restock if the returned length is genuinely resellable |

Every status change should be timestamped and visible to the customer on their order-tracking page, and should trigger the relevant transactional notification (email/SMS) where appropriate (Confirmed, Shipped, Delivered, Cancelled at minimum).

---

## 14. Admin Dashboard

### 14.1 Principle
Must be usable by a small business owner or family member with no technical background — plain language, clear confirmation dialogs before destructive actions (e.g. deleting a product, cancelling an order), and no exposed technical jargon.

### 14.2 Required admin capabilities
- **Dashboard home:** orders needing attention (new/unprocessed), low-stock thaans, a simple recent-sales summary.
- **Products:** create/edit/archive; manage all attributes from Section 9.3; manage multiple photos per product (reorder, set primary image); link a product to one or more thaans.
- **Categories:** create/rename/reorder the end-use categories and any filter taxonomies (colour, pattern, fabric type).
- **Inventory / Thaans:** add a new thaan (with starting length, linked product, date received); adjust remaining length manually; mark a thaan discontinued/sold out; view stock history/audit trail for a thaan.
- **Orders:** list with filters by status/date; order detail view (items, quantities, customer, address, payment status); status update actions matching Section 13; ability to add a tracking number/courier reference.
- **Customers:** list of registered customers; view a customer's order history.
- **Store settings:** business name/address/phone/hours displayed on the site, shipping rule configuration, policy page text (so policy changes don't require a code deployment), and payment gateway keys (stored securely as secrets, never shown in plaintext in the UI after entry).

### 14.3 Permissions & safeguards
- Admin area is fully separate from the customer-facing authentication and must require its own login, not reachable via the same session as a customer account.
- Destructive actions (deleting a product with order history, cancelling a confirmed order) require an explicit confirmation step and, ideally, are soft-deletes/archives rather than permanent deletion, to preserve order history integrity.
- For MVP, a single admin role is sufficient (this is a small family business); the data model should not preclude adding staff-level roles with narrower permissions later (e.g. a "packing staff" role that can update order status but not edit prices).

---

## 15. Information Still Needed From the Business Owner Before Launch

This is the master checklist of real-world facts and assets that must be supplied by Goyal Textile before the site can go live with accurate content. **None of these should be invented, guessed, or filled in from the conflicting external listings in Section 1.2.**

**Business identity & contact**
- Verified legal/trading business name (exact spelling)
- Correct physical shop address (shop number, street, market, PIN code)
- Public contact phone number
- WhatsApp number for customer support (may be the same or different from the phone number)
- Public email address
- Shop opening hours (including any weekly closing day, common in Chandni Chowk markets)
- GST registration number, if applicable, for display/compliance and for gateway KYC

**Brand assets**
- Logo (vector/high-res)
- Any existing brand colours, if the family has a preference beyond this document's Section 8.3 starting point
- Any existing shop photography (storefront, interior) for the About/Contact pages

**Catalogue data**
- Full initial product list: names, categories, per-metre prices, and any known specifications from Section 9.3
- Product photography for each fabric (see Section 4.5 for the required shot types)
- Initial thaan inventory: which fabrics, how many thaans each, and the remaining length in each

**Business rules**
- Minimum order quantity and cutting increment (Section 10.1) — e.g. does the shop cut to the nearest 0.25m, 0.5m, or whole metre?
- Typical fabric requirement per garment type (shirt/kurta/trouser/suit/coat), to power the helper text in 10.1
- Shipping rule preferences — flat rate, free-above-threshold, or distance/weight based; which regions are served initially
- Whether Cash on Delivery will be offered
- Return/cancellation policy answers (Section 11.5)
- Any planned discounts, coupon codes, or gifting-specific rules (e.g. gift wrapping, gift messages) for the Gifting Fabrics category

**Payments & tax**
- PAN and bank account details required for Razorpay (or chosen gateway) merchant KYC — collected directly through the gateway's onboarding, never stored by this application
- Applicable GST rate(s) on the products sold, for correct tax display at checkout

---

## 16. MVP Definition

### 16.1 MVP must include
- Full product catalogue with categories, filters, and search
- Product detail pages with quantity-in-metres selection and live price calculation
- Thaan-backed inventory with stock validation and the concurrency-safe reservation described in 10.3
- Cart with edit/remove and live totals
- Complete checkout: address collection, order review, Razorpay payment integration, order confirmation
- Guest checkout plus optional account creation, login, order history, saved address, wishlist
- Order tracking (guest lookup + account history) reflecting the full order-status lifecycle
- Transactional emails for order confirmation, shipped, delivered, cancelled
- Full admin dashboard: products, categories, thaans/inventory, orders, customers, store settings
- All policy pages (with clearly marked placeholder text where owner decisions are pending)
- Mobile-first responsive design across the full flow
- Manual shipping (no aggregator integration required at MVP)
- Free-tier analytics instrumentation for the events in Section 20

### 16.2 Explicitly deferred to the roadmap (Section 17), not MVP
- Fabric swatch/sample ordering
- Customer reviews
- Shipping aggregator integration
- Wholesale/B2B ordering
- Loyalty program
- Personalised recommendations

---

## 17. Future Roadmap (post-MVP)

- **Fabric swatch/sample ordering** — let a customer order a small physical sample before committing to a full cut; reduces return-related friction given the cut-fabric return constraints in 11.5.
- **Customer reviews**, moderated, tied to verified purchases.
- **Shipping aggregator integration** (Section 11.4) once volume justifies the cost.
- **B2B/wholesale ordering** — a separate pricing tier/portal for tailors or repeat bulk buyers, distinct from retail per-metre pricing.
- **Corporate gifting** — a dedicated flow for bulk gifting orders (building on the existing Gifting Fabrics category).
- **Tailoring partnerships** — optionally connect fabric purchase with a recommended tailoring service.
- **Multiple locations** — if the business ever opens a second outlet, inventory model would need a location dimension.
- **Advanced inventory** (barcode/QR per thaan, mill-level sourcing tracking).
- **International shipping**, if demand emerges from the diaspora market for Indian fabric.
- **Loyalty/repeat-customer rewards.**

---

## 18. Database Design (Conceptual — no SQL)

### 18.1 Core entities and key fields

**Category**
- id, name, slug, description, display order, parent category (nullable, to allow simple sub-categorisation if ever needed)

**Product**
- id, name, slug, description (short + long), price per metre, status (draft/published/archived), created/updated timestamps
- Attribute fields as listed in Section 9.3, all nullable except the essential set
- Relationship: many-to-many with Category (a product can appear in more than one end-use category)
- Relationship: one-to-many with ProductImage
- Relationship: one-to-many with Thaan (a product's total available quantity is derived, not stored directly, from the sum of its active thaans' remaining length)

**ProductImage**
- id, product id, image URL/storage key, display order, is-primary flag, alt text

**Thaan (inventory lot)**
- id, product id, original length (metres), remaining length (metres), date received, internal batch/lot reference (optional), cost basis (optional, admin-only), status (active/sold out/discontinued)

**InventoryAdjustment (audit trail)**
- id, thaan id, adjustment amount (+/-), reason (e.g. "online order," "in-store sale," "stocktake correction," "damage"), staff user, timestamp — every change to a thaan's remaining length should be traceable

**Customer**
- id, name, phone, email (optional depending on owner decision), password/auth reference, created timestamp
- Relationship: one-to-many with Address, one-to-many with Order, one-to-many with WishlistItem

**Address**
- id, customer id (nullable for guest orders — see Order below), name, phone, address lines, city, state, PIN code, is-default flag

**Cart**
- id, customer id (nullable for guest/session-based carts), session identifier (for guests), created/updated timestamp

**CartItem**
- id, cart id, product id, thaan allocation is NOT decided at cart stage (only at checkout reservation — see 10.3), quantity (metres, decimal-capable), price per metre snapshot (so price changes don't silently alter an existing cart)

**Order**
- id, order reference/number (customer-facing, human-readable), customer id (nullable for guest — store guest name/phone/email directly on the order in that case), shipping address (snapshotted at order time, not just a reference, so later address edits don't rewrite history), status (per Section 13), subtotal, shipping charge, discount (if any), total, created/updated timestamps, cancellation/return reason (nullable), tracking number/courier reference (nullable)

**OrderItem**
- id, order id, product id, quantity (metres), price per metre at time of order (snapshot), line subtotal, thaan(s) the quantity was deducted from (for traceability back to physical stock)

**Payment**
- id, order id, gateway (e.g. Razorpay), gateway payment reference, amount, status (initiated/succeeded/failed/refunded), raw gateway response reference (for audit/debugging), timestamps

**WishlistItem**
- id, customer id, product id, added timestamp

**Discount** (optional, only if the business wants coupon codes — otherwise omit entirely from MVP)
- id, code, type (percentage/flat), value, valid from/to, usage limit, minimum order value

**AdminUser**
- id, name, email, password/auth reference, role (single "admin" role sufficient for MVP, extensible later)

**StoreSetting**
- key-value style configuration for business info, shipping rules, and policy text, editable from the admin without a code deployment

### 18.2 Key constraints and business logic to encode
- A Product's displayed "available quantity" is always computed as the live sum of remaining length across its active Thaans — never a separately stored, independently-editable number, to avoid the two going out of sync.
- An Order's shipping address, item prices, and quantities must be **snapshotted at order creation**, not live-referenced to Product/Address records, so that later catalogue or address changes never rewrite historical orders.
- A CartItem's quantity must always respect its product's minimum/increment/maximum rules at both add-time and at checkout re-validation time (10.3).
- Every InventoryAdjustment must reference a reason and (where applicable) the order that triggered it, so stock discrepancies are always explainable.
- A Payment record's status is the only source of truth for whether an Order is allowed to move to "Confirmed" — never infer payment success purely from client-side navigation reaching a "success" page.

---

## 19. Edge Cases & Failure States

| Scenario | Required behaviour |
|---|---|
| Customer requests more metres than currently available | Add-to-cart blocked with a clear "only X metres available" message; suggest the maximum available quantity |
| Two customers try to buy the last remaining length of the same thaan simultaneously | Checkout-time reservation (10.3) ensures only one succeeds; the second sees a live "no longer available at that quantity" message before paying |
| Payment fails after checkout submission | No order confirmed; reserved stock released after the hold window; customer can retry with the same cart intact |
| Customer double-submits payment (slow network, double click) | Idempotency check on the order/payment reference prevents duplicate orders or double charges |
| Cart contains a product that was archived/deleted since it was added | Clearly flag the affected line item at cart/checkout, remove it from the orderable total, let the customer proceed with the rest |
| Admin corrects a thaan's remaining length while orders are in flight | The correction is the new source of truth; any in-progress reservations still honour their hold, but new orders see the corrected figure |
| Customer enters an invalid or non-serviceable PIN code | Inline validation error at checkout before payment is attempted, not after |
| Server error during checkout | No partial order/payment state left behind; customer sees a clear retry message; nothing is charged without a fully confirmed order |
| Network interruption right after payment but before the confirmation page loads | Server-side webhook/callback from the gateway is the source of truth for order status, independent of whether the customer's browser successfully loaded the confirmation page — so a "did I get charged?" situation resolves correctly via order lookup, not by re-attempting payment blindly |
| Product deleted/archived after being ordered historically | Historical OrderItem data (Section 18.1) remains intact via snapshotted fields, even though the live Product record is gone or archived |
| Failed delivery / customer unreachable | A distinct handling path from "Delivered" — admin can log a failed-delivery attempt and decide next steps (reattempt, hold for pickup, cancel per policy) |
| Authentication failure (wrong password, expired session) | Clear, non-revealing error messaging (don't confirm/deny whether an email/phone is registered, for basic account-enumeration protection) |

---

## 20. SEO Requirements

- **Descriptive, human-readable URLs** for categories and products (e.g. `/shirting/navy-pinstripe-cotton-shirting` rather than an opaque ID-only URL).
- **Per-page metadata:** unique title and meta description for every category and product page, generated from real product data, not duplicated boilerplate.
- **Structured data (schema.org):** Product markup (name, image, price, availability) on product pages so search engines and rich results can surface price/stock; Organization/LocalBusiness markup on the About/Contact page for local search.
- **Image SEO:** meaningful, descriptive filenames and alt text (tied to the same descriptive attributes used in the UI — Section 9.3), not generic "image1.jpg."
- **XML sitemap**, auto-generated and kept in sync as products are added/archived, submitted to Search Console.
- **robots.txt** configured to allow indexing of storefront pages while disallowing admin routes, cart, and checkout pages (no reason to index a personal cart URL).
- **Local SEO:** the About/Contact page should clearly and correctly state the shop's Chandni Chowk location (once confirmed per Section 15) since "fabric shop Chandni Chowk" / "suiting fabric Chandni Chowk" style searches are a realistic, valuable local-intent channel for this business.
- **Fabric-related search terms:** category and product copy should naturally include the terms real customers search (e.g. "suiting fabric online," "shirting fabric by the metre," "kurta fabric India") without keyword-stuffing.

---

## 21. Analytics Requirements

- **Recommended approach:** a free web analytics tool — either the hosting provider's bundled free analytics (e.g. Cloudflare Web Analytics if hosting on Cloudflare Pages) for a lightweight, privacy-respecting option, or Google Analytics 4 (also free) for more detailed e-commerce reporting. Either is ₹0 at this business's scale; pick one and instrument it consistently rather than running both.
- **Events to track, at minimum:**
  - Product view (which product, which category)
  - Category page view
  - Search queries performed (and whether they returned results)
  - Add-to-cart events (product, quantity)
  - Checkout started
  - Checkout step completions (address entered, payment initiated)
  - Purchase completed (order value, items)
  - Cart abandonment (cart created but no purchase within a reasonable window)
  - Most-viewed / best-selling products (derivable from the above events plus order data)
  - Revenue over time
- These events double as the raw material for a simple "abandoned cart" follow-up email in the future roadmap, even though building that automation is not MVP.

---

## 22. Security Requirements

- **Authentication:** handled by the managed auth provider (Section 4.2) rather than a hand-rolled password system; passwords never stored or logged in plaintext.
- **Authorisation:** strict separation between customer-level and admin-level access; every admin API route must independently verify admin identity server-side, never trust a client-supplied "is admin" flag.
- **Database security:** row-level security (or equivalent access rules) so, for example, a customer's API session can only ever read their own orders/addresses, never another customer's, even if they guess an ID.
- **API input validation:** validate and sanitise all input server-side (not just client-side) for every form — checkout, product search, admin product entry — to prevent injection and malformed-data issues.
- **Payment security:** no raw card data ever touches this application's servers or database (Section 11.3); rely entirely on the gateway's hosted/tokenized flow.
- **Secrets management:** API keys (payment gateway, email provider, database) stored as environment-level secrets in the hosting platform's secret store, never committed to source control, never exposed to the browser.
- **Rate limiting:** apply sensible rate limits to login attempts, checkout submission, and search/API endpoints to reduce brute-force and abuse risk.
- **Common web attack protections:** standard defenses against XSS (output encoding), CSRF (token-protected state-changing requests), and SQL/NoSQL injection (parameterised queries via the ORM/query layer, never raw string-concatenated queries).
- **Customer data protection:** collect only the personal data actually needed for the order (Section 11.2); avoid storing anything beyond that "just in case."

---

## 23. Performance Requirements

- **Image delivery:** responsive image sizes generated per breakpoint, modern formats (WebP/AVIF with fallback), and CDN delivery (Section 4.5) — critical given how photography-heavy this catalogue will be.
- **Lazy loading:** below-the-fold images (category grids, secondary product photos) load as the user scrolls, not all at once on page load.
- **Caching:** static assets and product/category pages cached at the CDN edge where possible, with sensible cache invalidation when a product or its stock changes.
- **Code splitting:** load only the JavaScript needed for the current page (e.g. checkout logic shouldn't be downloaded on the home page).
- **Perceived performance:** skeleton loading states for product grids/detail pages (Section 8.6) rather than blank screens or spinners, so the image-heavy pages feel fast even while assets load.

---

## 24. Development & Deployment Plan (for the coding agent)

### 24.1 Suggested build order
1. **Foundations:** project scaffold (chosen React/Next.js framework), database schema from Section 18, basic auth wiring (customer + separate admin).
2. **Admin: product & inventory management first**, since the storefront has nothing to display without it — build Product, Category, Thaan CRUD before building the customer-facing catalogue UI.
3. **Storefront: browse, search, filter, product detail pages**, including the quantity-selection UI and live pricing (Section 10.1).
4. **Cart**, including stock re-validation logic.
5. **Checkout**, including address collection, order review, and the checkout-time stock reservation (Section 10.3) — build this before payment integration so the reservation logic can be tested independently.
6. **Payment integration (Razorpay)**, including the server-side webhook verification flow (Section 11.3) — this is security-sensitive and should be built and tested carefully, including deliberately simulating failed/duplicate payments.
7. **Order lifecycle & admin order management**, wiring the statuses in Section 13 to both the admin UI and customer-facing order tracking.
8. **Transactional email notifications.**
9. **Accounts:** registration/login, saved addresses, order history, wishlist.
10. **Policy pages, About/Contact, SEO metadata, analytics instrumentation.**
11. **Design polish pass:** apply the aesthetic direction in Section 8 across every screen built in steps 1–10, including animation/micro-interaction implementation and full responsive/accessibility review.
12. **Testing & hardening pass** (see 24.2) before go-live.
13. **Write the Owner Guide** (Section 25) documenting the finished system as it actually exists, once every admin feature above is complete and stable.

### 24.2 What must be verified before launch
- Full purchase flow tested end-to-end with real (sandbox) payment gateway credentials, including a deliberately failed payment and a deliberately duplicated payment attempt.
- Stock concurrency tested with simulated simultaneous checkouts on the same thaan's last remaining length.
- All owner-confirmation placeholders from Section 15 either replaced with real content or, if still pending, clearly and honestly marked as "policy to be confirmed" rather than silently shipped as if final.
- Mobile responsiveness checked on at least one small phone-width viewport and one tablet-width viewport, for the full flow (browse → cart → checkout → confirmation), not just the home page.
- Accessibility pass: keyboard-only navigation through checkout, screen-reader labeling spot-check on the product page and checkout form, contrast check on the final chosen colour palette.
- Admin dashboard walked through by a non-technical person (ideally the actual owner/family member) before launch, to confirm it is genuinely usable without developer help.
- Domain, SSL, and all environment secrets confirmed working in the production environment (not just local/staging).

---

## 25. Owner Guide — a second Markdown deliverable, written after the build

The business owner is new to coding and cannot read source code to figure out how to run the store day-to-day. In addition to building the website itself, **the coding agent must produce a second, separate Markdown file — a beginner-friendly Owner Guide — once the website is fully built.** This document is written for the coding agent's future self (or whichever agent finishes the build) as a binding requirement, not an optional nice-to-have.

### 25.1 Why a second file, and why only after the build
This specification (the current document) describes what to build. The Owner Guide instead documents **the system as it actually ended up being built** — exact admin menu names, exact button labels, exact field names — so it must be written last, after implementation, and must be kept accurate to the real, finished admin dashboard rather than to this specification's plans. If implementation details diverge from this spec during the build (e.g. a field gets renamed), the Owner Guide must reflect reality, not this document.

### 25.2 Required contents of the Owner Guide
Write entirely in plain language for a complete beginner — never assume the owner knows programming, hosting, or web terminology. For every topic below, explicitly state whether the change is possible **(a) in the admin dashboard**, **(b) in a configuration file/environment variable a developer would need to touch**, or **(c) only with a developer's help** — and for (a), give exact click-by-click steps (which menu, which button, which field, what to type, how to save, and how to verify the change appears correctly on the live site).

- **Adding a new product** — every field, what it means, which are required vs optional (matching Section 9.3), how to upload photos, how to set the price per metre, how to link it to a thaan/set initial stock.
- **Editing an existing product** — changing the name, price, description, category, images, and available quantity in metres.
- **Removing / hiding a product** — the difference between archiving (recommended, preserves order history) and permanent deletion, and why archiving is usually the right choice.
- **Changing a product's category or categories.**
- **Managing inventory/thaans** — adding a new thaan when new stock arrives, adjusting remaining length after an in-store sale, marking a thaan sold out, and understanding how the "available quantity" shown to customers is calculated automatically from thaans (so the owner understands why they shouldn't expect to edit that number directly).
- **Viewing and managing orders** — finding an order, understanding what each order status (Section 13) means in plain language, updating a status, adding a tracking number.
- **Worked, concrete examples**, written as full step-by-step walkthroughs: adding a brand-new fabric product from scratch; changing the price of an existing fabric; updating stock in metres after a new thaan arrives; replacing a product's main photo; marking a product temporarily unavailable; moving a product into a different category.
- **What can't be done in the admin dashboard**, and what to do instead — for each: website text/copy not tied to a product (e.g. About page story, homepage messaging), navigation menu items, branding/logo/colours, policy page legal text if it lives in code rather than store settings, shipping rule logic beyond simple settings, and any third-party integration setup. State plainly, for each, whether it needs a developer or whether the owner could learn to do it themselves with guidance.
- **Connecting or updating third-party services** — where to sign up for Supabase, Razorpay, the shipping provider, and the email provider if not already done; where the resulting keys/values need to be entered (pointing to the `.env`/environment-variable mechanism from Section 4.4, described in plain language, e.g. "these go into a settings file your developer sets up — do not paste them into any page of the website itself"); and an explicit, repeated warning **never to share secret/API keys publicly (e.g. in chat messages, screenshots, or support tickets) and never to place them into any customer-facing page or code.**
- **Troubleshooting section**, covering at least: a product not appearing on the site, a product image not loading, inventory/available-quantity looking wrong, needing to change an order's status, checkout not working for a customer, and payment/webhook issues — for each, plain-language first steps the owner can try themselves, and a clear signal for when it's time to contact a developer instead.

### 25.3 Design implication for the build itself
Because this guide must be able to say "yes, do this in the admin dashboard" as often as possible, the coding agent should **prefer making things admin-editable** wherever reasonable during the build (Section 14) — store settings, policy text, and shipping configuration should live in the admin/database rather than hard-coded, specifically so the Owner Guide has fewer "needs a developer" answers to give.

---

## 26. Final Implementation Checklist

**Design & UX**
- [ ] Distinct, non-templated visual identity applied consistently (Section 8)
- [ ] Animations/micro-interactions implemented per Section 8.6, with reduced-motion support
- [ ] Glassmorphism and pill buttons applied intentionally per Section 8.5, not uniformly
- [ ] Mobile (iOS + Android) and desktop both verified as first-class experiences per Section 8.8
- [ ] Mobile-first responsive layouts verified at multiple breakpoints
- [ ] Accessibility pass complete (contrast, keyboard nav, alt text, semantic headings)

**Pages & navigation**
- [ ] All MVP pages from Section 6.1 built
- [ ] Desktop nav and mobile bottom-tab nav both implemented per Section 7

**Catalogue**
- [ ] All categories from Section 9.1 in place
- [ ] Filters (fibre, colour, pattern, price, season) functioning
- [ ] Product attribute fields match Section 9.3, optional fields genuinely optional in the UI

**Quantity & inventory**
- [ ] Quantity selector supports decimal metres, min/increment/max rules, live price calculation
- [ ] Thaan-based inventory model implemented per Section 10.2
- [ ] Checkout-time stock reservation and release implemented per Section 10.3
- [ ] Manual staff stock adjustment available in admin

**Cart & checkout**
- [ ] Cart supports edit/remove, live totals, stock re-validation
- [ ] Checkout collects all fields from Section 11.2 with proper validation
- [ ] Guest checkout available and is the default path

**Payment**
- [ ] Razorpay (or chosen gateway) integrated with server-side webhook verification
- [ ] No raw card data touches the application
- [ ] Duplicate-payment and failed-payment handling verified

**Orders**
- [ ] Full order-status lifecycle from Section 13 implemented in both admin and customer views
- [ ] Guest order tracking (order ID + phone/email lookup) and account order history both functional

**Admin**
- [ ] Product, category, thaan, order, customer, and store-settings management all implemented per Section 14
- [ ] Admin auth fully separate from customer auth

**Shipping & policies**
- [ ] Manual shipping workflow functional for MVP
- [ ] All policy pages present, with placeholder-but-honest text wherever an owner decision (Section 15) is still pending

**SEO**
- [ ] Descriptive URLs, unique metadata, structured data, sitemap, robots.txt all in place

**Performance**
- [ ] Responsive image delivery, lazy loading, caching, and code splitting implemented

**Security**
- [ ] Row-level access rules, input validation, secrets management, and rate limiting all in place

**Analytics**
- [ ] Chosen free analytics tool instrumented for every event in Section 21

**Deployment**
- [ ] Live on the chosen host with custom domain and SSL
- [ ] All environment secrets confirmed working in production, and no secret ever present in frontend/client-side code (Section 4.4)
- [ ] `.env.example` template present and documented
- [ ] End-to-end purchase flow tested in production with real (or sandbox-to-production-verified) payment credentials before public launch

**Owner enablement**
- [ ] Separate beginner-friendly Owner Guide Markdown file written per Section 25, documenting the system as actually built
- [ ] Owner Guide covers every topic in Section 25.2, including troubleshooting and third-party account setup
