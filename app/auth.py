"""Simple admin login for the config pages. A single password (ADMIN_PASSWORD).
If the password is empty, auth is disabled and everything is open — fine
locally / behind a VPN.

The cookie is an HMAC token derived from the secret, so it cannot be forged
without knowing the password/SESSION_SECRET. Stateless, no extra
dependencies."""

import hashlib
import hmac

from .config import settings

COOKIE_NAME = "avisa_session"


def auth_enabled() -> bool:
    return bool(settings.admin_password.strip())


def _secret() -> bytes:
    base = settings.session_secret.strip() or settings.admin_password
    return base.encode("utf-8")


def make_token() -> str:
    return hmac.new(_secret(), b"avisa-admin", hashlib.sha256).hexdigest()


def check_password(pw: str) -> bool:
    return hmac.compare_digest(pw or "", settings.admin_password)


def is_authed(request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(token, make_token())
