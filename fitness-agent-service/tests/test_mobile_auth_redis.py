"""使用显式指定的隔离 Redis 和模拟认证上游验证会话轮换。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import replace
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


def old_but_unexpired_context() -> str:
    now = int(time.time())
    payload = json.dumps(
        {
            "sub": "user-1",
            "orgs": ["org-1"],
            "roles": ["STUDENT"],
            "iat": now - 100,
            "exp": now + 150,
            "nonce": "test-nonce",
        }
    ).encode()
    signature = hmac.new(SECRET.encode(), payload, hashlib.sha256).digest()
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return encode(payload) + "." + encode(signature)


async def test_stale_context_is_refreshed_before_authenticated_access():
    service, redis, calls = await make_service()
    marker = uuid4().hex
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        # Authenticate once to obtain the internal family, then simulate passage of time.
        ref, _ = await service.authenticate(tokens.access_token)
        session, _ = await service._load(ref)
        await redis.set(
            service._family(ref),
            service._encode(
                service._family(ref), replace(session, context=old_but_unexpired_context())
            ),
            ex=300,
        )
        before = calls.count("/sessions/refresh")
        _, identity = await service.authenticate(tokens.access_token)
        assert identity.subject == "user-1" and calls.count("/sessions/refresh") == before + 1
    finally:
        await service.upstream.close()
        await redis.aclose()


async def test_concurrent_stale_context_refresh_does_not_duplicate_upstream_rotation():
    service, redis, calls = await make_service()
    marker = uuid4().hex
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        ref, _ = await service.authenticate(tokens.access_token)
        session, _ = await service._load(ref)
        await redis.set(
            service._family(ref),
            service._encode(
                service._family(ref), replace(session, context=old_but_unexpired_context())
            ),
            ex=300,
        )
        before = calls.count("/sessions/refresh")
        outcomes = await asyncio.gather(
            *(service.authenticate(tokens.access_token) for _ in range(8)), return_exceptions=True
        )
        assert calls.count("/sessions/refresh") == before + 1
        assert all(isinstance(value, tuple) for value in outcomes)
        assert (await service.authenticate(tokens.access_token))[1].subject == "user-1"
    finally:
        await service.upstream.close()
        await redis.aclose()


async def test_busy_refresh_never_returns_stale_identity():
    service, redis, _ = await make_service()
    marker = uuid4().hex
    ref = None
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        ref, _ = await service.authenticate(tokens.access_token)
        session, _ = await service._load(ref)
        await redis.set(
            service._family(ref),
            service._encode(
                service._family(ref), replace(session, context=old_but_unexpired_context())
            ),
            ex=300,
        )
        await redis.set(service._family(ref) + ":lock", "other-request", ex=15)
        service._IDENTITY_LOCK_WAIT_SECONDS = 0.15
        with pytest.raises(BrowserAuthError) as exc:
            await service.authenticate(tokens.access_token)
        assert exc.value.status == 409
    finally:
        if ref:
            await redis.delete(service._family(ref) + ":lock")
        await service.upstream.close()
        await redis.aclose()


async def test_logout_during_upstream_refresh_cannot_restore_the_session():
    service, redis, _ = await make_service()
    marker = uuid4().hex
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()
    cancelled_tokens = []
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        ref, _ = await service.authenticate(tokens.access_token)
        session, _ = await service._load(ref)
        await redis.set(
            service._family(ref),
            service._encode(
                service._family(ref), replace(session, context=old_but_unexpired_context())
            ),
            ex=300,
        )
        await service.upstream.client.aclose()

        async def upstream(request):
            if request.url.path.endswith("/refresh"):
                refresh_started.set()
                await release_refresh.wait()
                return httpx.Response(
                    200,
                    json={
                        "agent_context": signed_context(ttl=300),
                        "refresh_token": "replacement-refresh-token-test-only",
                    },
                )
            cancelled_tokens.append(json.loads(request.content)["refresh_token"])
            return httpx.Response(200, json={"ok": True})

        service.upstream.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        refresh = asyncio.create_task(service.identity(ref))
        await asyncio.wait_for(refresh_started.wait(), timeout=2)
        waiter = asyncio.create_task(service.identity(ref))
        await service.logout(tokens.access_token)
        release_refresh.set()
        assert await refresh is None
        assert await waiter is None
        assert set(cancelled_tokens) == {
            "private-upstream-refresh-token-test-only",
            "replacement-refresh-token-test-only",
        }
        with pytest.raises(BrowserAuthError):
            await service.authenticate(tokens.access_token)
    finally:
        release_refresh.set()
        await service.upstream.close()
        await redis.aclose()


async def test_stale_login_context_cannot_create_a_mobile_session():
    service, redis, _ = await make_service()
    marker = uuid4().hex
    try:
        await service.upstream.client.aclose()

        calls = []

        async def upstream(request):
            calls.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "agent_context": old_but_unexpired_context(),
                    "refresh_token": "test-only-refresh-token-long-enough",
                },
            )

        service.upstream.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        with pytest.raises(BrowserAuthError) as exc:
            await service.login(
                {"username": marker, "password": "test-only", "organization_id": "org-1"},
                marker,
            )
        assert exc.value.status == 503
        assert calls == ["/sessions/login", "/sessions/logout"]
    finally:
        await service.upstream.close()
        await redis.aclose()


@pytest.mark.parametrize("create_result", ["rejected", "unavailable"])
async def test_failed_mobile_session_creation_revokes_upstream(create_result):
    service, redis, calls = await make_service()
    marker = uuid4().hex

    class FailCreate:
        async def eval(self, script, *args):
            if script == service._CREATE:
                if create_result == "unavailable":
                    raise ConnectionError("isolated test")
                return 0
            return await redis.eval(script, *args)

    service.redis = FailCreate()
    try:
        with pytest.raises((BrowserAuthError, ConnectionError)):
            await service.login(
                {"username": marker, "password": "test-only", "organization_id": "org-1"},
                marker,
            )
        assert calls == ["/sessions/login", "/sessions/logout"]
    finally:
        await service.upstream.close()
        await redis.aclose()


@pytest.mark.parametrize("mode", ["stale", "forbidden", "wrong_org", "wrong_user"])
async def test_revoked_stale_or_wrong_scope_never_restores_session(mode):
    service, redis, _ = await make_service()
    marker = uuid4().hex
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        ref, _ = await service.authenticate(tokens.access_token)
        session, _ = await service._load(ref)
        await redis.set(
            service._family(ref),
            service._encode(
                service._family(ref), replace(session, context=old_but_unexpired_context())
            ),
            ex=300,
        )
        await service.upstream.client.aclose()
        upstream_paths = []

        async def upstream(request):
            upstream_paths.append(request.url.path)
            if request.url.path.endswith("/logout"):
                return httpx.Response(200, json={"ok": True})
            if mode == "forbidden":
                return httpx.Response(401, json={})
            context = (
                old_but_unexpired_context()
                if mode == "stale"
                else signed_context(
                    subject="other-user" if mode == "wrong_user" else "user-1",
                    orgs=["other-org"] if mode == "wrong_org" else ["org-1"],
                    ttl=300,
                )
            )
            return httpx.Response(
                200,
                json={
                    "agent_context": context,
                    "refresh_token": "test-only-refresh-token-long-enough",
                },
            )

        service.upstream.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        with pytest.raises(BrowserAuthError):
            await service.authenticate(tokens.access_token)
        assert await service.identity(ref) is None
        assert upstream_paths == (
            ["/sessions/refresh"]
            if mode == "forbidden"
            else ["/sessions/refresh", "/sessions/logout"]
        )
    finally:
        await service.upstream.close()
        await redis.aclose()


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


async def test_signed_context_is_server_only_scope_checked_and_revoked_on_logout() -> None:
    from dataclasses import replace

    service, redis, _ = await make_service()
    marker = uuid4().hex
    try:
        tokens = await service.login(
            {"username": marker, "password": "test-only", "organization_id": "org-1"}, marker
        )
        ref, identity = await service.authenticate(tokens.access_token)
        signed = await service.signed_context(ref, identity)
        assert service.verifier.verify(signed).subject == identity.subject
        with pytest.raises(BrowserAuthError):
            await service.signed_context(ref, replace(identity, roles=frozenset({"COACH"})))
        with pytest.raises(BrowserAuthError):
            await service.signed_context(ref, replace(identity, subject="other"))
        await service.logout(tokens.access_token)
        with pytest.raises(BrowserAuthError):
            await service.signed_context(ref, identity)
    finally:
        await service.upstream.close()
        await redis.aclose()
