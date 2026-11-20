"""Admin Security and Authentication Module for MITS ONE.
Secures the Admin Dashboard and all /api/admin/* endpoints.

Key features:
- Reads admin secret strictly from MITS_ADMIN_TOKEN environment variable.
- Uses signed, expiring session tokens (HMAC-SHA256 via itsdangerous / hashlib).
- Issues HttpOnly, Secure, SameSite cookies.
- Implements IP-based rate limiting on authentication to prevent brute force.
- Enforces role-based endpoint protection with @admin_required decorator.
"""
import functools
import hmac
import logging
import os
import time
from collections import defaultdict

from flask import jsonify, make_response, redirect, request

logger = logging.getLogger("mits_one.admin_auth")

COOKIE_NAME = "mits_admin_session"
SESSION_MAX_AGE_SECONDS = 86400  # 24 hours

# Rate limiting storage: { ip: [timestamp1, timestamp2, ...] }
_FAILED_ATTEMPTS = defaultdict(list)
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 900  # 15 minutes


def get_admin_token() -> str:
    """Retrieve MITS_ADMIN_TOKEN from environment."""
    return os.environ.get("MITS_ADMIN_TOKEN", "").strip()


def is_admin_token_configured() -> bool:
    """Check if admin token is configured in environment."""
    return bool(get_admin_token())


def get_signing_key() -> bytes:
    """Derive secret key for signing admin sessions."""
    secret = get_admin_token() or os.environ.get("FLASK_SECRET_KEY", "mits-one-fallback-secret-2026")
    return secret.encode("utf-8")


# ── Rate Limiting ─────────────────────────────────────────────────────────────

def get_client_ip() -> str:
    """Determine the originating IP address of the client."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def is_rate_limited(ip: str) -> bool:
    """Check if the given IP address is currently rate-limited."""
    now = time.time()
    # Prune old attempts outside the window
    _FAILED_ATTEMPTS[ip] = [t for t in _FAILED_ATTEMPTS[ip] if now - t < RATE_LIMIT_WINDOW_SECONDS]
    return len(_FAILED_ATTEMPTS[ip]) >= RATE_LIMIT_MAX_ATTEMPTS


def record_failed_attempt(ip: str):
    """Record a failed admin login attempt for rate limiting."""
    _FAILED_ATTEMPTS[ip].append(time.time())


def reset_failed_attempts(ip: str):
    """Clear failed attempts on successful login."""
    _FAILED_ATTEMPTS.pop(ip, None)


# ── Signed Session Tokens ─────────────────────────────────────────────────────

try:
    from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
    _SERIALIZER = URLSafeTimedSerializer(get_signing_key(), salt="mits-admin-session-salt")

    def create_session_token() -> str:
        return _SERIALIZER.dumps({"role": "admin", "iat": int(time.time())})

    def verify_session_token(token: str) -> bool:
        if not token:
            return False
        try:
            # Enforce max age of 24 hours
            data = _SERIALIZER.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
            return data.get("role") == "admin"
        except (BadSignature, SignatureExpired, Exception):
            return False

except ImportError:
    # Lightweight HMAC fallback if itsdangerous is somehow unavailable
    import base64
    import json

    def create_session_token() -> str:
        payload = json.dumps({"role": "admin", "exp": int(time.time()) + SESSION_MAX_AGE_SECONDS}).encode()
        b64_payload = base64.urlsafe_b64encode(payload).decode()
        sig = hmac.new(get_signing_key(), b64_payload.encode(), "sha256").hexdigest()
        return f"{b64_payload}.{sig}"

    def verify_session_token(token: str) -> bool:
        if not token or "." not in token:
            return False
        try:
            b64_payload, sig = token.split(".", 1)
            expected_sig = hmac.new(get_signing_key(), b64_payload.encode(), "sha256").hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                return False
            payload = json.loads(base64.urlsafe_b64decode(b64_payload.encode()).decode())
            return payload.get("role") == "admin" and payload.get("exp", 0) > time.time()
        except Exception:
            return False


# ── Authentication Checks ─────────────────────────────────────────────────────

def verify_token_match(submitted_token: str) -> bool:
    """Safely verify submitted token against configured MITS_ADMIN_TOKEN using constant-time comparison."""
    expected = get_admin_token()
    if not expected or not submitted_token:
        return False
    return hmac.compare_digest(submitted_token.strip(), expected)


def is_admin_authenticated() -> bool:
    """Check if current request has a valid admin session cookie or Authorization header."""
    # 1. Check HttpOnly cookie (primary)
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token and verify_session_token(cookie_token):
        return True

    # 2. Check Bearer token in Authorization header (API support)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_val = auth_header[7:].strip()
        # Direct token match or session token
        if verify_token_match(bearer_val) or verify_session_token(bearer_val):
            return True

    return False


def set_admin_cookie(response, token: str):
    """Attach secure HttpOnly session cookie to response."""
    # Determine if HTTPS is in use (Vercel sets X-Forwarded-Proto)
    is_https = request.is_secure or (request.headers.get("X-Forwarded-Proto") == "https")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=is_https,
        samesite="Lax",
        path="/"
    )
    return response


def clear_admin_cookie(response):
    """Remove admin session cookie on logout."""
    response.set_cookie(
        COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        samesite="Lax",
        path="/"
    )
    return response


# ── Route Decorator ───────────────────────────────────────────────────────────

def admin_required(f):
    """Decorator to require admin authentication for endpoints."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin_authenticated():
            return jsonify({
                "error": "Unauthorized. Valid administrator session required.",
                "authenticated": False
            }), 401
        return f(*args, **kwargs)
    return decorated_function
