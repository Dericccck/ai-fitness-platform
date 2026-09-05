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
from app.infrastructure.cache import SessionLockLost, SessionLockManager, SessionLockUnavailable
from app.infrastructure.database import FencedCheckpointSaver, StaleCheckpointWriter


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
        capabilities=frozenset(),
        qualifications=frozenset(),
    )


def test_agent_context_reads_signed_review_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.infrastructure.agent_context.time.time", lambda: 1_800_000_000)
    identity = AgentContextVerifier("context-secret").verify(
        signed_context(
            "context-secret",
            capabilities=["KNOWLEDGE_REVIEW_FITNESS"],
            qualifications=["VERIFIED_HEALTH_PROFESSIONAL"],
        )
    )

    assert identity.capabilities == frozenset({"KNOWLEDGE_REVIEW_FITNESS"})
    assert identity.qualifications == frozenset({"VERIFIED_HEALTH_PROFESSIONAL"})


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
        self.counters: dict[str, int] = {}
        self.eval_calls: list[tuple[Any, ...]] = []

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, count: int, *args: str) -> int:
        self.eval_calls.append((script, count, *args))
        if count == 2:
            key, counter_key, owner, _ttl = args
            if key in self.values:
                return 0
            token = self.counters.get(counter_key, 0) + 1
            self.counters[counter_key] = token
            self.values[key] = f"{owner}:{token}"
            return token
        key, owner_value = args[:2]
        if self.values.get(key) == owner_value:
            if len(args) == 3:
                return 1
            del self.values[key]
            return 1
        return 0


async def test_session_lock_rejects_concurrent_owner_and_releases_safely() -> None:
    redis = FakeRedis()
    manager = SessionLockManager(redis, ttl_seconds=60)

    async with manager.hold("thread-1") as first_lease:
        assert first_lease.fencing_token == 1
        with pytest.raises(SessionLockUnavailable):
            async with manager.hold("thread-1"):
                pass
        assert "fitness:agent:session-lock:thread-1" in redis.values

    assert redis.values == {}
    assert redis.eval_calls

    async with manager.hold("thread-1") as second_lease:
        assert second_lease.fencing_token > first_lease.fencing_token


@pytest.mark.asyncio
async def test_session_lock_lease_rejects_writes_after_ownership_loss() -> None:
    redis = FakeRedis()
    manager = SessionLockManager(redis, ttl_seconds=60)

    async with manager.hold("thread-1") as lease:
        lease.mark_lost()
        with pytest.raises(SessionLockLost):
            lease.ensure_owned()


def test_checkpoint_store_uses_psycopg_connection_scheme() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@db:5432/fitness_agent",
    )

    assert settings.checkpoint_conninfo == "postgresql://user:password@db:5432/fitness_agent"


class _AsyncContext:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_: Any) -> None:
        return None


class _FenceCursor:
    def __init__(self, pool: "_FencePool") -> None:
        self.pool = pool

    async def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        if "INSERT INTO agent_session_fences" in sql:
            token = int(params[1])
            if self.pool.token is None or token > self.pool.token:
                self.pool.token = token

    async def fetchone(self) -> dict[str, int] | None:
        return None if self.pool.token is None else {"fencing_token": self.pool.token}


class _FenceConnection:
    def __init__(self, pool: "_FencePool") -> None:
        self.pool = pool

    def transaction(self) -> _AsyncContext:
        return _AsyncContext()

    def cursor(self) -> _AsyncContext:
        return _AsyncContext(_FenceCursor(self.pool))


class _FencePool:
    def __init__(self) -> None:
        self.token: int | None = None

    def connection(self) -> _AsyncContext:
        return _AsyncContext(_FenceConnection(self))


class _CheckpointDelegate:
    def __init__(self) -> None:
        self.write_count = 0

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        self.write_count += 1
        return config


@pytest.mark.asyncio
async def test_checkpoint_fencing_rejects_stale_writer_after_new_generation() -> None:
    pool = _FencePool()
    delegate = _CheckpointDelegate()
    saver = FencedCheckpointSaver(delegate, pool)
    old_config = {"configurable": {"thread_id": "thread-1", "fencing_token": 1}}

    await saver.activate_fence("thread-1", 1)
    await saver.aput(old_config, {}, {}, {})
    await saver.activate_fence("thread-1", 2)

    with pytest.raises(StaleCheckpointWriter):
        await saver.aput(old_config, {}, {}, {})
    assert delegate.write_count == 1
