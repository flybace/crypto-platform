"""FastAPI dependencies for authenticated API routes."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from .service import AuthService, AuthenticatedUser, AuthenticationError


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


def require_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    try:
        return get_auth_service(request).verify_token(token)
    except AuthenticationError as error:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
