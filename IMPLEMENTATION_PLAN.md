# Ayre Scanner — Live Entry Detection: Implementation Plan

Status: **All phases 1 to 8 are COMPLETED (Phase 8 Part A, the live feed, was skipped by the owner) (implemented; awaiting owner testing — Phase 1 and 2 text below is unchanged).** 
Repos: backend = `ayre-scanner` (Flask + React website), app = `ayre-scanner-app` (Flutter).
This file is both the specification and the running record. The implementing AI updates it after every phase (see Section 9 and Section 10).

---

## Revision note (read this first)

**New owner requirement (applies to every remaining phase):** nothing is ever pushed or shown to Flutter users automatically. The system may *detect* entry-level touches by itself and show them on the admin website, but a detected signal only reaches Flutter users after the admin explicitly publishes it from the website, and any phone notification is also sent only by an explicit admin action.

**What changed in this revision**
- Phases 1 and 2 are completed. They and their progress-log entries are kept exactly as they were. Do not rewrite, redo or "improve" them.
- The old Phase 3 (detection **plus** automatic push) and old Phase 4 (Flutter + hardening) are replaced by **six** smaller phases (3 to 8), so each delivery stays small and testable:
  - **Phase 3 — Publication gate** (backend + minimal website): drafts vs published, publish/unpublish buttons, app feed shows only published signals, every automatic push removed.
  - **Phase 4 — Manual notification centre** (backend + website): every notification is sent by an admin button, with confirmation, duplicate protection, a daily cap and an audit log.
  - **Phase 5 — Automatic entry detection, admin-only** (backend only): sweep-based detection into an admin-only entry-hit store. Nothing here can reach the app.
  - **Phase 6 — Entry Hits panel and manual "entry reached" publication** (backend + website).
  - **Phase 7 — Flutter app**: show published entry-reached information, handle the new notification, fix the missing "tapped custom message" handling.
  - **Phase 8 — Live-feed speed-up (optional) and final hardening, documentation and automation audit**.
- The detection design from the earlier plan (SMA44 touch, armed set, confirmation rule, once per day, sweep as safety net, live feed for stocks that matter) is **kept**. Only its output changed: it now feeds an admin-only list, never the app.
- After Phase 3 is deployed, nothing is pushed automatically any more. Between Phase 3 and Phase 4 the admin can publish signals but cannot yet send "new signal" notifications (exit calls still work). That is intentional and safe.

---

## 0. How to read this document

- Sections 1–4: rules, the problem, verified facts from the code, and design decisions.
- Section 5: the phases (the actual work). Phases 1–2 are done; 3–6 remain.
- Section 6: everything the owner must do outside the code (Railway, Firebase, Fyers, website, app release), in simple words, separated from code changes.
- Section 7: storage-growth and safety controls that apply to every phase.
- Section 8: risks and fallbacks.
- Section 9: progress log, filled in by the implementing AI after each phase.
- Section 10: the delivery checklist the implementing AI must follow at the end of each phase.

This document contains no code on purpose. Function and file names are given only to point the implementing AI at the right place in the existing code.

---

## 1. Ground rules for the implementing AI

1. **Analyse the actual code before every decision.** The findings in Section 3 come from a first analysis and may be incomplete or wrong in details. Re-verify each one in the code you are given before relying on it. If the code disagrees with this document, follow the code, state the difference in the progress log, and explain the decision.
2. **Do not build, run, compile or test anything.** The owner does all building and testing. Write code only. Do not troubleshoot local environment setup unless there is clear evidence it is the actual problem.
3. **Give complete updated files**, never diffs or partial snippets.
4. **Preserve existing contracts.** API response shapes, saved-file formats, push payload types and app behaviour must stay backward compatible. New fields are additive only. Older installed app versions must keep working.
5. **Keep every new behaviour behind a setting** (with a safe default and an environment-variable override where the existing code does the same) so the owner can switch back to the old behaviour without a redeploy of code.
6. **Stay inside the Fyers limits** (10 requests per second and 200 per minute, with the existing 190 per minute self-limit). Every Fyers REST call added by this work must go through the same shared request pacing as the existing history calls. Do not add unpaced Fyers calls.
7. **Do not revive dead code by accident.** `scanner/trade_levels.py` is not imported anywhere and refers to settings that do not exist. Do not wire it in. Entry levels in this project are defined in Section 4.
8. **Complete one phase at a time** and stop after the deliverables of that phase. Do not start the next phase until told to.
9. **Nothing reaches Flutter users automatically. Ever.** No scan, sweep, live-feed tick, detection, timer, schedule, startup routine or background loop may (a) send a push notification, or (b) make a signal, an entry-reached fact or any other new information visible in the Flutter app. Only an explicit action by a signed-in admin on the website may do either. This is a hard rule for every remaining phase.
10. **Detection and publication are separate systems with a one-way wall between them.** Detection (Layer 1 sweep, Layer 2 live feed, the entry-hit store) writes only to admin-internal data. Publication (the admin's buttons) may *read* detection results when the admin acts, but detection code must never import, call or write to the push module, the app signals feed, or any app-facing endpoint. The app-facing endpoints must never read the entry-hit store. The implementing AI must confirm this wall in the code at the end of every phase and list the evidence in the progress log.
11. **Deliberate exceptions to rules 4 and 5.** Today, saving a signal on the website pushes automatically, and a saved visible signal is immediately visible in the app. This is intentionally removed (Phases 3 and 4). Because the owner's requirement is "never automatic", **no setting, environment variable or flag may be added that switches automatic pushing or automatic publication back on.** Everything else in rules 4 and 5 still applies: response shapes stay backward compatible (new fields are additive), signals that are live in the app when Phase 3 is deployed must stay live, and older installed app versions must keep working.
12. **Publication decisions are made on the server from the admin's identity, never from anything the app can send.** The app feed must hide drafts because the server decides it from the login/session role, not because of a query parameter or header the client controls.
13. **Every manual send is a separate, confirmed, logged action.** Server-side protection against accidental double sends is required, not only a confirmation box in the browser.

---

## 2. The problem and the chosen solution (plain words)

**Problem.** The scanner checks the market once an hour (at :30), and each check takes several minutes. Nothing compares live prices to entry levels. A stock can touch its level at 10:46 and the system only learns about it at the next scan, when the price has already moved on.

**Chosen solution: three separate layers, all on official Fyers data only.**

- **Layer 1 — minute sweep of all 500 stocks (done: Phase 2).** Every minute the backend asks Fyers for the price list of all 500 stocks (50 per request, so about 10 requests). Each reply includes the day's high and low, so a touch between two sweeps is never missed. History is downloaded once per trading day (Phase 1), stored, and the live "today" candle is built from the price list and added on top.
- **Layer 2 — live price feed for the stocks that matter (optional speed-up, Phase 8).** The Fyers live feed already runs in the backend. The stocks that matter right now (admin signals with an entry price, and scanner stocks close to their 44-day average line) are watched on that feed so touches are noticed within seconds, with no extra Fyers requests.
- **Layer 1 chooses what Layer 2 watches**, automatically, every day. Nothing is entered by hand for scanner stocks. Admin signals use the entry price the admin types.
- **Layer 3 — manual publication (new, Phases 3, 4, 6 and 7).** Detection only produces an **admin-only list of entry hits** on the website. The admin looks at each hit and decides whether to publish it to Flutter users and whether to send a phone notification. Nothing is automatic on this side.

**How data flows after this plan (in words)**
1. Detection side (automatic): sweep and live feed → entry-hit store (admin-only file) → website "Entry Hits" panel. Optionally an email to the admin. **It stops here.**
2. Publication side (manual): the admin presses a button on the website → the backend checks the admin login and the guards → the backend updates the signal's public fields and/or sends a push through the existing push module → the app sees it the next time it loads Signals, or receives the notification.
3. The only place the two sides meet is the admin's button press, which reads a hit and copies a few facts onto a signal record.

---

## 3. Facts from the first code analysis (verify each before use)

**Scheduling**
- `main.py` `_scan_loop`: passive checks (market status from Yahoo/NSE, no Fyers) hourly from 16:00 and at 09:15. Once the market is source-confirmed open, it re-authenticates Fyers once per trading day (automatic TOTP + PIN login in `auth/fyers_auth.py`, `reconnect_fyers`) and then runs `_do_scan` at fixed slots 09:30, 10:30 … 15:30 IST. If a scan is still running at the next slot, the slot is skipped.
- `_breadth_loop`: 09:45, 10:45 … 14:45. Only computes advances, declines and average change (about 10 quote calls). It does not generate signals. This is the "10:45" job. It is not a gainers/movers scan.
- Gainers, losers and most-active are computed on request from live WebSocket ticks (`_get_movers_payload`, `data/fyers_stream.py`).
- A quotes poller refreshes prices for scanner signals and watchlist every 15 seconds with one REST call, display only (`data/quotes.py` `fetch_ltp_bulk`, which silently keeps only the first 50 symbols).

**History download and storage (important for Phase 1)**
- `data/candles.py` downloads daily candles per symbol in two windows of 366 days each (`_WINDOW_DAYS`, `_NUM_WINDOWS`), so about two years. A live scan downloads the recent window for all ~500 stocks and the older window only if needed. The older window is cached **in memory only**, per day (`_W1_CACHE`), and is lost on restart.
- **There is no on-disk candle history today.** The only disk files related to scanning are the symbol resolution cache (`data/symbol_cache.py`), the Fyers symbol master CSV (`data/fyers_master.py`), and per-date saved scan results (`data/scan_store.py`, folder from `SCAN_RESULTS_DIR`).
- Backtests use a separate path (`fetch_candles_bulk_at_date`, non-live) that downloads history as of a past date and does not use the live cache or the symbol cache. Backtest page state and saved results are separate files.
- `config/persistence.py` routes only small named files to the Railway Volume (`PERSISTENT_DATA_DIR` or Railway's `RAILWAY_VOLUME_MOUNT_PATH`). `resolve()` only handles flat file names. Scan results, caches and logs are deliberately **not** routed there today.

**Entry levels**
- The scanner has no entry level. "Entry" is the admin-typed `entry_price` on a curated signal (`data/app_signals.py`; website `SignalsPanel.tsx`; `/api/signals` in `main.py`). Nothing compares live price to it.
- Push notifications are sent only when an admin publishes or revises a signal (`alerts/push.py`, `_push_signal_if_due`, `set_push_state`, `_signal_was_revised`). The scan loop never sends pushes.
- Scanner conditions (`scanner/conditions.py`): C1 SMA44 rising; C2a day's low within `SMA44_SUPPORT_BUFFER_PCT` of SMA44; C2b close at or above SMA44; C3 bullish MACD crossover within the lookback. Stocks passing C1 and C2 but not C3 go to the watchlist.
- The scanner already reconstructs the true trigger minute after the fact using one 1-minute history call per new signal (`_reconstruct_trade_ready_time` in `main.py`).

**Live feed**
- `data/fyers_stream.py` `FyersMarketStream` runs one WebSocket with about 64 symbols (3 indices plus about 61 unique equities from Nifty 50, Sensex 30, Bank Nifty), cap `_MAX_WS_SYMBOLS` = 200. The docstring claiming about 140 symbols is wrong. Subscriptions are set only when the socket opens and are rebuilt when the token changes. Ticks carry last price, day high, day low, open, volume.
- Fyers support pages disagree on the per-connection limit (50 vs 200). The code already subscribes more than 50 so 200 appears to work. The implementing AI must not assume more than 200 on one connection.

**Request cost today (approximate)**
- Scan: about 500 calls per scan, about 1,000 on the first scan of the day, 7 scans per day, paced at 190 per minute (a scan runs about 2.6 to 5.3 minutes). Breadth about 60 per day. Quotes poller about 1,500 per day. `/api/signals` price enrichment is on request.
- The pacer (`_PACER` in `data/candles.py`) covers only history calls. The quotes poller and `/api/signals` calls bypass it.

**Process model**
- `nixpacks.toml` starts `python main.py --port $PORT` (single process, threaded Flask, module-level state). Railway Volumes cannot be used with replicas, so single instance is also a requirement.

### Additional facts from the second analysis (verify each before use)

**Automatic push and publication paths that exist today and must be removed or neutralised (Phases 3 and 4)**
- `main.py` `api_signals_add` calls `_push_signal_if_due` after every save. A saved, visible signal triggers a "new signal" push by itself; an edit to symbol, entry, exit or stop-loss of an already announced signal triggers a "revised" push by itself (`_signal_was_revised`, `_SIGNAL_REVISION_FIELDS`).
- `_push_pending_loop` (started at the end of `main.py`) checks every 60 seconds and pushes scheduled signals when their `start_at` passes.
- `data/app_signals.py`: a signal is visible to the app when `enabled`/`active` is true and the optional `start_at`/`end_at` window allows it. There is no "draft" state, so every saved signal that is enabled is user-facing at once. `set_push_state` stores `push_sent_at` / `push_pending`.
- Already manual: exit calls (`/api/exits`, `ExitCallsPanel.tsx`, with a confirmation box) and the custom broadcast endpoint `/api/push/send`. `/api/push/send` and `/api/push/status` have **no screen on the website** today.
- Push audience: `signal` and `signal_update` go to devices whose "New signal alerts" switch is on (the `signals` flag stored in `data/app_devices.py`); `exit` and `general` go to every registered device. `alerts/push.py` keeps the last send result in memory only.
- Duplicate-send protection on the server does not exist for exit calls or custom messages; only the browser confirmation and a "submitting" flag.

**Flutter behaviour (`ayre-scanner-app`)**
- `push_service.dart` knows four types: `signal`, `signal_update`, `exit`, `general`. Foreground messages are recorded in the in-app Alerts list and shown as a banner. A tap from background or closed state handles only `signal` (opens Signals), `signal_update` (records, opens Signals) and `exit` (records, opens Alerts). **A tapped `general` message is not recorded and does nothing**, and a message that arrives in the background and is swiped away is never recorded for `exit`/`general`.
- An unknown type that carries a title and body is shown by the phone's OS in the background and is treated as `general` in the foreground, so older app builds do not crash on a new type.
- The Settings switch "New signal alerts" is sent to the backend as the `signals` flag and only controls `signal` and `signal_update`. The Signals tab loads on open and on resume (no timer). `SeenSignalsStore` adds an Alerts-list entry when a signal appears in the Signals feed that the app has not seen before, even without a push.
- The app's persisted notification log parses the notice kind by name from a fixed list, so a new kind must be added in a way that older stored entries still load.
- **Verify in code:** that no app-facing endpoint exposes scan results, the watchlist or any detection data. As far as this analysis saw, the app reads signals only from `/api/signals`.

---

## 4. Design decisions and defaults

| Topic | Decision |
|---|---|
| Entry rule for scanner stocks | Touch of the 44-day average line: the day's low reaches within the existing `SMA44_SUPPORT_BUFFER_PCT` of the live SMA44. Live SMA44 = average of the previous 43 stored closes plus the live price. This is the same math the scanner uses. |
| Entry rule for admin signals | The admin-typed `entry_price`. Direction (price must rise to it, or fall to it) is stored at arming time from where the live price is relative to the entry. A touch that already happened before arming (the day's high/low was already past the level when the signal was armed) must **not** be reported as a new hit. The implementing AI decides exact field names after reading `app_signals.py`; fields are additive. |
| What counts as a detection ("entry hit") | Scanner stock: the touch happened **and** a full scanner evaluation on the provisional live bar says signal (C1, C2, C3 pass). A touch where C3 has not crossed stays on the watchlist and records no hit. Admin signal: entry level reached. **A hit is only a record on the admin website. It is never a notification and never visible in the app.** |
| Frequency | One hit per stock per day. Editing an admin signal's entry price re-arms it. |
| When detection stops for an admin signal | After its entry-reached has been published or dismissed by the admin, detection for that signal stops until the entry price is edited or the admin presses "re-arm". Signals are not watched forever: they are armed only while active and younger than `ENTRY_ARM_MAX_AGE_DAYS` (default 10) and not past `end_at`. |
| Noise control | No detection before the market is source-confirmed open and not before about 09:15:30 (opening auction noise). Live-feed hits (Phase 8) also need confirmation (default: two consecutive ticks or about 3 seconds). Both are settings. |
| Sweep interval | Default 60 seconds (Phase 2 setting `SWEEP_INTERVAL_SECONDS`; the owner may raise it to 120 if CPU is high). About 10 requests per sweep. |
| Arming distance | Default: scanner stock is armed when C1 passes and price is within about 3% above its SMA44 line. Setting-controlled. |
| Daily history job timing | Once per trading day, early morning before the market opens (target about 08:45 IST). Done in Phase 1. |
| History retention | Exactly one stored generation at any time (the latest). No dated archives. |
| Fallback | If the history store is missing, stale or invalid, the system automatically behaves like today (history-based hourly scan). Detection switches on only when the sweep is healthy. |
| **Who can see what** | **Flutter users see only signals the admin has published.** Drafts, entry hits, scanner detections and detection details are admin-only. |
| **Publication model** | A signal record has a publication state: **Draft** (admin only), **Published** (in the app feed), **Hidden/deactivated** (as today). New signals start as Draft. Signals that are live in the app when Phase 3 is deployed are treated as Published (legacy records without the new field count as Published, so nothing disappears). |
| **Publishing vs notifying** | Two separate admin actions: "Publish to app" (Phase 3; makes it appear in the app, and from Phase 4 a phone notification is sent only if the admin ticks the box in the confirmation) and "Send notification" (a phone notification for something already published). Both are manual, confirmed and logged. The tick box defaults to **off**. |
| Edits to a live signal | Saving an edit to a Published signal changes what app users see immediately (the admin pressed Save on a live record, and the website warns about it). Saving **never** sends a notification. A "Send update notification" button appears when price levels changed since the last notification. Staged (draft-copy) editing is out of scope. |
| Scheduled signals (`start_at`/`end_at`) | Kept only as a visibility window that the admin sets as part of publishing. Reaching `start_at` **never** sends a notification. The scheduled-push loop is removed. |
| Entry-reached publication | Manual, for admin signals only, and only after the signal itself is Published. The admin copies the detection facts (time, price at detection, "extended" flag) onto the signal, optionally sending a notification of the new type `entry_reached` (final name recorded in the log). Detection source (live feed / sweep) stays admin-only. |
| Scanner-stock hits | Website only. To reach app users, the admin presses "Create draft signal" on the hit, reviews and edits it, then publishes it with the normal Phase 3 and 4 buttons. There is no setting that sends scanner hits to the app. |
| Notification wording | Informational only ("level reached"), not advice, in line with the app's compliance content. Title and body always present so older app versions show something sensible. |
| Notification audience | `signal`, `signal_update` and `entry_reached` go to devices with "New signal alerts" on. `exit` and custom messages go to every registered device (unchanged). |
| Manual-send safety | Server-side duplicate window (default 60 s for the same type and stock), "send again" needs an explicit flag, a daily cap on manual sends (default 30), and a bounded audit log of every send. |
| Staleness warnings for entry-reached | The admin sees "reached N minutes ago" and the "extended" flag. Warning when the hit is older than `ENTRY_STALE_MINUTES` (default 15) or the price has moved more than the extended percentage past the level. A **notification** is blocked after the market close; the admin may still publish the fact without a notification. |
| Admin awareness | Entry Hits panel on the website (refreshes by asking our own backend, never Fyers), optional sound and tab-title counter, optional admin email via the existing email alert channel (default off). All admin-only. |
| Live feed (Layer 2) | Optional speed-up in Phase 8, off by default. The sweep alone is sufficient for the manual workflow because the admin needs tens of seconds to react anyway. |

---

## 5. Phases

**Phase map**

| Phase | Name | Type | Status |
|---|---|---|---|
| 1 | Daily history store and bootstrap | Backend | ✅ Completed |
| 2 | One-minute sweep of all 500 stocks (Layer 1) | Backend | ✅ Completed |
| 3 | Publication gate (drafts, publish/unpublish, no automatic pushes) | Backend + minimal website | ✅ Completed (awaiting owner testing) |
| 4 | Manual notification centre | Backend + website | ✅ Completed (awaiting owner testing) |
| 5 | Automatic entry detection, admin-only (sweep-based) | Backend | ✅ Completed (awaiting owner testing) |
| 6 | Entry Hits panel and manual entry-reached publication | Backend + website | Not started |
| 7 | Flutter app | Flutter | Not started |
| 8 | Live-feed speed-up (optional) and final hardening | Backend + docs | Not started |

Phases 3 and 4 can be tested at any time of day. Phases 5 and 6 must be tested on a trading day during market hours. Phase 7 needs a real phone.

---

### Phase 1 — Daily history store and bootstrap (foundation) — ✅ COMPLETED

> ✅ **ALREADY COMPLETED (implemented; awaiting owner testing). Do not rewrite, redo or modify this phase.**

**Goal.** Create a safe, small, on-disk store of daily candle history for the scanner universe that is downloaded once per trading day, replaces the previous day's copy, survives restarts, and is loaded into memory for the live logic. Nothing else changes in scanner behaviour in this phase.

**Why.** Layer 1 only works if the unchanged past days are available without being re-downloaded every scan. Today history exists only in memory and is partly re-fetched every scan.

**Analysis the implementing AI must do first**
- Read `data/candles.py` end to end: windows, W1 cache, resume stash, per-symbol fetch, stale-bar handling, symbol resolution, pacing, cancellation, and what the live path returns to `scanner/engine.py` `run_scan`.
- Read `scanner/engine.py` and the `indicators` code to see exactly what columns and index the evaluation expects (including how the forming "today" bar is treated) so the store can reproduce the same input.
- Read `config/persistence.py`, `config/settings.py` and `data/scan_store.py` to understand what is on the volume and what is on local disk.
- Read how the universe (about 500 symbols) is built and refreshed, and how Fyers symbols are resolved (`data/symbol_cache.py`, `data/fyers_master.py`).
- Read the backtest flow (`_run_backtest_job` and its fetch path) and confirm it never touches the live cache.
- Check `requirements.txt` for what file formats and compression are already available. Do not add a heavy new dependency unless clearly necessary. Choose a compact format (target well under 50 MB for the whole store).

**Changes (backend)**
1. **New history store module** that owns: the store location, the manifest, writing, loading, validation, replacement and clean-up.
   - Location: a dedicated subfolder under the persistent data directory (`data_dir()` from `config/persistence.py`). If persistence is off, fall back to a local folder with a loud log line. Do not use the flat `resolve()` helper for a folder.
   - Contents per generation: for each stock, its daily candles up to the last completed trading day, its resolved Fyers symbol, and the universe list. A small manifest records generation id, "as of" date (last completed session), creation time, symbol count, and a checksum or size per file.
2. **Daily bootstrap job** (new scheduled thread, or an extension of the existing scheduler loop, whichever the code supports more cleanly):
   - Runs once per day at the configured early-morning time on weekdays.
   - Authenticates with the existing automatic login. Today the daily login happens only after the 09:15 market-open confirmation; the implementing AI must make the token available earlier for this job without breaking the existing once-per-day login logic.
   - **New-session probe (1 request):** fetch the latest daily bar of a liquid index or stock and compare its date with the store's "as of" date. If nothing new has completed (weekend, holiday, or already refreshed), skip the full download. This avoids burning about 1,000 requests on non-trading days and avoids needing a holiday calendar.
   - If a new session exists, download the full history (both windows, the same range logic as today's live fetch but ending at the last completed session), through the shared request pacing, honouring cancellation and the existing retry behaviour.
   - Catch-up: if the backend starts (or is redeployed) later in the day and the store is missing or older than the last completed session, run the same download immediately instead of waiting for tomorrow.
3. **Safe replacement (single generation, atomic).**
   - Download into a staging area, never into the live generation.
   - Validate before publishing: minimum coverage of the universe (setting, default high, for example 95%), each stock's last bar date equals the expected "as of" date or is explainably stale, no empty or corrupt files, row counts plausible.
   - Publish by writing the new manifest atomically (use the existing atomic write helper pattern), then load the new generation into memory with a single reference swap. A running scan or sweep keeps using the in-memory snapshot it already holds and picks up the new one at its next run. Nothing reads files directly during a scan, so replacing files cannot break an active scan.
   - Only after publishing, delete the previous generation and any staging leftovers. Peak disk use is two generations for a short time.
   - On startup, clean orphans: staging folders and any generation not referenced by the manifest.
   - Before a download, check free space on the volume and abort with a clear log line if it is below a setting (default generous, for example 200 MB). A failed download must leave the previous valid generation untouched.
4. **In-memory access layer** that returns, for a symbol, the stored history as the same kind of table the engine expects, plus metadata (as-of date, resolved Fyers symbol). Include a function that reports store health (valid, as-of date, age, symbol count) for later phases and for the status endpoint.
5. **Do not change backtesting.** Backtests keep their own fetch path. The store holds only the latest generation and cannot serve past-date backtests, so it must not be wired into backtests. Add a short note in code comments and the progress log confirming this was checked.
6. **Status visibility:** add the store health to an existing status/health endpoint in an additive way, or add a small read-only endpoint, whichever matches existing conventions.
7. **Settings** for: enable flag, download time, minimum coverage, minimum free space, store folder name.

**Changes (Flutter).** None.

**Dependencies.** None.

**Done when**
- The store can be created, validated, atomically replaced and cleaned up; only one generation exists after a successful run.
- The bootstrap job decides correctly between "skip" and "download" using the probe, and catches up after a late start.
- Restart recovery loads the stored history without any Fyers calls.
- Existing scans, breadth, quotes poller, WebSocket and backtests behave exactly as before.
- Disabling the setting returns the system to today's behaviour.

**Owner verification checklist (owner tests, not the AI)**
- After deploying, check logs the next morning for the probe result and download progress, and the volume usage in Railway.
- Redeploy mid-day and confirm the store reloads from disk with no re-download.
- Run a backtest and confirm it still works.

**Deliverables.** Per Section 10.

---

### Phase 2 — Layer 1: the one-minute sweep of all 500 stocks — ✅ COMPLETED

> ✅ **ALREADY COMPLETED (implemented; awaiting owner testing). Do not rewrite, redo or modify this phase.**

**Goal.** Replace the hourly history-based scan with a quote-based sweep that evaluates all ~500 stocks every minute, producing the same results, state and saved outputs as today's scan, with automatic fallback to the old scan when the store is not valid.

**Why.** This delivers whole-universe, gap-free coverage at a request cost close to today's, and frees the request budget the hourly scan consumes. It also produces the per-stock data Layer 2 needs (SMA44, distance to the line, day high and low).

**Analysis the implementing AI must do first**
- Re-read `_do_scan` in `main.py` and everything it updates: scan state, signals, watchlist items, universe stats and insights, scan progress, alert logging, new-signal detection, trade-ready-time reconstruction, saved results (`data/scan_store.py`), and end-of-session finalisation. The sweep must keep all of these contracts.
- Read `scanner/engine.py` `run_scan` and `scanner/conditions.py` so the sweep reuses the same evaluation code on the same shaped input. Do not reimplement the conditions.
- Read `data/quotes.py` (`_parse_fyers_quote_row`, `fetch_ltp_bulk`, `fetch_constituents_quotes_bulk`, `fetch_full_market_breadth`) to confirm exactly which fields a quote reply provides (last price, open, high, low, volume, previous close, timestamp) and how many symbols each call accepts.
- Check how the engine treats the forming bar today, particularly volume and which price is used as the close, so the sweep-built bar matches it. If the history API and the quote data can differ (volume especially), design a shadow comparison.
- Check CPU cost of recomputing indicators for about 500 stocks per sweep. If a full recompute per sweep is too heavy for the Railway plan, design incremental computation from stored past state, but only if exact parity with the engine's results is preserved. Parity beats speed.
- Read the pacing code and decide how quote calls join the shared pacer.

**Changes (backend)**
1. **Sweep engine** (new module plus integration in `main.py`):
   - Each sweep: request quotes for all stocks in batches (using the Fyers symbols stored in Phase 1), build today's provisional candle per stock, append it to that stock's stored history, and run the existing evaluation. Stocks with no trade yet today, or a stale quote timestamp, are handled the same way the current code handles stale bars.
   - Produce the same outputs the hourly scan produces: signals, watchlist, universe stats, insights, saved result for the date. Saving to disk must overwrite the single per-date file at a sensible cadence (not necessarily every sweep) and finalise after the close.
   - Keep new-signal handling as today, including the one 1-minute history call per new signal for the true trigger minute. Make sure a stock that flickers in and out between sweeps does not create duplicate "new signal" events.
2. **Scheduling.** Sweeps run every configured interval (default 60 s) only while the market is source-confirmed open, never overlap, and respect the existing locks so sweeps, the legacy scan, backtests and breadth do not collide. Define clearly what happens when a sweep is still running at the next tick (skip, as the scan loop does).
3. **Automatic fallback.** If the history store is not valid for today, or the sweep fails repeatedly, the existing hourly history-based scan keeps working exactly as before. A setting can force either mode. Log which mode is active.
4. **Reuse sweep data to cut other calls.**
   - Breadth: derive advances, declines and average change from the same sweep quotes, removing the separate 10-call hourly breadth fetch once verified. Keep the breadth endpoint's output unchanged.
   - Quotes poller: the sweep already holds fresh prices for every scanner stock, so the 15-second poller's data can come from the sweep. Remove or reduce that Fyers call only when the sweep is healthy.
   - Fix the silent 50-symbol truncation risk wherever batching is touched.
5. **Shadow/parity mode** (setting): for a configurable period, run the sweep alongside the old hourly scan and log differences in signal and watchlist membership per slot, without publishing sweep results. The owner uses this to gain confidence before switching on. This must not double the Fyers load beyond the budget: the old scan already runs hourly, the sweep adds about 10 calls per minute.
6. **Request budget safeguards.** All sweep calls go through the shared pacer. Add a counter/log of Fyers calls per minute so the owner can see headroom. Back off sweeps (never the legacy scan) if the budget is under pressure or Fyers returns rate-limit errors.
7. **Concurrency.** The sweep runs in its own thread, does not hold locks while waiting on Fyers, and must not starve Flask request threads (heavy dataframe work chunked or yielded). Publish results by swapping a complete result object, not by mutating shared state in pieces.

**Changes (Flutter).** None. Existing endpoints keep their shape. If the app shows a "last scanned" time, make sure it now reflects the latest sweep.

**Dependencies.** Phase 1 (valid history store).

**Done when**
- With the setting on and a valid store, results refresh about every minute, with the same fields and files as before.
- With the store invalid or the setting off, behaviour equals today.
- Breadth and price data no longer need their own separate Fyers calls while the sweep is healthy.
- Total Fyers calls per minute stay well under 200, and a log shows it.
- Shadow mode can show owner-readable differences against the legacy scan.

**Owner verification checklist**
- Run shadow mode for at least one full session, review the difference log, then switch on.
- Check the website and app show updated signals and times during the day.
- Watch Railway CPU and memory for the first sessions.

**Deliverables.** Per Section 10.

---

### Phase 3 — Publication gate: drafts, publish/unpublish, no automatic pushes (backend + minimal website)

**Goal.** Make "visible to Flutter users" an explicit admin decision, and remove every automatic push. After this phase: a newly saved signal is a Draft that only the admin sees; the admin publishes it with a button; saving, editing and scheduling never notify anyone.

**Why.** Today a saved visible signal appears in the app immediately and triggers a push by itself. The owner requires that nothing ever reaches users without a manual action. This phase builds the wall that all later phases rely on.

**Analysis the implementing AI must do first**
- `data/app_signals.py`: `_is_visible`, `_normalize_signal`, `load_signals(active_only)`, `add_signal`, `update_signal`, `delete_signal`, `set_push_state`. Understand exactly how `enabled`, `active`, `start_at`, `end_at` interact today.
- `main.py`: `GET /api/signals` (who calls it: the app, the website, or both, and how the code tells them apart), `api_signals_add`, `api_signals_delete`, `_push_signal_if_due`, `_signal_was_revised`, `_push_pending_loop`, the thread start at the end of `main.py`, `_is_admin`, and `g.app_user`. List **every** call into `alerts/push.py` in the whole backend.
- Website: `SignalsPanel.tsx`, `SignalCard.tsx`, `types.ts`, `App.tsx` (how signals are loaded and saved).
- Flutter (read only, no changes): `api_service.dart`, `market_data_service.dart`, `market_models.dart` to confirm the app reads signals only from the feed endpoint, and that unknown or missing fields do not break parsing.
- Confirm the app never receives scanner results, the watchlist or any detection data from any endpoint. If it does, report it in the progress log and stop to ask the owner.

**Changes (backend)**
1. **Publication state on signal records (additive).** Add fields for: published or not, when, and by whom (names decided after reading the code). New signals are created as Draft. **Legacy records that have no such field are treated as Published**, so every signal currently live stays live after deploy. Keep `enabled`/`active` meaning "hidden/deactivated" exactly as today. Add an "unpublish" state change (back to Draft; hidden from the app, kept for the admin).
2. **App feed gating.** The app-facing feed returns only signals that are Published **and** visible by the existing rules. Drafts, unpublished and deactivated signals are never returned. The admin website gets a separate admin view (or the same endpoint with an admin-only mode) that returns everything with its publication state. Which one is returned is decided on the server from the admin login, never from a client-supplied parameter (Ground rule 12). The response shape for the app is unchanged.
3. **Publish and unpublish endpoints (admin only, additive).** Publish: makes a Draft visible in the app. It requires an explicit confirmation field in the request so a stray call cannot publish. It sends **no notification** in this phase. Unpublish: hides it from the app, sends nothing. Both write one line to the normal log.
4. **Remove every automatic push.** Saving or editing a signal must never call the push module. Remove `_push_pending_loop` and its thread start. Keep `_signal_was_revised` and the revision-field list because Phase 4 uses them to show "changed since last notification". Keep the `notify_*` functions in `alerts/push.py` unchanged (Phase 4 calls them from admin buttons). After this phase the only calls into the push module are the exit-call endpoint and the custom broadcast endpoint, both already admin-only.
5. **Scheduling.** `start_at` and `end_at` remain a visibility window the admin sets while publishing. Reaching `start_at` sends nothing.
6. **Deploy safety.** Signals that carry old "push pending" or "push sent" markers must not cause any send after deploy. Verify that nothing runs at startup that could send. Existing signals keep their current look in the app.
7. **No setting that turns automatic pushing back on** (Ground rule 11).

**Changes (website, minimal)**
- Signals panel shows a clear state label on every signal: Draft, Published, or Hidden.
- Saving a new signal creates a Draft and says so ("Not visible in the app yet").
- "Publish to app" and "Unpublish" buttons, each with a confirmation box ("Users of the app will see this signal. No notification is sent."). A busy state prevents double clicks.
- When editing a Published signal, show a visible warning: "This signal is live. Saving changes what app users see. No notification is sent."
- Keep the page's existing look and structure. The notification tick box and "send" buttons arrive in Phase 4.

**Changes (Flutter).** None. The feed shape is unchanged. A signal that is published later simply appears the next time the Signals tab loads.

**Dependencies.** None on Phases 1 and 2 beyond the existing codebase. Can be tested at any time, including outside market hours.

**Done when**
- A newly saved signal is not returned by the app feed and nobody receives a push.
- Publishing makes it appear in the app feed with no push. Unpublishing removes it.
- Editing a Published signal changes what the app sees and sends no push.
- A scheduled signal reaching `start_at` sends nothing.
- Signals that were live before the deploy are still live.
- A search of the code shows no automatic path into the push module.

**Owner verification checklist**
- Before deploying: take a Railway Volume backup (this phase changes the signals file) and write down which signals are live in the app today.
- After deploying (outside market hours, after 15:45 IST): the same signals are still live. Create a test signal and confirm it is a Draft: not visible in the app, no phone notification.
- Publish it: it appears in the app after opening or resuming the Signals tab, with no notification. Unpublish it: it disappears.
- Edit a published test signal's price: the app shows the change, no notification arrives.
- Restart the service (redeploy) and confirm no notification is sent.
- Tell everyone who uses the admin website that saving no longer notifies.

**Deliverables.** Per Section 10.

---

### Phase 4 — Manual notification centre (backend + website)

**Goal.** Every notification is sent by an explicit admin button, with confirmation, server-side duplicate protection, a daily cap and a permanent (bounded) record of what was sent.

**Why.** Phase 3 removed the automatic pushes. This phase gives the admin the manual replacements and makes manual sending safe, because a push cannot be recalled.

**Analysis the implementing AI must do first**
- `alerts/push.py` end to end: `send_to_devices`, `notify_new_signal`, `notify_revised_signal`, `notify_exit`, `broadcast`, `last_send`, the wording pools, and the audience rules (`signals` flag via `list_devices(topic="signals")`).
- `main.py`: `/api/exits`, `/api/push/send`, `/api/push/status`, `device_count`.
- `ExitCallsPanel.tsx` (the existing pattern: confirmation box, busy state, result message) and `SignalsPanel.tsx`.
- `config/persistence.py` and `data/app_exits.py` (how a small capped history file is kept on the volume), to copy the pattern for the audit log.
- Phase 3 results in the progress log (the publication fields and the endpoint names).

**Changes (backend)**
1. **One shared guard for every manual send** (new small module or helper): admin login required; an explicit confirmation field in the request; a short duplicate window (default 60 seconds, same type and stock) that rejects accidental repeats; a "send again" flag required to re-send something already sent; a daily cap on manual sends (default 30, refuses clearly when reached); and clear error messages the website can show. Exit calls and custom messages go through the same guard.
2. **Manual notification endpoints (admin only, additive):**
   - Send the "new signal" notification for a **Published** signal that has not been announced yet.
   - Send the "update" notification for a Published signal whose symbol or price levels changed since the last notification (uses the revision logic kept in Phase 3).
   - Extend Phase 3's publish endpoint with an optional "also send notification" choice that performs the same action as the first bullet, in one step. Default is off.
   - Keep the existing exit-call endpoint and the custom-message endpoint; route them through the shared guard.
   Notification types and payloads stay exactly as they are today (`signal`, `signal_update`, `exit`, `general`), so existing app builds keep working. A Draft or unpublished signal can never be announced.
3. **Markers on the signal record (additive):** when the "new" and "update" notifications were sent and the price levels at that time, so the website can show "changed since last notification".
4. **Audit log (new file on the volume, bounded):** one entry per manual send: time, admin username, type, stock, audience size, delivered / failed counts, and the reason if refused. Capped (default 500 entries, oldest dropped). `last_send` in memory resets on restart; the audit log replaces it for display.
5. **`/api/push/status` extended (additive):** push configured or not, registered device count, sends today versus the cap, and the most recent audit entries.
6. **Settings (with safe defaults):** daily manual cap, duplicate window, audit log file name and size cap. Suggested names: `PUSH_DAILY_MAX_MANUAL` = 30, `PUSH_DUPLICATE_WINDOW_SECONDS` = 60, `PUSH_AUDIT_LOG_FILE` = push_audit_log.json, `PUSH_AUDIT_LOG_MAX` = 500 (final names recorded in the log).
7. **Log every send and every refusal** (rate-limited).

**Changes (website)**
- Publish confirmation gets an **"Also send a phone notification" tick box, default off**, plus a short statement of who will receive it ("N phones with signal alerts on").
- On a Published signal: a "Send notification" button (if not yet announced) and a "Send update notification" button (visible only when levels changed since the last notification), each with a confirmation box and a busy state. After sending, show "Sent at HH:MM to N phones"; the button then asks for an extra confirmation to send again.
- A **Notifications panel** (new, near the Exit Calls panel): custom message form (title and body, confirmation, result), push status (configured, device count, today's sends versus the cap) and the recent audit entries. This gives the existing custom-message endpoint its first screen.
- Existing Exit Calls panel keeps working, and shows the new duplicate-window and daily-cap messages when they apply.

**Changes (Flutter).** None.

**Dependencies.** Phase 3.

**Done when**
- Nothing can be sent except through an admin button, and every send appears in the audit log.
- A double click or a repeated request does not send twice. The cap and the "send again" rule work.
- A Draft or unpublished signal cannot be announced.
- Existing app builds still show all four notification types correctly.

**Owner verification checklist**
- From the Notifications panel send a custom test message to your own phone (this also proves Firebase end to end). Check it arrives with the app in the foreground, background and closed.
- Publish a test signal with the notification box off: no notification. Press "Send notification": one arrives. Press it again: refused until you confirm "send again".
- Change the test signal's price and press "Send update notification".
- Check the audit entries match what you sent. Deactivate the test signal afterwards.

**Deliverables.** Per Section 10.

---

### Phase 5 — Automatic entry detection, admin-only (backend only)

**Goal.** Detect, automatically and without extra Fyers requests, when an admin signal's entry level or a scanner stock's 44-day average line is touched, record each touch **once per day** in an admin-only store, and expose it to the admin website. This phase must not be able to notify or publish anything.

**Why.** This is the original purpose of the project: noticing touches within about a minute instead of at the next hourly scan, using the Phase 2 sweep that already holds fresh prices and day highs and lows for all 500 stocks.

**Analysis the implementing AI must do first**
- `scanner/sweep.py` and `main.py` `_sweep_tick` / `_sweep_loop`: what each sweep produces, the latest quote per stock (`RUNTIME.rows`: last price, day high, day low), the SMA44 value per stock, health checks, and where a per-sweep hook can run **without extra Fyers requests**.
- `scanner/engine.py` `run_scan`, `scanner/conditions.py`: how signals and watchlist items are determined on the provisional bar, and the existing "new signal" event and once-per-day alert log. **Decide how a scanner hit relates to that existing event** and reuse the sweep's evaluation result instead of evaluating again, to save CPU and avoid two different answers.
- `data/app_signals.py` and how admin signals are created, edited, deactivated and deleted (arming and disarming follow these).
- `alerts/notify.py` (existing email and sound alert channel, for the optional admin email), `config/persistence.py`, `alert_log.json` handling.
- Confirm in the code that nothing in the push module or the app feed will be imported by the new detection code (Ground rule 10).

**Changes (backend)**
1. **Armed set manager (new module).**
   - Admin signals: any signal that is not deactivated, has an entry price, is younger than `ENTRY_ARM_MAX_AGE_DAYS` (default 10), is not past `end_at`, and is not already "done" (entry-reached published or dismissed). **Drafts are armed too**, so the admin can see a touch before deciding to publish. Direction (price must rise to, or fall to, the level) is stored at arming time from the live price. Baseline day high/low are recorded at arming time so a touch that happened **before** arming is never reported as new.
   - Scanner stocks: C1 passing and price within the arming distance above the live SMA44 line, plus current watchlist and signal stocks, refreshed from each sweep result.
   - Arming and disarming follow admin changes (create, edit entry price → re-arm, deactivate, delete) and the daily reset.
2. **Detection inside the sweep.** On every healthy sweep, apply the touch rule to armed stocks using the quote's day high and low, so a touch between sweeps is not missed. Rules: market source-confirmed open; not before about 09:15:30; quote timestamp must be fresh; one hit per stock per day. Scanner stocks: touch **and** evaluation says signal (watchlist-only touches record nothing). Detection runs only while the sweep is healthy; in fallback mode (hourly scan) it does nothing.
3. **Entry-hit store (new, admin-only, on the volume).** Each record: stock, kind (admin signal or scanner), linked signal id if any, the level, direction, time of detection, price at detection, which source detected it, an "already extended past the level" flag (price more than the setting percentage past the level, for example after an opening gap), and an admin status (new, reviewed, dismissed, draft created, entry-reached published). Bounded: keeps `ENTRY_STORE_RETENTION_DAYS` days (default 7), atomic writes, resets its "once per day" state automatically each trading day, restart-safe so a restart never creates duplicates.
4. **Admin-only endpoints (read and simple status changes):** list hits (today and recent days), mark reviewed, dismiss, re-arm a signal, and **create a Draft signal from a scanner hit** (pre-filled with stock and level, created as Draft using the Phase 3 rules, never published). All behind the admin check. No endpoint in this phase publishes or notifies.
5. **One-way wall.** The entry-hit store and detection modules are never read by the app-facing endpoints and never call the push module. Record the evidence in the progress log.
6. **Guards and safety valves:** a maximum number of stored hits per day (`ENTRY_MAX_HITS_PER_DAY`, default 200), stale-quote rejection, rate-limited logs, clear log lines for every arm, disarm and hit.
7. **Optional admin email** (setting, default off): when a hit is recorded, email the admin through the existing alert email channel, rate-limited and capped per day. This goes to the admin only, never to app users.
8. **Optional exact-minute correction** (setting, default off): one 1-minute history call for a new hit, using the existing pattern and the shared pacer, to record the exact touch minute.
9. **Status:** additive fields in the status endpoint: detection on or off, armed count, last hit, last sweep used for detection.
10. **Alert log growth:** check `alert_log.json` for a retention cap and add one if it grows without bound.
11. **Settings (all with safe defaults, detection off by default):** suggested names `ENTRY_DETECTION_ENABLED` = false, `ENTRY_SCANNER_STOCKS_ENABLED` = true, `ENTRY_ARMING_DISTANCE_PCT` = 3.0, `ENTRY_NOISE_UNTIL_TIME` = 09:15:30, `ENTRY_EXTENDED_PCT` = 0.5, `ENTRY_ARM_MAX_AGE_DAYS` = 10, `ENTRY_STORE_RETENTION_DAYS` = 7, `ENTRY_MAX_HITS_PER_DAY` = 200, `ENTRY_ADMIN_EMAIL_ENABLED` = false, `ENTRY_EXACT_MINUTE_ENABLED` = false (final names recorded in the log).

**Changes (website).** None in this phase beyond what the admin endpoints return. The panel arrives in Phase 6. (The owner can check hits through the admin endpoint and the logs.)

**Changes (Flutter).** None. The app cannot see any of this.

**Dependencies.** Phases 1 and 2 (store, sweep), Phase 3 (Draft creation rule). No new Fyers requests (only the optional correction call, through the pacer).

**Done when**
- With detection on and the sweep healthy, a touch of an admin signal's entry price or a scanner stock's SMA44 line produces exactly one hit for that day, within about one sweep interval.
- Hits survive a restart, never duplicate, and disappear from "active" at the next trading day.
- A test confirms (by reading the code) that no detection path reaches the push module or the app feed, and the app feed output is identical with detection on or off.
- With detection off, behaviour is exactly as after Phase 4.

**Owner verification checklist**
- On a trading day, create a **Draft** test signal on a liquid stock with an entry price just above (or below) the current price. A hit appears in the admin list within a minute or two of the touch.
- Confirm the app shows nothing new and no notification arrives.
- Redeploy mid-day: no duplicate hit. Edit the test signal's entry price: it re-arms. Deactivate the test signal afterwards.
- Check Railway CPU, memory and the Fyers calls per minute (should be unchanged from Phase 2).

**Deliverables.** Per Section 10.

---

### Phase 6 — Entry Hits panel and manual "entry reached" publication (backend + website)

**Goal.** Give the admin a live view of detected hits on the website and a deliberate, guarded way to publish "entry reached" for an admin signal to Flutter users, with an optional notification of a new type. Nothing here is automatic.

**Why.** Phase 5 only records hits. The owner wants to decide, hit by hit, whether users should hear about it, knowing how old it is and whether the price has already run past the level.

**Analysis the implementing AI must do first**
- Phase 3 and 4 results (publication fields, shared send guard, audit log, endpoint names) and Phase 5 results (hit record fields, statuses, endpoint names).
- `alerts/push.py` wording pools and `send_to_devices` payload (visible title and body, the `data` fields, the Android channel `ayre_signals`).
- `main.py` `GET /api/signals` for the app: how to add public fields additively.
- Website: `SignalsPanel.tsx`, `ExitCallsPanel.tsx`, `App.tsx` (where a new panel fits), `types.ts`, `index.css` conventions.

**Changes (backend)**
1. **Public entry-reached fields on the signal (additive).** Time reached, price at detection, and the "extended" flag. They are copied from the hit **only** when the admin publishes entry-reached. The detection source and hit status stay admin-only. The app feed includes these fields only for signals where entry-reached has been published; for all others the feed is identical to before.
2. **Publish-entry-reached endpoint (admin only).** Preconditions: the signal is Published (a Draft or unpublished signal must be published first, with a clear message); a hit exists for it. Needs the explicit confirmation field and goes through the Phase 4 guard (duplicate window, "send again" flag, cap, audit log). Optional notification choice, default off.
3. **Notification for entry-reached.** New push type (suggested `entry_reached`, final name recorded in the log) with a backend wording pool in the existing style: informational only, for example a short heading, the stock and "level reached". Title and body are always present so older app builds show it. Audience: devices with "New signal alerts" on. One notification per signal per day unless "send again" is confirmed.
4. **Staleness and safety information returned with the request:** age of the hit, the extended flag, the price now (from sweep data, no new Fyers calls). If older than `ENTRY_STALE_MINUTES` (default 15) or the price has moved more than the extended percentage past the level, the endpoint requires an extra "I understand" confirmation. A **notification** is refused after the market close; the admin may still publish the fact without a notification.
5. **Hit status updates.** Publishing marks the hit "entry-reached published" and "done", which stops further detection for that signal until re-armed (Section 4).
6. **Edit rule.** If the admin changes a signal's entry price after entry-reached was published, the published entry-reached fields are withdrawn from the app view at save time (they described the old level), detection re-arms, and the website's edit warning says so.
7. **Unpublish rule.** Unpublishing a signal hides it and its entry-reached fields from the app; nothing is sent.
8. **Optional email to the admin** (from Phase 5) is unchanged.

**Changes (website)**
- **Entry Hits panel (new).** Lists today's hits (and recent days) with: stock, kind, level, direction, time, **age ("reached 7 min ago")**, price at detection, price now, extended flag, detection source, admin status, and the signal's publication state. It refreshes by asking **our own backend** every 10 to 15 seconds while the tab is visible (no Fyers calls). A sound on/off switch (the browser needs one click to allow sound) and a count in the browser tab title for new hits.
- Actions per hit: **Publish entry reached…** (admin signals; opens a confirmation showing age, extended warning and the "also send notification" tick box, default off), **Create draft signal** (scanner hits), **Dismiss**, **Re-arm**.
- Signals panel shows armed state, direction, hit, and entry-reached published state, and the new edit-warning text from item 6.
- Keep the existing visual style. Rebuilding the website happens in the owner's deployment.

**Changes (Flutter).** None in this phase. Older and current app builds ignore the new fields and show the new notification type through the phone's own notification (title and body), without crashing. Phase 7 adds proper handling.

**Dependencies.** Phases 3, 4 and 5.

**Done when**
- Hits appear on the website automatically and can be dismissed, turned into drafts, or published as entry-reached with or without a notification.
- The app shows entry-reached information only after that manual action, and removes it when withdrawn or unpublished.
- The extra confirmation appears for stale or extended hits; no notification can be sent after the close; no double sends.
- No detection data is visible through any app-facing endpoint.

**Owner verification checklist**
- On a trading day: with a published test signal, wait for a hit, confirm the app shows nothing and nothing arrives. Press **Publish entry reached** with the notification box off: the app shows it after reopening or resuming Signals, no notification. Repeat with another test signal with the box on: a notification arrives.
- Try publishing an old hit (wait more than the stale limit): the extra confirmation appears.
- Change the test signal's entry price: the entry-reached label disappears from the app.
- Confirm the audit log shows every send. Deactivate the test signals.

**Deliverables.** Per Section 10.

---

### Phase 7 — Flutter app: show published entry-reached information and handle its notification

**Goal.** The app displays entry-reached information that the admin published, handles the new notification type correctly, and fixes the existing gap for tapped custom messages. The app never invents or fetches anything beyond what the backend publishes.

**Why.** Phase 6 publishes the new fields and the new notification type. Users need to see and open them, and the notification must be recorded in the Alerts list the same way as other kinds.

**Analysis the implementing AI must do first (Flutter repo)**
- `lib/services/market_models.dart`: how `Signal` is parsed and cached, and tolerance for missing fields.
- `lib/screens/signals_tab.dart`: how cards render entry, exit and stop-loss; when data loads (open and resume, no timer); how "seen" state works and how a signal that disappears and later returns (unpublished, then published again) is handled.
- `lib/services/push_service.dart`: `_noticeFrom`, `_onForegroundMessage`, `_onMessageOpened`, `_recordFromTap`, `_showBanner`, and how the Alerts list is filled.
- `lib/services/notification_copy.dart` and `lib/services/settings_store.dart`: copy pools; the `NoticeKind` list and how stored notices are parsed by name (a new kind must not break old stored entries); the "New signal alerts" switch (`newSignalAlerts`) and the `signals` flag sent to the backend.
- `lib/services/api_service.dart`; Android channel `ayre_signals` and anything iOS-specific if the project targets iOS. Do not create a new channel.
- Phase 6 results in the progress log: exact field names and the push type string.

**Changes (Flutter)**
1. Parse and store the new public signal fields (time reached, price at detection, extended flag), tolerant of their absence (older backend responses or signals without entry-reached).
2. Signal card: show "Entry reached at time, price" and a distinct label when the alert was already extended past the level, in the existing card style. Show nothing when the fields are absent. A signal that is unpublished or has withdrawn fields simply stops showing them on the next load.
3. Recognise the new push type in all three paths: foreground (record in Alerts, banner with a "View" action, refresh the Signals tab), background/closed tap (record in Alerts, open the Signals tab), and the cold-start tap. Add informational copy for the type in `notification_copy.dart`, matching the backend wording style, used only if the notification text is missing. Add a notice kind in a way that older stored entries still load (or reuse an existing kind if the analysis shows that is safer).
4. **Reuse the existing "New signal alerts" switch.** No new setting, no new Settings screen and **no change to device registration**, because the backend already filters this notification type by the same `signals` flag. This also means older installed app versions respect the user's opt-out. The foreground filter must treat the new type like the other signal kinds.
5. **Fix the existing gap for custom messages:** a `general` notification tapped from the background or from a closed app is currently not recorded in the Alerts list and does nothing. Record it on tap (and open the Alerts screen or just record, whichever matches how `exit` behaves, decided after reading the code).
6. No new polling. Signals continue to load on open and on resume.
7. **Expected behaviour to document:** a signal published without a notification appears the next time the Signals tab loads; because the app already adds an Alerts-list entry when a new signal first appears in the feed, it will show up in Alerts then. This follows an explicit admin publish and is acceptable.
8. Do not touch Flutter code unrelated to the above.

**Changes (backend and website).** None, except small corrections if the Phase 6 field names or payload need an adjustment found during this analysis (record any such change).

**Dependencies.** Phase 6.

**Done when**
- A published entry-reached fact appears on the signal card with time and price; extended labelling works; nothing appears for unpublished signals.
- The new notification arrives and is recorded in foreground, background and closed states, and tapping it opens Signals.
- Tapping a custom message from the background or closed state is recorded.
- Older app builds still show the new notification as a normal phone notification and do not crash.
- The "New signal alerts" switch off stops the new notification type.

**Owner verification checklist**
- Install the new app build and test an entry-reached notification in foreground, background and closed.
- Send a custom message from the Notifications panel with the app closed, tap it, and confirm it appears in Alerts.
- Turn "New signal alerts" off and confirm an entry-reached notification no longer arrives, while exit and custom messages still do.
- Confirm an older app build still shows the notification readably.

**Deliverables.** Per Section 10, including the final app version and release note for the owner's manual build.

---

### Phase 8 — Live-feed speed-up (optional) and final hardening

**Part A — Live-feed speed-up (optional; the owner may skip it).**

**Goal.** Notice touches within seconds instead of within a sweep, for the stocks that matter, using the existing Fyers live feed and no extra REST requests. Hits still go only to the admin-only store (same one-way wall).

**Why optional.** The admin reacts by hand, so the one-minute sweep is already fast enough for the manual workflow. Build this only once sweep-based detection has proven reliable.

**Analysis first**
- `data/fyers_stream.py` end to end: connection lifecycle, symbol configuration, tick normalisation, locking, thread model, staleness checks, token-change restarts. Confirm from the installed Fyers SDK whether symbols can be subscribed and unsubscribed on an open connection. Do not assume more than 200 symbols on one connection.
- Phase 5 armed set and hit store.

**Changes (backend, all behind `ENTRY_LIVE_FEED_ENABLED`, default false)**
1. **Fixed shortlist, not constant churn.** Subscribe once (at socket open and after reconnects or token change) to a shortlist: indices first, then armed admin signals, then scanner stocks closest to their line, up to `ENTRY_LIVE_FEED_MAX_SYMBOLS` (suggested 120, safely under the 200 cap, leaving room for the existing movers feed). Refresh the shortlist at controlled moments (not more often than every `ENTRY_LIVE_FEED_REFRESH_MINUTES`, suggested 15). Anything not on the shortlist, and any time the feed is down, is covered by the sweep.
2. **Light detection on each tick** (no I/O on the socket thread): compare day high/low and last price with the level, apply the confirmation rule (two consecutive ticks or about 3 seconds), hand a hit to a queue. A worker thread evaluates scanner stocks (full evaluation on the provisional bar, throttled per stock) and writes the hit to the **same** entry-hit store, using the **same** once-per-day state as the sweep so one touch is never recorded twice.
3. Reject stale ticks, respect the market-open gate and the opening noise window, keep the existing movers and market-snapshot users of the feed unaffected, and correct the wrong "about 140 symbols" comment.
4. Status fields for live-feed health and last tick age.
5. **No push, no publication** from this code (Ground rule 10).

**Done when (Part A):** with the setting on, a test touch is recorded within seconds and by the sweep if the feed is down; no duplicates; no new Fyers REST calls; with the setting off, behaviour equals Phase 6.

**Part B — Final hardening, documentation and automation audit (required).**

1. **Automation audit.** Search the whole backend and Flutter code for every path that can send a push or change what app users see. Produce a table in the progress log: each path, what triggers it, and why it is an admin action. The only acceptable triggers are admin endpoints. Confirm there is no scheduled, startup, sweep, scan or live-feed path. Confirm the app feed is identical with detection on or off.
2. **Consistency review of all phases:** setting names, log messages, status endpoint content (history store health, active mode, sweep timing, armed count, last hit, live-feed health, push audit summary), and a single place where the owner can see Fyers calls per minute.
3. **Remove or reduce redundant Fyers calls** only where earlier phases proved them redundant (quotes poller, separate breadth fetch). Keep the legacy hourly scan available as the fallback.
4. **Fix incorrect comments and misleading docstrings** found during the work (including the stale "about 140 symbols" comment if Part A was skipped).
5. **Storage review (Section 7):** confirm every new file is bounded (entry-hit store, push audit log, alert log, scan results folder, signals file growth).
6. **Rollback review:** confirm and document how to roll back each feature (Section 8 explains why the backend must not be rolled back below Phase 3).
7. **Docs:** update `README.md` with a short description of the architecture, the publication workflow and the settings.
8. Complete the Final record in Section 9.

**Owner verification checklist**
- Read the automation audit table and confirm every row is an admin action.
- Walk through the manual steps list in Section 6 and tick each one.
- Do one full-day dry run: let detection run, publish one hit by hand, confirm nothing else reached the app.

**Deliverables.** Per Section 10, plus the final complete version of this document.

---

## 6. Manual steps for the owner (outside the code), in simple words

These are things the code cannot do for you. The implementing AI must list the exact setting names it created in each phase's progress log entry. Suggested setting names below may be adjusted by the AI after reading `config/settings.py`; the final names are in the log.

### A. Already required for the completed Phases 1 and 2 (still to be confirmed by the owner)

**Railway (backend hosting)**
1. Open your Railway project, then the backend service, then Volumes. Confirm a Volume is attached and note its mount path. The history store, and everything new in this plan, lives there. If no Volume is attached, attach one now. The Hobby plan allows 5 GB per volume, far more than needed.
2. If Railway does not automatically give the service a variable named `RAILWAY_VOLUME_MOUNT_PATH`, add a variable `PERSISTENT_DATA_DIR` with the volume's mount path. (Railway normally sets the first one itself.)
3. Take a manual backup of the Volume (Railway has volume backups) because it also holds your signals, device list and alert log.
4. Keep the service at one instance. Do not add replicas. Volumes do not work with replicas, and the backend keeps state in one process.
5. Deploy outside market hours (after 15:45 IST). A service with a Volume can have a short downtime on redeploy.
6. Phase 2: set `SWEEP_MODE=shadow`, review `/api/sweep/shadow` after at least one full session, then set `SWEEP_MODE=live`. Rollback: `SWEEP_MODE=off`. Watch that Fyers calls per minute (shown in the logs and in `/api/status`) stay comfortably below 200, and watch Railway CPU and memory. **Phase 5 detection needs the sweep to be live and healthy.**

**Fyers**
7. Nothing to change in the Fyers portal for the planned design: same app, same token, same limits.
8. Make sure the login variables in Railway (login ID, PIN, TOTP key, app ID and secret) stay valid. The daily history job logs in earlier in the morning (about 08:45) using the same method.
9. If the implementing AI discovers that the live feed cannot take more symbols on one connection (Phase 8), it will say so in the log. Do not open extra connections yourself.
10. Review Fyers' API terms for a public app that shows derived signals (not a code issue).

### B. Phase 3 — Publication gate

11. **Take a fresh Railway Volume backup right before deploying.** This phase changes the stored signal records (additively).
12. Deploy after 15:45 IST. Before deploying, write down (or screenshot) which signals are live in the app.
13. After deploying, confirm the same signals are still live in the app. Create a test signal and check it stays hidden from the app until you press Publish.
14. Rebuild and redeploy the website as you do today (the backend build step builds the frontend). The new Draft / Published labels and the Publish button come with that.
15. Tell everyone who uses the admin website: **saving a signal no longer notifies or publishes anything.**
16. **Do not roll the backend back to a version older than Phase 3** after you have drafts saved. The old code would show drafts to app users and would start pushing again. If you must roll back, first publish or deactivate every draft.
17. No Firebase, Fyers or new environment variable is required for this phase.

### C. Phase 4 — Manual notification centre

18. Firebase: **no console change is expected**, because the same push system and the same Android channel (`ayre_signals`) are used. Make sure `FIREBASE_SERVICE_ACCOUNT_JSON` (or the base64 variant) is still set in Railway. Use the Notifications panel to send a test message to your own phone: that proves the whole chain.
19. If you ship to iPhones, confirm the Apple notification key is still uploaded in the Firebase console (existing setup, only worth re-checking if the iPhone test fails).
20. Optional environment variables (defaults exist; add only to change them): `PUSH_DAILY_MAX_MANUAL` (default 30), `PUSH_DUPLICATE_WINDOW_SECONDS` (default 60), `PUSH_AUDIT_LOG_FILE`, `PUSH_AUDIT_LOG_MAX` (default 500).
21. Website rebuild and redeploy (new buttons and the Notifications panel).
22. The audit log is a small file on the Volume (capped); nothing to manage.

### D. Phase 5 — Automatic entry detection (admin-only)

23. Detection is **off by default**. Turn it on with `ENTRY_DETECTION_ENABLED=true` in Railway Variables, after the sweep is live and healthy. Rollback: set it to false (no redeploy of code needed).
24. Optional variables (defaults exist): `ENTRY_ARMING_DISTANCE_PCT` (3.0), `ENTRY_NOISE_UNTIL_TIME` (09:15:30), `ENTRY_EXTENDED_PCT` (0.5), `ENTRY_ARM_MAX_AGE_DAYS` (10), `ENTRY_STORE_RETENTION_DAYS` (7), `ENTRY_MAX_HITS_PER_DAY` (200), `ENTRY_SCANNER_STOCKS_ENABLED` (true).
25. Optional admin email: `ENTRY_ADMIN_EMAIL_ENABLED=true`, which uses the same email settings the scanner's existing alerts already use. Emails go only to the admin address. Leave it off if you do not want email.
26. Test **only on a trading day, during market hours (09:15–15:30 IST)**, with a Draft test signal (never publish it). Deactivate the test signal afterwards.
27. No scheduled job is added by this phase and no Firebase change is needed. The entry-hit file lives on the Volume and is capped by retention days.

### E. Phase 6 — Entry Hits panel and manual entry-reached publication

28. Rebuild and redeploy the website.
29. In the browser you use as admin: open the Entry Hits panel and click the sound switch once so the browser allows sound. Keep that tab visible or in its own window; some browsers slow down hidden tabs. If you cannot watch the page all day, use the optional admin email from step 25.
30. Optional variable: `ENTRY_STALE_MINUTES` (default 15), the age after which the website asks for an extra confirmation before publishing a hit.
31. No Firebase console change: the new notification type uses the same system and channel. Test it on a trading day with a test signal.

### F. Phase 7 — Flutter app

32. Build and release the new Flutter app yourself. Older app versions keep working with the new backend, so you can roll out gradually. Release it **before or together with** you start using "Publish entry reached" with notifications on for all users, so most phones handle the tap properly (older builds still show the notification, but tapping it only opens the app).
33. Test on a real Android device with the app in the foreground, the background and fully closed.
34. If the AI says it added a new notification channel or changed the push payload shape, follow the exact note it writes. (Not expected: the existing channel is reused.)

### G. Phase 8 — Live feed (optional) and final hardening

35. Live feed (optional): `ENTRY_LIVE_FEED_ENABLED=true` (default false). Optional: `ENTRY_LIVE_FEED_MAX_SYMBOLS` (suggested 120) and `ENTRY_LIVE_FEED_REFRESH_MINUTES` (suggested 15). Rollback: set it to false.
36. After a few sessions, check Volume usage and the memory and CPU graphs. Expected: the history store is tens of MB; the new files are tiny. If memory or CPU looks high, tell the implementing AI (or raise `SWEEP_INTERVAL_SECONDS`).
37. Read the automation audit table in the progress log and confirm every push path is an admin button.

---

## 7. Storage-growth and safety controls (apply to every phase)

- **History store:** exactly one generation; staging area for downloads; atomic publish through the manifest; previous generation deleted only after the new one is published and loaded; orphan clean-up on startup; free-space check before download. Target total size well under 100 MB including temporary double during replacement.
- **Backtesting and active scans:** both are isolated from file replacement. Scans and sweeps read an in-memory snapshot, swapped by reference. Backtests use their own fetch path and are not wired to the store.
- **Entry-hit store (Phase 5):** admin-only, on the Volume, atomic writes, kept for `ENTRY_STORE_RETENTION_DAYS` days only, plus the per-day maximum of stored hits. Reset of the "once per day" state each trading day.
- **Push audit log (Phase 4):** capped number of entries (default 500), oldest dropped, atomic writes.
- **Signals file:** signals are never removed (they are deactivated), so it grows slowly with the admin's own activity. New publication and entry-reached fields are small. Note this in the final review; no action needed unless it becomes large.
- **Alert log and any other new log-like file:** must have a retention cap. Verify the existing cap or add one (Phase 5).
- **Scan results folder:** one file per date per kind, overwritten, never one file per sweep. Note that this folder is on local disk and not on the Volume today. Do not move it to the Volume unless the owner asks, because it grows by one file per day.
- **Logs:** new logging must be rate-limited so a minute-by-minute loop does not flood the logs.
- **Rollback:** every new detection behaviour has a setting. Turning those off returns the system to the previous behaviour. The publication gate (Phases 3–4) is the one deliberate exception: it has no switch that re-enables automatic publishing or pushing, and rolling the backend code back below Phase 3 is unsafe once drafts exist (see Section 8).

---

## 8. Risks and fallbacks

| Risk | Fallback / mitigation |
|---|---|
| Quote data and history data differ slightly for today's candle (volume or low) | Shadow mode in Phase 2 shows differences before switching on; adjust the bar-building logic or keep legacy scan |
| History store missing, corrupt or stale | Validation before publish; automatic fallback to the legacy scan; catch-up download on startup |
| Live feed cannot take more symbols or drops (Phase 8, optional) | Fixed shortlist with priority order; sweep performs the same detection every minute; live feed is off by default |
| Free market-status sources (Yahoo/NSE) fail during the day | Existing gate would block Fyers use; implementing AI must log this loudly and decide a safe override (for example: a fresh Fyers quote timestamp on a liquid stock proves the market is open), documenting the choice, and show a warning on the admin website |
| Rate-limit errors | Sweeps back off first; the pacer is shared; calls per minute are logged. Price display for the website and app is served from sweep data so users do not cause Fyers calls |
| Opening gap through the level | The hit is labelled "already extended"; the auction-noise window reduces false hits; the admin sees the label and decides whether to publish; an extra confirmation is required for stale or extended hits |
| Server restart mid-day | Store reloads from disk; hit store and trigger state are persisted; no duplicate hits; publication and "notification sent" markers live on the signal record, so no duplicate sends |
| Memory or CPU too high on the current plan | Incremental computation option in Phase 2, only with exact parity; otherwise lengthen the sweep interval via setting; detection can be switched off |
| Public-app data-use terms | Not a code issue. The owner should review Fyers' API terms for a public app that shows derived signals. |
| **Draft or detection data leaks to the app** | The app feed returns only Published signals, decided on the server from the admin login (Ground rule 12). Detection code has a one-way wall (Ground rule 10). Phase 8 automation audit and the owner's tests in Phases 3, 5 and 6 check it. |
| **Accidental publish or accidental notification** | Confirmation boxes, explicit confirmation field required by the server, duplicate window, "send again" flag, daily cap, audit log. A notification cannot be recalled, so the confirmation text says who will receive it. |
| **Admin is slow or away: a hit goes stale** | Hits show their age; an extra confirmation is needed after `ENTRY_STALE_MINUTES`; notifications are blocked after the close; optional admin email and website sound help the admin notice. A missed hit is never lost: it stays in the list until dismissed or retention ends. |
| **Admin edits a live signal and users see the change at once** | The website warns on every edit of a Published signal; saving never notifies; the admin can unpublish. Staged editing is out of scope. |
| **Entry-reached describes an old level after the price is edited** | Published entry-reached fields are withdrawn automatically when the entry price changes, and the website warns about it. |
| **Signals disappear from the app after deploying Phase 3** | Legacy records without the new field count as Published. The owner records which signals are live before deploying and checks after. |
| **Rolling the backend back to an older version** | Old code would show drafts to app users and push automatically again. Do not roll back below Phase 3 once drafts exist; first publish or deactivate every draft. |
| **Admin's browser tab is closed or hidden** | Detection and the hit list continue on the server; the optional admin email helps; hits wait in the list. |
| **Signals published without a notification still add an Alerts-list entry in the app** | Expected: the app adds an entry the first time a new signal appears in its feed, which only happens after the admin's manual publish. Documented in Phase 7. |
| **Older app builds do not know the new notification type** | The notification carries a title and body, so the phone shows it; tapping only opens the app. Phase 7 adds proper handling. The "New signal alerts" opt-out is respected because the server filters by it. |

---

## 9. Progress log (the implementing AI fills this in after every phase)

For each phase the log entry must contain: what was completed; every file changed with its exact location and repo; manual configuration required and whether the owner has confirmed it done; important implementation decisions and any places where the code differed from this document; new settings and environment variable names with defaults; what remains for the next phase.

### Phase 1 — status: ✅ COMPLETED — IMPLEMENTED (awaiting owner testing)  *(unchanged; do not edit)*
- Completed: on-disk single-generation daily history store (`data/history_store.py`); daily bootstrap thread (`_history_loop` in `main.py`) with the 1-request new-session probe, late-start catch-up, early-morning login shared with the scan loop; atomic staged publish (staging folder → validate → rename → manifest → memory swap → delete old); orphan clean-up at startup and after each publish; free-space check; coverage / freshness / plausibility validation; store health added to `/api/status` under `history_store`. Scans, breadth, quotes poller, WebSocket and backtests are untouched. Backtests were checked: they use `fetch_candles_bulk_at_date` (`live=False`) and never read the store or the W1 cache.
- Files changed (path, repo; all in `ayre-scanner`):
  - `data/history_store.py` — NEW
  - `data/candles.py` — replaces existing (adds `fetch_history_for_store`, `probe_latest_daily_bar`, optional `collect_resolved` flag; live-scan and backtest behaviour and report shape unchanged)
  - `auth/fyers_auth.py` — replaces existing (adds `client_from_cached_token`; nothing else changed)
  - `config/settings.py` — replaces existing (adds the `HISTORY_STORE_*` settings at the end)
  - `main.py` — replaces existing (shared once-per-day login state `_auth_lock` / `_last_auth_date`, `_history_loop`, `_ensure_fyers_today`, scan-due pause of a running download, `/api/status` addition, startup load)
- Manual configuration required / completed: Railway steps 1–5 of Section 6 (Volume attached, `PERSISTENT_DATA_DIR` only if Railway does not set `RAILWAY_VOLUME_MOUNT_PATH`, backup, single instance, deploy after 15:45 IST). Owner confirmation: pending.
- Settings and environment variables added (all optional; defaults shown):
  - `HISTORY_STORE_ENABLED` = true (false returns to today's behaviour: no early login, no download)
  - `HISTORY_STORE_DOWNLOAD_TIME` = 08:45 (IST, weekdays)
  - `HISTORY_STORE_MIN_COVERAGE` = 0.95
  - `HISTORY_STORE_MIN_FRESH_SHARE` = 0.90
  - `HISTORY_STORE_MIN_FREE_MB` = 200
  - `HISTORY_STORE_DIR_NAME` = history_store
  - `HISTORY_STORE_PROBE_SYMBOLS` = NSE:NIFTY50-INDEX,NSE:RELIANCE-EQ
  - `HISTORY_STORE_RETRY_MINUTES` = 15
- Decisions and deviations from this plan:
  - Format: one compressed NumPy `.npz` (numpy ships with pandas, no new dependency) plus `manifest.json` carrying size and SHA-256. Expected size is a few MB.
  - The store holds completed sessions only (bars dated today IST or later are cut), so a catch-up download during market hours never stores a forming bar. Phase 2 appends the live bar.
  - Download uses the existing live fetcher (`live=True`, same pacing, retry waves, symbol resolution and master hints) ending today. The Fyers ticker that answered is stored per stock (`resolved`), collected only when the store asks for it so backtest and scan reports keep their shape.
  - The download takes the shared heavy-job lock (kind `history`). A scheduled scan that becomes due pauses the download (cancel event); the previous store is untouched and the job retries after `HISTORY_STORE_RETRY_MINUTES`. A manual rescan or backtest started during a download gets the existing "busy" reply.
  - Login: the scan loop and the history job now share `_last_auth_date`. The history job first tries today's cached token (`client_from_cached_token`, never exits the process), then the existing automated login. If it logged in, the scan loop does not log in again that day. Weekdays only at the configured time; also runs immediately when no valid store exists, or when the store is older than the previous weekday (late start / long downtime). At most one completed check per IST day; holidays cost one probe request, no download.
  - Validation before publish: coverage ≥ min, ≥ min share of stocks end on the newest session date, newest session ≥ the probe date, no empty / non-finite frames, median history length ≥ 150 bars. Incomplete universe (< 400 stocks) never builds a store.
  - If `PERSISTENT_DATA_DIR` / the Railway Volume is not configured the store goes to local disk and a loud log line says it will be lost on redeploy.
  - `weekly` data is still derived from daily by the scanner; not stored.
- Assumptions for the owner to check when testing: `NSE:NIFTY50-INDEX` returns daily history from the Fyers history endpoint (otherwise the probe falls back to RELIANCE); first download takes about 5–6 minutes (~1,000 paced requests); the weekday-morning login at 08:45 works before the market opens (same TOTP/PIN method as today).
- Remaining for next phase: Phase 2 (sweep) reads `history_store.get_snapshot()` / `get_history()` / `get_resolved_symbol()` and `health()["valid"]` / `["as_of"]` for its fallback decision. Not started.

### Phase 2 — status: ✅ COMPLETED — IMPLEMENTED (awaiting owner testing — shadow first)  *(unchanged; do not edit)*
- Completed: one-minute quote sweep of the whole universe (`scanner/sweep.py`, thread `_sweep_loop` in `main.py`). ~9–10 paced quote requests per sweep; today's candle is built from the quote (open, day high, day low, last, volume) and appended to the Phase 1 stored history; the SAME `run_scan` code evaluates it (new optional `candle_provider`, `dry_run`, `quiet`, `yield_cpu` parameters — default behaviour unchanged). Modes `off` (default) / `shadow` / `live`. Live mode publishes to the same `_state` fields, saved per-date result file and insights stats as the hourly scan; new-signal handling, alert log (one alert per stock per day), watchlist and trade-ready reconstruction are the unchanged engine code. Hourly scan is skipped only while the sweep is healthy and runs exactly as before otherwise. Breadth and signal-price refresh reuse sweep quotes (0 extra calls) while the sweep is healthy. Shadow mode compares every hourly scan with the nearest sweep and writes a bounded log. Fyers calls per minute are shown in `/api/status` and the sweep log line. Silent 50-symbol truncation in `fetch_ltp_bulk` fixed (splits into batches).
- Files changed (path, repo; all in `ayre-scanner`):
  - `scanner/sweep.py` — NEW
  - `scanner/engine.py` — replaces existing (new optional parameters in `run_scan`; console output via `say`; file writes / alerts / intraday call skipped only when `dry_run`)
  - `main.py` — replaces existing (sweep thread + publish, hourly-scan skip while sweep is live, `_sweep_run_lock`, breadth/quotes reuse, shadow comparison call, `/api/status` `sweep` block, new `/api/sweep/status` and `/api/sweep/shadow`)
  - `data/candles.py` — replaces existing (public `PACER`, `AUTH_CODES`, `fyers_calls_last_minute()`, pacer `recent_calls()`)
  - `data/quotes.py` — replaces existing (`fetch_ltp_bulk` now batches in 50s instead of truncating)
  - `data/history_store.py` — replaces existing (adds `is_current_for(day)` / confirmed-current marker used by the sweep; `health()` gains `confirmed_current_on`)
  - `config/settings.py` — replaces existing (adds `SWEEP_*` settings at the end)
- Manual configuration required / completed: none beyond Phase 1 (volume, single instance). To start: set `SWEEP_MODE=shadow` in Railway Variables. After at least one full session of shadow with acceptable `/api/sweep/shadow` results, set `SWEEP_MODE=live`. Rollback: `SWEEP_MODE=off`. Owner confirmation: pending.
- Settings and environment variables added (defaults):
  - `SWEEP_MODE` = off (off | shadow | live)
  - `SWEEP_INTERVAL_SECONDS` = 60 (minimum 20)
  - `SWEEP_START_TIME` = 09:16 (IST; keeps opening-auction noise out)
  - `SWEEP_FINAL_TIME` = 15:46 (one closing sweep so the saved result is final)
  - `SWEEP_MAX_FAILED_SHARE` = 0.20 (sweep discarded if more stocks than this got no quote)
  - `SWEEP_MAX_CONSECUTIVE_FAILURES` = 3 (then unhealthy → hourly scan takes over)
  - `SWEEP_HEALTHY_MAX_AGE_SECONDS` = 180
  - `SWEEP_QUOTE_CARRY_SECONDS` = 180 (a failed batch re-uses the previous quote this long)
  - `SWEEP_RATE_LIMIT_BACKOFF_SECONDS` = 120
  - `SWEEP_SAVE_INTERVAL_SECONDS` = 300 (result file rewritten at most this often, or at once when signal/watchlist membership changes, and at the close)
  - `SWEEP_REUSE_FOR_BREADTH` = true, `SWEEP_REUSE_FOR_QUOTES` = true
  - `SWEEP_LOG_EVERY` = 10 (one summary log line per N sweeps; failures logged at once, repeats at most every 10 min)
  - `SWEEP_SHADOW_LOG_FILE` = sweep_shadow_log.json (on the volume), `SWEEP_SHADOW_LOG_MAX` = 300 entries
- Decisions and deviations from this plan:
  - Evaluation is not reimplemented and not made incremental: the sweep calls the unchanged `run_scan`, so parity is by construction. Cost: measured ~7 s of CPU per sweep for 450 synthetic stocks on a sandbox machine (a 1 ms yield per stock keeps web threads responsive). If Railway CPU is too high, raise `SWEEP_INTERVAL_SECONDS` (e.g. 120) or go back to `off`.
  - Shadow mode uses deep copies and `dry_run=True`: no watchlist/alert-log write, no alert, no intraday call. The comparison log lists symbols present in only one side with close / SMA44 / low for both, to diagnose bar differences (volume and low are the likely ones).
  - Sweep needs: valid, current history store (`is_current_for(today)`: ends on the previous weekday, or today's daily job confirmed it), complete universe (≥ 400), ≥ 90 % of the universe in the store, and today's Fyers login. Otherwise it skips (logged in `/api/status` → `sweep.last_skip`) and the hourly scan runs.
  - A stock with no trade today (quote says no open/low/high/volume, or the quote timestamp is not today) gets no today bar — evaluated on its last stored bar exactly like the hourly scan treats it. Stocks added to the index after the last history download are reported as "not yet in the daily history store".
  - Quote requests go through the same shared pacer as history calls; a rate-limit reply pauses sweeps only (never the hourly scan). The pacer counter covers paced calls only; the 15-s quotes poller, `/api/signals` price enrichment and the intraday trade-ready call remain unpaced as before (that is why breadth/quote reuse matters).
  - Hourly scan and published sweep serialise with `_sweep_run_lock`; the scan holds it for its whole run, so published sweeps are skipped while a (fallback or manual) hourly scan runs.
  - Not changed in this phase (kept as fallback / for later): the 15-second quotes poller and hourly breadth slots still exist and simply read sweep data when healthy. `scan_report.source` = "sweep" marks sweep results. Website progress bar is not driven by sweeps.
  - Backtests untouched.
- Assumptions for the owner to check when testing: Fyers quote fields `open_price`, `high_price`, `low_price`, `volume`, `tt` behave as in the existing quote parsers (the sweep ignores `tt` if it is missing or not today-ish); whether the quote's day volume and day low match the history API's today bar (shadow log answers this); Railway CPU/memory during sweeps.
- Remaining for next phase: Phase 3 can use `RUNTIME.rows` (latest quote per stock incl. day high/low), `run_sweep` outcome (SMA44 per stock is in payloads / can be exposed), and the per-sweep hook in `_sweep_tick` for the trigger check. Not started.

### Phase 3 — Publication gate — status: ✅ COMPLETED — IMPLEMENTED (awaiting owner testing)
- Completed: signals now have a publication state. New signals are saved as **Draft** and are not returned by the app feed. `POST /api/signals/<id>/publish` and `/unpublish` (admin only, `{"confirm": true}` required) change the state and send nothing. The app feed (`GET /api/signals` without an admin `?all=1`) returns only signals that are Published **and** visible by the old rules (`enabled`/`active` + `start_at`/`end_at`). Every automatic push was removed: saving/editing a signal no longer calls the push module, and `_push_pending_loop` and its thread start are gone. Website Signals panel shows Draft / Published / Hidden labels, Publish and Unpublish buttons with confirmation and busy state, a "new signals are saved as Drafts" note, and a "this signal is live" warning while editing a Published signal.
- Files changed (path, repo):
  - `data/app_signals.py` — `ayre-scanner`, replaces existing (publication fields, `is_live`, `set_published`, `load_signals(published_only=...)`, Drafts on create, protected publication fields)
  - `main.py` — `ayre-scanner`, replaces existing (feed gating, publish/unpublish endpoints, push helpers/loop removed, imports trimmed)
  - `frontend/src/components/SignalsPanel.tsx` — `ayre-scanner`, replaces existing
  - `frontend/src/types.ts` — `ayre-scanner`, replaces existing (additive `published`, `published_at`, `published_by` on `SignalPick`)
  - `frontend/src/index.css` — `ayre-scanner`, replaces existing (adds `.tag-draft`, `.tag-published`, `.warn-bar`)
  - Flutter repo: no change.
- Manual configuration required / completed: Section 6 B (items 11–17): Volume backup before deploy, deploy after 15:45 IST, note live signals first, rebuild website, tell admin users saving no longer notifies, never roll the backend below Phase 3 while drafts exist. No new environment variable. Owner confirmation: pending.
- Settings and environment variables added: none (Ground rule 11: no switch that re-enables automatic push or publication).
- Publication field names, legacy-record rule, endpoint names (needed by Phases 4, 6, 7):
  - Fields on the signal record: `published` (bool), `published_at` (ISO UTC), `published_by` (username), `unpublished_at` (ISO UTC, set on unpublish, removed on publish).
  - Legacy rule: a record with no `published` field (or `null`) counts as **Published**; `_normalize_signal` writes the explicit value the next time the file is saved.
  - Endpoints: `POST /api/signals/<id>/publish`, `POST /api/signals/<id>/unpublish`, both admin only, body `{"confirm": true}`. Publish of a deactivated signal returns 409. Repeating the same action returns `changed: false`.
  - The app feed strips `published`, `published_at`, `published_by`, `unpublished_at` so its shape is unchanged. The admin view (`?all=1`, admin session) includes them.
  - Existing push bookkeeping fields `push_sent_at` / `push_pending` are untouched and now unused. `set_push_state` is kept in `data/app_signals.py` for Phase 4 (it is no longer imported by `main.py`). `_signal_was_revised` and `_SIGNAL_REVISION_FIELDS` are kept in `main.py` for Phase 4.
- Evidence that no automatic push path remains: `grep push_alerts main.py` shows only: `notify_exit` inside `api_exits_add` (admin endpoint `POST /api/exits`), `is_configured` / `last_send` in `api_push_status` (read only), `broadcast` inside `/api/push/send` (admin endpoint), and the startup log line that prints whether push is configured (`is_configured()` only, sends nothing). No other module imports `alerts.push`. `_push_signal_if_due` and `_push_pending_loop` no longer exist; nothing runs at startup that can send. Old `push_pending` / `push_sent_at` markers are not read by any code.
- Decisions and deviations from this plan:
  - Admin view uses the existing `?all=1` + `_is_admin()` mechanism (admin cookie session only; an app Firebase token can never be admin and is refused on non-GET routes). The app cannot obtain Drafts with any parameter or header.
  - `_content_fields` maps a client `published` key to `enabled` (shared helper used by other resources). That behaviour is unchanged and cannot publish: `add_signal` / `update_signal` drop any client value for the publication fields; only `set_published` changes them.
  - Publishing/unpublishing does not change `updated_at`, so feed order does not change.
  - Unpublish keeps `published_at` / `published_by` of the last publication for the admin's reference.
  - `GET /api/signals` price enrichment is unchanged (still a Fyers request per feed load); Phase 3 did not touch it.
  - Backend startup message and Fyers/sweep/history code are untouched. Not built or run; syntax of the two Python files was only parsed.
- Remaining for next phase: Phase 4 (manual notification centre): add the shared send guard, the "send new signal" / "send update" endpoints (call `notify_new_signal` / `notify_revised_signal` only for Published signals), `push_sent_at` / levels-at-send markers, audit log, `/api/push/status` extension, website buttons and Notifications panel. Not started.

### Phase 4 — Manual notification centre — status: ✅ COMPLETED — IMPLEMENTED (awaiting owner testing)
- Completed: every notification now goes through one server-side guard (`alerts/manual_push.py`): admin login, explicit `confirm: true`, duplicate window, `send_again` flag, daily cap, audit-log entry for every send and every refusal. New admin buttons: "Send notification" and "Send update notification" on Published signals, "Send again" (extra confirmation), an optional "Also send a phone notification" tick box (default off) in the Publish confirmation, and a new Notifications panel (custom message, push status, today's sends vs cap, recent audit entries). Exit calls and custom messages are routed through the same guard. Payloads and notification types are unchanged (`signal`, `signal_update`, `exit`, `general`).
- Files changed (path, repo; all in `ayre-scanner`):
  - `alerts/manual_push.py` — NEW (the shared guard + typed helpers)
  - `data/push_audit.py` — NEW (bounded audit log; also the source of the duplicate window and daily cap)
  - `data/app_signals.py` — replaces existing (adds `set_notification_state`)
  - `config/settings.py` — replaces existing (adds `PUSH_*` guard settings at the end)
  - `main.py` — replaces existing (notify endpoint, publish `notify` option, exits and custom send via guard, extended `/api/push/status`)
  - `frontend/src/pushApi.ts` — NEW
  - `frontend/src/components/NotificationsPanel.tsx` — NEW
  - `frontend/src/components/SignalsPanel.tsx` — replaces existing
  - `frontend/src/components/ExitCallsPanel.tsx` — replaces existing
  - `frontend/src/App.tsx` — replaces existing (renders `NotificationsPanel` under Exit calls)
  - `frontend/src/types.ts` — replaces existing (additive types)
  - `frontend/src/index.css` — replaces existing (tags, modal, tick box)
  - `alerts/push.py` unchanged (its `notify_*` and `broadcast` are now unused; kept for reference). Flutter repo: no change.
- Manual configuration required / completed: Section 6 C (items 18–22): confirm Firebase variable is set, optional `PUSH_*` variables, rebuild/redeploy website, send a test message from the Notifications panel to your own phone. Owner confirmation: pending.
- Settings and environment variables added (defaults): `PUSH_DAILY_MAX_MANUAL` = 30, `PUSH_DUPLICATE_WINDOW_SECONDS` = 60, `PUSH_AUDIT_LOG_FILE` = push_audit_log.json (on the volume), `PUSH_AUDIT_LOG_MAX` = 500. No switch re-enables automatic pushing (Ground rule 11).
- Endpoint names, shared guard behaviour, audit log file name:
  - `POST /api/signals/<id>/notify` body `{confirm:true, kind:"new"|"update", send_again?:bool}`. 409 `already_sent` / `not_announced` / `no_change` / `duplicate`, 429 `daily_cap`, 503 `not_configured`, 409 `no_audience`, 409 `not_published`.
  - `POST /api/signals/<id>/publish` accepts optional `notify: true`; publishing always succeeds first, the response then carries `notification: {ok, audience | error, code}`.
  - `POST /api/exits` now needs `confirm: true` when push is configured (guard runs BEFORE saving; a refused call saves nothing). If push is not configured it saves exactly as before.
  - `POST /api/push/send` needs `confirm: true`.
  - `GET /api/push/status` adds `signal_devices`, `sends_today`, `daily_cap`, `duplicate_window_sec`, `audit` (latest 30).
  - Audit file `push_audit_log.json`; entry fields: `id, at, day (IST), admin, type, key, status (sending|done|refused), audience, attempted, sent, failed, reason`. Refusals are logged but do not count toward the cap.
  - Signal record additive fields (admin only, stripped from the app feed): `notified_at`, `notified_levels`, `update_notified_at`. The admin view adds `push_state {announced, announced_at, update_sent_at, changed_since}`. Legacy signals with the old `push_sent_at` count as announced; their old levels are unknown so they never show "changed".
- Table of every push path and its admin trigger:
  | Path | Trigger |
  |---|---|
  | `send_new_signal` | admin `POST /api/signals/<id>/notify` (kind new), or admin `POST /api/signals/<id>/publish` with `notify:true` |
  | `send_revised_signal` | admin `POST /api/signals/<id>/notify` (kind update) |
  | `send_exit` | admin `POST /api/exits` |
  | `send_custom` | admin `POST /api/push/send` |
  All four call `send_manual()` and nothing else in the backend sends. `grep` shows `manual_push` is imported only by `main.py` endpoint code; scan, sweep, history, breadth, startup code do not reference it.
- Decisions and deviations from this plan:
  - Duplicate window and daily cap are read from the audit file under one lock together with the new entry being written, so two simultaneous requests cannot both pass and the rules survive a restart.
  - The "send again" flag overrides the duplicate window and the "already announced" rule, never the daily cap or the Published requirement.
  - A notification with zero matching phones is refused (`no_audience`) instead of silently sending nothing, and does not use up the cap.
  - `alerts/push.py` `last_send` (in memory) is still returned by `/api/push/status`; the audit log is what the website now shows.
  - Publishing with the tick box off behaves exactly as Phase 3.
  - Not built or run; Python files were only parsed and the audit module was exercised once on its own.
- Remaining for next phase: Phase 5 (admin-only detection). Not started.

### Phase 5 — Automatic entry detection (admin-only) — status: ✅ COMPLETED — IMPLEMENTED (awaiting owner testing)
- Completed: after every healthy LIVE sweep, `entry_detect.process_sweep()` checks (a) every armed admin signal (Drafts included) against the quote's day high/low and (b) the sweep's own signal list for scanner stocks. Each touch is recorded once per day in an admin-only file (`entry_hits.json` on the volume). Admin endpoints list hits, mark reviewed, dismiss, re-arm a signal, and create a Draft signal from a scanner hit. No Fyers calls were added (the optional exact-minute lookup is one paced call per admin hit and is off by default). Detection is OFF by default.
- Files changed (path, repo; all in `ayre-scanner`):
  - `scanner/entry_detect.py` — NEW
  - `data/entry_hits.py` — NEW
  - `config/settings.py` — replaces existing (adds `ENTRY_*` settings at the end)
  - `main.py` — replaces existing (hook in `_sweep_tick`, `entry_detection` block in `/api/status`, the admin endpoints below)
  - Website and Flutter: no change.
- Manual configuration required / completed: Section 6 D (items 23–27). Owner confirmation: pending.
- Settings and environment variables added (defaults): `ENTRY_DETECTION_ENABLED` = false, `ENTRY_SCANNER_STOCKS_ENABLED` = true, `ENTRY_NOISE_UNTIL_TIME` = 09:15:30, `ENTRY_EXTENDED_PCT` = 0.5, `ENTRY_ARM_MAX_AGE_DAYS` = 10, `ENTRY_STORE_RETENTION_DAYS` = 7, `ENTRY_MAX_HITS_PER_DAY` = 200, `ENTRY_ADMIN_EMAIL_ENABLED` = false, `ENTRY_ADMIN_EMAIL_MAX_PER_DAY` = 20 (new), `ENTRY_EXACT_MINUTE_ENABLED` = false. `ENTRY_ARMING_DISTANCE_PCT` was NOT created (see decisions).
- Entry-hit record field names, statuses and admin endpoint names (needed by Phase 6):
  - Hit: `id, key, day, kind ("admin"|"scanner"), symbol, signal_id, level, direction ("up"|"down"|"touch"), detected_at (IST ISO), price_at_detection, day_high, day_low, source ("sweep"), extended, extended_pct, late_start, exact_minute, status, status_at, status_by, draft_signal_id`; scanner hits also `change_pct, cross_type, promoted`.
  - Statuses: `new | reviewed | dismissed | draft_created | entry_reached_published`. `dismissed` and `entry_reached_published` mark an admin signal's arm "done" (detection stops until re-armed or its entry price is edited). Phase 6 should call `entry_hits.set_status(hit_id, "entry_reached_published", admin)`.
  - Endpoints (all admin only; none is in `_APP_READABLE_RULES`): `GET /api/entries/status`, `GET /api/entries/hits?days=N` (adds `age_minutes`, `price_now`, `signal_state`), `POST /api/entries/hits/<id>/review`, `POST /api/entries/hits/<id>/dismiss`, `POST /api/entries/hits/<id>/create-draft` (scanner hits; creates a Draft only), `POST /api/signals/<id>/rearm`. `/api/status` gains `entry_detection` (enabled, armed_admin, armed_scanner, admin_without_quote, last_hit, last_sweep_used, hits_today, sweeps_used).
- How a scanner hit relates to the existing sweep new-signal event: a scanner hit is a stock present in the sweep's `signals` list (full evaluation C1+C2+C3, which already contains the "day's low within the SMA44 buffer" test) that has no hit yet today. The sweep's result is reused, so there is one evaluation and one answer. It does not depend on `is_new_alert`, so it is restart-safe. `trade_ready_at` (already reconstructed by the engine) fills `exact_minute` for free when `ENTRY_EXACT_MINUTE_ENABLED` is on.
- Evidence of the one-way wall: `scanner/entry_detect.py` imports only `config.settings`, `data.entry_hits`, `data.history_store`, and (lazily) `scanner.sweep` / `data.candles`. `data/entry_hits.py` imports only `config`. Neither imports `alerts.push`, `alerts.manual_push` or `data.app_signals`. Admin signals are handed in read-only by `main.py` (`entry_detect.configure(signals_provider=...)`). No app-facing endpoint reads the hit store (`grep entry_hits main.py` shows only the admin routes above). Only `main.py` imports `entry_detect` / `entry_hits`.
- Decisions and deviations from this plan:
  - Detection runs only when `SWEEP_MODE=live` and the sweep is healthy (`serving_live()`); not in shadow mode and not in hourly-scan fallback. It is skipped on the closing sweep.
  - Scanner "armed set" = current signals + watchlist items. A separate "C1 passes and price within 3% above the line" arming list was not built because the sweep's evaluation already tests the touch itself and reusing it avoids a second evaluation; therefore no `ENTRY_ARMING_DISTANCE_PCT`.
  - Admin arms: direction is "up" if the reference price is below the entry, else "down". A signal saved/edited during the session baselines on the current quote (earlier touches are ignored). A signal that existed before 09:15 is armed on the previous close with no baseline, so a gap through the level is recorded and flagged `extended`. If the price is exactly at the entry, the arm waits until it moves away.
  - Hit key includes an arm counter (`adm:<signal>:<entry>:<day>:<n>`), so "Re-arm" or an edited entry price can produce a new hit the same day.
  - `late_start` marks hits found by the first detection sweep of a day that started after 09:20 IST (detection switched on, or a redeploy, mid-session): the detection time is then not the touch time.
  - Admin signals for stocks outside the sweep's universe get no quote and cannot be detected; they are listed in `admin_without_quote`.
  - Alert log growth: `clean_alert_log` already drops every previous day's entries on each sweep/scan, so `alert_log.json` is bounded; no change made.
  - Logs: arm/disarm lines are rate-limited; a hit always logs one line.
  - Tested only with an offline script against stub data (arm, no-touch, touch, no duplicate, dismiss, re-arm, pre-arm touch ignored, scanner hit once, disarm). Not run on the real stack.
- Remaining for next phase: Phase 6 (Entry Hits panel and manual entry-reached publication). Not started.

### Phase 6 — Entry Hits panel and manual entry-reached publication — status: ✅ COMPLETED — IMPLEMENTED (awaiting owner testing)
- Completed: new admin **Entry hits** panel (polls our own backend every 12 s while the tab is visible; optional sound switch; unseen-hit count in the tab title). Per hit: Publish entry reached…, Send notification… (for an already-published fact), Create draft signal (scanner hits), Dismiss, Re-arm. New endpoint `POST /api/entries/hits/<id>/publish-entry-reached` copies the hit's facts onto the (Published) signal and optionally sends a new push type `entry_reached` through the Phase 4 guard. Signals panel shows armed state/direction, hit, entry-reached state and the new edit warning. Nothing is automatic.
- Files changed (path, repo; all in `ayre-scanner`):
  - `config/settings.py` — replaces existing (adds `ENTRY_STALE_MINUTES`)
  - `data/app_signals.py` — replaces existing (entry-reached fields, `set_entry_reached`, withdraw on edit/unpublish, client cannot set them)
  - `data/push_audit.py` — replaces existing (adds `sent_today`)
  - `alerts/push.py` — replaces existing (adds `build_entry_reached_text`; nothing else changed)
  - `alerts/manual_push.py` — replaces existing (adds `send_entry_reached`)
  - `main.py` — replaces existing (staleness helper, hits list extras, `/api/entries/signal-states`, publish endpoint, feed strips admin-only entry-reached fields)
  - `frontend/src/components/EntryHitsPanel.tsx` — NEW
  - `frontend/src/components/SignalsPanel.tsx`, `NotificationsPanel.tsx` — replace existing
  - `frontend/src/App.tsx`, `types.ts`, `index.css` — replace existing
  - Flutter repo: no change.
- Manual configuration: Section 6 E (items 28–31): rebuild/redeploy website, click the sound switch once, optional `ENTRY_STALE_MINUTES`. No Firebase change. Owner confirmation: pending.
- Settings added: `ENTRY_STALE_MINUTES` = 15.
- Public entry-reached field names and push type (needed by Phase 7):
  - App feed fields on a signal, present ONLY after the admin published entry-reached: `entry_reached_at` (IST ISO, the exact minute if known else detection time), `entry_reached_price` (price at detection), `entry_reached_extended` (bool). Absent otherwise; feed unchanged for all other signals.
  - Admin-only (stripped from the app feed): `entry_reached_level`, `entry_reached_hit_id`, `entry_reached_by`, `entry_reached_published_at`.
  - Push type string: `entry_reached`; data `{type, symbol, signal_id}`; title "<Heading>: SYMBOL", body informational ("SYMBOL … entry level. Open Signals for details."); audience = devices with "New signal alerts" on; Android channel unchanged (`ayre_signals`).
- Endpoint details: `POST /api/entries/hits/<id>/publish-entry-reached` body `{confirm:true, notify?, acknowledge_stale?, send_again?}`. Errors: 400 `confirm_required`; 409 `not_admin_hit`, `not_published`, `level_changed`, `stale_ack_required` (with `reasons`), `market_closed` (only when notify requested; nothing changes); 404 hit/signal missing. Success: `{signal, changed, stale, notification?: {ok, audience | error, code}}`. Per-signal-per-day rule: a second `entry_reached` notification for the same stock the same IST day returns `notification.code = already_sent` until `send_again`. `GET /api/entries/signal-states` (admin) feeds the Signals panel; `GET /api/entries/hits` adds `stale`, `stale_reasons`, `now_extended_pct`, `entry_reached_live`, `signal_entry_price`, and top-level `stale_minutes`, `market_open`.
- Decisions and deviations:
  - Signal-state info for the Signals panel is a separate admin endpoint, so `/api/signals` (app-facing) never reads the entry-hit store.
  - Stale = older than `ENTRY_STALE_MINUTES`, or extended at detection, or price now more than `ENTRY_EXTENDED_PCT` past the level, or late-start detection. The server enforces it; the website only displays it.
  - Notification refusal after close uses `_is_market_open()` (09:15–15:30 IST window) and is checked before anything is changed. Publishing the fact without a notification is always allowed.
  - Publishing the fact succeeds first; a refused notification (cap, duplicate, no audience) never undoes it, same pattern as Phase 4 publish.
  - A hit whose level no longer matches the signal's current entry price (or symbol) cannot be published.
  - Edit rule: changing entry price or stock withdraws the entry-reached fields at save time (in `update_signal`); the detector already re-arms on an entry change. Unpublish also withdraws them, so a later re-publish does not resurrect an old fact. Deactivating hides the signal from the feed anyway.
  - A published fact can be followed by a notification later from "Send notification…" on the hit (fact is not re-copied).
  - Scanner hits cannot be published directly (Draft → Phase 3/4 buttons), as specified.
  - Not built or run: Python files parsed; `app_signals` logic exercised offline (publish, client cannot set fields, edit withdraws, unpublish withdraws); website type-checked with `tsc` only.
- Evidence of the one-way wall: detection modules (`scanner/entry_detect.py`, `data/entry_hits.py`) are unchanged and still import no push/app-signals module. `data/app_signals.py` does not import the hit store. The only code that reads a hit and writes to a signal is the admin endpoint above. `/api/signals` and every `_APP_READABLE_RULES` route do not reference `entry_hits`; new routes (`/api/entries/*`) are not app-readable.
- Remaining for next phase: Phase 7 (Flutter): show `entry_reached_*`, handle `entry_reached` push (record + open Signals), fix tapped `general`. Not started.

### Phase 7 — Flutter app — status: ✅ COMPLETED — IMPLEMENTED (awaiting owner testing)
- Completed: the app shows published entry-reached facts on Signals, handles the new `entry_reached` push in foreground / background / cold start, and now records a tapped custom (`general`) message. No new setting, no polling, no change to device registration, no new Android channel.
- Files changed (path, repo; all in `ayre-scanner-app`):
  - `pubspec.yaml` — version 2.0.0+1 → 2.1.0+2
  - `lib/services/market_models.dart` — `Signal` parses/stores `entry_reached_at`, `entry_reached_price`, `entry_reached_extended` (absent = null/false; round-trips in `toJson` for the offline cache)
  - `lib/services/settings_store.dart` — `NoticeKind.entryReached` appended (old stored entries load unchanged); gated by `newSignalAlerts`
  - `lib/services/notification_copy.dart` — `entryReached(stock)` fallback copy, same wording as the backend pool
  - `lib/services/push_service.dart` — new type in `_noticeFrom`, foreground filter, banner "View", tap handlers, `refreshSignalsRequests`, `general`/unknown tap fix
  - `lib/screens/signals_tab.dart` — featured card line + compact-row line, reload on `refreshSignalsRequests`
  - `lib/screens/notifications_screen.dart` — icon for the new kind
  - Backend and website: no change.
- Manual configuration: Section 6 F (items 32–34): build and release the app yourself; release it before or together with using "Publish entry reached" with notifications for everyone; test on a real Android device. No Firebase change.
- Settings added: none.
- App version / build notes: 2.1.0+2. Release note: "Shows when a published pick reaches its entry level, and opens tapped notifications correctly."
- Decisions and deviations:
  - Foreground `entry_reached`: recorded in Alerts, banner with View (→ Signals), and Signals reloads once immediately (event-driven, no timer).
  - Background/closed tap: recorded from the tap (Signals cannot detect it itself), Signals reloads and opens. Cold start goes through the same `getInitialMessage` path.
  - Tapped `general` (and any unknown type): recorded, then the Alerts screen opens, matching `exit`. Foreground `general` is unchanged (already recorded when it arrives, so no duplicate).
  - The "New signal alerts" switch gates `entry_reached` both in the foreground filter and in `NotificationLog._allows`; the backend already filters the audience by the same `signals` flag, so older builds respect the opt-out.
  - Time is shown as IST clock time regardless of phone time zone. Extended label: "ALREADY PAST THE ENTRY LEVEL WHEN NOTICED" on the featured card, "past the level" on compact rows.
  - A signal that is unpublished or has withdrawn fields simply stops showing the line on the next load (the backend omits the fields).
  - Not built, analysed with the Dart analyzer, or run (no Flutter SDK here). Code was reviewed by hand against existing patterns.
- Assumptions to check when testing: that `AppTypo.label(...).copyWith(color:)` and `formatPrice` compile as used; the Alerts screen opening for a tapped custom message feels right.
- Remaining for next phase: Phase 8 (optional live feed and final hardening). Not started.

### Phase 8 — Live-feed speed-up (optional) and final hardening — status: ✅ COMPLETED — IMPLEMENTED (Part A skipped by the owner; awaiting owner testing)
- Completed: Part B (final hardening) only. Automation audit, removal of dead push helpers, one admin overview endpoint, comment fixes, storage and rollback review, README architecture section, final record.
- Files changed (path, repo; all in `ayre-scanner`):
  - `alerts/push.py` — replaces existing (removed the unused `notify_new_signal`, `notify_revised_signal`, `notify_exit`, `broadcast`, `_in_background`; docstring corrected; `send_to_devices` and the wording builders are unchanged)
  - `data/app_signals.py` — replaces existing (removed the unused `set_push_state`; old `push_sent_at` / `push_pending` markers on records are simply ignored)
  - `data/fyers_stream.py` — replaces existing (comment only: "about 140 symbols" corrected to about 64)
  - `main.py` — replaces existing (adds admin-only `GET /api/system/overview`)
  - `README.md` — replaces existing (architecture, publication workflow, settings table, rollback)
  - Flutter repo: no change.
- Manual configuration: Section 6 G items 36 and 37 (check Volume, memory and CPU after a few sessions; read the audit table). Item 35 (live feed) does not apply. No new environment variable.
- Settings added: none.
- Was Part A (live feed) built or skipped by the owner? **Skipped by the owner.** `ENTRY_LIVE_FEED_*` settings do not exist. Sweep-only detection (about one-minute latency) is the final design.
- Automation audit table (every path that can push or publish, and its admin trigger):
  | Path | What it does | Trigger | Why it is an admin action |
  |---|---|---|---|
  | `POST /api/signals/<id>/publish` | Draft → visible in app (optional `notify:true` sends "new signal") | Website button + confirm | `_is_admin()` (cookie session only), `confirm:true` required |
  | `POST /api/signals/<id>/unpublish` | Hides from app, withdraws entry-reached facts, sends nothing | Website button + confirm | same |
  | `POST /api/signals` (create/edit) | Saves a Draft / edits a signal; cannot publish, cannot notify; client cannot set publication or entry-reached fields | Website form | admin only |
  | `POST /api/signals/<id>/notify` | "new" / "update" push via guard | Website button + confirm | admin only, guard, audit |
  | `POST /api/entries/hits/<id>/publish-entry-reached` | Copies a hit's facts onto a Published signal; optional `entry_reached` push via guard | Website button + confirm (+ "I understand" when stale) | admin only, guard, audit, market-open check |
  | `POST /api/entries/hits/<id>/create-draft` | Creates a Draft from a scanner hit (not visible, nothing sent) | Website button + confirm | admin only |
  | `POST /api/exits` | Saves exit call and pushes via guard | Website form + confirm | admin only, guard, audit |
  | `POST /api/push/send` | Custom message via guard | Notifications panel + confirm | admin only, guard, audit |
  | `POST /api/signals/<id>/rearm`, hit `review` / `dismiss` | Admin-internal detection state only | Website buttons | admin only; no app effect |
  | `DELETE /api/signals/<id>` | Deactivates (hides) a signal | Website button | admin only; only removes things from the app |
  | Everything else | — | — | Verified by search below |
  Search evidence: `grep` for `send_to_devices`, `messaging.send`, `manual_push.`, `push_alerts.` shows `send_to_devices` is called only inside `alerts/manual_push.py`; `manual_push.send_*` is called only from the five admin endpoints above in `main.py`; `push_alerts` elsewhere only calls `is_configured()`, `list_devices()` and `last_send()` (read-only, plus one startup log line). The old `notify_*` / `broadcast` helpers no longer exist. `set_published`, `set_entry_reached`, `add_signal`, `update_signal` are called only from admin endpoints. No scan, sweep, history, breadth, quotes, market, close-snapshot or startup thread references any of them (thread starts at the end of `main.py` were listed and checked). `scanner/entry_detect.py` imports only `config`, `data.entry_hits`, `data.history_store` and (lazily) `scanner.sweep` / `data.candles`; `data/entry_hits.py` imports only `config`; `data/app_signals.py` does not import the hit store; the only importer of `entry_detect` / `entry_hits` is `main.py`, in admin routes (`/api/entries/*`, `/api/signals/<id>/rearm`, `/api/system/overview`, `/api/status` admin-only health). `/api/signals` does not read the hit store. None of those routes is in `_APP_READABLE_RULES`, so an app token gets 403. The app feed output does not depend on `ENTRY_DETECTION_ENABLED` (nothing in the feed code reads detection state).
  Flutter: the only POSTs are `/api/devices/register` and `/api/devices/unregister` (the user's own device and "New signal alerts" flag); everything else is GET. The app never publishes or sends.
- Consistency review: names follow `ENTRY_*` (detection), `PUSH_*` (guard), `SWEEP_*`, `HISTORY_*`. One place for the whole picture: new admin endpoint `GET /api/system/overview` (Fyers calls per minute and cap, active mode, history store health, sweep timing, entry detection armed count / last hit, push summary from the audit log, signal counts, file sizes, memory). `GET /api/status` keeps its existing shape.
- Redundant Fyers calls: **not removed.** The quotes poller and separate breadth fetch were not proven redundant in a live run (the sweep is still new and detection is off by default); removing them would risk the fallback. The legacy hourly scan stays as the fallback. Revisit after a few weeks of `SWEEP_MODE=live`.
- Comments fixed: `fyers_stream.py` symbol count; `alerts/push.py` module and `send_to_devices` docstrings (no longer says main.py calls `notify_new_signal` from endpoints); Phase 3/4 leftovers (`set_push_state`) removed. Left as is on purpose: `_SIGNAL_REVISION_FIELDS` / `_signal_was_revised` in `main.py` (used by "changed since last notification").
- Storage review (Section 7): history store keeps one generation; `entry_hits.json` pruned to `ENTRY_STORE_RETENTION_DAYS` days, `meta.days` pruned with it, per-day cap `ENTRY_MAX_HITS_PER_DAY`, arms removed when a signal is deactivated, deleted or ages out; `push_audit_log.json` capped at `PUSH_AUDIT_LOG_MAX` (500); `alert_log.json` keeps today's entries only (cleaned on every sweep/scan); `scan_results/` one file per date per kind, local disk, not on the Volume; signals file grows only with admin activity (a few hundred bytes per signal). All sizes are visible in `/api/system/overview` → `storage_kb`.
- Rollback review: see the Final record.
- Decisions and deviations: removed dead push helpers rather than leaving them, so a future edit cannot accidentally re-create an automatic path; the overview endpoint is read-only JSON (no new website screen). Not built or run; Python files parsed only.
- Remaining for next phase: none. The project plan is complete.

### Final record (completed at the end of Phase 8)
- Complete list of every changed file across all phases, grouped by repo:
  - `ayre-scanner` (backend + website): `main.py`, `config/settings.py`, `README.md`; `data/`: `history_store.py` and Phase 1/2 files (see their entries), `app_signals.py`, `push_audit.py` (new), `entry_hits.py` (new), `fyers_stream.py` (comment); `alerts/`: `push.py`, `manual_push.py` (new); `scanner/`: `sweep.py` and Phase 1/2 files, `entry_detect.py` (new); `frontend/src/`: `App.tsx`, `types.ts`, `index.css`, `pushApi.ts` (new), `components/SignalsPanel.tsx`, `ExitCallsPanel.tsx`, `NotificationsPanel.tsx` (new), `EntryHitsPanel.tsx` (new).
  - `ayre-scanner-app` (Flutter): `pubspec.yaml`, `lib/services/market_models.dart`, `settings_store.dart`, `notification_copy.dart`, `push_service.dart`, `lib/screens/signals_tab.dart`, `notifications_screen.dart`.
- Complete list of settings and environment variables, with defaults and purpose: see the table in `README.md` (section "Live entry detection and the publication workflow"); Phase 1 and 2 settings are in their own log entries and `config/settings.py`. No setting enables automatic push or publication.
- Complete list of manual configuration steps with status: Section 6 items 1–37 (item 35, live feed, not applicable). Owner status: pending for all; the owner ticks them during testing.
- Final architecture description (plain words): *Detection side:* a daily history download and a one-minute sweep of all 500 stocks feed a detector that records each touch once per day into an admin-only file shown on the website. *Publication side:* the admin presses explicit, confirmed buttons that publish a signal, publish an "entry reached" fact, or send a notification through one guarded, audited gate. *The wall:* detection code never imports the push or signals-feed code, and app-facing endpoints never read detection data; the only crossing is the admin's button, which reads a hit and copies a few facts onto a signal. The app only reads the published feed and registers its own device.
- Known limitations and how to roll back each feature:
  - Detection is sweep-based (about one-minute latency); no live-feed speed-up. Roll back: `ENTRY_DETECTION_ENABLED=false`.
  - Sweep: `SWEEP_MODE=off` returns to the hourly scan only; `shadow` compares without serving.
  - Admin signals for stocks outside the sweep's universe cannot be detected (listed under `admin_without_quote`).
  - A hit found by the first sweep of a day that started after 09:20 IST is labelled `late_start`; its time is not the touch time.
  - Entry-reached publication, entry-reached push, manual notification centre, publication gate: no off switch by design. **Do not roll the backend below Phase 3 once drafts exist** (old code shows drafts and pushes automatically). First publish or deactivate every draft.
  - The Flutter app can be rolled back freely; older builds ignore the new fields and show the new notification as a normal phone notification (tap only opens the app).
  - The quotes poller and breadth fetch still make their own Fyers calls (not proven redundant yet).
  - Review Fyers' API terms for a public app that shows derived signals (owner task).

---

---

## 10. Delivery checklist after every phase (for the implementing AI)

1. Provide **only the updated files**, each as a complete file. Do not provide a ZIP.
2. For every file, state the **exact location** where it must be placed (repo name and path from the repo root), and whether it is new or replaces an existing file.
3. Provide the **updated version of this `.md` file** as well.
4. In the `.md`, update that phase's entry in Section 9: completed work, changed files with locations, manual configuration required or completed, important decisions, new settings, and what remains for the next phase. Update the phase status line.
5. Repeat any manual steps the owner must do for that phase in simple words in the reply, separated from the code changes.
6. Do not build, run, compile or test. State any assumptions that the owner should check when testing.
7. State in the reply **every code path that can send a push notification or change what Flutter users see, and what triggers it** (Ground rule 9). It must be admin actions only.
8. Stop. Wait for the owner before starting the next phase.
