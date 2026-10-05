# Ayre Scanner — Flutter App Authentication Redesign
## Implementation Plan (specification for the implementing AI, with a plain-language manual guide for the owner)

**Status:** Phases 1–4 IMPLEMENTED (ready for owner testing). Phase O (optional) IMPLEMENTED (ready for owner testing).
**Last updated:** 2026-10-06 (progress log updated after code review of both projects)
**Projects covered:** Backend `ayre-scanner-main/` (Flask, hosted on Railway) and Flutter app `ayre-scanner-app-main/`.

---

## 0. How to use this document

### 0.1 Rules for the implementing AI (non-negotiable)

1. **Do not build, run, compile or test anything.** The owner does all building and testing. Do not tell the owner a thing "works"; say it is "implemented, ready for you to test".
2. **Do not write code in this MD file.** This file is a specification only. (Code goes only in the delivered source files.)
3. **Analyse the real code first.** Before each phase, re-read the files that phase touches. This plan was written from a read of both ZIPs; if the code has changed or something here contradicts what you see, trust the code, state the difference to the owner, and adjust the phase.
4. **Delivery format after every phase:** give *only* the changed or new files (never a ZIP), and for each one give the **exact path** it must be placed at, written relative to the project root (for example `ayre-scanner-app-main/lib/main.dart` or `ayre-scanner-main/main.py`). Also deliver the updated version of **this MD file**.
5. **After every phase, update Section 12 (Progress Log)** with the six items listed there.
6. **Never touch iOS files** during these phases (`ios/`, `macos/`, Info.plist, Podfile, Xcode project, `GoogleService-Info.plist`). Keep all auth logic platform-neutral so iOS can be added later (Section 9).
7. **Never put secrets in delivered files.** `google-services.json`, Firebase service-account keys and Railway credentials are provided by the owner through manual steps (Section 4) and are git-ignored by the project already.
8. **Do not change the Railway admin login for the website.** It must keep working exactly as today.
9. Whenever the owner must do something outside the files (Firebase Console, Railway, Android folder, env vars), say so in a separate, clearly marked **"YOUR MANUAL STEPS"** block, in simple words, with exact click paths. Never bury manual steps inside a code-change description.

### 0.2 How the owner uses this document

- Section 4 is your manual checklist. Each item says **which phase needs it** and **when**.
- Do the manual items for a phase *before* testing that phase.
- Between phases, replace the files the AI gives you at the exact paths it states, then test.

---

## 1. What is happening today (findings from the actual code)

### 1.1 Backend (`ayre-scanner-main/main.py`, Flask)

- **One login system for everyone.** `/api/auth/login` compares the submitted username/password with Railway environment variables `SCANNER_USERS` (format `name:password,name2:password2`) or the older `SCANNER_USERNAME` / `SCANNER_PASSWORD`. On success it stores a Flask **cookie session** (`authenticated`, `username`).
- The **admin website** (`frontend/src/App.tsx`) and the **Flutter app** both use this same endpoint. That is why Flutter users are expected to type the Railway admin credentials.
- **Global gate.** An `@app.before_request` hook (`_require_authentication`) answers every `/api/*` call with **401** unless the caller has the cookie session. Exceptions: the three `/api/auth/*` routes and `/api/devices/register` + `/api/devices/unregister` (public because the app currently has no login). A setting `AUTH_REQUIRED` (default `true`) can be set to `false` to let anonymous **GET/HEAD** calls through.
- **Admin check is too loose.** `_is_admin()` returns true for *any* logged-in session when `SCANNER_ADMIN_USERS` is empty. If app users were ever put into this session system, every app user would become an admin who can write signals, learn articles, insights, weekly reports and trigger scans. **App users must never use this session system.**
- There is **no registration, no per-user data, no password hashing, no rate limiting** on this login.
- `app.secret_key` falls back to a random value if `FLASK_SECRET_KEY` / `SESSION_SECRET` is not set, so every Railway restart/deploy logs everyone (including the website admin) out.
- All data stores (`app_devices.json`, `app_signals.json`, etc.) are **JSON files at relative paths**. Whether they survive a Railway redeploy depends on whether a Railway **Volume** exists (unknown from the files).
- `firebase-admin` is **already** in `requirements.txt` and `alerts/push.py` already builds Firebase credentials from `FIREBASE_SERVICE_ACCOUNT_JSON` / `FIREBASE_SERVICE_ACCOUNT_BASE64` / `GOOGLE_APPLICATION_CREDENTIALS` (named app inside `push.py`). The backend can verify Firebase sign-in tokens with no new library and no new cost.
- The CORS fallback (used only if `flask_cors` is missing) allows only the `Content-Type` header; an `Authorization` header must be allowed for the new design to work from any web client.

### 1.2 Flutter app (`ayre-scanner-app-main/`)

- `lib/services/api_service.dart` implements cookie auth: `login()` posts to `/api/auth/login`, captures `Set-Cookie`, stores it in **SharedPreferences (plain text)**, replays it in a `Cookie` header on every call. `getSession()`, `logout()`, `loadSavedCookie()`, `authHeaders()` all belong to this system.
- `lib/screens/login_screen.dart` has a Username/Password form that calls `ApiService.login` — i.e., the Railway credentials.
- `lib/main.dart`: `kEnableAuthStartupGate = false` only skips the *startup check* in `_StartupGate`. It does **not** stop the app calling the backend.
- **Why the login screen still appears with the flag off (most likely, based on the code):** `market_data_service.dart` `_get()` treats any **401 or 403** as "session expired" → calls `ApiService.notifySessionExpired()` → `main.dart` shows `SessionExpiredScreen` → its button opens `LoginScreen` (Railway login). With the flag off no cookie is ever loaded, and with the backend's default `AUTH_REQUIRED=true` every data call returns 401. (The exact trigger depends on the value of `AUTH_REQUIRED` on Railway, which cannot be seen from the files.)
- A second path: Profile → Sign out calls `ApiService.logout()` then opens `LoginScreen`, regardless of the flag.
- `home_tab.dart`, `profile_tab.dart`, `settings_screen.dart` read name/username from `ApiService.getSession()`. `edit_profile_screen.dart` saves the display name **only on the device** (`SettingsStore`, key `profile_display_name`).
- Firebase is initialised **inside `PushService.init()`**, not awaited, after settings load, and silently skipped if Firebase isn't configured. Authentication needs Firebase ready *before* the first screen decision, so this ordering must change.
- `pubspec.yaml` has `firebase_core` and `firebase_messaging`, **not** `firebase_auth`.
- Android: `android/app/build.gradle.kts` applies the Google Services plugin **only if** `android/app/google-services.json` exists; `settings.gradle.kts` already declares the plugin (version 4.4.4). Package/application ID is the placeholder `com.example.ayre_scanner`. `AndroidManifest.xml` label is `ayre_scanner`. `.gitignore` already excludes `google-services.json` and service-account keys.
- Per-device data that must not leak between accounts: `profile_display_name`, the content caches (`content_learn_articles`, `content_insights` and their `_saved_at` keys), the in-app alert log, and "seen signal symbols".

---

## 2. Decisions (locked) and assumptions

| # | Decision | Reason |
|---|---|---|
| D1 | **Login is mandatory.** No guest mode. The constant `kEnableAuthStartupGate` is removed, not set to true. | Owner requirement. A switch that can disable login is unnecessary and caused confusion. |
| D2 | **Firebase Authentication, Email + Password** is the app's identity system. | Free for email/password on Spark and Blaze (per [Firebase pricing](https://firebase.google.com/pricing); only phone/SMS is billed). Firebase is already in the app and backend. No new vendor. |
| D3 | **Two completely separate identity systems.** Website admin = Flask cookie session + Railway env credentials (unchanged). App users = Firebase accounts, authenticated by a **Bearer ID token** header. | Owner requirement; also prevents the privilege-escalation risk in 1.1. |
| D4 | Flutter **never calls `/api/auth/*`** and never sends or stores the Flask cookie. | Complete separation. |
| D5 | Registration fields: **Name, Email, Password, Confirm Password.** | Owner requirement. |
| D6 | **Forgot Password** is included in the current scope (Phase 2). | Owner requirement. |
| D7 | **Email verification is sent and tracked but NOT enforced.** Enforcement is a later single switch. | Owner requirement. |
| D8 | **Android only** now. iOS documented as future; **no iOS file changes.** | Owner requirement. |
| D9 | Android package ID stays `com.example.ayre_scanner` for now. | Owner decision; see 4.1 for consequences. |
| D10 | No mobile OTP / SMS. | Cost. |
| D11 | The app never stores a password or token itself. The Firebase SDK holds the session. | Secure credential handling. |
| D12 | Username-based login is **not** used; email is the identifier. A display name is a profile field. | Firebase has no native username login; faking one breaks password recovery. |

**Assumptions to confirm at the start of Phase 1 (ask the owner if unclear):** the Firebase project used for push is the project to use for auth; the Android app is already registered in it (Section 4.1); the Railway service already has `FIREBASE_SERVICE_ACCOUNT_JSON` (push works today).

---

## 3. Target architecture

### 3.1 Identity separation

- **Website admin:** browser → `/api/auth/login` → Flask cookie session → admin-only endpoints (`_is_admin()`), scan/backtest endpoints. Untouched.
- **App user:** Flutter → Firebase Auth (email/password) → Firebase issues an **ID token** (valid ~1 hour, auto-renewed by the SDK) → every API call carries `Authorization: Bearer <token>` → backend verifies it with the Firebase Admin SDK → request is treated as "app user (uid, email, email_verified)".
- The backend must **never** convert an app-user token into the admin cookie session, and `_is_admin()` must **never** return true because of an app token. An app token sent to an admin/write endpoint must be refused (the owner's `?all=1` hidden-content views are admin-only and remain so).

### 3.2 Backend route classes (implementing AI must derive the exact lists by reading `main.py` and the Flutter service files)

| Class | Who may call | Examples (verify against code) |
|---|---|---|
| Public | Anyone | `OPTIONS`, static website assets, `/api/auth/login`, `/api/auth/logout`, `/api/auth/session` (these three are for the website only; the app does not use them) |
| App-readable | Valid app token **or** admin session | The read-only GET endpoints the Flutter app actually calls: `/api/market`, `/api/sentiment`, `/api/signals`, `/api/learn`, `/api/insights`, `/api/market/gainers`, `/api/market/losers`, `/api/market/most-active`, `/api/market/<index>/constituents`, `/api/breadth/full`, `/api/insights/volatility`, `/api/insights/momentum`, `/api/insights/volume-surge`, `/api/weekly-report`, `/api/compliance` |
| App-account | Valid app token only (after Phase 4) | `/api/devices/register`, `/api/devices/unregister`, new `/api/app/me` |
| Admin-only | Admin cookie session only | Everything else: all writes (POST/DELETE), rescan, backtests, push send/status, stock directory, `?all=1` views |

Anything not explicitly classified as app-readable or app-account **defaults to admin-only** (safe by default).

### 3.3 Backend token verification rules

- Reuse the existing Firebase Admin credentials already used for push. Refactor into one shared place so push and auth use the same initialised Firebase app; do not break push.
- Verify signature, expiry, audience (= your Firebase project ID) and issuer. **Tolerate a few seconds of clock skew** (a known cause of random "token used too early" failures between phone and server).
- Do **not** check revocation on every request for read-only content (it adds a network call per request). Provide the option for sensitive future actions.
- **Fail closed:** if Firebase Admin isn't configured on the server, app-token requests get a clear **503 `auth_unavailable`** — never "let everyone in".
- Never log tokens, passwords or full Authorization headers.
- Add `Authorization` to the allowed CORS headers in the fallback path (and confirm `flask_cors` path allows it).
- Error response contract (JSON, with a stable machine-readable `code` field so Flutter doesn't parse English text):
  - **401** `app_auth_required` (no token), `app_token_invalid` (bad token), `app_token_expired` (expired) → Flutter tries one token refresh, then signs out.
  - **403** `forbidden` (valid user, not allowed for this endpoint) and `email_not_verified` (future enforcement) → Flutter must **not** sign the user out.
  - **503** `auth_unavailable` → Flutter shows a calm "service temporarily unavailable" state, not a login screen.
- Provide a small **`/api/app/me`** endpoint that returns uid, email, email-verified flag and display name from the verified token. Purpose: a single, cheap end-to-end check that token auth works, and the future home of profile data. It stores nothing.
- Add an optional backend switch (default **off**) named for requiring a verified email on app-readable endpoints, so enforcement later is a configuration change plus a Flutter message, not a redesign.

### 3.4 Flutter architecture

- A new **`AuthService`** layer (one abstract contract + one Firebase implementation) is the *only* code that talks to Firebase Auth. The rest of the app depends on the contract (current user, auth-state stream, register, sign in, sign out, send reset email, send/reload verification, update name, get fresh token). This is what lets email-link, phone, Google or Apple sign-in be added later without touching screens or `ApiService`.
- **`ApiService` and `MarketDataService`** obtain the token from `AuthService` for every request (header `Authorization: Bearer ...`). All cookie code is deleted: `login`, `getSession`, `logout` (cookie), `loadSavedCookie`, `_captureCookie`, `_saveCookie`, the `session_cookie` pref, the `Cookie` header, and the `LoginScreen → ApiService.login` path. On first run after the update, remove any leftover `session_cookie` value from SharedPreferences.
- **Firebase initialisation moves to app startup** (before the first screen decision) and is idempotent; `PushService` must tolerate Firebase already being initialised. If Firebase cannot initialise (e.g., `google-services.json` missing), show a **blocking, clear error screen with Retry** — never let the user into the app unauthenticated.
- **Startup state machine** (replaces `_StartupGate` logic and the `SessionExpiredScreen` routing):
  1. *Initialising* — splash (existing).
  2. *Auth unavailable* — blocking error with Retry.
  3. *Signed out* — Sign in / Register / Forgot password.
  4. *Signed in* — `HomeShell`.
  The decision comes from Firebase's auth-state stream, so sign-in, sign-out and server-side invalidation all move the user between states automatically, and no screen has to push `LoginScreen` by hand.
- **Offline start:** if the SDK has a cached signed-in user, the user enters the app even offline (cached content shows, existing offline banner appears). Being offline is **not** an auth failure.

### 3.5 Flows

**Registration**
1. User enters Name, Email, Password, Confirm Password. Live validation and a visible password checklist (3.6).
2. Create the Firebase account → set the display name on the account → send the verification email (non-blocking; failure of either of the last two must not undo or block the account — surface a gentle retry later).
3. User is signed in and enters the app. A non-intrusive "verify your email" prompt is shown (Phase 3).
- Error mapping (Firebase error → plain message): email already in use → "An account with this email already exists. Try signing in or resetting your password."; invalid email → "Enter a valid email address."; weak password → show checklist guidance; network failure → "No connection. Check your internet and try again."; too many requests → "Too many attempts. Please wait a few minutes."; operation not allowed → treat as configuration error (blocking message + hint to owner in logs only).

**Login**
- Email + Password. A single generic message for wrong email *or* wrong password ("Email or password is incorrect") — do not reveal which. Handle disabled account, too many attempts, network failure separately. Firebase's *email enumeration protection* is on by default for newer projects, so the app must not rely on "does this email exist" lookups.

**Remembered session / app restart**
- The Firebase SDK persists the session on Android automatically, with no code and no stored password. Session ends only on explicit sign-out, account deletion/disable, or password change elsewhere (refresh token revoked). The app handles that last case by returning to Signed out with a calm explanation.

**Logout** (order matters)
1. Best-effort: tell the backend to remove this device's push token (needs a still-valid token).
2. Sign out of Firebase.
3. Clear per-account local data: `profile_display_name`, content caches, in-app alert log, seen-signal list. **Keep** device preferences (theme, text size).
4. Return to Signed out, clearing the navigation stack.
- Update the sign-out confirmation wording from "username and password" to "email and password".

**Forgot password**
- Screen with email field → Firebase sends a reset email → show the same neutral confirmation whether or not the email exists ("If an account exists for this email, we've sent a reset link"). Client-side cooldown (about 60 s) on resend. The reset link opens a Firebase-hosted page, so **no domain or deep-link setup is needed now**.

**Email verification (not enforced)**
- Sent on registration; "Resend" with cooldown; "I've verified" and automatic re-check when the app returns to the foreground (reload the user, then refresh the token so the backend sees the updated flag). Status is shown on Profile (badge) and as a dismissible banner. Nothing is blocked.

**Token handling for API calls**
- Fresh token per request from the SDK (it caches and renews itself). On 401: force one refresh and retry once; if still 401 → sign out to Signed out. On 403: never sign out.

### 3.6 Password and field rules (shown to the user while typing)

- **Name:** 2–60 characters after trimming; letters (any language), spaces, apostrophe, hyphen, dot; not only digits.
- **Email:** trimmed, case-insensitive, valid format.
- **Password:** minimum **8** characters (maximum 128); at least one uppercase letter, one lowercase letter and one number; a special character is *recommended* but not required; must not equal the email. Show: guidance text before typing, a live checklist with ticks, a show/hide toggle, and a Confirm-Password match indicator.
- Client-side rules are for guidance only. **Also** set the same policy in Firebase Console (Authentication → Settings → Password policy, "Require" mode). *Caveat:* Firebase's default minimum is 6, and I could not confirm from the documentation whether the password-policy setting requires upgrading the project to Identity Platform. **Do not upgrade anything.** If the setting is unavailable on the free setup, rely on client-side rules and tell the owner this limit plainly.
- Credential hygiene: password fields obscured by default; no logging of credentials; controllers cleared and disposed; use Android autofill hints so Google Password Manager can offer to save/fill; no password or token written to SharedPreferences or logs.
- Terms/Privacy: no extra checkbox (owner listed exact fields). Show a small line "By creating an account you agree to the Terms and Privacy Policy" linking the existing `TermsScreen` and `PrivacyPolicyScreen`. *Open question for the owner:* a required consent checkbox may be advisable for a SEBI-regulated research service — ask a lawyer/compliance contact.

### 3.7 User data

- **Now:** Firebase stores the account (uid, email, display name, verified flag). The backend stores **nothing** about users; it only reads claims from the verified token. No database is needed for Phases 1–4.
- **Later** (profiles, subscriptions, per-user settings, watchlists): store records **keyed by Firebase uid**. Storage choice is a future decision between Firebase's Firestore (free tier, no server to manage) and a Railway-hosted database/volume. Do not build this now.
- Display name: Firebase account is the source of truth. The old device-only `profile_display_name` override is retired in favour of updating the account name (Phase 3).

---

## 4. YOUR MANUAL STEPS (owner) — plain words

> Menu names in Firebase and Railway change occasionally. If a button has a slightly different name, look for the closest match; the idea stays the same.

### 4.1 Firebase Console — Android setup

**Needed before testing Phase 2.** (Phase 1 needs only 4.1-A and 4.1-B for the backend part.)

**A. Check which Firebase project you use.** Go to https://console.firebase.google.com and open the project you already use for push notifications. Click the gear icon → **Project settings** → **General**. Write down the **Project ID**. Everything below (and Railway's key) must belong to this *same* project, or sign-in will fail.

**B. Turn on Email/Password sign-in.**
1. In the left menu click **Build → Authentication**. If you see "Get started", click it.
2. Open the **Sign-in method** tab.
3. Click **Email/Password**, switch **Enable** on, leave "Email link (passwordless)" **off**, click **Save**.
   This is free. You do not need to add a credit card for this.

**C. Check the Android app is registered.** In **Project settings → General → Your apps**, you should see an Android app whose **package name** is `com.example.ayre_scanner`. If it's missing, click **Add app → Android**, enter that exact package name, any nickname, skip the SHA-1 box (not needed for email/password), and register.

**D. Download `google-services.json`** (from the same card: **Download google-services.json**). Put it exactly here in your Flutter project: **`ayre-scanner-app-main/android/app/google-services.json`** (inside the `app` folder, next to `build.gradle.kts`). It is already in `.gitignore`, so it won't be uploaded to GitHub. If you already have this file because push works, you can keep it; re-downloading the latest copy is harmless.

**E. (Phase 3) Email templates.** Authentication → **Templates**. Open "Email address verification" and "Password reset". You can change the sender name (for example "Ayre Scanner"), subject and wording. The sender address stays Firebase's default for now (a custom sending domain is a future step). During testing, emails may land in **Spam** — check there.

**F. (Phase 2, optional but recommended) Password policy.** Authentication → **Settings → Password policy**. If it is available without an upgrade prompt, set minimum length 8 and require uppercase, lowercase and numeric, mode **Require**. If it asks you to upgrade or add billing, **skip it** and tell the AI.

**About the package ID `com.example.ayre_scanner`:** it is the app's permanent identity. It can stay for testing. Before you publish to Google Play you must choose your real ID (for example `com.yourcompany.ayrescanner`); the Play Store does not allow changing it afterwards. Changing it later means: register a *new* Android app in Firebase, download a new `google-services.json`, and replace the old one. Users' accounts are **not** lost, because accounts belong to the Firebase project, not the package ID.

**About the visible app name:** the name on the phone's home screen is separate from the package ID (it is the `android:label` in the manifest, currently `ayre_scanner`). You can change it at any time without affecting Firebase or accounts. It is not part of these phases.

### 4.2 Railway — how to set variables and what each one means

**Where:** railway.app → open your project → click the **service** that runs the backend → open the **Variables** tab → **New Variable** (name + value) → after saving, Railway offers to **Deploy**; click it so the change takes effect. Variables are secret to Railway; they never go in your code.

| Variable | What it means | What to set | Needed by |
|---|---|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | A private key that lets *your server* talk to Firebase. It already sends your push notifications; it will also let the server check that an app user's sign-in token is genuine. | **First check if it already exists** in Variables (it should, if push works). If not: Firebase Console → gear → **Project settings → Service accounts → Generate new private key** → a `.json` file downloads → open it, copy the **entire** text, paste as the variable's value. (If pasting multi-line text is awkward, base64-encode it and use `FIREBASE_SERVICE_ACCOUNT_BASE64` instead; the backend supports both.) The key must come from the **same project** as in 4.1-A. **Never** share it, commit it, or put it in the Flutter app. | Phase 1 (server must have it to verify tokens) |
| `AUTH_REQUIRED` | An old switch: `true` (or unset) = every API call needs a login; `false` = anyone may *read* data without login. It was added to let the app run without login. | **Check its current value.** Leave it exactly as it is until Phase 4 is deployed and your phone runs the new app; this keeps your old test build working meanwhile. After Phase 4, **delete this variable** (the new code ignores it and always requires a valid app token for the app endpoints). **Never** set it to `false` again. | Phase 4 |
| `SCANNER_USERS` (or `SCANNER_USERNAME` + `SCANNER_PASSWORD`) | The admin **website** logins. | **Do not change.** Used only by the website after this work. | — |
| `SCANNER_ADMIN_USERS` | Which website usernames count as administrators (comma-separated, e.g. `raghav`). If empty, *any* website login is an admin. | Set to your admin username(s). This is a safety belt. | Phase 4 (recommended earlier) |
| `FLASK_SECRET_KEY` | Secret used to sign the website's login cookie. If missing, a random one is made each restart, which logs the admin out on every deploy. | Set a long random value (use a password manager's generator, 64+ characters). Changing it later logs the admin out once. | Phase 4 (recommended) |
| `SESSION_COOKIE_SECURE` | Makes the website cookie travel only over HTTPS. | Set `true` (Railway serves HTTPS). Affects website only. | Phase 4 (recommended) |

**Persistent storage (Railway "Volume") — what it is and whether you need it.**
- *What:* By default a Railway service's files are wiped on every redeploy/restart. A **Volume** is a disk that survives redeploys.
- *Why it matters here:* Your backend saves devices, signals, learn articles, insights and weekly reports in `.json` files. Without a Volume, those files can reset on each deploy. (Push tokens self-heal, because the app re-registers; admin-entered content does not.)
- *For authentication specifically:* **not needed.** Firebase stores the user accounts, not your server.
- *How to check:* in the service, look for a **Volumes** section/tab (or look at the project canvas for a disk attached to the service). If none exists, you have none.
- *If you want one:* it's attached from the project canvas (right-click the service or use the command palette → add a **Volume**, choose a mount path). **Be aware:** your code writes to relative paths, so attaching a volume alone changes nothing; a small backend change (pointing data files to the volume path) is also required. This is outside the auth work, so it is listed as an **optional separate phase (Phase O)** — tell the AI if you want it.
- *Size and cost (Hobby plan):* a Hobby volume can be up to 5 GB and you are billed only for the storage actually used (per [Railway volumes docs](https://docs.railway.com/reference/volumes)). The small files described below are kilobytes, so cost should be negligible. Watch the volume's usage on the Railway dashboard now and then; a full volume makes writes fail. Because scan results, logs and uploaded images can grow, **they are deliberately kept off the volume** (see Phase O).

### 4.3 Android project settings

- Nothing to edit by hand beyond placing `google-services.json` (4.1-D). The AI will deliver any changed Gradle/manifest files; expect **none or minimal** changes (the Google Services plugin is already declared). The AI must check that the app's `minSdk` is high enough for the Firebase Auth version it picks and tell you if a change is needed.
- After replacing files that change `pubspec.yaml`, **you** run Flutter's dependency fetch (`flutter pub get`) — the AI will not.
- Because the Firebase plugin is applied *only if* `google-services.json` exists, a build without that file compiles but cannot sign in; the app will show the "sign-in unavailable" screen. That's expected and safe.

### 4.4 Domains and other services

- **None required now.** Reset and verification links use Firebase's own hosted pages. A custom email-sending domain and custom action URLs are future polish.

### 4.5 Future: iOS (do NOT do now)

When you start iOS: (1) an Apple Developer account; (2) in Firebase → Project settings → **Add app → iOS** with your iOS bundle ID; (3) download `GoogleService-Info.plist` and add it to the Runner target in Xcode; (4) for push, create an APNs key in the Apple Developer portal and upload it in Firebase → Cloud Messaging; (5) a Mac with Xcode to build. No backend change and no redesign is needed — the auth layer, token header and server verification are platform-neutral by design (Section 9).

---

## 5. Recommended phase structure

The implementing AI must confirm or adjust this after re-reading the code. The phases follow real dependencies: backend first (additive, breaks nothing), then the app, then account features, then lock-down once the new app is installed.

```
Phase 1  Backend: app-user token verification (additive, nothing breaks)
   ↓     [owner deploys backend]
Phase 2  Flutter: Firebase Auth core — register, login, forgot password,
         logout, remembered session, startup gate, token-based API calls,
         Railway-login code removed
   ↓     [owner installs new app on Android]
Phase 3  Account layer: email verification (not enforced), profile
         integration, per-account data cleanup
   ↓
Phase 4  Lock-down & hardening: close anonymous access, require app token
         for device registration, admin safeguards, cleanup, docs
Phase O  (optional, separate) Persistent storage for JSON data files
```

---

## 6. Phase 1 — Backend: app-user token verification

**1. What is implemented**
- Shared Firebase Admin initialisation (reused by push; push behaviour unchanged).
- Token verification module and the route classification from 3.2, including safe-by-default admin-only.
- Request context carrying the verified app user (uid, email, verified flag, name).
- New `/api/app/me` endpoint.
- Standard JSON error codes (3.3). `Authorization` allowed in CORS.
- Optional "require verified email" switch, default off.
- **`AUTH_REQUIRED` behaviour is left as-is in this phase** so the existing app keeps working during the transition.

**2. Why this phase**
It is purely additive and independent of the app. It must be live before any app build can authenticate, and it lets you verify the server side first.

**3. Areas affected**
Backend only. The website must behave identically (explicitly re-check the website's 401 handling and admin endpoints).

**4. Depends on:** nothing.

**5. Manual configuration**
4.1-A, 4.1-B (enable Email/Password), 4.2 `FIREBASE_SERVICE_ACCOUNT_JSON` check. Then deploy the backend the way you normally do (typically pushing the updated files to the GitHub repo Railway watches).

**6. Expected files to change** (confirm by reading the code)
- `ayre-scanner-main/main.py` (hook, new endpoint, CORS header)
- `ayre-scanner-main/auth/` — new module for app-user auth, plus a shared Firebase-app helper
- `ayre-scanner-main/alerts/push.py` (only to use the shared Firebase helper, if the AI judges it safe)
- `ayre-scanner-main/config/settings.py` (new setting names)
- `ayre-scanner-main/README.md` (document the two identity systems)
- This MD

**7. Working after this phase**
- Website login/admin work exactly as before.
- Server accepts a valid Firebase token on app-readable endpoints, refuses app tokens on admin/write endpoints, returns the documented error codes, and fails closed (503) if Firebase isn't configured.
- Old app build unaffected.
- *Owner's quick check (you run it):* none needed beyond the website still logging in; real token testing happens in Phase 2.

---

## 7. Phase 2 — Flutter: Firebase Auth core

**1. What is implemented**
- `firebase_auth` dependency (version compatible with the existing `firebase_core`).
- `AuthService` contract + Firebase implementation (3.4).
- Firebase initialised at startup; `PushService` made safe with that.
- New **Sign in**, **Register** (Name/Email/Password/Confirm with live checklist, 3.6) and **Forgot password** screens, in the app's existing design language (reuse `ayre_components`, theme tokens, `terminalRoute`, `SectionLabel`, state views).
- Startup state machine (3.4) replacing `_StartupGate` logic; `kEnableAuthStartupGate` removed; `SessionExpiredScreen` replaced by automatic return to Signed out (with a calm message when a session genuinely ends).
- `ApiService` and `MarketDataService` send `Authorization: Bearer`; refresh-once-on-401; **403 no longer signs the user out**; 503 shown as "temporarily unavailable".
- All cookie/Railway-login code deleted (3.4 list) and the old `session_cookie` preference removed on first launch.
- Logout implemented per 3.5 (basic version here; extra cleanup completed in Phase 3).
- Home / Profile / Settings read name and email from the signed-in Firebase user instead of `getSession()`.
- Push registration sends the token header and happens only while signed in; unregister happens on logout.
- Existing tests referencing removed APIs updated (not run).

**2. Why this phase**
Removing the Railway login and adding the new login cannot be split: the app would otherwise have no way in. Register, login, forgot password, logout and persistence share one service and one gate, so they ship together.

**3. Areas affected**
Flutter (auth, startup, networking, push init, Profile/Home/Settings). No backend change.

**4. Depends on:** Phase 1 deployed to Railway.

**5. Manual configuration**
4.1-B, 4.1-C, 4.1-D (`google-services.json` in `android/app/`), optionally 4.1-F. Run `flutter pub get` after replacing files. Confirm the Railway Phase 1 deploy is live.

**6. Expected files to change** (confirm by reading the code)
`ayre-scanner-app-main/`: `pubspec.yaml` (and `pubspec.lock` regenerates on your side), `lib/main.dart`, `lib/services/api_service.dart`, `lib/services/market_data_service.dart`, `lib/services/push_service.dart`, new `lib/services/auth_service.dart`, `lib/screens/login_screen.dart` (rewritten), new `lib/screens/register_screen.dart`, new `lib/screens/forgot_password_screen.dart`, new shared auth widgets (field/checklist), `lib/screens/profile_tab.dart`, `lib/screens/home_tab.dart`, `lib/screens/settings_screen.dart`, `lib/widgets/state_views.dart` (wording/presets for session/unavailable states), `test/*` files affected. Android Gradle/manifest only if a minSdk or similar need is found.

**7. Working after this phase**
- App opens to Sign in if signed out; Register creates an account and signs in; wrong credentials give clear messages; Forgot password sends a reset email.
- Closing and reopening the app keeps you signed in; Sign out returns to Sign in.
- **No screen in the app can ever ask for the Railway username/password.** The Flutter code contains no call to `/api/auth/*`.
- All market/signal/learn/insight screens load using the Bearer token.
- Old behaviour of "Sign in again" → Railway login is gone.

---

## 8. Phase 3 — Account layer: verification, profile, cleanup

**1. What is implemented**
- Verification email on registration (if not already sent in Phase 2), Resend with cooldown, "I've verified" re-check, automatic re-check on foreground, token refresh to update the flag.
- Verification status on Profile and a dismissible banner; **no feature is blocked**.
- Backend `/api/app/me` consumed by the app for a health/identity check; `email_not_verified` (403) handled gracefully for the future.
- Edit Profile updates the **account display name** (persisted in Firebase), replacing the device-only override; `SettingsStore` display-name override retired/migrated.
- Complete logout cleanup (3.5): caches, alert log, seen-signals, name override.
- Account-switch safety on a shared device.

**2. Why this phase**
It builds on a working login. It's separable from the core and low-risk, so it can be tested independently.

**3. Areas affected:** Flutter profile/settings/home; small backend touch only if `/api/app/me` needs adjusting.

**4. Depends on:** Phase 2.

**5. Manual configuration:** 4.1-E (customise email templates; check Spam folder when testing).

**6. Expected files to change:** `lib/services/auth_service.dart`, `lib/screens/profile_tab.dart`, `lib/screens/edit_profile_screen.dart`, `lib/services/settings_store.dart`, `lib/screens/home_tab.dart`, `lib/screens/settings_screen.dart`, `lib/main.dart` (foreground re-check hook, if needed), new verification banner widget, `lib/services/api_service.dart` (cache clearing helpers), possibly `ayre-scanner-main/main.py`.

**7. Working after this phase**
New users receive a verification email; the app shows verified/unverified state and updates after they verify; the name edited in Profile survives reinstall/sign-in on another device; signing out and into a different account shows none of the previous account's data. Nothing is enforced.

---

## 9. Phase 4 — Lock-down and hardening

**1. What is implemented**
- Remove the anonymous-read path for app endpoints: the `AUTH_REQUIRED=false` bypass is deleted or made inert, so app-readable endpoints always need a valid app token or admin session.
- `/api/devices/register` and `/unregister` require a valid app token (and may record the uid alongside the token for future per-user notifications).
- Website-side safeguards, without changing website behaviour: `_is_admin()` documented/hardened so an app token can never satisfy it; guidance and defaults for `SCANNER_ADMIN_USERS`, `FLASK_SECRET_KEY`, `SESSION_COOKIE_SECURE`; basic throttling on `/api/auth/login` (the website) if the AI judges it safe.
- Cleanup: stale comments/docs that mention the old flag or Railway login (`main.dart` leftovers, `README`, `BACKEND_ANALYSIS.md`, `REDESIGN_V5_CHANGES.md` notes about `AUTH_REQUIRED`), and the fault-injection "session expired" simulation re-pointed at the new states.
- Final review pass of the whole auth surface (a short written security checklist in the MD).

**2. Why last:** closing anonymous access would break any build that doesn't yet send tokens. It is done only after the new app is installed and confirmed working.

**3. Areas affected:** Backend (`main.py`, config), small Flutter cleanup, docs.

**4. Depends on:** Phases 1–3 deployed and tested; your phone runs the new app.

**5. Manual configuration:** Railway: **delete `AUTH_REQUIRED`**, set `SCANNER_ADMIN_USERS`, `FLASK_SECRET_KEY`, `SESSION_COOKIE_SECURE=true` (4.2), then redeploy. Confirm the website admin still logs in (you may be logged out once after setting the secret key).

**6. Expected files to change:** `ayre-scanner-main/main.py`, `ayre-scanner-main/config/settings.py`, `ayre-scanner-main/data/app_devices.py` (if uid is stored), `ayre-scanner-main/README.md`, `ayre-scanner-app-main/lib/services/push_service.dart`, `lib/services/fault_injection.dart`, `README.md`/`BACKEND_ANALYSIS.md`/`REDESIGN_V5_CHANGES.md`, this MD.

**7. Working after this phase**
An anonymous request (no token, no admin session) to any `/api/*` data endpoint gets 401; the website admin still works; an app user can never reach an admin endpoint; push registration is tied to signed-in users.

---

## 10. Phase O (optional, separate) — Persistent storage (small admin files + push-device list only)

Only if the owner confirms. **Owner decision (2026-10-06): only the small admin files and the push-device list go on the Railway Volume.**

**Goes on the volume (small, must survive deploys):**
- `app_signals.json`, `app_learn.json`, `app_insights.json`, `app_weekly_report.json`, `app_exits.json` (admin content shown in the Flutter app)
- `app_devices.json` (push-device list)
- Also reasonable and tiny: `watchlist.json` and `alert_log.json` — the implementing AI confirms by reading how they are used and tells the owner.

**Stays on the normal (wipeable) disk — NOT on the volume:**
- `scan_results/` (dated backtest files), `logs/`, `backtest_state.json`, market caches and snapshots (`symbol_cache.json`, `universe_stats.json`, `breadth_full.json`, `market_close.json`, the Fyers master CSV, `.fyers_token`). These are regenerated by the backend and can grow, so keeping them off the volume protects the 5 GB Hobby limit. If any of them turns out not to be regenerable, the AI must flag it to the owner before leaving it off the volume.
- Uploaded images (`static/uploads`) are **not included by default**. Ask the owner whether uploaded images must also survive deploys. If yes, the AI must also add a file-size limit to the upload endpoint (none exists today) and keep them in a separate sub-folder of the volume.

**What the AI implements:** one configurable data-directory setting (an environment variable) used only by the files in the first list; when it is unset, behaviour is exactly as today. Touches `config/settings.py` and the `data/app_*.py` stores involved. **Safe first deploy:** if the volume is empty and old files exist on the disk, copy them in once rather than starting with empty content — the AI must design this and tell the owner.

**YOUR MANUAL STEPS (when you decide to do Phase O):** (1) in Railway, attach a Volume to the backend service and note its mount path; (2) add the data-directory variable in the service's Variables tab pointing to that path (the AI will give the exact name and value); (3) redeploy; (4) afterwards, check the volume's usage on the dashboard. Do step 1 and 2 only after you have the AI's updated files, and enter your admin content again only if the old files were not carried over.

Not required for authentication.

---

## 11. Future expansion (design already supports it)

| Future feature | How it plugs in without redesign |
|---|---|
| **Email verification enforcement** | Turn on the backend switch (3.3); Flutter already handles `email_not_verified` and shows the verify prompt. |
| **Password recovery improvements** | Already present; add custom email templates/domain. |
| **Mobile number** | Phase A (free): collect as an unverified profile field. Phase B (paid per SMS): link Firebase phone verification to the *same* account via `AuthService` — no screen or API change beyond the new step. Needs SHA-1/SHA-256 registered in Firebase for Android phone auth. |
| **Profiles** (photo, preferences, subscription tier) | Records keyed by Firebase uid; `/api/app/me` becomes the profile endpoint; tier/role delivered as **Firebase custom claims** set by the backend through the Admin SDK. |
| **Google / Apple sign-in** | New provider methods on `AuthService`; same token flow. Apple sign-in becomes mandatory on iOS if other social logins are offered. |
| **Roles/plans & admin management of users** | Custom claims + Admin SDK (list/disable users) from the website, separate from the Railway admin login. |
| **Account deletion** | `AuthService` delete + backend purge by uid. **Google Play requires an in-app way to delete an account for apps that create accounts — plan this before a Play release.** |
| **Multi-factor auth** | Provider feature; may require upgrading to Identity Platform (pricing to be checked then). |
| **Per-user push/notification settings** | Phase 4's uid-linked device records are the base. |
| **iOS** | Section 4.5. Architecture is already neutral: platform-independent `AuthService`, header-based API auth, server-side verification. Avoid Android-only assumptions in auth code. |

---

## 12. Progress Log (the implementing AI updates this after every phase)

For each completed phase, record exactly these six items:

1. **What was completed**
2. **Files changed** (with the placement path for each)
3. **Manual configuration completed** (confirmed by the owner)
4. **Manual configuration still required**
5. **What remains for the next phase**
6. **Important implementation decisions made during the phase**

| Phase | Status | Notes |
|---|---|---|
| 1 — Backend token verification | **Done** (implemented, ready for owner testing) | Verified in code 2026-10-06. See record below. |
| 2 — Flutter Firebase Auth core | **Done** (implemented, ready for owner testing) | Verified in code 2026-10-06. |
| 3 — Account layer | **Done** (implemented, ready for owner testing) | Verified in code 2026-10-06. |
| 4 — Lock-down & hardening | **Done** (implemented, ready for owner testing) | Two minor doc items remain open (see Phase 4 record). |
| O — Persistent storage (optional; small admin files + push-device list only) | **Done** (implemented, ready for owner testing) | See Phase O record. Not required for authentication. |

> Status basis: these entries come from reading the delivered code of both projects. Nothing was built, run or tested by the AI. "Done" means implemented and ready for the owner's tests in Section 13.

### Phase 1 record — Backend: app-user token verification
1. **What was completed:** Shared Firebase Admin initialisation reused by push; app-token verification module; route classes (public / app-readable / app-account / admin-only, admin-only by default); verified app user carried per request; `GET /api/app/me`; standard JSON error codes (401 `app_auth_required` / `app_token_invalid` / `app_token_expired`, 403 `forbidden` / `email_not_verified`, 503 `auth_unavailable`); `Authorization` allowed in CORS; `APP_REQUIRE_VERIFIED_EMAIL` switch (default off).
2. **Files changed:** `ayre-scanner-main/main.py`, `ayre-scanner-main/auth/app_auth.py` (new), `ayre-scanner-main/auth/firebase_app.py` (new), `ayre-scanner-main/alerts/push.py`, `ayre-scanner-main/config/settings.py`, `ayre-scanner-main/README.md`.
3. **Manual configuration completed:** `FIREBASE_SERVICE_ACCOUNT_JSON` is present in Railway Variables (owner screenshot, 2026-10-06). Not yet confirmed by the owner: that it belongs to the same Firebase project as the Android app (4.1-A).
4. **Manual configuration still required:** Confirm same Firebase project; enable Email/Password sign-in (4.1-B); deploy the backend to Railway.
5. **What remains for the next phase:** nothing.
6. **Important decisions:** Anything not explicitly listed as app-readable or app-account is admin-only. Fail closed with 503 when Firebase Admin is not configured. No per-request revocation check for read-only content.

### Phase 2 record — Flutter: Firebase Auth core
1. **What was completed:** `firebase_auth` added; `AuthService` contract plus Firebase implementation; Firebase initialised at startup (`firebase_bootstrap.dart`) with a blocking "Sign-in unavailable" screen and Retry; Sign in, Register (Name/Email/Password/Confirm with live checklist) and Forgot password screens; startup state machine (initialising / unavailable / signed out / signed in); Bearer token on every API call with one refresh-and-retry on 401; 403 never signs the user out; all cookie / Railway-login code removed and the old `session_cookie` pref deleted on first launch; push registration only while signed in; Home / Profile / Settings read the Firebase user.
2. **Files changed:** `ayre-scanner-app-main/pubspec.yaml`, `lib/main.dart`, `lib/services/auth_service.dart` (new), `lib/services/auth_validators.dart` (new), `lib/services/firebase_bootstrap.dart` (new), `lib/services/api_service.dart`, `lib/services/market_data_service.dart`, `lib/services/push_service.dart`, `lib/screens/login_screen.dart`, `lib/screens/register_screen.dart` (new), `lib/screens/forgot_password_screen.dart` (new), `lib/screens/home_tab.dart`, `lib/screens/profile_tab.dart`, `lib/screens/settings_screen.dart`, tests under `test/`.
3. **Manual configuration completed:** none confirmed by the owner yet.
4. **Manual configuration still required:** 4.1-B, 4.1-C, 4.1-D (`google-services.json` in `ayre-scanner-app-main/android/app/`; it was not in the reviewed zip), optional 4.1-F, `flutter pub get`.
5. **What remains for the next phase:** nothing.
6. **Important decisions:** Login is mandatory and `kEnableAuthStartupGate` is removed (D1). The Flutter app never calls `/api/auth/*` (D4). Email is the identifier (D12).

### Phase 3 record — Account layer
1. **What was completed:** Verification email on registration; resend with cooldown; re-check on app foreground; verification banner and Profile status (nothing blocked); `/api/app/me` consumed by the app; `email_not_verified` (403) handled gracefully; Edit Profile updates the Firebase account display name; full sign-out cleanup (content caches, alert log, seen signals, name override) via `account_session.dart`; account-switch safety on a shared device.
2. **Files changed:** `ayre-scanner-app-main/lib/services/email_verification.dart` (new), `lib/services/account_session.dart` (new), `lib/widgets/verification_banner.dart` (new), `lib/services/auth_service.dart`, `lib/services/api_service.dart`, `lib/screens/profile_tab.dart`, `lib/screens/edit_profile_screen.dart`, `lib/screens/home_shell.dart`, `lib/screens/home_tab.dart`, `lib/screens/settings_screen.dart`, `lib/main.dart`.
3. **Manual configuration completed:** none confirmed by the owner yet.
4. **Manual configuration still required:** 4.1-E (customise Firebase email templates; check Spam when testing).
5. **What remains for the next phase:** nothing.
6. **Important decisions:** Verification is sent and tracked but not enforced (D7). The Firebase account is the source of truth for the display name.

### Phase 4 record — Lock-down and hardening
1. **What was completed:** Anonymous access removed (`AUTH_REQUIRED` retired and ignored; anonymous `/api/*` calls always get 401); `/api/devices/register` and `/unregister` require a valid app token (an admin cookie does not satisfy them) and store the Firebase `uid`, and an account cannot unregister another account's token; `_is_admin()` can only be satisfied by the website cookie session; in-memory throttle on the website login (10 failures per IP per 15 minutes, then 429); session-expired fault simulation re-pointed at the new states; backend README documents the two identity systems; security checklist added below.
2. **Files changed:** `ayre-scanner-main/main.py`, `ayre-scanner-main/config/settings.py`, `ayre-scanner-main/data/app_devices.py`, `ayre-scanner-main/README.md`, `ayre-scanner-app-main/lib/services/push_service.dart`, `lib/services/fault_injection.dart`, this MD.
3. **Manual configuration completed:** Railway currently has `SCANNER_USERS`, `FLASK_SECRET_KEY` and `FIREBASE_SERVICE_ACCOUNT_JSON`, and no `AUTH_REQUIRED` (owner screenshot, 2026-10-06), so there is nothing to delete.
4. **Manual configuration still required:** Add `SCANNER_ADMIN_USERS` (admin username only) and `SESSION_COOKIE_SECURE=true`; confirm `FLASK_SECRET_KEY` is 64+ random characters; redeploy; confirm the website admin still logs in.
5. **What remains:** Minor doc cleanup — `ayre-scanner-app-main/REDESIGN_V5_CHANGES.md` (line ~69) still describes `AUTH_REQUIRED=false` as an active option, and `BACKEND_ANALYSIS.md` still discusses the old cookie flow for the app. Neither affects behaviour.
6. **Important decisions:** `AUTH_REQUIRED` is ignored rather than honoured. Website login throttling is per-IP and in memory (resets on restart).

### Phase O record — Persistent storage
1. **What was completed:** One shared data-directory helper. When a Volume is in use, these eight small files are read from and written to it: `app_signals.json`, `app_learn.json`, `app_insights.json`, `app_weekly_report.json`, `app_exits.json`, `app_devices.json`, `watchlist.json`, `alert_log.json`. First start on an empty volume copies any existing local copy in once (never overwrites a file already on the volume). If the directory is unwritable the backend logs a warning and uses the old local paths. The five stores that wrote files non-atomically (signals, learn, insights, weekly report, watchlist/alert log) now write via a temp file + replace, so a restart mid-write cannot leave a truncated file. Exits and devices already did this.
2. **Files changed:** `ayre-scanner-main/config/persistence.py` (new), `ayre-scanner-main/config/settings.py`, `ayre-scanner-main/data/app_signals.py`, `ayre-scanner-main/data/app_learn.py`, `ayre-scanner-main/data/app_insights.py`, `ayre-scanner-main/data/app_weekly_report.py`, `ayre-scanner-main/scanner/watchlist.py`, `ayre-scanner-main/README.md`, this MD.
3. **Manual configuration completed:** Volume attached and variable set (owner confirmation, 2026-10-06). The variable name was not stated; the code accepts `PERSISTENT_DATA_DIR` or Railway's automatic `RAILWAY_VOLUME_MOUNT_PATH`.
4. **Manual configuration still required:** Deploy the updated backend; confirm in the Railway deploy logs the line `[persistence] Using persistent data dir: ...`; afterwards watch the volume's usage on the dashboard. Optional: set `PERSISTENT_DATA_DIR` to the mount path explicitly.
5. **What remains:** Uploaded images (`static/uploads`) are not on the volume; decision pending (needs an upload size limit if included). Nothing else.
6. **Important decisions:** `watchlist.json` and `alert_log.json` included (tiny; the watchlist has a 5-day TTL and the alert log prevents duplicate alerts after a deploy). `scan_results/` stays off the volume per owner scope; note its `live/` files are saved results of past scans and cannot be regenerated for old dates, so they reset on redeploy (backtests can be re-run).

### Security checklist (Phase 4 deliverable)
- [x] Anonymous requests to `/api/*` data endpoints get 401.
- [x] App tokens work only on the listed read-only GETs (`_APP_READABLE_RULES`) and `/api/app/me`; all other endpoints return 403 `forbidden` for app tokens.
- [x] `_is_admin()` ignores app tokens and the `Authorization` header.
- [x] Device registration and removal need an app token; the `uid` is recorded.
- [x] Fails closed (503 `auth_unavailable`) when Firebase Admin is not configured.
- [x] Tokens, passwords and full Authorization headers are not logged (by design; the owner should spot-check Railway logs).
- [x] Flutter stores no password or token of its own; the Firebase SDK holds the session.
- [x] Website login is throttled.
- [ ] Owner: set `SCANNER_ADMIN_USERS`, `SESSION_COOKIE_SECURE=true`, a strong `FLASK_SECRET_KEY`.
- [ ] Owner: confirm the Firebase service account and the Android app belong to the same project.

### Decisions log
- 2026-10-06: Phases 1–4 recorded as done after a code review of both ZIPs. Phase O deferred by the owner.
- 2026-10-06: Phase O implemented per Section 10 scope (eight small files; atomic writes added).
- 2026-10-06: Android build error "Failed connecting to the daemon in 4 retries" addressed by changing `ayre-scanner-app-main/android/gradle.properties` — Kotlin now compiles in-process (`kotlin.compiler.execution.strategy=in-process`), Gradle heap reduced to 4 GB and metaspace to 1 GB. Not an auth change; ready for the owner to test.

---

## 13. Owner's test checklist (you run these; the AI does not)

**After Phase 2:** register a new account; see a clear message for a weak password and for mismatched confirmation; sign out; sign in; close and reopen the app (stays signed in); wrong password message; Forgot password email arrives (check Spam); airplane mode shows the offline banner, not a login screen; confirm the app never shows a Railway-style username/password screen.
**After Phase 3:** verification email arrives and the app reflects "verified" after you click it; edit your name and confirm it persists after sign-out/sign-in; sign in as a second account and confirm nothing from the first account appears.
**After Phase 4:** the website admin login still works; the app still loads data; a browser opening an API data URL without logging in gets a 401.

---

## 14. Risks and open questions for the owner

1. Does `AUTH_REQUIRED` currently equal `false` on Railway, and does `FIREBASE_SERVICE_ACCOUNT_JSON` exist and belong to the same Firebase project as the Android app? (4.1-A, 4.2)
2. Is the Firebase password-policy setting available on the free setup? (4.1-F) If not, only client-side rules apply.
3. Required consent checkbox for Terms/Privacy — compliance decision (3.6).
4. Real Android package ID before any Play Store upload (4.1).
5. Persistent storage — do you already have a Railway Volume, and do you want Phase O? (4.2)
6. Verification and reset emails come from Firebase's default sender during testing and may be spam-filtered.
7. This plan was prepared by reading the code only; nothing has been run, and the cause of the Railway login still appearing (1.2) is inferred from the code and the `AUTH_REQUIRED` default.
