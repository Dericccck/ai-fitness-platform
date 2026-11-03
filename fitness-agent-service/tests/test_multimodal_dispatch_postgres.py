"""仅允许对隔离临时数据库运行真实并发与租约验收。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.agent.movement_models import MovementReport
from app.confirmation.cipher import AesGcmPayloadCipher
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.infrastructure.multimodal_gateway import MultimodalOutcomeUnknown
from app.media.deletion import MediaDeletionRepository
from app.media.deletion_worker import MediaDeletionWorker
from app.media.repository import MediaAccessError, MediaRepository
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import DurableAccessError, DurableMultimodalRepository
from app.streaming.multimodal_worker import MultimodalWorker
from app.streaming.repository import ChatRunRepository, RunConflict

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MULTIMODAL_PG_TESTS") != "1", reason="仅允许隔离临时库验收"
)


def database() -> Database:
    settings = Settings(_env_file=None)
    assert (make_url(settings.database_url).database or "") == "fitness_multimodal_test"
    return Database(settings)


@pytest.fixture(autouse=True)
async def isolate_test_data() -> None:
    """该测试数据库仅供本套测试使用；每例清除旧租约避免重复运行互相干扰。"""
    db = database()
    try:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE agent_chat_conversations,agent_chat_runs CASCADE")
            )
    finally:
        await db.close()


async def ready_asset(db: Database, identity: AgentIdentity, conversation: str) -> str:
    asset = str(uuid4())
    async with db.engine.begin() as connection:
        await connection.execute(
            text("""INSERT INTO agent_media_assets
          (asset_id,owner_hash,subject_user_id,organization_scope,conversation_id,kind,content_type,
           size_bytes,final_key,final_version,sha256,status,expires_at)
          VALUES (:asset,:owner,:subject,'org',:conversation,'video','video/mp4',10,
           'final/x/y','version-1',:sha,'READY',CURRENT_TIMESTAMP+INTERVAL '1 day')"""),
            {
                "asset": asset,
                "owner": run_owner(identity),
                "subject": identity.subject,
                "conversation": conversation,
                "sha": "a" * 64,
            },
        )
    return asset


async def test_idempotency_encrypted_payload_lease_fencing_and_unknown_recovery() -> None:
    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "test-v1")
    repo = DurableMultimodalRepository(db, cipher)
    identity = AgentIdentity("user-1", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999)
    conversation, run, client = str(uuid4()), str(uuid4()), str(uuid4())
    try:
        await repo.create_conversation(identity, conversation)
        asset = await ready_asset(db, identity, conversation)
        kwargs = {
            "identity": identity,
            "conversation_id": conversation,
            "run_id": run,
            "client_message_id": client,
            "question": "敏感训练问题",
            "asset_ids": [asset],
            "auth_session_ref": "a" * 64,
            "model": "model-v1",
            "prompt_version": "v1",
        }
        results = await asyncio.gather(*(repo.submit(**kwargs) for _ in range(8)))
        assert sum(item.created for item in results) == 1
        assert {item.run_id for item in results} == {run}
        with pytest.raises(RunConflict):
            await repo.submit(**{**kwargs, "question": "不同的问题"})
        with pytest.raises(DurableAccessError):
            await repo.submit(**{**kwargs, "identity": replace(identity, subject="other")})
        async with db.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""SELECT payload_ciphertext,auth_session_ref
                FROM agent_chat_run_dispatch WHERE run_id=:id"""),
                        {"id": run},
                    )
                )
                .mappings()
                .one()
            )
            assert "敏感训练问题".encode() not in bytes(row["payload_ciphertext"])
            assert row["auth_session_ref"] == "a" * 64
        claims = await asyncio.gather(*(repo.claim() for _ in range(5)))
        leases = [item for item in claims if item is not None]
        assert len(leases) == 1
        first = leases[0]
        assert first.payload["question"] == "敏感训练问题" and first.attempt == 1
        assert await repo.heartbeat(first)
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_run_dispatch SET lease_until=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE run_id=:id"
                ),
                {"id": run},
            )
        assert await repo.reap() == 1
        assert not await repo.heartbeat(first)
        second = await repo.claim()
        assert second and second.attempt == 2 and second.lease_token != first.lease_token
        assert await repo.mark_provider_started(second)
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_run_dispatch SET lease_until=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE run_id=:id"
                ),
                {"id": run},
            )
        assert await repo.reap() == 1
        assert await repo.claim() is None
        current = await ChatRunRepository(db, cipher, timeout=60, retention_hours=1).get(
            run, run_owner(identity)
        )
        assert (
            current
            and current.status == "UNKNOWN"
            and current.error_code == "PROVIDER_OUTCOME_UNKNOWN"
        )
    finally:
        await db.close()


async def test_queued_cancel_prevents_provider_dispatch() -> None:
    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "test-v1")
    repo = DurableMultimodalRepository(db, cipher)
    identity = AgentIdentity("user-2", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999)
    conversation, run = str(uuid4()), str(uuid4())
    try:
        await repo.create_conversation(identity, conversation)
        asset = await ready_asset(db, identity, conversation)
        await repo.submit(
            identity=identity,
            conversation_id=conversation,
            run_id=run,
            client_message_id=str(uuid4()),
            question="分析",
            asset_ids=[asset],
            auth_session_ref="a" * 64,
            model="model-v1",
            prompt_version="v1",
        )
        runs = ChatRunRepository(db, cipher, timeout=60, retention_hours=1)
        cancelled = await runs.request_cancel(run, run_owner(identity), "cancel-id")
        assert cancelled and cancelled.status == "CANCELLED"
        assert await repo.claim() is None
    finally:
        await db.close()


async def test_publish_is_atomic_and_fenced_by_media_epoch_and_lease() -> None:
    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "test-v1")
    repo = DurableMultimodalRepository(db, cipher)
    identity = AgentIdentity(
        "user-publish", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation, run = str(uuid4()), str(uuid4())
    try:
        await repo.create_conversation(identity, conversation)
        asset = await ready_asset(db, identity, conversation)
        await repo.submit(
            identity=identity,
            conversation_id=conversation,
            run_id=run,
            client_message_id=str(uuid4()),
            question="分析",
            asset_ids=[asset],
            auth_session_ref="a" * 64,
            model="model-v1",
            prompt_version="v1",
        )
        lease = await repo.claim()
        assert lease and lease.run_id == run
        report = MovementReport.model_validate(
            {
                "exercise": {"name": "坐姿划船", "identification": "IDENTIFIED"},
                "assessment": "INSUFFICIENT_EVIDENCE",
                "summary": "画面不足",
                "source_assets": [{"asset_id": asset, "revision": 1}],
                "model_version": "model-v1",
                "prompt_version": "v1",
                "knowledge_status": "EMPTY",
            }
        )
        assert not await repo.publish(lease, report)
        assert await repo.mark_provider_started(lease)
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_conversations SET content_epoch=1 WHERE conversation_id=:id"
                ),
                {"id": conversation},
            )
        assert not await repo.publish(lease, report)
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_conversations SET content_epoch=0 WHERE conversation_id=:id"
                ),
                {"id": conversation},
            )
            await connection.execute(
                text("UPDATE agent_media_assets SET final_version='other' WHERE asset_id=:id"),
                {"id": asset},
            )
        assert not await repo.publish(lease, report)
        async with db.engine.begin() as connection:
            await connection.execute(
                text("UPDATE agent_media_assets SET final_version='version-1' WHERE asset_id=:id"),
                {"id": asset},
            )
        assert not await repo.publish(replace(lease, lease_token="obsolete"), report)
        assert await repo.publish(lease, report)
        assert not await repo.publish(lease, report)
        current = await ChatRunRepository(db, cipher, timeout=60, retention_hours=1).get(
            run, run_owner(identity)
        )
        assert (
            current
            and current.status == "COMPLETED"
            and current.result["report"]["summary"] == "画面不足"
        )
        async with db.engine.connect() as connection:
            encrypted = (
                await connection.execute(
                    text("SELECT report_ciphertext FROM agent_movement_reports WHERE run_id=:id"),
                    {"id": run},
                )
            ).scalar_one()
            assert "画面不足".encode() not in bytes(encrypted)
    finally:
        await db.close()


async def test_worker_executes_only_with_live_authorization() -> None:
    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "test-v1")
    repo = DurableMultimodalRepository(db, cipher)
    identity = AgentIdentity(
        "user-worker", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation = str(uuid4())
    captured = []

    class Storage:
        def signed_read_url(self, stored: object, *, ttl_seconds: int) -> str:
            assert ttl_seconds == 600
            return "https://assets.example/private?signature=secret"

    class Analysis:
        async def analyze(self, **kwargs: object) -> MovementReport:
            captured.append(kwargs)
            media = kwargs["media"]
            return MovementReport.model_validate(
                {
                    "exercise": {"name": "坐姿划船", "identification": "IDENTIFIED"},
                    "assessment": "INSUFFICIENT_EVIDENCE",
                    "summary": "画面不足",
                    "source_assets": [
                        {"asset_id": media[0].asset_id, "revision": media[0].revision}
                    ],
                    "model_version": "model-v1",
                    "prompt_version": "v1",
                    "knowledge_status": "EMPTY",
                }
            )

    active = True

    async def reauthorize(ref: str) -> AgentIdentity | None:
        assert ref == "a" * 64
        return identity if active else None

    async def submit_run(asset: str) -> str:
        run = str(uuid4())
        await repo.submit(
            identity=identity,
            conversation_id=conversation,
            run_id=run,
            client_message_id=str(uuid4()),
            question="分析",
            asset_ids=[asset],
            auth_session_ref="a" * 64,
            model="model-v1",
            prompt_version="v1",
        )
        return run

    try:
        await repo.create_conversation(identity, conversation)
        asset = await ready_asset(db, identity, conversation)
        worker = MultimodalWorker(repo, MediaRepository(db), Storage(), Analysis(), reauthorize)  # type: ignore[arg-type]
        run = await submit_run(asset)
        assert await worker.run_once()
        runs = ChatRunRepository(db, cipher, timeout=60, retention_hours=1)
        result = await runs.get(run, run_owner(identity))
        assert result and result.status == "COMPLETED"
        assert len(captured) == 1 and captured[0]["media"][0].url.startswith(
            "https://assets.example/"
        )
        revoked_run = await submit_run(asset)
        active = False
        assert await worker.run_once()
        revoked = await runs.get(revoked_run, run_owner(identity))
        assert revoked and revoked.status == "FAILED" and revoked.error_code == "AUTH_EXPIRED"
        assert len(captured) == 1

        class UnknownAnalysis:
            async def analyze(self, **kwargs: object) -> MovementReport:
                raise MultimodalOutcomeUnknown("timeout")

        active = True
        uncertain_run = await submit_run(asset)
        uncertain_worker = MultimodalWorker(
            repo,
            MediaRepository(db),
            Storage(),
            UnknownAnalysis(),
            reauthorize,  # type: ignore[arg-type]
        )
        assert await uncertain_worker.run_once()
        uncertain = await runs.get(uncertain_run, run_owner(identity))
        assert uncertain and uncertain.status == "UNKNOWN"
        assert uncertain.error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert not await uncertain_worker.run_once()

        deletion = MediaDeletionRepository(db, cipher)
        with pytest.raises(MediaAccessError):
            await deletion.request(replace(identity, subject="another-user"), asset)
        deletion_id = await deletion.request(identity, asset)
        assert await deletion.request(identity, asset) == deletion_id
        deleted_result = await runs.get(run, run_owner(identity))
        assert deleted_result and deleted_result.result == {"content_deleted": True}
        async with db.engine.connect() as connection:
            report_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT report_ciphertext,deleted_at FROM agent_movement_reports WHERE run_id=:id"
                        ),
                        {"id": run},
                    )
                )
                .mappings()
                .one()
            )
            assert report_row["report_ciphertext"] is None and report_row["deleted_at"] is not None
    finally:
        await db.close()


async def test_deletion_blocks_provider_dispatch_for_an_already_claimed_run() -> None:
    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "test-v1")
    repo = DurableMultimodalRepository(db, cipher)
    identity = AgentIdentity(
        "user-delete", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation, run = str(uuid4()), str(uuid4())
    try:
        await repo.create_conversation(identity, conversation)
        asset = await ready_asset(db, identity, conversation)
        await repo.submit(
            identity=identity,
            conversation_id=conversation,
            run_id=run,
            client_message_id=str(uuid4()),
            question="分析",
            asset_ids=[asset],
            auth_session_ref="a" * 64,
            model="model-v1",
            prompt_version="v1",
        )
        lease = await repo.claim()
        assert lease and lease.run_id == run
        await MediaDeletionRepository(db, cipher).request(identity, asset)
        assert not await repo.mark_provider_started(lease)
        current = await ChatRunRepository(db, cipher, timeout=60, retention_hours=1).get(
            run, run_owner(identity)
        )
        assert current and current.status == "CANCEL_REQUESTED"
    finally:
        await db.close()


async def test_deletion_rechecks_storage_after_upload_policy_expires() -> None:
    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "test-v1")
    identity = AgentIdentity(
        "user-cleanup", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation = str(uuid4())
    keys = []

    class Storage:
        def delete_all_versions(self, key: str) -> int:
            keys.append(key)
            return 1

    try:
        await DurableMultimodalRepository(db, cipher).create_conversation(identity, conversation)
        upload = await MediaRepository(db).create_upload(
            identity=identity,
            conversation_id=conversation,
            idempotency_key=str(uuid4()),
            content_type="image/png",
            size=100,
        )
        deletion_id = await MediaDeletionRepository(db, cipher).request(identity, upload.asset_id)
        worker = MediaDeletionWorker(db, Storage())  # type: ignore[arg-type]
        assert await worker.run_once()
        async with db.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT status,cleared_once FROM agent_media_deletions WHERE deletion_id=:id"
                        ),
                        {"id": deletion_id},
                    )
                )
                .mappings()
                .one()
            )
            assert row["status"] == "PENDING" and row["cleared_once"]
            await connection.execute(
                text(
                    "UPDATE agent_media_uploads SET expires_at=CURRENT_TIMESTAMP-INTERVAL '10 minutes' WHERE upload_id=:id"
                ),
                {"id": upload.upload_id},
            )
            await connection.execute(
                text(
                    "UPDATE agent_media_deletions SET retry_at=CURRENT_TIMESTAMP WHERE deletion_id=:id"
                ),
                {"id": deletion_id},
            )
        assert await worker.run_once()
        async with db.engine.connect() as connection:
            state = (
                await connection.execute(
                    text("SELECT status FROM agent_media_deletions WHERE deletion_id=:id"),
                    {"id": deletion_id},
                )
            ).scalar_one()
            assert state == "COMPLETED"
        assert len(keys) == 4 and keys[0].startswith("staging/") and keys[1].startswith("final/")
    finally:
        await db.close()
