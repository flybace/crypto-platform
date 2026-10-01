"""Authentication endpoints for the standalone web application."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.dependencies import get_auth_service, require_user
from ..auth.service import AuthService, AuthenticatedUser, AuthenticationError


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, str]


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> LoginResponse:
    try:
        user = auth.authenticate(payload.username, payload.password)
    except AuthenticationError as error:
        raise HTTPException(status_code=401, detail="用户名或密码错误") from error
    return LoginResponse(access_token=auth.issue_token(user), user=user.as_dict())


@router.get("/me")
def current_user(user: AuthenticatedUser = Depends(require_user)) -> dict[str, str]:
    return user.as_dict()


@router.post("/logout")
def logout(_: AuthenticatedUser = Depends(require_user)) -> dict[str, str]:
    return {"status": "signed_out"}
