from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request

from app.api.routes.auth import BrowserSessionMiddleware, auth_error, router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.browser_auth import BrowserAuthError, BrowserAuthService

SECRET = "test-only-context-key-not-a-production-secret"


def signed_context(*, subject: str = "user-1", orgs: list[str] | None = None, ttl: int = 45) -> str:
    now = int(time.time())
    raw = json.dumps(
        {
            "sub": subject,
            "orgs": orgs or ["org-1"],
            "roles": ["STUDENT"],
            "iat": now,
            "exp": now + ttl,
            "nonce": secrets.token_hex(16),
        }
    ).encode()
    enc = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return enc(raw) + "." + enc(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest())


class MemoryRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False) -> bool:
        if nx and key in self.data:
            return False
        self.data[key], self.ttls[key] = value, ex
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self.data:
            return False
        self.ttls[key] = seconds
        return True

    async def delete(self, key: str) -> int:
        return int(self.data.pop(key, None) is not None)

    async def eval(self, script: str, count: int, key: str, *args: str) -> int:
        if "incr" in script:
            value = int(self.data.get(key, "0")) + 1
            self.data[key] = str(value)
            return value
        if self.data.get(key) != args[0]:
            return 0
        if "redis.call('set'" in script:
            return int(await self.set(key, args[1], ex=int(args[2])))
        return await self.delete(key)


def build() -> tuple[FastAPI, BrowserAuthService, list[str]]:
    settings = Settings(
        _env_file=None,
        browser_auth_enabled=True,
        browser_auth_upstream_url="https://issuer.test/sessions",
        browser_auth_upstream_service_token="test-upstream-service-token",
        browser_auth_allowed_origins=["https://fitness.test"],
        browser_auth_cookie_secure=True,
    )
    calls: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-internal-service-token"] == "test-upstream-service-token"
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "agent_context": signed_context(
                    ttl=45 if request.url.path.endswith("login") else 300
                ),
                "refresh_token": "upstream-only-refresh-token-not-for-the-browser",
            },
        )

    auth = BrowserAuthService(
        settings,
        MemoryRedis(),
        AesGcmPayloadCipher(b"1" * 32, "key-v1"),
        AgentContextVerifier(SECRET),
        client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    app = FastAPI()
    app.state.settings, app.state.browser_auth = settings, auth
    app.add_exception_handler(BrowserAuthError, auth_error)
    app.add_exception_handler(TimeoutError, auth_error)
    app.add_middleware(BrowserSessionMiddleware)
    app.include_router(router)

    @app.api_route("/api/protected", methods=["GET", "POST"])
    async def protected(request: Request) -> dict[str, Any]:
        identity = auth.verifier.verify(request.headers["x-agent-context"])
        return {"subject": identity.subject, "orgs": sorted(identity.organization_ids)}

    return app, auth, calls


async def logged_in(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://fitness.test"},
        json={"username": "test-user", "password": "test-password", "organization_id": "org-1"},
    )


async def test_cookie_login_signed_scope_encrypted_storage_and_logout() -> None:
    app, auth, calls = build()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://fitness.test"
    ) as client:
        response = await logged_in(client)
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert (
            "HttpOnly" in cookie
            and "Secure" in cookie
            and "SameSite=strict" in cookie
            and "Path=/" in cookie
        )
        assert (
            "agent_context" not in response.text
            and "refresh_token" not in response.text
            and "test-password" not in response.text
        )
        assert "upstream-only-refresh" not in repr(auth.redis.data)
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get("/api/protected")).json()["subject"] == "user-1"
        headers = {"Origin": "https://fitness.test", "X-CSRF-Token": response.json()["csrf_token"]}
        assert (await client.post("/api/protected", headers=headers)).status_code == 200
        sid = client.cookies.get(auth.cookie_name)
        assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 200
        assert await auth.redis.get(auth.key(sid)) is None
    assert calls == ["/sessions/login", "/sessions/logout"]
    await auth.close()


async def test_csrf_origin_identity_mix_and_password_validation_no_echo() -> None:
    app, auth, calls = build()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://fitness.test"
    ) as client:
        assert (
            await client.post("/api/v1/auth/login", json={"password": "DO_NOT_ECHO"})
        ).status_code == 403
        invalid = await client.post(
            "/api/v1/auth/login",
            headers={"Origin": "https://fitness.test"},
            json={"password": "DO_NOT_ECHO"},
        )
        assert invalid.status_code == 400 and "DO_NOT_ECHO" not in invalid.text
        response = await logged_in(client)
        assert (
            await client.post("/api/protected", headers={"Origin": "https://fitness.test"})
        ).status_code == 403
        assert (
            await client.post(
                "/api/protected",
                headers={
                    "Origin": "https://evil.test",
                    "X-CSRF-Token": response.json()["csrf_token"],
                },
            )
        ).status_code == 403
        assert (
            await client.get("/api/protected", headers={"X-Agent-Context": signed_context()})
        ).status_code == 400
    assert calls == ["/sessions/login"]
    await auth.close()


async def test_refresh_rotates_credentials_and_does_not_repeat_fresh_refresh() -> None:
    app, auth, calls = build()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://fitness.test"
    ) as client:
        response = await logged_in(client)
        headers = {"Origin": "https://fitness.test", "X-CSRF-Token": response.json()["csrf_token"]}
        for _ in range(2):
            refreshed = await client.post("/api/v1/auth/refresh", headers=headers)
            assert refreshed.status_code == 200
            assert refreshed.json()["expires_at"] >= int(time.time()) + 290
    assert calls.count("/sessions/refresh") == 1
    await auth.close()


async def test_refresh_lock_rejects_parallel_rotation() -> None:
    _, auth, _ = build()
    sid, session = await auth.login(
        {"username": "u", "password": "p", "organization_id": "org-1"}, "peer"
    )
    await auth.redis.set(auth.key(sid) + ":refresh", "other-owner", nx=True, ex=15)
    with pytest.raises(BrowserAuthError) as error:
        await auth.refresh(sid, session.csrf_token)
    assert error.value.status == 409
    await auth.close()


@pytest.mark.parametrize("problem", ["signature", "organization", "multi_org", "upstream_error"])
async def test_untrusted_login_never_creates_session(problem: str) -> None:
    _, auth, _ = build()

    def upstream(request: httpx.Request) -> httpx.Response:
        if problem == "upstream_error":
            return httpx.Response(500, text="provider-secret-DO_NOT_ECHO")
        token = signed_context(
            orgs=["org-2"]
            if problem == "organization"
            else ["org-1", "org-2"]
            if problem == "multi_org"
            else None
        )
        return httpx.Response(
            200,
            json={
                "agent_context": token + "tamper" if problem == "signature" else token,
                "refresh_token": "valid-length-test-refresh-token",
            },
        )

    await auth.client.aclose()
    auth.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    with pytest.raises(BrowserAuthError) as error:
        await auth.login({"username": "u", "password": "p", "organization_id": "org-1"}, "peer")
    assert "provider-secret" not in str(error.value)
    assert not any(key.startswith("fitness:browser-session:") for key in auth.redis.data)
    await auth.close()


async def test_logout_during_refresh_cannot_resurrect_session() -> None:
    _, auth, _ = build()
    sid, session = await auth.login(
        {"username": "u", "password": "p", "organization_id": "org-1"}, "peer"
    )

    async def upstream(request: httpx.Request) -> httpx.Response:
        await auth.redis.delete(auth.key(sid))
        return httpx.Response(
            200,
            json={
                "agent_context": signed_context(ttl=300),
                "refresh_token": "rotated-valid-refresh-token",
            },
        )

    await auth.client.aclose()
    auth.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    with pytest.raises(BrowserAuthError) as error:
        await auth.refresh(sid, session.csrf_token)
    assert error.value.status == 401 and await auth.redis.get(auth.key(sid)) is None
    await auth.close()


async def test_encrypted_browser_session_survives_rotation_and_absolute_expiry_is_enforced() -> (
    None
):
    _, auth, _ = build()
    sid, session = await auth.login(
        {"username": "u", "password": "p", "organization_id": "org-1"}, "peer"
    )
    auth.cipher = AesGcmPayloadCipher(b"2" * 32, "key-v2", {"key-v1": b"1" * 32})
    assert (await auth.load(sid))[0].subject == "user-1"
    key = auth.key(sid)
    await auth.redis.set(
        key, auth._encode(key, replace(session, absolute_expiry=int(time.time()) - 1)), ex=300
    )
    with pytest.raises(BrowserAuthError):
        await auth.load(sid)
    assert await auth.redis.get(key) is None
    await auth.close()


async def test_login_rate_limit_is_fail_closed() -> None:
    _, auth, _ = build()
    for _ in range(10):
        await auth.login({"username": "u", "password": "p", "organization_id": "org-1"}, "peer")
    with pytest.raises(BrowserAuthError) as error:
        await auth.login({"username": "u", "password": "p", "organization_id": "org-1"}, "peer")
    assert error.value.status == 429
    await auth.close()
