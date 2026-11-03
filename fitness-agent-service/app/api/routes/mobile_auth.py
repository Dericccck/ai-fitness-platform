"""移动端账户会话；上游仍是唯一的用户认证和权限签发方。"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.infrastructure.browser_auth import BrowserAuthError
from app.infrastructure.mobile_auth import MobileAuthService

router = APIRouter(prefix="/api/v1/mobile-auth", tags=["mobile-auth"])


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024, repr=False)
    organization_id: str = Field(min_length=1, max_length=128)


class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=43, max_length=43, repr=False)


def service(request: Request) -> MobileAuthService:
    result = getattr(request.app.state, "mobile_auth", None)
    if result is None:
        raise BrowserAuthError(503, "移动端登录暂未配置")
    return cast(MobileAuthService, result)


def bearer(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
        raise BrowserAuthError(401, "需要移动端访问凭证")
    # 不允许浏览器 Cookie 或调用者注入的 AgentContext 混用。
    if request.headers.get("x-agent-context"):
        raise BrowserAuthError(400, "不能混用移动端与签名身份")
    auth = getattr(request.app.state, "browser_auth", None)
    if auth is not None and request.cookies.get(auth.cookie_name):
        raise BrowserAuthError(400, "不能混用移动端与浏览器会话")
    return authorization[7:]


async def _body(request: Request, model: type[LoginInput | RefreshInput]) -> Any:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise BrowserAuthError(400, "请求格式无效")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 16384:
            raise BrowserAuthError(413, "请求内容过大")
    try:
        return model.model_validate(json.loads(data))
    except (ValueError, TypeError, ValidationError):
        raise BrowserAuthError(400, "请求格式无效") from None


@router.post("/login")
async def login(request: Request, response: Response) -> dict[str, Any]:
    payload: LoginInput = await _body(request, LoginInput)
    result = await service(request).login(
        payload.model_dump(), request.client.host if request.client else "unknown"
    )
    response.headers["Cache-Control"] = "no-store"
    return asdict(result)


@router.post("/refresh")
async def refresh(request: Request, response: Response) -> dict[str, Any]:
    payload: RefreshInput = await _body(request, RefreshInput)
    result = await service(request).rotate(payload.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return asdict(result)


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    await service(request).logout(bearer(request))
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}
