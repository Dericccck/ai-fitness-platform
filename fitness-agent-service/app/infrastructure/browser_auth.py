"""同源 BFF：浏览器只持有随机 Cookie，签名身份与刷新凭证只在加密 Redis 中。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentContextVerifier, AgentIdentity


class BrowserAuthError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BrowserSession:
    context: str = field(repr=False)
    refresh_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    subject: str
    organization_id: str
    roles: tuple[str, ...]
    expires_at: int
    absolute_expiry: int

    def public(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "subject": self.subject,
            "organization_id": self.organization_id,
            "roles": self.roles,
            "expires_at": self.expires_at,
            "session_expires_at": self.absolute_expiry,
            "csrf_token": self.csrf_token,
        }


class BrowserAuthService:
    _CAS = """
    if redis.call('get',KEYS[1]) == ARGV[1] then
      redis.call('set',KEYS[1],ARGV[2],'EX',ARGV[3]); return 1
    end
    return 0
    """
    _RELEASE = """
    if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end
    return 0
    """

    def __init__(
        self,
        settings: Settings,
        redis: Any,
        cipher: AesGcmPayloadCipher,
        verifier: AgentContextVerifier,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.cipher = cipher
        self.verifier = verifier
        self.client = client or httpx.AsyncClient(
            timeout=5, follow_redirects=False, trust_env=False
        )

    @property
    def cookie_name(self) -> str:
        return (
            "__Host-fitness-session"
            if self.settings.browser_auth_cookie_secure
            else "fitness-session"
        )

    def require_origin(self, origin: str | None) -> None:
        if origin not in self.settings.browser_auth_allowed_origins:
            raise BrowserAuthError(403, "请求来源未获授权")

    @staticmethod
    def key(sid: str) -> str:
        if len(sid) != 43 or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for ch in sid
        ):
            raise BrowserAuthError(401, "会话无效，请重新登录")
        return "fitness:browser-session:" + hashlib.sha256(sid.encode()).hexdigest()

    def _encode(self, key: str, session: BrowserSession) -> str:
        plaintext = json.dumps(session.__dict__, ensure_ascii=False).encode()
        ciphertext = self.cipher.encrypt(plaintext, associated_data=key)
        return json.dumps(
            {"version": self.cipher.key_version, "cipher": base64.b64encode(ciphertext).decode()}
        )

    async def load(self, sid: str, *, touch: bool = False) -> tuple[BrowserSession, str]:
        key = self.key(sid)
        raw = await self.redis.get(key)
        if not raw:
            raise BrowserAuthError(401, "会话已失效，请重新登录")
        try:
            envelope = json.loads(raw)
            data = json.loads(
                self.cipher.decrypt(
                    base64.b64decode(envelope["cipher"], validate=True),
                    associated_data=key,
                    key_version=envelope["version"],
                )
            )
            data["roles"] = tuple(data["roles"])
            session = BrowserSession(**data)
        except Exception as exc:
            raise BrowserAuthError(401, "会话无法恢复，请重新登录") from exc
        remaining = session.absolute_expiry - int(time.time())
        if remaining <= 0:
            await self.redis.delete(key)
            raise BrowserAuthError(401, "会话已过期，请重新登录")
        if touch:
            await self.redis.expire(key, min(remaining, self.settings.browser_auth_idle_seconds))
        return session, str(raw)

    @staticmethod
    def require_csrf(session: BrowserSession, token: str | None) -> None:
        if not token or not hmac.compare_digest(session.csrf_token, token):
            raise BrowserAuthError(403, "会话请求校验失败")

    async def _upstream(self, operation: str, payload: dict[str, str]) -> dict[str, Any]:
        try:
            async with self.client.stream(
                "POST",
                self.settings.browser_auth_upstream_url.rstrip("/") + "/" + operation,
                headers={
                    "X-Internal-Service-Token": self.settings.browser_auth_upstream_service_token
                },
                json=payload,
            ) as response:
                if response.status_code in {401, 403}:
                    raise BrowserAuthError(401, "认证失败，请重新登录")
                if response.status_code != 200:
                    raise BrowserAuthError(503, "认证服务暂时不可用")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise BrowserAuthError(503, "认证响应不符合契约")
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise TypeError("object required")
                return data
        except BrowserAuthError:
            raise
        except Exception as exc:
            raise BrowserAuthError(503, "认证服务暂时不可用") from exc

    def _validated(self, data: dict[str, Any]) -> tuple[str, str, AgentIdentity]:
        context, refresh = data.get("agent_context"), data.get("refresh_token")
        if (
            not isinstance(context, str)
            or not isinstance(refresh, str)
            or not 20 <= len(refresh) <= 8192
        ):
            raise BrowserAuthError(503, "认证响应不符合契约")
        try:
            identity = self.verifier.verify(context)
        except Exception as exc:
            raise BrowserAuthError(503, "认证响应的签名身份无效") from exc
        if identity.expires_at <= int(time.time()) + 30:
            raise BrowserAuthError(503, "认证响应有效期不足")
        return context, refresh, identity

    async def login(self, credentials: dict[str, str], peer: str) -> tuple[str, BrowserSession]:
        for name, value, limit in (
            ("peer", peer, 30),
            ("account", credentials["username"].strip().casefold(), 10),
        ):
            quota = (
                "fitness:browser-login:" + name + ":" + hashlib.sha256(value.encode()).hexdigest()
            )
            count = await self.redis.eval(
                "local n=redis.call('incr',KEYS[1]); if n==1 then redis.call('expire',KEYS[1],60) end; return n",
                1,
                quota,
            )
            if int(count) > limit:
                raise BrowserAuthError(429, "登录请求过于频繁")
        context, refresh, identity = self._validated(await self._upstream("login", credentials))
        organization = credentials["organization_id"]
        if identity.organization_ids != frozenset({organization}):
            raise BrowserAuthError(403, "当前账号不能访问所选机构")
        sid = secrets.token_urlsafe(32)
        session = BrowserSession(
            context,
            refresh,
            secrets.token_urlsafe(32),
            identity.subject,
            organization,
            tuple(sorted(identity.roles)),
            identity.expires_at,
            int(time.time()) + self.settings.browser_auth_session_seconds,
        )
        if not await self.redis.set(
            self.key(sid),
            self._encode(self.key(sid), session),
            ex=min(
                self.settings.browser_auth_idle_seconds, self.settings.browser_auth_session_seconds
            ),
            nx=True,
        ):
            raise BrowserAuthError(503, "无法创建会话")
        return sid, session

    async def refresh(self, sid: str, csrf: str | None) -> BrowserSession:
        key = self.key(sid)
        session, original = await self.load(sid)
        self.require_csrf(session, csrf)
        lock_owner = secrets.token_urlsafe(32)
        lock = key + ":refresh"
        if not await self.redis.set(lock, lock_owner, nx=True, ex=15):
            raise BrowserAuthError(409, "会话正在刷新，请稍后重试")
        try:
            session, original = await self.load(sid)
            self.require_csrf(session, csrf)
            if session.expires_at > int(time.time()) + 60:
                return session
            try:
                context, refresh, identity = self._validated(
                    await self._upstream("refresh", {"refresh_token": session.refresh_token})
                )
            except (BrowserAuthError, asyncio.CancelledError, TimeoutError):

                async def invalidate() -> None:
                    async with asyncio.timeout(2):
                        await self.redis.delete(key)

                await asyncio.shield(invalidate())
                raise
            if identity.subject != session.subject or identity.organization_ids != frozenset(
                {session.organization_id}
            ):
                await self.redis.delete(key)
                raise BrowserAuthError(401, "账号授权范围已变化，请重新登录")
            updated = replace(
                session,
                context=context,
                refresh_token=refresh,
                roles=tuple(sorted(identity.roles)),
                expires_at=identity.expires_at,
            )
            remaining = updated.absolute_expiry - int(time.time())
            if remaining <= 0 or not await self.redis.eval(
                self._CAS,
                1,
                key,
                original,
                self._encode(key, updated),
                str(min(remaining, self.settings.browser_auth_idle_seconds)),
            ):
                raise BrowserAuthError(401, "会话已撤销，请重新登录")
            return updated
        finally:
            await self.redis.eval(self._RELEASE, 1, lock, lock_owner)

    async def logout(self, sid: str, csrf: str | None) -> None:
        session, _ = await self.load(sid)
        self.require_csrf(session, csrf)
        await self.redis.delete(self.key(sid))
        try:
            await self._upstream("logout", {"refresh_token": session.refresh_token})
        except BrowserAuthError:
            pass

    async def close(self) -> None:
        await self.client.aclose()
