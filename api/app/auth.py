from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable

import jwt
from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import settings
from .models import User


security = HTTPBearer(auto_error=False)
password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
get_db_session: Callable[[], Session] | None = None

DEV_USERS = {
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


def configure_auth(session_factory: Callable[[], Session]):
    global get_db_session
    get_db_session = session_factory


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_context.verify(password, password_hash)


def seed_dev_users():
    if not settings.seed_dev_users:
        return
    if get_db_session is None:
        raise RuntimeError("Auth database session factory is not configured")

    db = get_db_session()
    try:
        for username, data in DEV_USERS.items():
            user = db.query(User).filter_by(username=username).first()
            if user:
                continue
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(data["password"]),
                    roles=data["roles"],
                    is_active=True,
                )
            )
        db.commit()
    finally:
        db.close()


def create_access_token(user: AuthUser) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.username,
        "roles": user.roles,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> AuthUser:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return AuthUser(username=data["sub"], roles=list(data.get("roles", [])))
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")


def authenticate(username: str, password: str) -> AuthUser:
    if get_db_session is None:
        raise RuntimeError("Auth database session factory is not configured")

    db = get_db_session()
    try:
        user = db.query(User).filter_by(username=username).first()
        if not user or not user.is_active or not verify_password(password, user.password_hash):
            raise HTTPException(401, "Invalid username or password")
        return AuthUser(username=user.username, roles=list(user.roles))
    finally:
        db.close()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    token: Annotated[str | None, Query()] = None,
) -> AuthUser:
    bearer = credentials.credentials if credentials else None
    query_token = token if settings.allow_query_token_auth else None
    return decode_access_token(bearer or query_token or "")


def require_roles(*allowed_roles: str):
    def dependency(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        if "admin" in user.roles or set(user.roles).intersection(allowed_roles):
            return user
        raise HTTPException(403, "Insufficient role")

    return dependency
