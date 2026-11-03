"""移动端不透明会话：凭据仅存哈希，上游刷新凭证和身份上下文加密保存。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field, replace
from typing import Any

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    AgentContextVerifier,
    AgentIdentity,
)
from app.infrastructure.browser_auth import BrowserAuthError, BrowserAuthService


@dataclass(frozen=True)
class MobileSession:
    context: str = field(repr=False)
    upstream_refresh: str = field(repr=False)
    access_hash: str = field(repr=False)
    refresh_hash: str = field(repr=False)
    subject: str
    organization: str
    absolute_expiry: int


@dataclass(frozen=True)
class MobileTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    session_expires_at: int
    subject: str
    organization_id: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _check_token(token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise BrowserAuthError(401, "移动会话无效，请重新登录")
    return _hash(token)


class MobileAuthService:
    """所有令牌轮换和上游刷新共用 family 锁及密文 CAS。"""

    _CREATE = """
      if redis.call('exists',KEYS[1]) ~= 0 or redis.call('exists',KEYS[2]) ~= 0
         or redis.call('exists',KEYS[3]) ~= 0 then return 0 end
      redis.call('set',KEYS[1],ARGV[1],'EX',ARGV[2])
      redis.call('set',KEYS[2],ARGV[3],'EX',ARGV[4])
      redis.call('set',KEYS[3],ARGV[3],'EX',ARGV[2]); return 1
    """
    _CAS = """
      if redis.call('get',KEYS[1]) ~= ARGV[1] or redis.call('get',KEYS[2]) ~= ARGV[2]
         then return 0 end
      redis.call('set',KEYS[1],ARGV[3],'EX',ARGV[4]); return 1
    """
    _ROTATE = """
      if redis.call('get',KEYS[1]) ~= ARGV[1] or redis.call('get',KEYS[2]) ~= ARGV[2]
         or redis.call('get',KEYS[3]) ~= ARGV[3] then return 0 end
      redis.call('set',KEYS[1],ARGV[4],'EX',ARGV[5])
      redis.call('del',KEYS[3],KEYS[4])
      redis.call('set',KEYS[5],ARGV[3],'EX',ARGV[6])
      redis.call('set',KEYS[6],ARGV[3],'EX',ARGV[5])
      redis.call('set',KEYS[7],ARGV[3],'EX',ARGV[5]); return 1
    """
    _REVOKE = """
      if redis.call('get',KEYS[1]) ~= ARGV[1] then return 0 end
      redis.call('del',KEYS[1],KEYS[2],KEYS[3]); return 1
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
        upstream: BrowserAuthService,
    ) -> None:
        self.settings, self.redis, self.cipher = settings, redis, cipher
        self.verifier, self.upstream = verifier, upstream

    @staticmethod
    def _family(ref: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{64}", ref):
            raise BrowserAuthError(401, "移动会话无效，请重新登录")
        return "fitness:mobile:family:" + ref

    @staticmethod
    def _access(digest: str) -> str:
        return "fitness:mobile:access:" + digest

    @staticmethod
    def _refresh(digest: str) -> str:
        return "fitness:mobile:refresh:" + digest

    @staticmethod
    def _used_refresh(digest: str) -> str:
        return "fitness:mobile:used-refresh:" + digest

    def _encode(self, key: str, session: MobileSession) -> str:
        payload = json.dumps(session.__dict__, ensure_ascii=False).encode()
        encrypted = self.cipher.encrypt(payload, associated_data=key)
        return json.dumps({"v": self.cipher.key_version, "c": base64.b64encode(encrypted).decode()})

    async def _load(self, ref: str) -> tuple[MobileSession, str]:
        key = self._family(ref)
        raw = await self.redis.get(key)
        if not raw:
            raise BrowserAuthError(401, "移动会话已失效，请重新登录")
        try:
            envelope = json.loads(raw)
            value = self.cipher.decrypt(
                base64.b64decode(envelope["c"], validate=True),
                associated_data=key,
                key_version=envelope["v"],
            )
            session = MobileSession(**json.loads(value))
        except Exception as exc:
            raise BrowserAuthError(401, "移动会话无法恢复，请重新登录") from exc
        if session.absolute_expiry <= int(time.time()):
            raise BrowserAuthError(401, "移动会话已过期，请重新登录")
        return session, str(raw)

    async def _lock(self, ref: str) -> tuple[str, str]:
        key, owner = self._family(ref) + ":lock", secrets.token_urlsafe(32)
        if not await self.redis.set(key, owner, nx=True, ex=15):
            raise BrowserAuthError(409, "会话正在刷新，请稍后重试")
        return key, owner

    async def _release(self, key: str, owner: str) -> None:
        await self.redis.eval(self._RELEASE, 1, key, owner)

    async def _revoke(self, ref: str, session: MobileSession, raw: str) -> bool:
        return bool(
            await self.redis.eval(
                self._REVOKE,
                3,
                self._family(ref),
                self._access(session.access_hash),
                self._refresh(session.refresh_hash),
                raw,
            )
        )

    async def login(self, credentials: dict[str, str], peer: str) -> MobileTokens:
        for kind, value, limit in (
            ("peer", peer, 30),
            ("account", credentials["username"].strip().casefold(), 10),
        ):
            key = "fitness:mobile:login:" + kind + ":" + _hash(value)
            count = await self.redis.eval(
                "local n=redis.call('incr',KEYS[1]); if n==1 then redis.call('expire',KEYS[1],60) end; return n",
                1,
                key,
            )
            if int(count) > limit:
                raise BrowserAuthError(429, "登录请求过于频繁")
        context, upstream_refresh, identity = self.upstream._validated(
            await self.upstream._upstream("login", credentials)
        )
        organization = credentials["organization_id"]
        if identity.organization_ids != frozenset({organization}):
            raise BrowserAuthError(403, "当前账号不能访问所选机构")
        ref = _hash(secrets.token_urlsafe(32))
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        lifetime = self.settings.mobile_session_ttl_seconds
        session = MobileSession(
            context,
            upstream_refresh,
            _hash(access),
            _hash(refresh),
            identity.subject,
            organization,
            int(time.time()) + lifetime,
        )
        if not await self.redis.eval(
            self._CREATE,
            3,
            self._family(ref),
            self._access(session.access_hash),
            self._refresh(session.refresh_hash),
            self._encode(self._family(ref), session),
            str(lifetime),
            ref,
            str(min(lifetime, self.settings.mobile_access_ttl_seconds)),
        ):
            raise BrowserAuthError(503, "无法创建移动会话")
        return MobileTokens(
            access,
            refresh,
            min(lifetime, self.settings.mobile_access_ttl_seconds),
            session.absolute_expiry,
            session.subject,
            session.organization,
        )

    async def identity(self, ref: str) -> AgentIdentity | None:
        """供受信 worker 使用的不可逆 family 引用；客户端不能用引用直接认证。"""
        try:
            session, _ = await self._load(ref)
            try:
                identity = self.verifier.verify(session.context)
                if identity.expires_at > int(time.time()) + 60:
                    return identity
            except AgentContextVerificationError:
                identity = None
            lock, owner = await self._lock(ref)
            try:
                session, raw = await self._load(ref)
                try:
                    identity = self.verifier.verify(session.context)
                    if identity.expires_at > int(time.time()) + 60:
                        return identity
                except AgentContextVerificationError:
                    identity = None
                try:
                    context, upstream_refresh, identity = self.upstream._validated(
                        await self.upstream._upstream(
                            "refresh", {"refresh_token": session.upstream_refresh}
                        )
                    )
                except (BrowserAuthError, asyncio.CancelledError, TimeoutError):
                    await asyncio.shield(self._revoke(ref, session, raw))
                    raise
                if identity.subject != session.subject or identity.organization_ids != frozenset(
                    {session.organization}
                ):
                    await self._revoke(ref, session, raw)
                    return None
                updated = replace(session, context=context, upstream_refresh=upstream_refresh)
                remaining = updated.absolute_expiry - int(time.time())
                if remaining <= 0 or not await self.redis.eval(
                    self._CAS,
                    2,
                    self._family(ref),
                    lock,
                    raw,
                    owner,
                    self._encode(self._family(ref), updated),
                    str(remaining),
                ):
                    return None
                return identity
            finally:
                await self._release(lock, owner)
        except BrowserAuthError as exc:
            if exc.status == 401:
                return None
            raise

    async def authenticate(self, access_token: str) -> tuple[str, AgentIdentity]:
        digest = _check_token(access_token)
        ref = await self.redis.get(self._access(digest))
        if not ref:
            raise BrowserAuthError(401, "移动会话已失效，请重新登录")
        session, _ = await self._load(str(ref))
        if not hmac.compare_digest(session.access_hash, digest):
            raise BrowserAuthError(401, "移动会话已失效，请重新登录")
        identity = await self.identity(str(ref))
        if identity is None:
            raise BrowserAuthError(401, "移动会话已失效，请重新登录")
        return str(ref), identity

    async def rotate(self, refresh_token: str) -> MobileTokens:
        digest = _check_token(refresh_token)
        ref = await self.redis.get(self._refresh(digest))
        if not ref:
            # 轮换后的旧凭证再次出现可能是重放；撤销该 family 的当前凭证。
            used_ref = await self.redis.get(self._used_refresh(digest))
            if used_ref:
                for _ in range(3):
                    try:
                        current, raw = await self._load(str(used_ref))
                    except BrowserAuthError:
                        break
                    if await self._revoke(str(used_ref), current, raw):
                        break
            raise BrowserAuthError(401, "刷新凭证已失效，请重新登录")
        ref = str(ref)
        lock, owner = await self._lock(ref)
        try:
            session, raw = await self._load(ref)
            if not hmac.compare_digest(session.refresh_hash, digest):
                raise BrowserAuthError(401, "刷新凭证已失效，请重新登录")
            access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            updated = replace(session, access_hash=_hash(access), refresh_hash=_hash(refresh))
            remaining = session.absolute_expiry - int(time.time())
            access_ttl = min(remaining, self.settings.mobile_access_ttl_seconds)
            if remaining <= 0 or not await self.redis.eval(
                self._ROTATE,
                7,
                self._family(ref),
                lock,
                self._refresh(digest),
                self._access(session.access_hash),
                self._access(updated.access_hash),
                self._refresh(updated.refresh_hash),
                self._used_refresh(digest),
                raw,
                owner,
                ref,
                self._encode(self._family(ref), updated),
                str(remaining),
                str(access_ttl),
            ):
                raise BrowserAuthError(401, "移动会话已撤销，请重新登录")
            return MobileTokens(
                access,
                refresh,
                access_ttl,
                session.absolute_expiry,
                session.subject,
                session.organization,
            )
        finally:
            await self._release(lock, owner)

    async def logout(self, access_token: str) -> None:
        digest = _check_token(access_token)
        ref = await self.redis.get(self._access(digest))
        if not ref:
            return
        for _ in range(3):
            try:
                session, raw = await self._load(str(ref))
            except BrowserAuthError:
                return
            if not hmac.compare_digest(session.access_hash, digest):
                return
            if await self._revoke(str(ref), session, raw):
                try:
                    await self.upstream._upstream(
                        "logout", {"refresh_token": session.upstream_refresh}
                    )
                except BrowserAuthError:
                    pass
                return
        raise BrowserAuthError(409, "会话状态已变化，请重试退出")
