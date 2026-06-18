"""
Automated FYERS API v3 authentication using TOTP + PIN.

Identifier mapping:
- FYERS_CLIENT_ID: FYERS trading/login ID.
- FYERS_APP_ID_BASE: API app ID without the numeric suffix.
- FYERS_APP_ID_FULL: SDK client ID, for example APPID-100.

FYERS' TOTP login endpoints are not part of the public SDK. Their current
contract uses base64-encoded login ID/PIN values and a fixed login-channel
app_id of "2". The API app type (normally "100") is used later for app
authorization and must not be sent as the login-channel app_id.
"""

import base64
import datetime
import json
import os
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse

import ntplib
import pyotp
import requests
from fyers_apiv3 import fyersModel

from config.settings import (
    FYERS_APP_ID_BASE,
    FYERS_APP_ID_FULL,
    FYERS_APP_TYPE,
    FYERS_CLIENT_ID,
    FYERS_PIN,
    FYERS_REDIRECT_URI,
    FYERS_SECRET_KEY,
    FYERS_TOTP_KEY,
    TOKEN_FILE,
)

_BASE_VAGATOR = "https://api-t2.fyers.in/vagator/v2"
_BASE_API = "https://api-t1.fyers.in/api/v3"
_URL_LOGIN_OTP = _BASE_VAGATOR + "/send_login_otp_v2"
_URL_VERIFY_TOTP = _BASE_VAGATOR + "/verify_otp"
_URL_VERIFY_PIN = _BASE_VAGATOR + "/verify_pin_v2"
_URL_TOKEN = _BASE_API + "/token"
_URL_DIRECT_LOGIN = _BASE_API + "/direct-login"

_LOGIN_CHANNEL_APP_ID = "2"
_REQUEST_TIMEOUT = 20
_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; FYERS-API-client/3)",
}

_TOKEN_CACHE: dict[str, str | None] = {"token": None, "date": None}


def _check_credentials() -> None:
    credentials = {
        "FYERS_APP_ID": FYERS_APP_ID_FULL,
        "FYERS_SECRET_KEY": FYERS_SECRET_KEY,
        "FYERS_CLIENT_ID": FYERS_CLIENT_ID,
        "FYERS_PIN": FYERS_PIN,
        "FYERS_TOTP_KEY": FYERS_TOTP_KEY,
        "FYERS_REDIRECT_URI": FYERS_REDIRECT_URI,
    }
    missing = [name for name, value in credentials.items() if not value]
    if missing:
        raise RuntimeError(
            f"Missing credentials in .env / environment: {', '.join(missing)}"
        )

    if not FYERS_APP_ID_BASE or not FYERS_APP_TYPE.isdigit():
        raise RuntimeError(
            "FYERS_APP_ID must be the API app ID, normally in APPID-100 format."
        )
    if len(FYERS_PIN) != 4 or not FYERS_PIN.isdigit():
        raise RuntimeError("FYERS_PIN must be the 4-digit FYERS login PIN.")
    try:
        pyotp.TOTP(FYERS_TOTP_KEY).now()
    except Exception as exc:
        raise RuntimeError(
            "FYERS_TOTP_KEY is not a valid Base32 TOTP secret."
        ) from exc

    print(
        f"   Credentials loaded for {FYERS_CLIENT_ID} "
        f"(app {FYERS_APP_ID_FULL})"
    )


def _clear_cached_token() -> None:
    _TOKEN_CACHE["token"] = None
    _TOKEN_CACHE["date"] = None
    try:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
    except OSError:
        pass


def _load_cached_token() -> str | None:
    today = str(datetime.date.today())
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["date"] == today:
        return _TOKEN_CACHE["token"]

    try:
        with open(TOKEN_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("date") == today and data.get("token"):
            token = str(data["token"])
            _TOKEN_CACHE["token"] = token
            _TOKEN_CACHE["date"] = today
            return token
    except (OSError, ValueError, TypeError):
        pass
    return None


def _save_token(token: str) -> None:
    today = str(datetime.date.today())
    token_dir = os.path.dirname(os.path.abspath(TOKEN_FILE)) or "."
    temp_path = ""

    try:
        os.makedirs(token_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=token_dir,
            prefix=".fyers_token.",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(
                {
                    "token": token,
                    "date": today,
                    "saved_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                },
                handle,
            )
        os.replace(temp_path, TOKEN_FILE)
        if os.name != "nt":
            os.chmod(TOKEN_FILE, 0o600)
    except Exception as exc:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise RuntimeError(f"Could not securely save FYERS token: {exc}") from exc

    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["date"] = today


def _build_fyers_client(token: str) -> fyersModel.FyersModel:
    return fyersModel.FyersModel(
        client_id=FYERS_APP_ID_FULL,
        token=token,
        log_path="",
        is_async=False,
    )


def _extract_auth_code(response: requests.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        body = {}

    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    redirect_url = (
        body.get("Url")
        or body.get("url")
        or data.get("Url")
        or data.get("url")
        or response.headers.get("Location", "")
    )
    if not redirect_url:
        return None
    return parse_qs(urlparse(redirect_url).query).get("auth_code", [None])[0]


def _b64(value: str) -> str:
    return base64.b64encode(str(value).encode("ascii")).decode("ascii")


def _get_ntp_time() -> float | None:
    try:
        response = ntplib.NTPClient().request(
            "pool.ntp.org", version=3, timeout=5
        )
        return response.tx_time
    except Exception:
        return None


def _generate_totp_with_ntp_sync() -> str:
    ntp_time = _get_ntp_time()
    if ntp_time is not None:
        return pyotp.TOTP(FYERS_TOTP_KEY).at(ntp_time)
    return pyotp.TOTP(FYERS_TOTP_KEY).now()


def _response_json(
    response: requests.Response,
    step: str,
    *,
    expected_statuses: tuple[int, ...] = (200,),
) -> dict:
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"FYERS {step} returned non-JSON HTTP {response.status_code}."
        ) from exc

    if response.status_code not in expected_statuses or body.get("s") == "error":
        message = body.get("message") or body.get("error") or str(body)
        raise RuntimeError(
            f"FYERS {step} failed (HTTP {response.status_code}, "
            f"code {body.get('code')}): {message}"
        )
    return body


def _authorization_payload() -> dict:
    return {
        "fyers_id": FYERS_CLIENT_ID,
        "app_id": FYERS_APP_ID_BASE,
        "redirect_uri": FYERS_REDIRECT_URI,
        "appType": FYERS_APP_TYPE,
        "code_challenge": "",
        "state": "scanner",
        "scope": "",
        "nonce": "",
        "response_type": "code",
        "create_cookie": True,
    }


def _generate_access_token() -> str:
    session = requests.Session()
    session.headers.update(_DEFAULT_HEADERS)

    login_response = session.post(
        _URL_LOGIN_OTP,
        json={
            "fy_id": _b64(FYERS_CLIENT_ID),
            "app_id": _LOGIN_CHANNEL_APP_ID,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    login_data = _response_json(login_response, "login request")
    request_key = login_data.get("request_key")
    if not request_key:
        raise RuntimeError("FYERS login response did not include request_key.")
    print("   Step 1/6: login request accepted")

    remaining = 30 - (int(time.time()) % 30)
    if remaining < 8:
        time.sleep(remaining + 1)

    totp_response = session.post(
        _URL_VERIFY_TOTP,
        json={
            "request_key": request_key,
            "otp": _generate_totp_with_ntp_sync(),
        },
        timeout=_REQUEST_TIMEOUT,
    )
    totp_data = _response_json(totp_response, "TOTP verification")
    request_key = totp_data.get("request_key")
    if not request_key:
        raise RuntimeError("FYERS TOTP response did not include request_key.")
    print("   Step 2/6: TOTP verified")

    pin_response = session.post(
        _URL_VERIFY_PIN,
        json={
            "request_key": request_key,
            "identity_type": "pin",
            "identifier": _b64(FYERS_PIN),
        },
        timeout=_REQUEST_TIMEOUT,
    )
    pin_data = _response_json(pin_response, "PIN verification").get("data", {})
    trade_token = pin_data.get("access_token") or pin_data.get("token")
    if not trade_token:
        raise RuntimeError("FYERS PIN response did not include a session token.")
    print("   Step 3/6: PIN verified")

    authorization_payload = _authorization_payload()
    authorization_response = session.post(
        _URL_TOKEN,
        json=authorization_payload,
        headers={"Authorization": f"Bearer {trade_token}"},
        timeout=_REQUEST_TIMEOUT,
        allow_redirects=False,
    )
    authorization_data = _response_json(
        authorization_response,
        "app authorization",
        expected_statuses=(200, 308),
    )
    auth_code = _extract_auth_code(authorization_response)

    # FYERS currently returns one of two successful response shapes:
    # - HTTP 308 with Url containing auth_code for an already-approved app.
    # - HTTP 200 with data.auth, requiring /direct-login to obtain that Url.
    if not auth_code:
        data = authorization_data.get("data", {})
        temporary_auth = data.get("auth") if isinstance(data, dict) else None
        if not temporary_auth:
            raise RuntimeError(
                "FYERS app authorization returned neither an auth-code URL "
                "nor a temporary direct-login token."
            )
        direct_login_response = session.post(
            _URL_DIRECT_LOGIN,
            json={
                **authorization_payload,
                "user_id": FYERS_CLIENT_ID,
                "auth": temporary_auth,
            },
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        _response_json(
            direct_login_response,
            "direct login",
            expected_statuses=(200, 308),
        )
        auth_code = _extract_auth_code(direct_login_response)

    if not auth_code:
        raise RuntimeError("FYERS did not return an OAuth authorization code.")
    print("   Step 4/6: app authorization code obtained")

    app_session = fyersModel.SessionModel(
        client_id=FYERS_APP_ID_FULL,
        secret_key=FYERS_SECRET_KEY,
        redirect_uri=FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    app_session.set_token(auth_code)
    token_response = app_session.generate_token()
    token = token_response.get("access_token")
    if not token:
        raise RuntimeError(
            "FYERS access-token exchange failed "
            f"(code {token_response.get('code')}): "
            f"{token_response.get('message') or token_response}"
        )
    print("   Step 5/6: API access token generated")
    return token


def connect_fyers() -> fyersModel.FyersModel:
    try:
        _check_credentials()
    except RuntimeError as exc:
        sys.exit(f"FYERS configuration error: {exc}")

    print("\nConnecting to Fyers API...")
    token = _load_cached_token()

    for attempt in range(2):
        generated_token = False
        if token:
            print("   Using cached token from today")
        else:
            print("   No valid cached token found; running automated login...")
            try:
                token = _generate_access_token()
                generated_token = True
            except Exception as exc:
                sys.exit(f"FYERS authentication failed: {exc}")

        fyers = _build_fyers_client(token)
        profile = fyers.get_profile()
        if str(profile.get("code")) == "200":
            if generated_token:
                try:
                    _save_token(token)
                except RuntimeError as exc:
                    sys.exit(str(exc))
                print("   Step 6/6: token validated and cached")
            name = profile.get("data", {}).get("name", FYERS_CLIENT_ID)
            print(f"   Connected as {name} ({FYERS_CLIENT_ID})")
            return fyers

        error_message = (
            profile.get("message")
            or profile.get("error")
            or profile.get("s")
            or str(profile)
        )
        if attempt == 0:
            print(
                "   Profile validation rejected the cached token "
                f"({error_message}); clearing it and retrying..."
            )
            _clear_cached_token()
            token = None
            continue
        sys.exit(f"FYERS profile validation failed after retry: {profile}")

    sys.exit("FYERS authentication failed.")


def reconnect_fyers() -> fyersModel.FyersModel:
    """Force fresh authentication while preserving the caller's retry policy."""
    _check_credentials()
    _clear_cached_token()
    print("\n🔄  Fyers daily re-auth …")
    token = _generate_access_token()
    fyers = _build_fyers_client(token)
    profile = fyers.get_profile()
    if str(profile.get("code")) != "200":
        raise RuntimeError(f"Re-auth profile check failed: {profile}")
    _save_token(token)
    name = profile.get("data", {}).get("name", FYERS_CLIENT_ID)
    print(f"   ✅ Re-authenticated as {name}")
    return fyers
