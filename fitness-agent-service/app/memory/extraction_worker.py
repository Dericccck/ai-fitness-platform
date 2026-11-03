"""Lease-based extraction; source facts and proposals are committed separately from LLM calls."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database

from .candidate import MemoryCandidateSnapshotEntry
from .candidate_service import MemoryCandidateService
from .reconciliation import reconcile_candidate

_logger = structlog.get_logger("memory.extraction_worker")


class MemoryExtractionWorker:
    """Claims one bounded job per iteration; a stale lease can never publish proposals."""

    def __init__(
        self,
        database: Database,
        cipher: AesGcmPayloadCipher,
        service: MemoryCandidateService,
        *,
        lease_seconds: int = 180,
        max_attempts: int = 5,
    ) -> None:
        if lease_seconds < 10 or max_attempts < 1:
            raise ValueError("提取任务租约或重试次数无效")
        self.database, self.cipher, self.service = database, cipher, service
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts

    async def _claim(self) -> Any | None:
        token = secrets.token_hex(16)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("""
                UPDATE agent_memory_extraction_jobs
                SET status='FAILED', lease_token=NULL, lease_until=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE attempts >= :maximum AND (
                    (status='RUNNING' AND lease_until <= CURRENT_TIMESTAMP)
                    OR status='PENDING')
            """),
                {"maximum": self.max_attempts},
            )
            return (
                (
                    await connection.execute(
                        text("""
                WITH claim AS (
                    SELECT id FROM agent_memory_extraction_jobs
                    WHERE attempts < :maximum AND ((status='PENDING' AND available_at <= CURRENT_TIMESTAMP)
                       OR (status='RUNNING' AND lease_until <= CURRENT_TIMESTAMP))
                    ORDER BY available_at, created_at, id
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE agent_memory_extraction_jobs job
                SET status='RUNNING', lease_token=:token,
                    lease_until=CURRENT_TIMESTAMP + (:lease * INTERVAL '1 second'),
                    attempts=job.attempts+1, updated_at=CURRENT_TIMESTAMP
                FROM claim WHERE job.id=claim.id RETURNING job.*
            """),
                        {"token": token, "lease": self.lease_seconds, "maximum": self.max_attempts},
                    )
                )
                .mappings()
                .first()
            )

    async def run_once(self) -> int:
        job = await self._claim()
        if job is None:
            return 0
        try:
            await self._process(job)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - retry boundary; never log untrusted content
            # Never put user text or provider exceptions into logs or job columns.
            async with self.database.engine.begin() as connection:
                await connection.execute(
                    text("""
                    UPDATE agent_memory_extraction_jobs
                    SET status=CASE WHEN attempts >= :maximum THEN 'FAILED' ELSE 'PENDING' END,
                        available_at=CURRENT_TIMESTAMP + (:delay * INTERVAL '1 second'),
                        lease_token=NULL, lease_until=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND status='RUNNING' AND lease_token=:token
                """),
                    {
                        "maximum": self.max_attempts,
                        "delay": min(300, 2 ** min(int(job["attempts"]), 8)) + secrets.randbelow(5),
                        "id": job["id"],
                        "token": job["lease_token"],
                    },
                )
            _logger.warning(
                "memory_extraction_failed",
                attempt=int(job["attempts"]),
                terminal=int(job["attempts"]) >= self.max_attempts,
            )
        return 1

    async def _process(self, job: Any) -> None:
        async with self.database.engine.connect() as connection:
            source = (
                (
                    await connection.execute(
                        text("""
                SELECT * FROM agent_memory_source_messages
                WHERE id=:id AND subject_user_id=:subject AND organization_id=:org
                  AND status='COMPLETED' AND retain_until > CURRENT_TIMESTAMP
            """),
                        {
                            "id": job["source_message_id"],
                            "subject": job["subject_user_id"],
                            "org": job["organization_id"],
                        },
                    )
                )
                .mappings()
                .first()
            )
            profile = (
                (
                    await connection.execute(
                        text("""
                SELECT write_epoch, revision, extraction_enabled FROM agent_memory_profiles
                WHERE subject_user_id=:subject AND organization_id=:org
            """),
                        {"subject": job["subject_user_id"], "org": job["organization_id"]},
                    )
                )
                .mappings()
                .first()
            )
        if source is None:
            raise RuntimeError("提取来源不可用")
        if (
            profile is None
            or not bool(profile["extraction_enabled"])
            or int(profile["write_epoch"]) != int(job["write_epoch"])
            or int(source["write_epoch"]) != int(job["write_epoch"])
        ):
            async with self.database.engine.begin() as connection:
                await connection.execute(
                    text("""
                    UPDATE agent_memory_extraction_jobs SET status='DONE', lease_token=NULL,
                        lease_until=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND status='RUNNING' AND lease_token=:token
                """),
                    {"id": job["id"], "token": job["lease_token"]},
                )
            return
        base_revision = int(profile["revision"])
        aad = (
            f"memory-source:{source['id']}:{source['subject_user_id']}:{source['organization_id']}"
        )
        message = self.cipher.decrypt(
            bytes(source["message_ciphertext"]),
            associated_data=aad,
            key_version=source["key_version"],
        ).decode("utf-8")
        # Snapshot derives exclusively from an authenticated persisted job, never model output.
        identity = AgentIdentity(
            subject=str(job["subject_user_id"]),
            organization_ids=frozenset({str(job["organization_id"])}),
            roles=frozenset(),
            issued_at=0,
            expires_at=0,
        )
        candidates = await self.service.extractor.propose_controlled(message)
        old = await self.service.memory_service.list_active(
            identity=identity, organization_id=str(job["organization_id"])
        )
        proposals = []
        for item in candidates:
            related = [
                memory
                for memory in old
                if memory.memory_type == item.memory_type
                and (memory.slot_code or memory.memory_key) == item.slot_code
            ]
            # A delayed old source cannot propose overwriting a newer user decision.
            source_created = source["created_at"]
            later_same_slot = any(
                memory.memory_type == item.memory_type
                and (memory.slot_code or memory.memory_key) == item.slot_code
                and memory.updated_at > source_created
                for memory in related
            )
            if later_same_slot:
                candidate = item.model_copy(
                    update={
                        "action": "NEEDS_CLARIFICATION",
                        "clarification_reason": "这条消息产生后记忆已有变化，请重新说明",
                    }
                )
            else:
                candidate = await reconcile_candidate(item, old, self.service.extractor.models)
            if candidate.action != "NOOP":
                proposals.append(
                    candidate.model_copy(
                        update={
                            "source_message_id": str(source["id"]),
                            "write_epoch": int(job["write_epoch"]),
                            "base_revision": base_revision,
                            "conflict_snapshot": [
                                MemoryCandidateSnapshotEntry(
                                    memory_id=memory.id, version=memory.version
                                )
                                for memory in related
                            ]
                            if len(related) <= 50
                            else None,
                        }
                    )
                )
        async with self.database.engine.begin() as connection:
            profile = await self.service.memory_service.repository.lock_profile_on_connection(
                connection,
                subject_user_id=identity.subject,
                organization_id=str(job["organization_id"]),
            )
            locked = (
                (
                    await connection.execute(
                        text("""
                SELECT * FROM agent_memory_extraction_jobs WHERE id=:id AND status='RUNNING'
                    AND lease_token=:token AND lease_until > CURRENT_TIMESTAMP FOR UPDATE
            """),
                        {"id": job["id"], "token": job["lease_token"]},
                    )
                )
                .mappings()
                .first()
            )
            if locked is None:
                return
            if (
                bool(profile["extraction_enabled"])
                and int(profile["write_epoch"]) == int(job["write_epoch"])
                and int(profile["revision"]) != base_revision
            ):
                # Retry comparison on a consistent snapshot; no partial proposals commit.
                raise RuntimeError("Memory 比较期间已变化")
            source_live = (
                await connection.execute(
                    text("""
                SELECT id FROM agent_memory_source_messages
                WHERE id=:id AND subject_user_id=:subject AND organization_id=:org
                  AND status='COMPLETED' AND retain_until > CURRENT_TIMESTAMP FOR UPDATE
            """),
                    {
                        "id": job["source_message_id"],
                        "subject": identity.subject,
                        "org": job["organization_id"],
                    },
                )
            ).first()
            # Drop stale work instead of allowing pre-revocation inputs to resurrect memory.
            if (
                source_live is not None
                and bool(profile["extraction_enabled"])
                and int(profile["write_epoch"]) == int(job["write_epoch"])
            ):
                for candidate in proposals:
                    await self.service.repository.create_pending(
                        connection=connection,
                        identity=identity,
                        organization_id=str(job["organization_id"]),
                        candidate=candidate,
                        source_thread_id=str(job["thread_id"]),
                        source_request_id=str(job["source_request_id"]),
                        expires_at=datetime.now(UTC) + self.service.ttl,
                    )
            await connection.execute(
                text("""
                UPDATE agent_memory_extraction_jobs SET status='DONE', lease_token=NULL,
                    lease_until=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=:id
            """),
                {"id": job["id"]},
            )
