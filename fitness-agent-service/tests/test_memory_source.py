"""来源请求的防串租户、版本屏障和清理调度契约（模拟连接，非 PG 并发证明）。"""

import pytest

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.memory.source import MemorySource, MemorySourceRepository


def identity(*orgs: str) -> AgentIdentity:
    return AgentIdentity(
        subject="u", organization_ids=frozenset(orgs), roles=frozenset(), issued_at=1, expires_at=2
    )


class Result:
    def __init__(self, value=None):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return self.value or []


class Connection:
    def __init__(self, profile, source_found=True):
        self.profile = profile
        self.source_found = source_found
        self.statements = []

    async def execute(self, sql, params):
        query = str(sql)
        self.statements.append((query, dict(params)))
        if "SELECT write_epoch, extraction_enabled" in query:
            return Result(self.profile)
        if "UPDATE agent_memory_source_messages" in query:
            return Result(("id",) if self.source_found else None)
        return Result()


@pytest.mark.asyncio
async def test_multiorg_capture_does_not_guess_org_or_access_database():
    repository = MemorySourceRepository(None, AesGcmPayloadCipher(b"1" * 32, "v1"))
    assert (
        await repository.capture(
            identity=identity("org1", "org2"), thread_id="t", source_request_id="r", message="偏好"
        )
        is None
    )
    assert (
        await repository.capture(
            identity=identity(), thread_id="t", source_request_id="r", message="偏好"
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        None,
        {"write_epoch": 1, "extraction_enabled": False},
        {"write_epoch": 2, "extraction_enabled": True},
    ],
)
async def test_no_job_when_profile_missing_disabled_or_epoch_changed(profile):
    repository = MemorySourceRepository(None, AesGcmPayloadCipher(b"1" * 32, "v1"))
    connection = Connection(profile)
    source = MemorySource("id", "u", "org", "t", "r", 1)
    assert await repository.complete_on_connection(connection, source) is False
    assert all(
        "INSERT INTO agent_memory_extraction_jobs" not in sql for sql, _ in connection.statements
    )


@pytest.mark.asyncio
async def test_completed_source_requires_matching_authenticated_scope_and_valid_retention():
    repository = MemorySourceRepository(None, AesGcmPayloadCipher(b"1" * 32, "v1"))
    connection = Connection({"write_epoch": 1, "extraction_enabled": True}, source_found=False)
    source = MemorySource("id", "u", "org", "t", "r", 1)
    assert await repository.complete_on_connection(connection, source) is False
    update, params = connection.statements[1]
    for field in (
        "subject_user_id=:subject",
        "organization_id=:organization",
        "thread_id=:thread",
        "source_request_id=:request",
        "write_epoch=:epoch",
        "retain_until > CURRENT_TIMESTAMP",
    ):
        assert field in update
    assert params["subject"] == "u" and params["organization"] == "org"
    assert len(connection.statements) == 2


def test_retention_limit_bounds():
    repository = MemorySourceRepository(None, AesGcmPayloadCipher(b"1" * 32, "v1"))
    assert repository.retention_days == 30
    with pytest.raises(ValueError):
        MemorySourceRepository(None, AesGcmPayloadCipher(b"1" * 32, "v1"), retention_days=0)
