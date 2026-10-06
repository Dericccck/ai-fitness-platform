"""真实 Redis + 受控模拟认证端，绝不调用真实账号登录或业务写入。"""

import asyncio
import hashlib
import os
import time
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis

from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.browser_auth import BrowserAuthError, BrowserAuthService
from tests.test_browser_auth import SECRET, signed_context

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_RUNTIME_DELIVERY_TESTS") != "1", reason="需要隔离验收显式开启"
)


async def test_real_redis_refresh_lock_cas_logout_and_key_rotation() -> None:
    client = Redis.from_url(Settings(_env_file=None).redis_url, decode_responses=True)
    marker = "runtime-auth-" + uuid4().hex
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
                "refresh_token": "isolated-test-upstream-refresh-token",
            },
        )

    settings = Settings(
        _env_file=None,
        browser_auth_enabled=True,
        browser_auth_upstream_url="https://mock.test/sessions",
        browser_auth_upstream_service_token="isolated-test-service-token",
        browser_auth_allowed_origins=["https://fitness.test"],
    )
    auth = BrowserAuthService(
        settings,
        client,
        AesGcmPayloadCipher(b"1" * 32, "v1"),
        AgentContextVerifier(SECRET),
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    keys = [
        "fitness:browser-login:" + kind + ":" + hashlib.sha256(marker.encode()).hexdigest()
        for kind in ("peer", "account")
    ]
    try:
        sid, session = await auth.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        keys += [auth.key(sid), auth.key(sid) + ":refresh"]
        raw = await client.get(auth.key(sid))
        assert "refresh-token" not in raw and session.context not in raw
        assert 0 < await client.ttl(auth.key(sid)) <= settings.browser_auth_idle_seconds
        auth.cipher = AesGcmPayloadCipher(b"2" * 32, "v2", {"v1": b"1" * 32})
        assert (await auth.load(sid))[0].subject == session.subject
        results = await asyncio.gather(
            *(auth.refresh(sid, session.csrf_token) for _ in range(20)), return_exceptions=True
        )
        assert calls.count("/sessions/refresh") == 1
        assert all(
            not isinstance(result, Exception)
            or (isinstance(result, BrowserAuthError) and result.status == 409)
            for result in results
        )
        assert (await auth.load(sid))[0].expires_at >= int(time.time()) + 290
        assert '"version": "v2"' in await client.get(auth.key(sid))
        await auth.logout(sid, session.csrf_token)
        assert await client.get(auth.key(sid)) is None
    finally:
        await client.delete(*keys)
        await auth.close()
        await client.aclose()
