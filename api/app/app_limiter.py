"""Application-wide rate limiter configuration.

SlowAPI is initialized once and attached to the FastAPI app in ``main.py``.
Routes opt into limits where abuse would create real cost or risk: login,
upload, and parse.  Authenticated actions use a username-based key so several
reviewers behind one corporate NAT do not share a single quota.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from .auth import decode_access_token
from .config import settings


def user_limit_key(request: Request) -> str:
    """Return a stable rate-limit key for authenticated users.

    If the token is missing or invalid we fall back to IP address.  The fallback
    keeps unauthenticated requests protected without failing the request during
    limiter key calculation.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        try:
            return f"user:{decode_access_token(auth_header.split(' ', 1)[1]).username}"
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.redis_url,
    default_limits=[],
)
