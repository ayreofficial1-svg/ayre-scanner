"""
auth/app_auth.py
────────────────
Verification of mobile-app users' Firebase ID tokens (Authorization: Bearer).

This is completely separate from the website's Flask cookie session. A
verified app user is NEVER an admin: main._is_admin() only looks at the
cookie session and never at anything in this module.

verify_bearer() returns (user, None) on success or (None, (status, code, message))
on failure. Error codes are the stable contract the Flutter app relies on:

  401 app_auth_required   no token
  401 app_token_invalid   bad / unverifiable token
  401 app_token_expired   expired token
  403 forbidden           valid user, endpoint not available to app users
  403 email_not_verified  verified email required (APP_REQUIRE_VERIFIED_EMAIL)
  503 auth_unavailable    Firebase Admin not configured on the server (fail closed)

Tokens, passwords and Authorization headers are never logged.
"""

from auth import firebase_app

try:
    from firebase_admin import auth as _fb_auth
except ImportError:
    _fb_auth = None

# Phone/server clock difference tolerated when checking iat/exp (seconds).
CLOCK_SKEW_SECONDS = 10


def extract_bearer(header_value: str | None) -> str | None:
    """Return the token from 'Authorization: Bearer <token>', else None."""
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def verify_bearer(token: str):
    """Verify a Firebase ID token. See module docstring for the return shape."""
    if _fb_auth is None or not firebase_app.is_configured():
        return None, (503, "auth_unavailable", "Sign-in service is temporarily unavailable")
    app_obj = firebase_app.get_app()
    if app_obj is None:
        return None, (503, "auth_unavailable", "Sign-in service is temporarily unavailable")
    try:
        decoded = _fb_auth.verify_id_token(
            token,
            app=app_obj,
            check_revoked=False,
            clock_skew_seconds=CLOCK_SKEW_SECONDS,
        )
    except _fb_auth.ExpiredIdTokenError:
        return None, (401, "app_token_expired", "Session expired")
    except _fb_auth.RevokedIdTokenError:
        return None, (401, "app_token_invalid", "Session is no longer valid")
    except _fb_auth.UserDisabledError:
        return None, (401, "app_token_invalid", "Account is disabled")
    except (ValueError, _fb_auth.InvalidIdTokenError):
        return None, (401, "app_token_invalid", "Invalid sign-in token")
    except _fb_auth.CertificateFetchError:
        return None, (503, "auth_unavailable", "Sign-in service is temporarily unavailable")
    except Exception:
        return None, (503, "auth_unavailable", "Sign-in service is temporarily unavailable")

    uid = decoded.get("uid") or decoded.get("user_id") or decoded.get("sub")
    if not uid:
        return None, (401, "app_token_invalid", "Invalid sign-in token")
    return {
        "uid": uid,
        "email": decoded.get("email"),
        "email_verified": bool(decoded.get("email_verified", False)),
        "name": decoded.get("name"),
    }, None


def delete_user(uid: str):
    """
    Permanently delete a Firebase account. Returns None on success, or an
    (status, code, message) tuple on failure. A user that is already gone
    counts as success, so a retried request finishes cleanly.
    """
    if _fb_auth is None or not firebase_app.is_configured():
        return (503, "auth_unavailable", "Sign-in service is temporarily unavailable")
    app_obj = firebase_app.get_app()
    if app_obj is None:
        return (503, "auth_unavailable", "Sign-in service is temporarily unavailable")
    try:
        _fb_auth.delete_user(uid, app=app_obj)
    except _fb_auth.UserNotFoundError:
        return None
    except Exception:
        return (502, "delete_failed", "Could not delete the account right now")
    return None
