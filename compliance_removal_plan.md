# Ayre Scanner: Compliance Removal Implementation Plan

**Status:** PLAN ONLY. No application code has been written, built, run or tested in preparing this file.
**Source requirements:** `AYRE_LIVE_DATA_REMOVAL_PLAN.md` (referred to below as "the Removal Plan").
**Codebases analysed (read-only):**

| Alias | Zip | Root folder | Stack |
|---|---|---|---|
| **APP** | `ayre-scanner-app-main.zip` | `ayre-scanner-app-main/` | Flutter (Dart 3.12, `pubspec.yaml` v2.1.0+2), 796 files, 72 `lib/` files, 25 `test/` files |
| **BACKEND** | `ayre-scanner-main.zip` | `ayre-scanner-main/` | Flask (`main.py`, 5,778 lines) + React admin site (`frontend/`) + scanner / data / alerts packages |

**Goal (from the Removal Plan):** the app shows only the research analyst's own published work: recommendations (entry / target / stop-loss typed by the analyst), Weekly Report, Learn, and the analyst's hand-written insight notes. No exchange-sourced price, index level, mover, breadth, sentiment value or tick-derived "entry reached" fact reaches any app user.

---

## 0. How an AI must use this file

### 0.1 Rules for every phase (mandatory)

1. Read this whole file first, then the **Decision Log (section 2)** and the **Progress Log (section 9)**. Do not repeat the architectural analysis; use the verified paths and findings below. Re-open a source file only to take an exact copy of the text you are editing.
2. Work on **one phase only**. Never begin the next phase until the current phase's files **and** the updated copy of this plan have been delivered.
3. Do **not** build, run, compile or test anything. The owner does that. Write acceptance checks the owner can run, but do not run them.
4. Deliver **only the files that were changed or newly created**, each **individually** (never a ZIP). Each file must be the complete file, not a diff. For every file, state its **exact destination path** relative to the repository roots used here, for example `ayre-scanner-app-main/lib/screens/home_shell.dart`. List files that must be **deleted** separately (with exact paths); deleted files are not delivered.
5. Updating this plan is part of the phase: record completed work, changed/created/deleted files, outstanding issues, decisions taken, and the remaining phases, in section 9 and in the phase's own "Status" line. Deliver the updated `compliance_removal_plan.md` as one of the files.
6. Do not remove unrelated functionality. The Removal Plan's "What stays" (section 6 there) is binding; section 8 below restates it with verified details.
7. If a task depends on an **OPEN** decision (section 2) that the owner has not answered, **ask the owner before starting that task** (one short question per decision, with the stated default). If the owner does not answer, use the stated default and write that in the Progress Log.
8. Line numbers below were verified in the uploaded zips and are marked `~` when approximate. They will drift after earlier phases; locate by symbol name, not by line number.

### 0.2 Delivery template (copy into the end-of-phase message)

```
PHASE N DELIVERY
Files provided (individually, complete files):
  1. <exact destination path>   (updated | new)
  ...
Files to DELETE (owner removes them; not provided):
  1. <exact path>
Updated plan: compliance_removal_plan.md  (destination: <wherever the owner keeps it>)
Outstanding issues: ...
Next phase (not started): Phase N+1
```

---

## 1. Verified findings that change or extend the Removal Plan

The Removal Plan is the requirement source. Analysis of the real code found the following corrections and additions. **They are part of this plan's scope** unless the owner objects (see section 2).

| # | Finding (verified) | Consequence for the plan |
|---|---|---|
| F1 | `EquityDetailScreen` is **not** reachable only from Insights/Index Detail. `widgets/ayre_signals_section.dart` imports it (line 4) and `_openEquity` (lines 162-177) pushes it when any **signal card is tapped** (the card's `onTap`, line 228). It shows live quote, OHLC and volume. | Deleting `equity_detail_screen.dart` breaks `ayre_signals_section.dart`. A decision on what a card tap does is required (**D1**). |
| F2 | `widgets/ayre_instrument_tile.dart` is imported by `ayre_signals_section.dart` (line 13, used at line 356) and `ayre_weekly_report.dart` (line 8, used at ~536). It contains no price or change fields (grep found none). | **Keep** it. Only fix its doc comments that mention `Quote`, Insights movers and detail screens. |
| F3 | `widgets/ayre_charts.dart` is imported by `learn_tab.dart` (line 8) for `ProgressRing` (used at line 416). The file also holds `BreadthDonut` (line 125) and `SentimentGauge` (line 572) plus their painters. | **Keep the file.** Remove `BreadthDonut`, `SentimentGauge`, `_LegendKey`, `_DonutPainter`, `_GaugePainter`, and `_ArcGeometry`/`_DrawOn` only if `ProgressRing`/`_RingPainter` do not use them (verify). |
| F4 | `persistent_market_data_service.dart` caches `signals` under `market_cache::signals` using `Signal.toJson()`, which writes `last_price`, `change_pct`, `entry_reached_at`, `entry_reached_price`, `entry_reached_extended` (market_models.dart lines 378-386). | The Removal Plan's wipe list (section 3.8) **omits `signals`**. Old installs would keep cached prices under this key. Add it to the purge. Also purge `equity_<SYMBOL>` keys (prefix `equity_`), which the Removal Plan lists only as "equity quotes". |
| F5 | `NotificationLog` stores each alert by `kind.name`. `Notice.tryParse` returns null for an unknown kind (settings_store.dart lines 135-150). `NoticeKind.entryReached` is referenced in `settings_store.dart` (112, 203), `push_service.dart` (29, 234, 260-266, 310-325, 341-345, 392), `notifications_screen.dart` (92), `notification_copy.dart` (39-70). | Removing the enum value also silently drops old stored entry-reached alerts on devices (desired). Every `switch` over `NoticeKind` must be updated in the same phase or the app will not compile. |
| F6 | The server-side public "entry reached" fields (`ENTRY_REACHED_PUBLIC_FIELDS` in `data/app_signals.py` line 109) are **not** stripped from the app feed. Only `ENTRY_REACHED_ADMIN_FIELDS` are (main.py lines 2460-2464). `entry_reached_price` comes from `hit["price_at_detection"]` (live tick). | Hold requires stripping `entry_reached_at`, `entry_reached_price`, `entry_reached_extended` at the API, not only in the UI. |
| F7 | `/api/signals` builds the app payload with `{**s, "last_price":..., "change_pct":...}` and a **deny-list** of admin fields (main.py 2538-2556). Any stored field not on the deny-list reaches the app. | Replace the deny-list with an **allow-list** for the app feed so a future tick-derived field cannot leak by accident. |
| F8 | The website's cookie session bypasses `_APP_READABLE_RULES` entirely (main.py ~657-709). `_is_admin()` returns True for **any** website login when `SCANNER_ADMIN_USERS` is unset (main.py ~617-628). | App tokens are safely limited by the rules list. Anyone with a website login can call every endpoint. The owner must set `SCANNER_ADMIN_USERS` in production (section 8, release checklist). |
| F9 | `main.py` has background jobs that exist only to feed the app endpoints being removed: `_breadth_loop` (line 4624, feeds `_breadth_full_cache`, read only by `/api/sentiment` and `/api/breadth/full`), `_start_market_poller` (line ~5630, feeds `_market_cache`, read by `/api/market`), `_close_snapshot_loop` (line 4965, feeds `_close_snapshot`, read by `/api/market*`, `/api/sentiment`). The React admin site calls none of `/api/market*`, `/api/sentiment`, `/api/breadth/full`, `/api/insights/volatility|momentum|volume-surge` (grep of `frontend/src` found no caller). | Removing the endpoints from `_APP_READABLE_RULES` alone (the Removal Plan's approach) leaves these routes and jobs running with no consumer. Phase 4 deals with them (**D4**). |
| F10 | `_market_status_cache` (main.py line 258) shares `_market_lock` with `_market_cache`. `_fetch_market_status_from_yahoo/_nse` (lines 3898, 3948) scrape Yahoo and NSE to decide market open/holiday, used by the scan loop and `/api/status`. | When deleting `/api/market`, **keep `_market_lock`, `_market_status_cache` and the market-status helpers** unless the owner decides otherwise (**D5**). They are not app-facing but are scraping of unlicensed sources. |
| F11 | `ApiService.getMarket()`, `getSentiment()` and legacy `getSignals()` in `api_service.dart` (lines 169, 268, 184) have **no callers** (grep of `lib/` and `test/`). `getInsights()`/`getLearnArticles()` also have no callers but call allowed endpoints. | Delete the three live-data methods. Leave `getInsights`/`getLearnArticles` alone (out of scope; mention in outstanding issues). |
| F12 | `settings_screen.dart` line ~256 says Signal alerts cover "when a price reaches its entry level." | Not in the Removal Plan. Must change when the entry-reached feature is held. |
| F13 | `legal_content.dart` mentions market data at more places than the Removal Plan lists: lines ~87-89, 175-181, 434-443, 523, 744, 807, 955-956. | Phase 4 reviews every line (including 523, 744, 807) rather than only the four listed. |
| F14 | Theme and shared widgets built only for the removed screens: `AppIndexId`, `AppIndexTints`, `AppIndexTint` (app_theme.dart ~323-377), `AppMotion.livePulse` (~437), `LivePulseDot` and the LIVE chip tone (ayre_components.dart ~813-860), `DeltaFigure` (figure.dart, used by signals section, detail screens, index card), `DirectionBadge` (home_tab only). `test/identity_test.dart` lines 108-125 test the index tints. | Orphan sweep in Phase 3: delete only after grep proves zero remaining references. |
| F15 | `fault_injection.dart` `DataSurface` enum has `indexBoard, indexDetail, indexConstituents, sentiment, gainers, ...` entries (lines 38-60). | Must lose the removed surfaces in Phase 3 (and any test referencing them). |
| F16 | `ProfileTab` takes a `marketData` parameter only to pass to `LegalHubScreen` (profile_tab.dart 45-50, 324). | Keep (still needed for `getComplianceInfo`). No change. |
| F17 | Onboarding page 1 really shows the index-name pills (`onboarding_widgets.dart` ~line 137 uses `kOnboardingIndexNames`). Test `onboarding_content_test.dart` lines 21, 67 assert them. | Removal Plan's condition "if they show on the first page" is true. Remove, and re-balance that page's layout. |
| F18 | Backend zip also contains unrelated oddities: `.dart_tool/`, `filesddd.zip`, `goyal-textile-ecommerce-spec.md`, a file named `gitignore`. | Do not touch. Mention only so nobody "cleans" them under this task. |
| F19 | Entry-reached buttons: while the hold is on, the server refuses “Publish entry reached” with a 409. The admin site still shows those buttons and the text “App users will see ‘entry reached’…” in `EntryHitsPanel.tsx`, so an admin who clicks gets an error and the wording is wrong. Hiding the controls fixes that but needs a rebuild of `frontend/`. It’s scheduled in Phase 4 only if you say yes. | Tied to **D10**. Phase 1 does not touch `frontend/`. Phase 4 task 7.4 runs only if D10 = yes. |

Verified clean (no live exchange data): `GET /api/weekly-report` handlers and `data/app_weekly_report.py`, `/api/exits` and `data/app_exits.py` (profit and exit price are typed by the admin), `/api/insights` notes and `data/app_insights.py`, `/api/learn*`, `/api/compliance`, `/api/app/me`, device registration and `alerts/push.py` new-signal / revised / exit text builders.

---

## 2. Decision Log (ambiguities needing the owner)

Status values: **OPEN**, **DECIDED (by owner)**, **DEFAULT USED**. The executing AI must update this table.

| ID | Question | Why it matters | Default if unanswered | Status |
|---|---|---|---|---|
| **D1** | What should tapping a signal card do after `EquityDetailScreen` is removed? (a) nothing (card not tappable); (b) open a new static "Recommendation detail" screen showing only typed fields. | F1. (b) adds a new file and tests. | **(a)** Card becomes non-interactive; remove `onOpenEquity`, `HapticFeedback` and the "opens details" semantics text. | DEFAULT USED (Phase 2) |
| **D2** | Entry-reached hold mechanism: (a) server flag that disables serving and publishing; (b) delete the feature code; (c) keep but let the analyst mark "entry reached" by hand. | Removal Plan section 2 says "hold" and offers manual marking as an alternative. | **(a)** Env flag `APP_ENTRY_REACHED_ENABLED`, default off. Code is kept dormant, the app code for it is removed. | DEFAULT USED (Phase 1) |
| **D3** | Market Sentiment card: remove now, or later replace with a hand-typed analyst view? | Removal Plan recommends "remove now". | **Remove now.** No replacement in this work. | DEFAULT (Removal Plan recommendation) |
| **D4** | After app tokens lose access, should the now-unconsumed market endpoints and their feeding jobs be **deleted** from the backend (Phase 4) or only left admin-only (Removal Plan 4.1/4.4)? | The task asks not to leave obsolete endpoints/jobs, but Removal Plan 4.4 says keep internal systems. | Delete: `/api/market`, `/api/market/gainers|losers|most-active`, `/api/market/<key>/constituents`, `/api/sentiment`, `/api/breadth/full`, `/api/insights/volatility|momentum|volume-surge`, and the jobs/caches that feed only them (`_breadth_loop`, market poller, `_close_snapshot_loop`). **Keep** `_fyers_stream`, `_sweep_loop`, entry-hit detector, history store, scanner, `/api/quotes` + quotes poller (used by `/api/ranges/*` at main.py ~2241). | DEFAULT USED (Phase 4): routes, feeding jobs and orphaned helpers deleted |
| **D5** | Yahoo/NSE market-status scraping (`_fetch_market_status_from_yahoo/_nse`), NSE session (`_get_nse_session`), and `fetch_nifty500_with_meta` (`data/symbols.py`) are internal, not app-facing. Remove or keep? | Removal Plan 4.3 says "separate terms question, check with your lawyer". | **Keep** (internal). Only remove NSE/Yahoo code that exists solely to build the removed app payloads (`_fetch_market_from_nse`, `_fetch_market_from_yahoo`, `_fetch_constituents*` NSE parsing, hard-coded fallback). | DEFAULT USED (Phase 4): internal NSE/Yahoo status, NSE session and symbol fetch kept; `_get_constituents` kept (admin stock directory) |
| **D6** | `universe_stats` (`data/universe_stats.py`, `_state["universe_stats"]`, written by every scan at main.py ~3709) is served only by the three `/api/insights/*` metric routes. Keep computing and saving it? | Scanner output is internal; storing it has no app exposure once routes go. | **Keep** computing and saving. Remove only the routes. | DEFAULT USED (Phase 4): universe_stats still computed and saved |
| **D7** | Old installed app versions will call removed endpoints and get 403. Is any force-update / minimum-version gate wanted? None exists in the backend. | The app is being prepared for first store publication, so likely only test builds exist. | **No gate.** Owner confirms there are no public installs. | OPEN |
| **D8** | Index-name pills on onboarding page 1: replace with what? | F17. | Remove the pill row; keep the title/body text. If the page looks empty, the executor proposes a layout using existing widgets only. | DEFAULT USED (Phase 2): pills removed; page 1 now shows wash + logo + title/body. Title changed to "Your research, in one place." |
| **D9** | Legal copy replacement wording. | Statements become false after removal. | The executing AI drafts neutral replacement text; the owner's securities lawyer reviews (Removal Plan 5). | DEFAULT USED (Phase 4): neutral wording drafted; lawyer review pending |
| **D10** | Admin website: hide the "Publish entry reached" controls when the hold flag is off? Requires a rebuild of `frontend/`. | Keeps admin from clicking a refused action. | Yes, hide, driven by a new field in an admin-only status response. See F19. Phase 1 task 1.5 and Phase 4 task 7.4 are **not executed** until the owner says yes. | OPEN |

---

## 3. Dependency map and phase order

```
Phase 1  BACKEND lock-down  ───────────────►  safe to ship on its own (server stops serving data)
   │
   ▼
Phase 2  APP: UI + navigation removal  ───►  app compiles (data layer still present, unused)
   │
   ▼
Phase 3  APP: data layer, models, device state, entry-reached, notification/settings copy, theme orphans
   │
   ▼
Phase 4  APP legal/copy + BACKEND decommission + docs + final verification
```

Why this order:
- Phase 1 first: it is the only phase that protects users regardless of app version, and the app phases can then be tested against a backend that already behaves as the final one will.
- Phase 2 before 3: screens must stop calling the data layer before the data layer can be removed without compile errors.
- Phase 3 touches `market_models.dart`, the service interface and `NoticeKind`, which many tests depend on, so it follows the UI phase.
- Phase 4 last: legal text must describe the final state, and backend dead-code deletion needs the app proven free of the old endpoints.

Every phase leaves **both** codebases in a state that compiles and runs. No phase may leave a dangling import, interface method, enum case or test that refers to something deleted in the same phase.

---

## 4. PHASE 1: Backend lock-down and payload sanitising

**Status:** DONE (delivered 2026-10-17; not built, run or tested by the AI; owner runs the checks in 4.4). Task 1.5 deferred (D10 open).
**Codebase:** BACKEND (plus one admin-site file under D10)
**Why first:** removes server-side exposure for all app versions. No Flutter file changes.

### 4.1 Tasks

**1.1 Remove market/derived endpoints from the app allow-list.** `ayre-scanner-main/main.py`, set `_APP_READABLE_RULES` (~line 633).
Remove: `/api/market`, `/api/market/gainers`, `/api/market/losers`, `/api/market/most-active`, `/api/market/<string:market_key>/constituents`, `/api/sentiment`, `/api/breadth/full`, `/api/insights/volatility`, `/api/insights/momentum`, `/api/insights/volume-surge`.
Keep exactly: `/api/signals`, `/api/learn`, `/api/learn/<string:article_id>`, `/api/insights`, `/api/weekly-report`, `/api/compliance`, `/api/app/me`.
`_APP_ACCOUNT_RULES` (`/api/devices/register`, `/api/devices/unregister`, `/api/account/delete`) is a separate set and stays.
Mechanism check (verified): `_require_authentication` (~657-709) returns 403 `forbidden` for an app token on any GET whose `request.url_rule.rule` is not in the set, and for any non-GET. No other change is needed to refuse the removed paths.

**1.2 Introduce the entry-reached hold flag (D2 default).**
- `ayre-scanner-main/config/settings.py`: add `APP_ENTRY_REACHED_ENABLED` read from an environment variable, default **False**. Follow the file's existing env-reading helper and naming style (read `settings.py` first; it already defines `APP_SIGNALS_FILE` at line 166).
- Import it in `main.py` next to the existing `config.settings` imports.

**1.3 Rebuild the app feed of `/api/signals` as an allow-list.** `main.py` `api_signals_list` (lines 2486-2561).
- New file `ayre-scanner-main/data/app_signal_payload.py` with one pure function, e.g. `to_app_signal(signal: dict, entry_reached_enabled: bool) -> dict`, that returns only an explicit set of keys. **Derive the allow-list from the code, do not guess:** read `_content_fields` and `_signal_price_fields` in `main.py` (called at ~2584-2608) and `add_signal` in `data/app_signals.py` (record built at ~215-230). The Flutter app currently reads only these keys (market_models.dart `Signal.tryParse`, lines 351-372): `symbol`, `rationale`, `name`, `entry_price`, `exit_price`, `stop_loss`, `date_added`, plus the lastPrice / percentChange / entry-reached keys that are being removed. The allow-list must include at least `id`, `symbol`, `name` (if stored), `rationale`, `date_added`, `entry_price`, `entry_low`, `entry_high`, `exit_price`, `exit_low`, `exit_high`, `stop_loss`, and the fixed neutral values the route already sets (`featured=False`, `pinned=False`, `display_order=0`).
- `entry_reached_at`, `entry_reached_price`, `entry_reached_extended` are included only when `entry_reached_enabled` is True (they are not, by default).
- In `api_signals_list`: call `fetch_constituents_quotes_bulk(_fyers, symbols)` **only when `include_hidden` is True** (admin `?all=1`). For the app feed set no quote and add no `last_price` / `change_pct` keys at all (not null values; the keys must be absent). The admin feed is unchanged (the admin SPA's `SignalsPanel.tsx` lines 442, 516-518 still displays them, and the Removal Plan 4.2 keeps this for admin).
- Keep the existing ordering rule for the app feed (most recently published first) and the `published_only`/`active_only` filtering.
- Update the docstring example response to remove `last_price` and `change_pct` from the app shape.

**1.4 Refuse publishing and notifying "entry reached" while on hold.**
- `api_entries_publish_reached` (main.py 1743-1842): after the admin check, if `not APP_ENTRY_REACHED_ENABLED`, return a 409 with `{"error": "...held...", "code": "feature_held"}` before any state change.
- `alerts/manual_push.py` `send_entry_reached` (line 143): raise `SendRefused` with code `feature_held` when the flag is off (defence in depth; it is the only sender of `type: "entry_reached"`).
- Do **not** delete `set_entry_reached`, the entry hit detector, `entry_hits.py` or `push.build_entry_reached_text`; they stay dormant (Removal Plan 4.4 keeps the detector).
- Existing data already stored in `app_signals.json` with `entry_reached_*` stays on disk; it is simply never served while the flag is off.

**1.5 Expose the flag to the admin site (only if D10 = yes).** Add one boolean (`entry_reached_enabled`) to an existing admin-only response (inspect `/api/system/overview` at main.py 1459 or `/api/entries/status` at 1540; choose the one `EntryHitsPanel.tsx` already loads). Frontend change is delivered in Phase 4 task 4.7 so Phase 1 stays backend-only.

**1.6 Add an offline check script (new file, owner runs it).** `ayre-scanner-main/test_app_signal_payload.py`, in the style of the existing root-level `test_auth.py`, but pure Python with no network: feeds `to_app_signal` a signal dict containing every known tick-derived and admin key and asserts the output has none of `last_price`, `change_pct`, `entry_reached_*` (flag off), `push_sent_at`, `notified_*`, `published*`, `added_by`, etc. Write it, do not run it.

### 4.2 Files

| Action | Path |
|---|---|
| Update | `ayre-scanner-main/main.py` (full file; 5,778 lines, edits at `_APP_READABLE_RULES`, `api_signals_list`, `api_entries_publish_reached`, imports) |
| Update | `ayre-scanner-main/config/settings.py` |
| Update | `ayre-scanner-main/alerts/manual_push.py` |
| New | `ayre-scanner-main/data/app_signal_payload.py` |
| New | `ayre-scanner-main/test_app_signal_payload.py` |
| Update | `compliance_removal_plan.md` |

### 4.3 Avoiding breakage
- Admin SPA reads `/api/signals?all=1` and `/api/entries/*`; those paths and shapes are unchanged. Do not change `_SIGNAL_ADMIN_ONLY_FIELDS` semantics for the admin feed.
- `fetch_constituents_quotes_bulk` is also used at main.py 2247 (ranges) and ~5443 (constituents); do not remove the import.
- `_fyers_market_data_allowed()` stays as is; it is a switch, not a removal (Removal Plan 4.2).
- Do not alter token verification, `_AUTH_PUBLIC_API`, or `_APP_ACCOUNT_RULES`.

### 4.4 Acceptance criteria (owner-run)
- [ ] With an app Bearer token, each of the ten removed paths returns 403 `forbidden`.
- [ ] With an app token, the seven kept paths still return 200.
- [ ] `GET /api/signals` with an app token: no item contains `last_price`, `change_pct`, `entry_reached_at`, `entry_reached_price`, `entry_reached_extended`, `published*`, `notified_*`, `added_by`, `push_*`.
- [ ] `GET /api/signals?all=1` with a website session still returns prices for the admin panel.
- [ ] Publishing entry reached (`POST /api/entries/hits/<id>/publish-entry-reached`) returns 409 `feature_held` and changes nothing.
- [ ] `python test_app_signal_payload.py` passes.
- [ ] Server starts with no import error; no `fetch_constituents_quotes_bulk` call occurs for an app-token `/api/signals` request.

### 4.5 Expected outcome
The backend serves only static analyst content to app tokens. Old app builds degrade (Home board/Insights show their failed states) but nothing exchange-sourced is delivered.

---

## 5. PHASE 2: App UI and navigation removal

**Status:** DONE (delivered 2026-10-17; not built, run or tested by the AI; owner runs the checks in 5.8). Data layer untouched.
**Codebase:** APP (`ayre-scanner-app-main/`)
**Rule:** the data layer (`MarketDataService`, models, caches) is **not touched** in this phase. Screens simply stop using it, so everything still compiles.

### 5.1 Navigation: 5 tabs to 4
| File | Change |
|---|---|
| `lib/widgets/ayre_bottom_nav.dart` | Remove `NavDestination(label: 'Insights', glyph: AyreGlyph.insights)` from `kNavDestinations` (~line 24). Order becomes Home, Reports, Learn, Profile. Update any doc comment saying five. Re-check the nav-rail and dock code for hard-coded counts; counts follow `kNavDestinations.length` per the Removal Plan, verify. |
| `lib/screens/home_shell.dart` | Remove `import 'insights_tab.dart';` (line 19). In `_tabRoot` (lines ~335-370) delete the `2 => InsightsTab(...)` branch; Learn becomes `2`, Profile the default branch (`3`); `active: scope.index == 2` for Learn. Change both `_select(4)` calls to `_select(3)`: `onOpenProfile` (line ~346) and the `onEnter` in `_startProfileTour` (~275). Update "five" wording at lines 25, 284, 401. Rewrite the `HomeShell` constructor comment (lines 36-47) to drop the "constituents cache / movers and breadth" explanation (the `PersistentMarketDataService(RemoteMarketDataService())` default stays until Phase 3). Fix the comment at ~397-402 about `LivePulseDot` and "live quote" tickers (keep the `TickerMode` wrapper, which also mutes other animations). |
| `lib/widgets/ayre_icons.dart` | `AyreGlyph.insights` is also used by `onboarding_content.dart` (Insights feature row, being removed) and the nav. After both removals, grep; delete the glyph only if unused (the Removal Plan allows this). Note `ayre_insight_carousel.dart` is a **different** feature (analyst notes) and keeps working. |

### 5.2 Delete these files (grep each class name first; all usages must be in files that are also deleted or edited here)
| File | Verified reason / dependants |
|---|---|
| `lib/screens/insights_tab.dart` | Imported only by `home_shell.dart`. |
| `lib/screens/index_detail_screen.dart` | Imported only by `home_tab.dart` (line 27). |
| `lib/screens/equity_detail_screen.dart` | Imported by `ayre_signals_section.dart` (F1, see 5.4). |
| `lib/widgets/ayre_compact_index_card.dart` | Imported only by `home_tab.dart` (line 15). |
| `lib/widgets/ayre_index_art.dart` | Imported only by the index card. |
| `lib/widgets/ticker_trace.dart` | Used only by the two detail screens and tests. |

**Do not delete** `ayre_instrument_tile.dart` (F2) or `ayre_charts.dart` (F3, trimmed in Phase 3).

### 5.3 `lib/screens/home_tab.dart` (1,038 lines)
Delete, by symbol (current lines in parentheses):
- Imports: `ayre_compact_index_card.dart` (15), `index_detail_screen.dart` (27).
- State: `_board`, `_breadth`, `_fullBreadth` (and the long comment above them), `_liveTimer`, `_liveRefreshInFlight`, the `Timer.periodic(liveMarketRefreshInterval, ...)` in `initState`, `_liveTimer?.cancel()` in `dispose`, `_refreshLive()` entirely, the `didUpdateWidget` call to `_refreshLive` (keep the signals-focus logic), the `_onAppResumed` branch that calls `_refreshLive` (keep resume-triggered `_load()` for insight notes if wanted; simplest: reload notes when away > 5 min).
- `_load()`: remove `getIndexBoard`, `getSentiment`, `getFullBreadth`; keep `getInsightNotes` and the account-name logic.
- `build`: remove the `_SentimentCard` entrance (index 1), the `Index board` `SectionLabel` entrance (index 2) and `_IndexBoard`; renumber remaining `Entrance(index:)` values (Signals 1, Insights 2). Keep header, `SignalsSection` (with its `_signalsKey`, `_signalsController`), `_InsightSection`, `_showsInsights`, `_openInsight`.
- Delete `_openIndex` (~363-376), `_indexIdFor` (628-635), `_IndexBoard` (647-800), `_Mood` enum (846), `_SentimentCard` (882-1019), `_SentimentSkeleton` (1021-1038), sentiment colour/bucket helpers (838-1038 region).
- Header doc comment (lines 34-52) and the `HomeTab.active` comment about `_liveTimer` (lines 65-72): rewrite to describe header, Recommendations, Market Insight carousel. Update `_focusPending` comment ("sentiment card landing").
- Remove now-unused imports after the edit (e.g. `market_models.dart` still needed for `InsightNote`; `state_views.dart`, `ayre_components.dart` only if still referenced; `DirectionBadge` is used only here, so the widget becomes an orphan for Phase 3).
- **Keep and do not alter behaviour:** `_Header`, `_ThemeToggle`, `_HeaderControl`, `_AccountControl`, `TourKeys.homeTheme`/`homeAlerts` keyed subtrees, alerts button, `onAccountResolved`, `signalsFocusToken`/`_scrollToSignals`, pull-to-refresh via `AyreTabScroll`.
- The 10-second `liveMarketRefreshInterval` constant stays in `market_data_service.dart` until Phase 3 (still referenced there only; confirm no other file uses it after deletion of the Insights/detail screens).

### 5.4 `lib/widgets/ayre_signals_section.dart` (641 lines)
- Remove import `../screens/equity_detail_screen.dart` (line 4).
- **D1 default (a):** remove `onOpenEquity` param, `_openEquity`, `HapticFeedback`/`services.dart` import if unused; `_SignalList` and `_SignalCard` lose `onTap`; `AyreCard` is built without `onTap` (verify `AyreCard` supports null `onTap`; if it does not, add a minimal parameter-safe path in `ayre_components.dart`). Remove `button: true` and the `'opens details'` item from `_spoken()`.
  **D1 option (b):** create `lib/screens/signal_detail_screen.dart` showing symbol, name, rationale, the three typed levels, date added; reuse `_Levels` styling (move to a shared file). Needs tests.
- Remove price block: in `_spoken()` the `lastPrice` and `percentChange` items (316-319); in `_SignalCard.build` the whole `priceBlock` and the width-switch (386-422); the card header becomes just `identity`. Remove `_sideBySide` constant and the `LayoutBuilder` if no longer needed. Remove `DeltaFigure` use.
- Remove `_EntryReachedLine` (600-641) and its use at 447-450 (`signal.hasEntryReached`). Remove `_clock`.
- Remove the comment at 336-338 about "market-sentiment system on Home".
- Remove `PushService.instance.refreshSignalsRequests` listener (`_onRefreshRequested`, lines 88, 91-95, 111-112) **only in Phase 3** together with the push change (keep compiling now).
- Keep: `_load`, `keepingLastGood`, `SeenSignalsStore.diffAndRecord`, `NotificationLog.add`, controller, skeleton, `_Levels` / `_Level` (entry / exit / stop typed values), rationale, `ADDED <date>` line. Leave label text "Exit" as is; the app calls it exit while the Removal Plan says target (copy decision, flagged in section 10, not changed).
- Update the class doc comment about `GET /api/signals` "live price".

### 5.5 Tutorials, onboarding and support text
| File | Change |
|---|---|
| `lib/onboarding/tour_content.dart` | Header comment (lines ~91-98 area and the doc above `buildAppTourSteps`) rewrite. Home step body: replace "NIFTY 50, BANK NIFTY and SENSEX, market sentiment, ..." with text about Recommendations (Entry, Exit, Stop) and desk notes. Delete the Insights `AppTourStep` (tab 2). Renumber `tab:` Learn 3 to 2, Profile 4 to 3. |
| `lib/onboarding/onboarding_content.dart` | Remove the Insights `OnboardingFeature` (lines ~85-90). `kOnboardingFeatures` becomes 2 rows. Remove `kOnboardingIndexNames` (line 75) and fix the page-1 body that says "Index levels, research signals and lessons" (line ~60) to wording without index levels (D8). |
| `lib/widgets/onboarding_widgets.dart` | Remove the index-name pill row (~lines 120-150 using `kOnboardingIndexNames`); keep the feature rows loop (line ~160). Check layout of page 1 without the pills. |
| `lib/screens/support_screen.dart` | Line 59 subtitle: "The five tabs..." becomes "The four tabs and the Home controls." |
| `lib/screens/faq_screen.dart` | No change needed in this file; FAQ content lives in `legal_content.dart` (`kLegalFaqs`), handled in Phase 4. |
| `lib/screens/settings_screen.dart` | Line ~421 comment "shared with the Insights time-window toggle" reword. (Line ~256 entry-level text is handled in Phase 3.) |
| `lib/services/reachability.dart` | Comment (lines 8-12) lists `InsightsTab` and "four tabs polling every 10s"; reword to the tabs that still fetch. Wording only. |

### 5.6 Tests for this phase (update so the suite still passes when the owner runs it)
Delete: `test/insights_phase4_test.dart`, `test/insights_phase5_test.dart`, `test/compact_index_card_test.dart`.
Update (remove or rewrite only the removed-feature parts):
| Test | What to change |
|---|---|
| `test/layout_matrix_test.dart` (25 hits) | Remove cases for `IndexDetailScreen`, `EquityDetailScreen`, `TickerTrace` (imports lines 3, 5, 21; cases ~130-170, 439-470, 621-633), Insights tab; keep Home/Reports/Learn/Profile/Signals matrix cases. |
| `test/home_phase3_test.dart` | Remove index art import (line 5), index board and sentiment expectations; keep header/greeting/signals/notes. |
| `test/home_reports_phase4_test.dart` | Remove `AyreCompactIndexCard` expectations (lines ~141-153) and index board; keep reports tab tests. |
| `test/nav_phase2_test.dart`, `test/shell_phase2_test.dart` | Expect 4 destinations; label list and "Tab i of 4" hints. |
| `test/spotlight_tour_test.dart`, `test/support_tutorials_test.dart` | Four-tab tour; step counts and tab indices. |
| `test/onboarding_content_test.dart` | Lines 21-22, 55, 67-68, 80: no pill names, 2 features, tour covers four tabs. |
| `test/signals_section_test.dart`, `test/home_signals_focus_test.dart` | No price/delta; no entry-reached line; card not tappable (D1 a). |
| `test/fault_states_test.dart` | Remove detail-screen and index-board fault cases (lines ~204, 256, 292, 385-396). |
| `test/plain_labels_test.dart` | Imports `ayre_charts.dart`; `BreadthDonut` group (line 20+) stays until Phase 3 (the widgets still exist). No change now. |
| `test/support/fake_market_data.dart` | **No change in Phase 2** (interface unchanged). |

### 5.7 Files list
Updated: `ayre_bottom_nav.dart`, `home_shell.dart`, `home_tab.dart`, `ayre_signals_section.dart`, `tour_content.dart`, `onboarding_content.dart`, `onboarding_widgets.dart`, `support_screen.dart`, `settings_screen.dart`, `reachability.dart`, possibly `ayre_icons.dart` and `ayre_components.dart`, and the tests listed above. Deleted: the six files in 5.2 and three tests. New: none (unless D1 option b: `lib/screens/signal_detail_screen.dart`).

### 5.8 Acceptance criteria (owner-run)
- [ ] `flutter analyze` shows no error or unused-import warning attributable to this phase.
- [ ] Only four tabs show (Home, Reports, Learn, Profile) in dock and rail; Profile jump from the Home header opens Profile (index 3).
- [ ] Home shows header, Recommendations, Market Insight carousel and nothing else; no index board, sentiment card, or "LIVE" chip on Home.
- [ ] Signal cards show symbol, name, rationale, Entry/Exit/Stop, added date; no price, no % change, no "Entry reached" line, no navigation to a quote screen.
- [ ] App tour and onboarding walk four tabs in order with no Insights or index text.
- [ ] `grep -rn "InsightsTab\|IndexDetailScreen\|EquityDetailScreen\|AyreCompactIndexCard\|TickerTrace\|AyreIndexArt" lib test` returns nothing.
- [ ] `flutter test` passes (owner run).

---

## 6. PHASE 3: App data layer, models, device state and alert copy

**Status:** DONE (delivered 2026-10-17; not built, run or tested by the AI; owner runs the checks in 6.10).
**Codebase:** APP

### 6.1 Models: `lib/services/market_models.dart` (773 lines)
- Delete `IndexId` (line 10), `Quote` (33-156 incl. `toJson`), `Sentiment` (158), `FullBreadth` (215), and private helpers used only by them (check `_num`/`_str` are still used by `Signal`, `Course`, `InsightNote`, `WeeklyReport...`; keep them).
- `Signal` (303): remove `lastPrice`, `percentChange`, `bullish` getter (only `home_tab` used it, gone), `entryReachedAt`, `entryReachedPrice`, `entryReachedExtended`, `hasEntryReached`, and the matching `tryParse` and `toJson` lines (357-369, 378-386). `toJson` must write only typed fields so the persistent cache is clean.
- Keep: `ComplianceInfo`, `Signal`, `Course`, `InsightNote`, `WeeklyReportStock`, `WeeklyReport` (the weekly report's `entry_price` / `exit_price` are analyst-entered, verified in `data/app_weekly_report.py` which has no market access).
- Fix the file header (lines 1-6) if it mentions market surfaces.

### 6.2 Service interface and implementations
`lib/services/market_data_service.dart` (701 lines):
- Interface (lines 74-110): remove `getIndexBoard`, `getIndex`, `getConstituents`, `getEquity`, `getSentiment`, `getTopGainers`, `getTopLosers`, `getMostActive`, `getFullBreadth`. Keep `getSignals`, `getCourses`, `getInsightNotes`, `getWeeklyReports`, `getComplianceInfo`.
- `RemoteMarketDataService`: remove `_market`, `_sentiment`, `_gainers`, `_losers`, `_mostActive`, `_constituents`, `_breadthFull` (lines 155-175), `_constituentsClosed`, `_constituentFetchesInFlight` (193-205), and the methods at 208-440 and 471-565 (`getIndexBoard`... `_allConstituents`, `_movers`, caches, `_fetchConstituents*`). Keep `_get`, retry logic, `_run`, `_parsedList`, `getSignals`/`getCourses`/`getInsightNotes`/`getWeeklyReports`/`getComplianceInfo`, `DataResult`/`DataFailure`.
- Delete `liveMarketRefreshInterval` (line ~67) and its doc comment **after** grep shows no user; delete the long "endpoint map" doc about movers/sentiment (lines ~111-139). Keep `cadence` only if still read by `_run`/staleness logic (verify).
- `lib/services/persistent_market_data_service.dart`: remove the nine surfaces and their keys (`_kIndexBoard`, `_kIndex`, `_kConstituents`, `_kEquity`, `_kSentiment`, `_kGainers`, `_kLosers`, `_kMostActive`, `_kFullBreadth`) and `_decodeQuote`. Keep signals, courses, insight notes, weekly reports, compliance. Rewrite the class doc. Replace the constructor's `purgeRetiredCache()` call with the new purge (6.3) or keep a thin delegating call.
- `lib/services/market_data_cache.dart`: update class docs (index board, constituents, sentiment mentions). `clear` and `clearPrefix` stay (the purge uses them).
- `lib/services/fault_injection.dart` (200 lines): remove `DataSurface` values `indexBoard, indexDetail, indexConstituents, sentiment, gainers` and the other removed ones (read lines 35-70 for the full list, including the "full breadth" surface), plus any switch using them. Keep weekly report, compliance, signals, notes, courses surfaces. Also the doc comment on line 36-37.

### 6.3 Device-state purge (new file)
Create `lib/services/legacy_cache_purge.dart`:
- `LegacyCachePurge.run()`: idempotent, best-effort, guarded by a version flag key in `SharedPreferences` (e.g. `compliance_purge_v1`), so it runs once per install and is cheap afterwards.
- Remove, under the `market_cache::` prefix used by `MarketDataCache._prefix`: `index_board`, prefix `index_`, prefix `constituents_`, prefix `equity_`, prefix `sentiment_`, `gainers`, `losers`, `most_active`, `full_breadth`, `volatility`, `momentum`, prefix `volume_surge_`, and **`signals`** (F4; it will repopulate clean from the first fetch).
- Also remove any stored `NotificationLog` entries of kind `entryReached`: either rely on `Notice.tryParse` returning null once the enum value is gone (F5; simplest), or filter explicitly and rewrite the stored list. Rely on the enum removal; document it.
- Call it from `lib/main.dart` `_bootstrap` (lines 103-112) inside the `Future.wait([...])` that already contains `ApiService.purgeLegacyCookie()`. It must finish before the first `getSignals()` result is persisted (it is awaited in `Future.wait`, which precedes `PushService.init()` and the shell).
- `AccountSession.clearLocalData` (account_session.dart 64-71) clears `content_*` keys but **not** `market_cache::*`. Add a call that clears the persisted `signals` surface on account switch/sign-out so one account's cached research does not show for another (outside the strict removal scope, but it removes a stale-data path; flag as optional in the log if the owner objects).
- Remove `PersistentMarketDataService.purgeRetiredCache()` once the new purge covers volatility/momentum/volume_surge, or keep it delegating.

### 6.4 `lib/services/api_service.dart`
Delete `getMarket()` (lines 168-180), the legacy `getSignals()` (183-199) and `getSentiment()` (267-280) (F11). Leave everything else (auth, devices, delete account, `getLearnArticles`, `getInsights`, `_getCachedList`).

### 6.5 Entry-reached in the app (D2 default: held server-side, removed app-side)
- `lib/services/settings_store.dart`: remove `entryReached` from `NoticeKind` (line 112) and its doc (108-111); remove the case at line ~203 in `_allows`.
- `lib/services/push_service.dart`: delete `_typeEntryReached` (234), the `case _typeEntryReached` blocks in `_noticeFrom` (260-268), the `isSignalKind` term (312), the `entryReached` refresh block (323-326) and the `onMessageOpenedApp` case (341-346), and the `NoticeKind.entryReached => _requestOpenSignals` line (392). Add an explicit branch so an in-flight push of type `entry_reached` from an older backend is **ignored** (returns null) rather than falling through to a `general` alert. Rewrite the file's header comment (lines 22-33).
- `refreshSignalsRequests` in `push_service.dart` (line 68-70) and its listeners in `ayre_signals_section.dart` (88, 91-95, 111-112) existed only for entry-reached. Remove all of them together (grep for other users first).
- `lib/services/notification_copy.dart`: delete `_entryReached` pool (39-43) and `entryReached()` (66-74). Update the file header comment.
- `lib/screens/notifications_screen.dart`: remove the `NoticeKind.entryReached` case (line 92).
- `lib/screens/settings_screen.dart` (~256): change the Signal-alerts subtitle to "New signals, changes to them, and exit calls." (confirm exit alerts are gated by the same switch at `settings_store.dart` ~203-210 before wording it).
- Keep new-signal, revised and exit notifications, and `NotificationCopy.exitSignal` (prices there are analyst-typed `profit` and `exit_price`, verified in `/api/exits`).

### 6.6 Widgets and theme orphan sweep (delete only with zero remaining references; run a grep per symbol and record results)
- `lib/widgets/ayre_charts.dart`: remove `BreadthDonut`, `_LegendKey`, `_DonutPainter`, `SentimentGauge`, `_GaugePainter`, and `_ArcGeometry`/`_DrawOn` only if unused by `ProgressRing`/`_RingPainter`. Update the header and class docs.
- `lib/widgets/ayre_instrument_tile.dart`: reword doc comments that mention `Quote`, Insights movers, detail screens (lines 7, 29-33, 50).
- `lib/widgets/figure.dart`: `DeltaFigure` (used by signals section before Phase 2, detail screens, index card) and `Figure` helpers; remove `DeltaFigure` if unused. `formatPrice` stays (weekly report, signals levels).
- `lib/widgets/ayre_components.dart`: `LivePulseDot`, `ChipTone.live` and the `pulse` parameter of `AyreChip` (lines ~770-860); `DirectionBadge`; `SectionLabel` stays; doc comments at lines 105, 1423, 1737-1741 mention removed files. Remove only if no remaining reference.
- `lib/theme/app_theme.dart`: `AppIndexId`, `AppIndexTint`, `AppIndexTints` (~323-380), `AppMotion.livePulse` (~437), stale comment lines 282-285, 439. Update `test/identity_test.dart` (remove the "index identity tints" test, lines ~115-125).
- `lib/screens/learn_tab.dart`: comment at line ~392 refers to "the breadth donut on Home"; reword.
- `lib/services/stock_logo_service.dart`: update comments (lines 2, 18, 24, 139, 159). Logo assets and service stay (Removal Plan decision).
- `pubspec.yaml` comment mentioning `AyreInstrumentTile` for logos: still true; no change. No dependency removal is needed (no package is used solely by the removed code; `http`, `shared_preferences` remain in use).

### 6.7 `HomeShell` default service
`HomeShell` still builds `PersistentMarketDataService(RemoteMarketDataService())`. Keep that composition (it still serves signals, courses, notes, reports, compliance). Update the comment only.

### 6.8 Tests
- `test/support/fake_market_data.dart` (267 lines, 20 hits): remove all removed-interface members and their fixture data (quotes, sentiment, breadth); keep signals, courses, notes, reports, compliance fakes. The fake `implements MarketDataService`, so it fails to compile if members mismatch; this is why it belongs in this phase.
- Update every test that still builds `Quote`, `IndexId`, `Sentiment`, `FullBreadth`, uses `lastPrice` / `percentChange` / `entryReached*` in `Signal(...)`, or uses `DataSurface` removed values: `fault_states_test.dart`, `layout_matrix_test.dart`, `home_phase3_test.dart`, `home_reports_phase4_test.dart`, `signals_section_test.dart`, `home_signals_focus_test.dart`, `weekly_reports_tab_test.dart`, `learn_phase5_test.dart` (uses the fake), `plain_labels_test.dart` (remove the `BreadthDonut` and gauge groups, lines ~20-60; keep a `ProgressRing` group if present), `identity_test.dart`.
- New `test/legacy_cache_purge_test.dart` (written, not run): seeds `SharedPreferences.setMockInitialValues` with every purged key including `market_cache::signals`, runs `LegacyCachePurge.run()`, asserts removal and that `market_cache::courses`, `::insight_notes`, `::weekly_reports`, `::compliance` survive, and that a second run does nothing.
- Add a signals-parse test: `Signal.tryParse` ignores `last_price`/`change_pct`/`entry_reached_*` if an old payload contains them, and `toJson` never emits them.

### 6.9 Files list
Updated: `market_models.dart`, `market_data_service.dart`, `persistent_market_data_service.dart`, `market_data_cache.dart`, `fault_injection.dart`, `api_service.dart`, `account_session.dart`, `main.dart`, `settings_store.dart`, `push_service.dart`, `notification_copy.dart`, `notifications_screen.dart`, `settings_screen.dart`, `ayre_signals_section.dart`, `ayre_charts.dart`, `ayre_instrument_tile.dart`, `figure.dart`, `ayre_components.dart`, `app_theme.dart`, `learn_tab.dart`, `stock_logo_service.dart`, `home_shell.dart` (comment), and the tests above. New: `lib/services/legacy_cache_purge.dart`, `test/legacy_cache_purge_test.dart`. Deleted: none (only trimming).

### 6.10 Acceptance criteria (owner-run)
- [ ] `grep -rn "getIndexBoard\|getSentiment\|getTopGainers\|getTopLosers\|getMostActive\|getFullBreadth\|getConstituents\|getEquity\|getMarket" lib test` returns nothing.
- [ ] `grep -rn "lastPrice\|last_price\|percentChange\|change_pct\|entryReached\|entry_reached" lib` returns nothing except the single "ignore old entry_reached push" branch (and comments describing it).
- [ ] `grep -rnE "\b(Quote|IndexId|Sentiment|FullBreadth)\b" lib` returns nothing.
- [ ] On a device that previously ran the old build, after first launch no `market_cache::` key for index, constituents, equity, sentiment, movers, breadth, volatility, momentum, volume_surge, or `signals` holds old data (inspect `SharedPreferences`).
- [ ] An old stored alert of kind `entryReached` no longer appears in Alerts.
- [ ] `flutter analyze` is clean; `flutter test` passes.

---

## 7. PHASE 4: Legal text, backend decommission, admin site, docs and final verification

**Status:** DONE except 7.4 (D10 not answered, skipped) (delivered 2026-10-17; not built, run or tested by the AI; owner runs the checks in 7.6/7.8).
**Codebases:** APP (legal/docs) and BACKEND (dead code) and admin SPA.

### 7.1 App legal text: `lib/legal/legal_content.dart` (993 lines), `lib/legal/legal_config.dart`
Review every mention, not only the four the Removal Plan lists (F13). For each, rewrite or delete so the text matches an app that shows **no market data**:
| Lines (verify) | Claim | Action |
|---|---|---|
| ~87-91 | "Insights: market information such as index levels, top gainers..." | Remove the Insights description; keep the sentence on notes/education if it stands alone. |
| ~175-181 | "8. Market data and third-party content": prices, index levels, movers, breadth from third-party sources | Remove or reword; keep logo attribution. Check heading numbering references (other sections may say "see section 8"). |
| ~434-443 | "Market data and indicators" bullets, sentiment indicator | Remove; keep the "market notes and educational articles are not recommendations" bullet. |
| ~523 | "Research is based on publicly available information and market data." | Review: this describes the analyst's method, not app display; keep only if accurate, flag for lawyer. |
| ~744 | Privacy: "...and cached market data so screens load quickly" | Reword to cached research content (signals, courses, notes, reports). |
| ~807 | "Market data providers and exchanges supply price data to the..." | Remove or reword (data-sources / third-party list). |
| ~955-956 | "Market data comes from third parties ... Use your broker's live price" | Keep the second sentence (sound advice); drop the claim that the app shows market data. |
- Also search `kLegalFaqs` entries (the FAQ screen renders them) for Insights, movers, sentiment, live data, "index", "gainers".
- `legal_config.dart` has blank RA placeholders (registration number, BSE enlistment, contact, grievance). **Do not invent values.** Leave blanks, list them in outstanding issues for the owner (Removal Plan section 5 and 8).
- Check tests that assert legal text (grep `test/` for legal strings; `plain_labels_test.dart`, `support_tutorials_test.dart` may). Update them.
- Draft text is for the owner's securities lawyer to review (D9); say so in the delivery.

### 7.2 Backend decommission (per D4/D5/D6 defaults; **re-verify each caller with grep before deleting**)
In `ayre-scanner-main/main.py`:
| Remove | Verified notes |
|---|---|
| Routes `api_market` (1886), `api_market_gainers` (1998), `api_market_losers` (2010), `api_market_most_active` (2022), `api_market_constituents` (2034), `api_sentiment` (3022), `api_breadth_full` (3074), `api_insights_volatility` (3095), `api_insights_momentum` (3122), `api_insights_volume_surge` (3143) | No caller in `frontend/src` (grep). |
| Helpers only they used: `_fetch_market_from_fyers/_nse/_yahoo`, `_build_market_payload`, `_read_index_board`, `_closing_board_payload`, the constituents fetch/parse (`equity-stockIndices` parsing at ~5370, 5489-5520), movers cache `_movers_lock` and its helpers (~1926-2000), `_constituents_cache`/`_constituents_lock`, `_market_cache` and the `"hardcoded_fallback"` payload (~5339) | Grep each name; do not delete anything referenced by `/api/quotes`, `/api/ranges/*`, `/api/status`, the scan loop or the sweep. Keep `_get_nse_session` while market-status or symbol fetching uses it (D5). |
| Jobs and caches that fed only those routes: `_breadth_loop` (4624) and its `threading.Thread` start (5744), `_breadth_full_cache`/`_breadth_full_lock`/`_breadth_full_cache = load_full_breadth()` (5692), `_start_market_poller` (def ~5630, call ~5750), `_close_snapshot_loop` (4965, start 5754), `_close_snapshot`, `_build_close_snapshot`, `_store_close_snapshot`, `_closing_breadth`, `load_close_snapshot()` at startup (5693) | Re-verify `_market_is_closed_now()` is still used elsewhere (it is referenced at ~4993-5025 and 4738); keep it if any other code needs it. |
| Imports of `data.breadth`, `data.market_close`, `fetch_full_market_breadth` etc., and the now-unused files `data/breadth.py`, `data/market_close.py`, `data/app_sentiment.py` | Delete a file only when `grep -rn "<module>" --include=*.py` finds no import left. Persisted JSON files they wrote (breadth, close snapshot) are listed for the owner to delete on the server; code cannot safely delete them. |
| Constants for breadth/close schedules in `config/settings.py` (`BREADTH_CHECK_HOURS`, `BREADTH_CHECK_MINUTE`, close-window settings) | Remove only if unused after the above. |
| Fyers stream accessors `movers()`, `breadth()`, `constituents()` in `data/fyers_stream.py` (lines 359, 387, and the constituents method) | Only remove if no caller remains; they may be small and harmless. If kept, say so in the log. |
**Keep:** `_fyers_stream` and its poller, `_sweep_loop`, entry-hit detection, `entry_hits.py`, history store, scanner and backtests, `/api/quotes` and quotes poller, `/api/ranges/*`, `_market_status_cache` + `_market_lock` + status fetchers (D5), universe stats generation (D6), admin routes. Update the startup banner prints and the `app.run(...)` comment (lines ~5764-5777 mention Index Detail / Equity Detail / Insights polling).
- Admin-only guarantee (Removal Plan 4.4): after the edit, list every `/api/*` route and confirm none is in `_APP_READABLE_RULES` except the seven kept; confirm `_AUTH_PUBLIC_API` is still only login, logout, session.
- If the owner chose "keep routes admin-only" for D4, instead only delete the jobs that become useless **or** leave all and record why; do not half-delete.

### 7.3 Optional hardening to propose (separate small tasks, only with owner approval)
- Document that `SCANNER_ADMIN_USERS` must be set in production (F8).
- Consider adding an automated test or startup assertion that `_APP_READABLE_RULES` is a subset of an explicit constant list (prevents silent re-addition).

### 7.4 Admin website (D10)
**Runs only if the owner answers yes to D10 (see F19).** Also requires the deferred Phase 1 task 1.5 (`entry_reached_enabled` boolean in an admin-only response). The `EntryHitsPanel.tsx` text “App users will see ‘entry reached’…” must be reworded or hidden together with the buttons.

If the hold flag should hide the entry-reached controls: `ayre-scanner-main/frontend/src/components/EntryHitsPanel.tsx` (publish at line ~147, buttons ~253-258, dialog ~294-332) and `frontend/src/types.ts` for the new boolean; `SignalsPanel.tsx` lines 468-472 show "Entry reached live" tags (admin-only, keep). The built `static/` output must be rebuilt by the owner; the executing AI provides source files only.

### 7.5 Documentation and stale references
- APP: `README.md`, `BACKEND_ANALYSIS.md`, `LEGAL_NOTES.md`, `REDESIGN_CHANGES.md` ... `REDESIGN_V5_CHANGES.md`, `ayrescan_final.md`, `ayrescan_redesign_v2.md`, `ayre_scanner_redesign_v3.md` describe the removed tabs. Add a short "Status after compliance removal" note at the top of README and BACKEND_ANALYSIS; do not rewrite history files.
- BACKEND: `README.md`, `IMPLEMENTATION_PLAN.md`, `AUTH_IMPLEMENTATION_PLAN.md`, `ayrescan_final.md`: same note; update endpoint tables in README.
- Update the in-code docstrings that still describe the removed endpoints.

### 7.6 Final verification (this is the Removal Plan checklist plus verified additions)
Owner-run:
- [ ] All Phase 1-3 checklists still pass.
- [ ] `grep -rniE "nifty|sensex|gainers|losers|breadth|sentiment|insights tab" lib` shows no user-visible text (code comments about "Market Insight" notes are fine; the string `Nifty 500` may remain only in non-user-visible doc comments).
- [ ] `grep -rn "api/market\|api/sentiment\|api/breadth" lib test ayre-scanner-main/frontend/src` returns nothing.
- [ ] Walk the app: onboarding, tour, Home, Reports, Learn, Profile, Settings, Alerts, FAQ, Legal screens: no price, level, index, mover, breadth, sentiment, or entry-reached time/price anywhere.
- [ ] Airplane-mode cold start on an upgraded install shows no cached price (purge ran).
- [ ] Production env: `SCANNER_ADMIN_USERS` set; `APP_ENTRY_REACHED_ENABLED` unset or `0`.

### 7.7 Files list
Updated: `legal_content.dart`, possibly `legal_config.dart` (comments only), legal-text tests, docs listed in 7.5; BACKEND `main.py`, `config/settings.py`, possibly `data/fyers_stream.py`, `frontend/src/components/EntryHitsPanel.tsx`, `frontend/src/types.ts`. Deleted: `data/breadth.py`, `data/market_close.py`, `data/app_sentiment.py` (only if orphaned). New: none required.

### 7.8 Acceptance criteria
As 7.6, plus: server starts cleanly and the scan loop, history loop, sweep loop, fyers-stream poller and quotes poller still start (check startup log lines); admin site still loads Signals, Entry Hits, Weekly Report, Learn, Notes panels.

---

## 8. What must be preserved (verified)

| Item | Where | Note |
|---|---|---|
| Signals with typed Entry / Exit / Stop, rationale, added date | `ayre_signals_section.dart`; `GET /api/signals` | Static analyst text only. |
| Weekly Report (admin-entered, includes losses) | `weekly_reports_tab.dart`, `ayre_weekly_report.dart`, `GET /api/weekly-report`, `data/app_weekly_report.py` | `AyreInstrumentTile` logos used here (F2). |
| Learn tab and `ProgressRing` | `learn_tab.dart`, `ayre_charts.dart` (trimmed) | F3. |
| Profile, Settings, Support, FAQ, Grievance, Legal, Research Analyst screens | unchanged except text | `ProfileTab.marketData` still needed (F16). |
| Insight notes carousel and `InsightNoteScreen` | `ayre_insight_carousel.dart`, `insight_note_screen.dart`, `GET /api/insights` | Different from the removed `/api/insights/*` metrics. |
| Alerts: new signal, revised, exit, custom | `push_service.dart`, `notification_copy.dart`, `alerts/push.py`, `manual_push.py` | Prices in them are typed by the analyst. |
| Stock logos | `assets/logos/`, `stock_logo_service.dart` | Owner checks AllInvestView attribution (Removal Plan 2). |
| Scanner, backtests, entry-hit detector, `fyers_stream`, history store, admin website | BACKEND | Internal only (Removal Plan 4.4). Their permitted use is the FYERS email question. |

---

## 9. Progress Log (to be updated by each phase)

| Phase | Status | Date | Files changed / created / deleted | Decisions taken | Outstanding issues |
|---|---|---|---|---|---|
| 1 Backend lock-down | DONE (owner to verify) | 2026-10-17 | Updated: `main.py`, `config/settings.py`, `alerts/manual_push.py`, `compliance_removal_plan.md`. New: `data/app_signal_payload.py`, `test_app_signal_payload.py`. Deleted: none. | D2 default used (flag `APP_ENTRY_REACHED_ENABLED`, off). Allow-list kept to analyst-typed keys only (`id, symbol, name, rationale, date_added, entry_*, exit_*, stop_loss` + neutral `featured/pinned/display_order`). | Task 1.5 deferred (D10 open, F19). Admin SPA still shows the entry-reached buttons; clicking returns 409 `feature_held`. `_SIGNAL_ADMIN_ONLY_FIELDS` is now unused by `api_signals_list` (left in place; remove in Phase 4 only if grep shows no user). Removed routes still run for admin sessions until Phase 4 (D4). Set `SCANNER_ADMIN_USERS` in production (F8). |
| 2 App UI + navigation | DONE (owner to verify) | 2026-10-17 | Updated (lib): `onboarding_content.dart`, `tour_content.dart`, `home_shell.dart`, `home_tab.dart` (rewritten), `onboarding_screen.dart`, `settings_screen.dart`, `support_screen.dart`, `reachability.dart`, `app_theme.dart` (comments), `ayre_bottom_nav.dart`, `ayre_charts.dart` (comment), `ayre_components.dart` (comments), `ayre_instrument_tile.dart` (comment), `ayre_signals_section.dart`, `onboarding_widgets.dart`. Updated (test): `fault_states_test.dart`, `home_phase3_test.dart`, `home_reports_phase4_test.dart`, `layout_matrix_test.dart`, `onboarding_content_test.dart`, `signals_section_test.dart`. Deleted: `lib/screens/insights_tab.dart`, `lib/screens/index_detail_screen.dart`, `lib/screens/equity_detail_screen.dart`, `lib/widgets/ayre_compact_index_card.dart`, `lib/widgets/ayre_index_art.dart`, `lib/widgets/ticker_trace.dart`, `test/compact_index_card_test.dart`, `test/insights_phase4_test.dart`, `test/insights_phase5_test.dart`. New: none. | D1 default (a) used; D8 default used. Nav is Home, Reports, Learn, Profile (Profile = index 3). Tour has 6 steps over 4 tabs. | `AyreGlyph.insights` kept (still referenced by `test/identity_test.dart` ~329 and `ayre_icons.dart`): decide in Phase 3 sweep. Orphans now unused, left for Phase 3: `DirectionBadge`, `LivePulseDot`/`ChipTone.live`, `DeltaFigure`, `AppIndexId/Tint/Tints`, `AppMotion.livePulse`, `liveMarketRefreshInterval`, `BreadthDonut`/`SentimentGauge`. `PushService.refreshSignalsRequests` listener in `ayre_signals_section.dart` kept for Phase 3. Tests needing no edit: `nav_phase2_test`, `shell_phase2_test` (follow `kNavDestinations`), `spotlight_tour_test`, `support_tutorials_test`, `plain_labels_test`, `identity_test`, `support/fake_market_data.dart`. `home_signals_focus_test.dart` unchanged: it asserts Home scrolls when focused at 390x600; Signals now sits near the top, so if it fails reduce the test viewport height. `fault_states_test` surfaces for removed screens dropped from the tests only; `DataSurface` enum values remain until Phase 3. |
| 3 App data layer + device state | DONE (owner to verify) | 2026-10-17 | Updated (lib): `main.dart`, `learn_tab.dart`, `notifications_screen.dart`, `settings_screen.dart`, `account_session.dart`, `api_service.dart`, `fault_injection.dart`, `market_data_cache.dart`, `market_data_service.dart`, `market_models.dart`, `notification_copy.dart`, `persistent_market_data_service.dart`, `push_service.dart`, `settings_store.dart`, `app_theme.dart`, `ayre_charts.dart`, `ayre_components.dart`, `ayre_instrument_tile.dart`, `ayre_signals_section.dart`, `figure.dart`. New: `lib/services/legacy_cache_purge.dart`, `test/legacy_cache_purge_test.dart`, `test/signal_model_test.dart`. Deleted: none. | Purge flag `compliance_purge_v1`. `signals` cache cleared on account switch (`AccountSession.clearLocalData`). Signal-alerts subtitle: "New signals and changes to them." (exit alerts are not gated by that switch). Removed with the orphan sweep: `TickerRow`, `DirectionBadge`, `LivePulseDot`, `ChipTone.live`, `AyreChip.pulse`, `DeltaFigure`, `AppIndexId/Tint/Tints`, `AppMotion.livePulse`, `parseIstStamp`, `RemoteMarketDataService.cadence`. `PersistentMarketDataService` constructor is now `const` and no longer purges. | The zip has no `test/` folder, so existing tests could NOT be edited: owner must update `test/support/fake_market_data.dart` (remove removed-interface members and Quote/Sentiment/FullBreadth fixtures), `fault_states_test`, `layout_matrix_test`, `home_phase3_test`, `home_reports_phase4_test`, `signals_section_test`, `home_signals_focus_test`, `weekly_reports_tab_test`, `learn_phase5_test`, `plain_labels_test` (drop `BreadthDonut`/gauge groups), `identity_test` (drop index tint test). New tests import `package:ayre_scanner/...`. Kept (may be used by unseen tests; delete if grep over `test/` is clean): `DirectionGlyph`, `formatVolume`, `AyreGlyph.insights`. The `'entry_reached'` push-type string remains in `push_service.dart` on purpose (ignore branch). |
| 4 Legal + backend decommission + final verification | DONE (owner to verify; 7.4 skipped) | 2026-10-17 | APP updated: `lib/legal/legal_content.dart`, `lib/services/market_data_service.dart` (comment), `lib/widgets/state_views.dart` (comment), `lib/widgets/ayre_insight_carousel.dart` (comment), `README.md`, `BACKEND_ANALYSIS.md`. BACKEND updated: `main.py`, `config/settings.py`, `README.md`. BACKEND deleted: `data/breadth.py`, `data/app_sentiment.py`. | Removed from `main.py`: 10 routes; `_get_movers_payload` and movers cache; `_breadth_loop`, `_refresh_full_breadth`, `_next_breadth_slot_after`; `_start_market_poller`, `_get_market_snapshot`, `_fetch_market_from_nse/fyers/yahoo`, `_build_market_payload`, `_normalize_market_item`, `_pick_number`, `_market_cache`; `_close_snapshot_loop` and its capture/store/build helpers; `_closing_board_payload`, `_closing_movers_payload`, `_closing_breadth`; `_fyers_busy_for_extras`; breadth/close constants; thread starts and startup load of breadth. Removed `BREADTH_*`, `BREADTH_FULL_FILE`, sentiment comment from settings. Kept: `_get_constituents` chain (admin stock directory), `_market_is_closed_now`, close-snapshot read helpers and `load_close_snapshot` (status endpoint, stream close window), `data/market_close.py`, market-status scrapers, `universe_stats`, `data/fyers_stream.py` accessors `movers()/breadth()`. Legal copy: removed Insights/market-data/sentiment text, terms section 8 retitled 'Third-party content', FAQ price question replaced by 'Does the app show live prices?'. | Legal wording is draft for the securities lawyer. `legal_config.dart` blanks untouched. Legal-text tests could not be checked (no `test/` folder in the zip). The index-close capture no longer runs, so `_close_window_holds_stream` never holds the Fyers socket after 15:30; harmless now. Owner: delete stale server files (`breadth_full.json`, close snapshot JSON, `app_signals.json` entry_reached_* history). Optional 7.3 hardening not done. D10/7.4 and Phase 1 task 1.5 still pending. `main.py` was checked only with `py_compile` and pyflakes (no undefined names). |

**Remaining phases:** none (open: D10 / task 1.5 / 7.4, lawyer review, server file cleanup).

---

## 10. Items outside code, or not decided by the Removal Plan (do not act on them silently)

1. **FYERS / NSE questions** (Removal Plan section 8): the owner sends them; nothing in these phases substitutes for written permission.
2. **Store submissions:** the Play Console "Data safety" form and the App Store privacy answers must match the final app (no market data shown). The Flutter project's Android/iOS metadata (`android/`, `ios/`) was not part of this analysis and is untouched.
3. **Terminology:** the app labels the profit level "Exit" while the Removal Plan says "target". Left unchanged; owner to confirm wording with the lawyer.
4. **"Buy"-worded notification copy** (`NotificationCopy._newPick` includes "Buy Setup", "Buy Signal") is outside this requirement; flagged for the lawyer's RA-compliance review only.
5. **Unlicensed sources still used internally** (D5): NSE website session, Yahoo market-status check, NSE universe fetch (`data/symbols.py`). Not removed by default; owner and lawyer decide.
6. **Unused legacy API helpers** `getLearnArticles` / `getInsights` in `api_service.dart` are left alone.
7. **Old installs and deployment order** (D7): if any real users already have the old app, deploy Phase 1 only after the new app build is available, or accept degraded screens.
8. **Persisted server files** (breadth, close snapshot, signals JSON containing historic `entry_reached_*`) remain on disk; the owner decides whether to scrub them.
