"""使用显式指定的隔离 Redis 和模拟认证上游验证会话轮换。"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.routes.auth import auth_error
from app.api.routes.mobile_auth import router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.browser_auth import BrowserAuthError, BrowserAuthService
from app.infrastructure.mobile_auth import MobileAuthService
from tests.test_browser_auth import SECRET, signed_context

pytestmark = pytest.mark.skipif(
    not os.getenv("AGENT_MOBILE_TEST_REDIS_URL"), reason="需要显式提供隔离 Redis"
)


async def make_service() -> tuple[MobileAuthService, Redis, list[str]]:
    url = os.environ["AGENT_MOBILE_TEST_REDIS_URL"]
    assert url.startswith("redis://127.0.0.1:") and url.endswith("/15")
    redis = Redis.from_url(url, decode_responses=True)
    calls: list[str] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("refresh"):
            await asyncio.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "agent_context": signed_context(
                    ttl=45 if request.url.path.endswith("login") else 300
                ),
                "refresh_token": "private-upstream-refresh-token-test-only",
            },
        )

    settings = Settings(
        _env_file=None,
        mobile_auth_enabled=True,
        browser_auth_upstream_url="https://issuer.test/sessions",
        browser_auth_upstream_service_token="test-service-secret",
    )
    cipher = AesGcmPayloadCipher(b"m" * 32, "v1")
    verifier = AgentContextVerifier(SECRET)
    backend = BrowserAuthService(
        settings,
        redis,
        cipher,
        verifier,
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    return MobileAuthService(settings, redis, cipher, verifier, backend), redis, calls


async def test_rotation_replay_revokes_family_and_never_stores_secrets_in_plaintext() -> None:
    service, redis, calls = await make_service()
    marker = uuid4().hex
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        ref, identity = await service.authenticate(tokens.access_token)
        assert identity.subject == "user-1" and calls.count("/sessions/refresh") == 1
        encrypted = await redis.get(service._family(ref))
        assert tokens.access_token not in encrypted and "private-upstream" not in encrypted
        results = await asyncio.gather(
            *(service.rotate(tokens.refresh_token) for _ in range(8)), return_exceptions=True
        )
        successes = [r for r in results if not isinstance(r, Exception)]
        # 一次成功，其余并发调用受锁或重放撤销约束；不会重复签发有效家族。
        assert len(successes) <= 1
        if successes:
            with pytest.raises(BrowserAuthError):
                await service.rotate(tokens.refresh_token)
            with pytest.raises(BrowserAuthError):
                await service.authenticate(successes[0].access_token)
        assert await service.identity(ref) is None
    finally:
        await service.upstream.close()
        await redis.aclose()


async def test_logout_revokes_worker_reference_and_old_access() -> None:
    service, redis, _ = await make_service()
    marker = uuid4().hex
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        ref, _ = await service.authenticate(tokens.access_token)
        rotated = await service.rotate(tokens.refresh_token)
        with pytest.raises(BrowserAuthError):
            await service.authenticate(tokens.access_token)
        await service.logout(rotated.access_token)
        assert await service.identity(ref) is None
        with pytest.raises(BrowserAuthError):
            await service.rotate(rotated.refresh_token)
    finally:
        await service.upstream.close()
        await redis.aclose()


async def test_mobile_http_login_refresh_and_logout_are_no_store() -> None:
    service, redis, _ = await make_service()
    app = FastAPI()
    app.state.mobile_auth = service
    app.state.browser_auth = None
    app.add_exception_handler(BrowserAuthError, auth_error)
    app.include_router(router)
    marker = uuid4().hex
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://app.test"
        ) as client:
            login = await client.post(
                "/api/v1/mobile-auth/login",
                json={
                    "username": marker,
                    "password": "test-only",
                    "organization_id": "org-1",
                },
            )
            assert login.status_code == 200 and login.headers["cache-control"] == "no-store"
            tokens = login.json()
            refreshed = await client.post(
                "/api/v1/mobile-auth/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
            assert (
                refreshed.status_code == 200
                and refreshed.json()["access_token"] != tokens["access_token"]
            )
            access = refreshed.json()["access_token"]
            mixed = await client.post(
                "/api/v1/mobile-auth/logout",
                headers={
                    "Authorization": "Bearer " + access,
                    "X-Agent-Context": "untrusted",
                },
            )
            assert mixed.status_code == 400
            logout = await client.post(
                "/api/v1/mobile-auth/logout", headers={"Authorization": "Bearer " + access}
            )
            assert logout.status_code == 200 and logout.json() == {"authenticated": False}
    finally:
        await service.upstream.close()
        await redis.aclose()
