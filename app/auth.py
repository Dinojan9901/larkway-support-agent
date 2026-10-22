"""Authentication: OAuth2 password flow with short-lived JWTs.

Two demo users, one per role:

    customer  ->  cust-halcyon  (Halcyon Foods; one account, no operations console)
    ops       ->  staff-kavya   (support manager; all accounts, operations console)

Only scrypt hashes live here. The plaintext demo passwords are in the README for
reviewers and nowhere in the code.

The token names a user; the server maps that user to a Principal from its own
registry, so a token can never grant itself a role or an account. Every guarantee
downstream (scoping, redaction, the confirmation gate) keys off that Principal,
exactly as before.

The signing secret comes from LARKWAY_JWT_SECRET. Without it, local development gets
a per-process random secret (and a warning); on Vercel, login refuses to start,
because a per-instance secret would silently log people out between instances.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sys
import time
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .access import Principal, get_principal

TOKEN_URL = "/api/auth/token"
ALGORITHM = "HS256"
SECRET_ENV = "LARKWAY_JWT_SECRET"
TTL_ENV = "LARKWAY_TOKEN_MINUTES"
DEFAULT_TTL_MINUTES = 30

# scrypt cost parameters (RFC 7914 interactive-login guidance).
_N, _R, _P = 2**14, 8, 1


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(expected))


@dataclass(frozen=True)
class DemoUser:
    username: str
    principal_id: str
    password_hash: str


DEMO_USERS: dict[str, DemoUser] = {
    "customer": DemoUser(
        "customer",
        "cust-halcyon",
        "scrypt$16384$8$1$CBulxocQGcHzC1Ye2OhMuQ==$wm8NfMwrhY/UBNIkZaMfXOnTG/tE84bHMu/sYeW5ABc=",
    ),
    "ops": DemoUser(
        "ops",
        "staff-kavya",
        "scrypt$16384$8$1$cfzQuz9vKMEQiGZDagSE1g==$y8+0yNxdqujWEVKn1j7nwvrtYCRH5YrZwJIhyWJpdkE=",
    ),
}

# Verified against when the username is unknown, so a failed login costs the same
# either way and response timing doesn't reveal which usernames exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def authenticate(username: str, password: str) -> DemoUser | None:
    user = DEMO_USERS.get((username or "").strip().lower())
    ok = verify_password(password or "", user.password_hash if user else _DUMMY_HASH)
    return user if (user and ok) else None


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
_ephemeral_secret: str | None = None


def _secret() -> str:
    configured = os.getenv(SECRET_ENV, "").strip()
    if configured:
        return configured
    if os.getenv("VERCEL"):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Login is not configured on this deployment: set {SECRET_ENV}.",
        )
    global _ephemeral_secret
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(48)
        print(
            f"WARNING: {SECRET_ENV} is not set; using a random per-process secret. "
            "Sessions end when the server restarts.",
            file=sys.stderr,
        )
    return _ephemeral_secret


def token_ttl_seconds() -> int:
    try:
        minutes = int(os.getenv(TTL_ENV, DEFAULT_TTL_MINUTES))
    except ValueError:
        minutes = DEFAULT_TTL_MINUTES
    return max(1, minutes) * 60


def create_access_token(user: DemoUser, ttl_seconds: int | None = None) -> str:
    now = int(time.time())
    ttl = token_ttl_seconds() if ttl_seconds is None else ttl_seconds
    principal = get_principal(user.principal_id)
    claims = {
        "sub": user.username,
        "pid": user.principal_id,
        "role": "ops" if principal.is_internal else "customer",
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token, _secret(), algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.ExpiredSignatureError as exc:
        raise _unauthorized("Your session has expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("Invalid credentials.") from exc


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=TOKEN_URL, auto_error=False)


def current_user(token: str | None = Depends(oauth2_scheme)) -> DemoUser:
    if not token:
        raise _unauthorized("Sign in to continue.")
    claims = decode_token(token)
    user = DEMO_USERS.get(claims.get("sub", ""))
    # The registry decides who this is; a token naming another principal is refused.
    if user is None or claims.get("pid") != user.principal_id:
        raise _unauthorized("Invalid credentials.")
    return user


def current_principal(user: DemoUser = Depends(current_user)) -> Principal:
    return get_principal(user.principal_id)


def require_ops(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.can("read_operations_signals"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This needs the ops role.")
    return principal
