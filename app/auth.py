from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status


COOKIE_NAME = "codex_runner_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class AuthConfig:
    admin_password: str
    session_secret: str


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return _b64encode(digest)


def make_session_cookie(config: AuthConfig) -> str:
    payload = _b64encode(json.dumps({"sub": "admin", "iat": int(time.time())}).encode("utf-8"))
    return f"{payload}.{_sign(payload, config.session_secret)}"


def validate_session_cookie(cookie_value: str | None, config: AuthConfig) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False

    payload, signature = cookie_value.rsplit(".", 1)
    expected = _sign(payload, config.session_secret)
    if not hmac.compare_digest(signature, expected):
        return False

    try:
        data = json.loads(_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return False

    issued_at = int(data.get("iat", 0))
    return data.get("sub") == "admin" and time.time() - issued_at <= SESSION_MAX_AGE_SECONDS


def set_session_cookie(response: Response, config: AuthConfig) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_session_cookie(config),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def require_session(request: Request) -> None:
    config: AuthConfig = request.app.state.auth_config
    if not validate_session_cookie(request.cookies.get(COOKIE_NAME), config):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

