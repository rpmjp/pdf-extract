import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .config import settings


security = HTTPBearer(auto_error=False)

USERS = {
    "uploader": {"password": "upload123", "roles": ["uploader"]},
    "reviewer": {"password": "review123", "roles": ["reviewer", "uploader"]},
    "admin": {"password": "admin123", "roles": ["admin", "reviewer", "uploader"]},
}


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUser(BaseModel):
    username: str
    roles: list[str]


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(message: str) -> str:
    digest = hmac.new(settings.jwt_secret.encode(), message.encode(), hashlib.sha256).digest()
    return _b64encode(digest)


def create_access_token(user: AuthUser) -> str:
    now = datetime.now(timezone.utc)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user.username,
        "roles": user.roles,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_minutes)).timestamp()),
    }
    signing_input = ".".join(
        [
            _b64encode(json.dumps(header, separators=(",", ":")).encode()),
            _b64encode(json.dumps(payload, separators=(",", ":")).encode()),
        ]
    )
    return f"{signing_input}.{_sign(signing_input)}"


def decode_access_token(token: str) -> AuthUser:
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}"
        if not hmac.compare_digest(_sign(signing_input), signature):
            raise ValueError("bad signature")
        data = json.loads(_b64decode(payload))
        if int(data["exp"]) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("expired")
        return AuthUser(username=data["sub"], roles=list(data.get("roles", [])))
    except Exception:
        raise HTTPException(401, "Invalid or expired token")


def authenticate(username: str, password: str) -> AuthUser:
    record = USERS.get(username)
    if not record or not hmac.compare_digest(record["password"], password):
        raise HTTPException(401, "Invalid username or password")
    return AuthUser(username=username, roles=record["roles"])


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    token: Annotated[str | None, Query()] = None,
) -> AuthUser:
    bearer = credentials.credentials if credentials else None
    return decode_access_token(bearer or token or "")


def require_roles(*allowed_roles: str):
    def dependency(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        if "admin" in user.roles or set(user.roles).intersection(allowed_roles):
            return user
        raise HTTPException(403, "Insufficient role")

    return dependency
