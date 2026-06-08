"""Authentication, authorization, and token lifecycle helpers.

The app uses short-lived JWT access tokens and rotating refresh tokens.  Only
refresh token hashes are stored in Postgres, so database access alone does not
grant a reusable browser session.  Route modules import the dependency helpers
from here to keep role checks consistent.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable

import jwt
from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import settings
from .models import RefreshToken, User


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


class RefreshRequest(BaseModel):
    refresh_token: str


def configure_auth(session_factory: Callable[[], Session]):
    """Inject the SQLAlchemy session factory after app wiring is complete."""
    global get_db_session
    get_db_session = session_factory


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_context.verify(password, password_hash)


def seed_dev_users():
    """Create local development users when explicitly enabled.

    Production startup validation refuses to run with dev seeding enabled.
    """
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
    """Create a JWT access token with a key id for safe signing-key rotation."""
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET must be set")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.username,
        "roles": user.roles,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256", headers={"kid": settings.jwt_key_id})


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_refresh_token(db: Session, user: User, created_from_ip: str | None = None) -> str:
    """Issue an opaque refresh token and persist only a SHA-256 hash."""
    token = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days),
            created_from_ip=created_from_ip,
        )
    )
    return token


def exchange_refresh_token(db: Session, token: str, created_from_ip: str | None = None) -> tuple[AuthUser, str]:
    """Rotate a refresh token into a new access/refresh pair.

    The presented refresh token is revoked as part of the exchange, making
    refresh tokens single-use and limiting replay if one is captured.
    """
    row = db.query(RefreshToken).filter_by(token_hash=hash_refresh_token(token)).first()
    now = datetime.now(timezone.utc)
    if not row or row.revoked_at is not None or row.expires_at <= now:
        raise HTTPException(401, "Invalid or expired refresh token")

    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid or expired refresh token")

    row.revoked_at = now
    next_token = create_refresh_token(db, user, created_from_ip)
    auth_user = AuthUser(username=user.username, roles=list(user.roles))
    return auth_user, next_token


def revoke_refresh_token(db: Session, token: str) -> bool:
    row = db.query(RefreshToken).filter_by(token_hash=hash_refresh_token(token)).first()
    if not row or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    return True


def revoke_all_refresh_tokens(db: Session, username: str) -> int:
    user = db.query(User).filter_by(username=username).first()
    if not user:
        return 0
    now = datetime.now(timezone.utc)
    rows = db.query(RefreshToken).filter_by(user_id=user.id, revoked_at=None).all()
    for row in rows:
        row.revoked_at = now
    return len(rows)


def decode_access_token(token: str) -> AuthUser:
    """Validate a JWT against the current signing key and rotation window."""
    secrets = settings.jwt_secret_versions
    if not secrets:
        raise HTTPException(401, "Invalid or expired token")
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        candidate_secrets = [secrets[kid]] if kid in secrets else list(secrets.values())
        last_error = None
        for secret in candidate_secrets:
            try:
                data = jwt.decode(token, secret, algorithms=["HS256"])
                return AuthUser(username=data["sub"], roles=list(data.get("roles", [])))
            except jwt.PyJWTError as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise jwt.InvalidTokenError("No signing key available")
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


def authenticate_user_record(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter_by(username=username).first()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    return user


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    token: Annotated[str | None, Query()] = None,
) -> AuthUser:
    bearer = credentials.credentials if credentials else None
    query_token = token if settings.allow_query_token_auth else None
    return decode_access_token(bearer or query_token or "")


def require_roles(*allowed_roles: str):
    """Build a FastAPI dependency that allows admins or selected roles."""
    def dependency(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        if "admin" in user.roles or set(user.roles).intersection(allowed_roles):
            return user
        raise HTTPException(403, "Insufficient role")

    return dependency
