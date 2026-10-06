"""受信任认证端的同源登录适配；不自行创建账号或签发用户权限。"""

import asyncio
import json
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from redis.exceptions import RedisError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.infrastructure.agent_context import AgentContextVerificationError
from app.infrastructure.browser_auth import BrowserAuthError, BrowserAuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def service(request: Request) -> BrowserAuthService:
    auth = getattr(request.app.state, "browser_auth", None)
    if auth is None:
        raise BrowserAuthError(503, "正式登录尚未配置，请联系管理员")
    return cast(BrowserAuthService, auth)


async def auth_error(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, BrowserAuthError):
        code, message = exc.status, str(exc)
    else:
        code, message = 503, "认证依赖暂时不可用"
    headers = {"Cache-Control": "no-store"}
    if code == 429:
        headers["Retry-After"] = "60"
    return JSONResponse({"detail": message}, code, headers=headers)


@router.get("/config")
async def config(request: Request, response: Response) -> dict[str, bool]:
    settings = request.app.state.settings
    response.headers["Cache-Control"] = "no-store"
    return {
        "enabled": settings.browser_auth_enabled,
        "demo_identity_allowed": settings.environment != "production"
        and not settings.browser_auth_enabled,
    }


@router.post("/login")
async def login(request: Request, response: Response) -> dict[str, Any]:
    auth = service(request)
    auth.require_origin(request.headers.get("origin"))
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise BrowserAuthError(400, "登录请求格式无效")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            raise BrowserAuthError(413, "登录请求过大")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "username",
            "password",
            "organization_id",
        }:
            raise ValueError()
        if any(
            not isinstance(value, str) or not value or len(value) > 1024
            for value in payload.values()
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise BrowserAuthError(400, "登录请求格式无效") from None
    previous = request.cookies.get(auth.cookie_name)
    async with asyncio.timeout(12):
        sid, session = await auth.login(
            payload, request.client.host if request.client else "unknown"
        )
        if previous:
            try:
                await auth.redis.delete(auth.key(previous))
            except BrowserAuthError:
                pass
    response.set_cookie(
        auth.cookie_name,
        sid,
        max_age=auth.settings.browser_auth_session_seconds,
        secure=auth.settings.browser_auth_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return session.public()


@router.get("/session")
async def session(request: Request, response: Response) -> dict[str, Any]:
    auth = service(request)
    async with asyncio.timeout(5):
        stored, _ = await auth.load(request.cookies.get(auth.cookie_name, ""), touch=True)
    response.headers["Cache-Control"] = "no-store"
    return stored.public()


@router.post("/refresh")
async def refresh(request: Request, response: Response) -> dict[str, Any]:
    auth = service(request)
    auth.require_origin(request.headers.get("origin"))
    async with asyncio.timeout(12):
        stored = await auth.refresh(
            request.cookies.get(auth.cookie_name, ""), request.headers.get("x-csrf-token")
        )
    response.headers["Cache-Control"] = "no-store"
    return stored.public()


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    auth = service(request)
    auth.require_origin(request.headers.get("origin"))
    async with asyncio.timeout(12):
        await auth.logout(
            request.cookies.get(auth.cookie_name, ""), request.headers.get("x-csrf-token")
        )
    response.delete_cookie(
        auth.cookie_name,
        secure=auth.settings.browser_auth_cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}


class BrowserSessionMiddleware:
    """还原现有签名 Header，复用业务接口 RBAC。读取失败绝不降级为匿名身份。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        auth = getattr(request.app.state, "browser_auth", None)
        try:
            if auth is not None and not scope["path"].startswith("/api/v1/auth/"):
                sid = request.cookies.get(auth.cookie_name)
                if sid:
                    if request.headers.get("x-agent-context"):
                        raise BrowserAuthError(400, "不允许混用浏览器会话与显式身份")
                    async with asyncio.timeout(5):
                        stored, _ = await auth.load(sid, touch=True)
                    if request.method not in {"GET", "HEAD", "OPTIONS"}:
                        auth.require_origin(request.headers.get("origin"))
                        auth.require_csrf(stored, request.headers.get("x-csrf-token"))
                    try:
                        auth.verifier.verify(stored.context)
                    except AgentContextVerificationError:
                        raise BrowserAuthError(401, "签名身份已过期，请刷新会话") from None
                    scope = dict(scope)
                    scope["headers"] = list(scope["headers"]) + [
                        (b"x-agent-context", stored.context.encode("ascii"))
                    ]
        except (BrowserAuthError, RedisError, ConnectionError, TimeoutError) as exc:
            await (await auth_error(request, exc))(scope, receive, send)
            return
        await self.app(scope, receive, send)
