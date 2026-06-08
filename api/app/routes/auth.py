"""Authentication and session-management routes.

The API uses short-lived access tokens plus persisted refresh tokens. Login also
participates in Redis-backed rate limiting and account lockout so brute-force
protection lives close to the credential check.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..app_limiter import limiter
from ..auth import (
    AuthUser,
    LoginRequest,
    RefreshRequest,
    authenticate_user_record,
    create_access_token,
    create_refresh_token,
    exchange_refresh_token,
    get_current_user,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
)
from ..db import SessionLocal
from ..login_helpers import clear_login_failures, is_account_locked, record_failed_login

router = APIRouter(tags=["Auth"])


class LogoutRequest(BaseModel):
    """Optional refresh token to revoke during single-session logout."""

    refresh_token: str | None = None


@router.post("/auth/login")
@limiter.limit("5/15minutes")
def login(request: Request, response: Response, payload: LoginRequest):
    """Authenticate credentials and mint a new access/refresh token pair."""

    locked_for = is_account_locked(payload.username)
    if locked_for:
        response.headers["Retry-After"] = str(locked_for)
        raise HTTPException(423, f"Account locked. Try again in {locked_for // 60 + 1} minutes.")

    db = SessionLocal()
    try:
        try:
            user_row = authenticate_user_record(db, payload.username, payload.password)
        except HTTPException:
            record_failed_login(payload.username)
            locked_for = is_account_locked(payload.username)
            if locked_for:
                response.headers["Retry-After"] = str(locked_for)
                raise HTTPException(423, f"Account locked. Try again in {locked_for // 60 + 1} minutes.")
            raise
        clear_login_failures(payload.username)
        auth_user = AuthUser(username=user_row.username, roles=list(user_row.roles))
        refresh_token = create_refresh_token(db, user_row, request.client.host if request.client else None)
        db.commit()
        return {
            "access_token": create_access_token(auth_user),
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": auth_user.model_dump(),
        }
    finally:
        db.close()


@router.post("/auth/refresh")
def refresh_auth(request: Request, payload: RefreshRequest):
    """Rotate a valid refresh token and return a fresh access token."""

    db = SessionLocal()
    try:
        user, refresh_token = exchange_refresh_token(db, payload.refresh_token, request.client.host if request.client else None)
        db.commit()
        return {
            "access_token": create_access_token(user),
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": user.model_dump(),
        }
    finally:
        db.close()


@router.post("/auth/logout")
def logout(payload: LogoutRequest, user: Annotated[AuthUser, Depends(get_current_user)]):
    """Revoke the supplied refresh token without ending other sessions."""

    db = SessionLocal()
    try:
        revoked = revoke_refresh_token(db, payload.refresh_token) if payload.refresh_token else False
        db.commit()
        return {"revoked": revoked}
    finally:
        db.close()


@router.post("/auth/logout-all")
def logout_all(user: Annotated[AuthUser, Depends(get_current_user)]):
    """Revoke every refresh token belonging to the authenticated user."""

    db = SessionLocal()
    try:
        count = revoke_all_refresh_tokens(db, user.username)
        db.commit()
        return {"revoked": count}
    finally:
        db.close()


@router.get("/auth/me")
def me(user: Annotated[AuthUser, Depends(get_current_user)]):
    """Return the authenticated user payload used by the frontend shell."""

    return user
