import base64
import hashlib
import hmac
import json
from typing import Any

import pytest

from app.core.config import Settings
from app.infrastructure.agent_context import (
    AgentContextVerificationError,
    AgentContextVerifier,
    AgentIdentity,
    conversation_thread_id,
)
from app.infrastructure.cache import SessionLockManager, SessionLockUnavailable


def signed_context(secret: str, **overrides: Any) -> str:
    now = 1_800_000_000
    claims: dict[str, Any] = {
        "sub": "user-1",
        "orgs": ["org-1"],
        "roles": ["STUDENT"],
        "iat": now - 10,
        "exp": now + 120,
        "nonce": "nonce-1",
    }
    claims.update(overrides)
    payload = json.dumps(claims, separators=(",", ":")).encode()
    encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{encoded_payload}.{encoded_signature}"


def test_agent_context_verifier_matches_java_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.infrastructure.agent_context.time.time", lambda: 1_800_000_000)
    verifier = AgentContextVerifier("context-secret")

    identity = verifier.verify(signed_context("context-secret"))

    assert identity == AgentIdentity(
        subject="user-1",
        organization_ids=frozenset({"org-1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1_799_999_990,
        expires_at=1_800_000_120,
    )


def test_agent_context_verifier_rejects_tampering_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.infrastructure.agent_context.time.time", lambda: 1_800_000_000)
    verifier = AgentContextVerifier("context-secret")

    with pytest.raises(AgentContextVerificationError):
        verifier.verify(signed_context("wrong-secret"))
    with pytest.raises(AgentContextVerificationError):
        verifier.verify(signed_context("context-secret", exp=1_799_999_999))


def test_thread_id_is_stable_but_isolated_by_identity_scope() -> None:
    base = AgentIdentity("user-1", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 2)
    another_user = AgentIdentity("user-2", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 2)
    coach = AgentIdentity("user-1", frozenset({"org-1"}), frozenset({"COACH"}), 1, 2)

    assert conversation_thread_id("conversation-1", base) == conversation_thread_id(
        "conversation-1", base
    )
    assert conversation_thread_id("conversation-1", base) != conversation_thread_id(
        "conversation-1", another_user
    )
    assert conversation_thread_id("conversation-1", base) != conversation_thread_id(
        "conversation-1", coach
    )
    assert "user-1" not in conversation_thread_id("conversation-1", base)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.eval_calls: list[tuple[Any, ...]] = []

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, count: int, key: str, owner: str) -> int:
        self.eval_calls.append((script, count, key, owner))
        if self.values.get(key) == owner:
            del self.values[key]
            return 1
        return 0


async def test_session_lock_rejects_concurrent_owner_and_releases_safely() -> None:
    redis = FakeRedis()
    manager = SessionLockManager(redis, ttl_seconds=60)

    async with manager.hold("thread-1"):
        with pytest.raises(SessionLockUnavailable):
            async with manager.hold("thread-1"):
                pass
        assert "fitness:agent:session-lock:thread-1" in redis.values

    assert redis.values == {}
    assert redis.eval_calls


def test_checkpoint_store_uses_psycopg_connection_scheme() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/fitness_agent",
    )

    assert settings.checkpoint_conninfo == "postgresql://user:password@db:5432/fitness_agent"
