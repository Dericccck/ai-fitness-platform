"""隔离 PostgreSQL 中验证上传幂等、版本绑定和所有权。"""

from __future__ import annotations

import asyncio
import io
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.media.metadata import MediaValidationError
from app.media.repository import MediaAccessError, MediaQuotaExceeded, MediaRepository
from app.media.service import MediaService
from app.media.storage import OssMediaStorage
from app.streaming.durable_multimodal import DurableMultimodalRepository
from app.streaming.repository import RunConflict
from tests.test_multimodal_dispatch_postgres import database

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MULTIMODAL_PG_TESTS") != "1", reason="仅允许隔离临时库验收"
)


class FakeBucket:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.copied = []

    def head_object(self, key, params=None):
        version = (params or {}).get("versionId", "source-v1")
        return SimpleNamespace(
            headers={"x-oss-version-id": version},
            content_length=len(self.data),
            content_type="image/png",
        )

    def get_object(self, key, params=None):
        assert params == {"versionId": "source-v1"}
        return io.BytesIO(self.data)

    def copy_object(self, bucket, source, target, params=None):
        self.copied.append((source, target, params))
        return SimpleNamespace(headers={"x-oss-version-id": "final-v1"})

    def sign_url(self, method, key, expires, params=None):
        assert params == {"versionId": "final-v1"}
        return "https://private-bucket.oss-cn-beijing.aliyuncs.com/file?signature=hidden"


def make_storage(bucket):
    return OssMediaStorage(
        endpoint="https://oss-cn-beijing.aliyuncs.com",
        bucket_name="private-bucket",
        region="cn-beijing",
        access_key_id="test-id",
        access_key_secret="test-secret",
        security_token="test-token",
        bucket=bucket,
    )


async def test_upload_quota_is_atomic_across_conversations_and_idempotent():
    db = database()
    identity = AgentIdentity(
        "quota-" + uuid4().hex, frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    repo = MediaRepository(db, max_assets=1, max_bytes=10)
    conversations = [str(uuid4()), str(uuid4())]
    keys = [str(uuid4()), str(uuid4())]
    try:
        chat = DurableMultimodalRepository(db, AesGcmPayloadCipher(b"x" * 32, "v1"))
        for conversation in conversations:
            await chat.create_conversation(identity, conversation)

        async def create(index):
            return await repo.create_upload(
                identity=identity,
                conversation_id=conversations[index],
                idempotency_key=keys[index],
                content_type="image/png",
                size=10,
            )

        results = await asyncio.gather(create(0), create(1), return_exceptions=True)
        assert sum(isinstance(result, MediaQuotaExceeded) for result in results) == 1
        winner = next(i for i, result in enumerate(results) if not isinstance(result, Exception))
        assert (await create(winner)).upload_id == results[winner].upload_id
    finally:
        await db.close()


async def test_upload_complete_is_idempotent_and_owner_scoped():
    db = database()
    identity = AgentIdentity(
        "media-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation, key = str(uuid4()), str(uuid4())
    data = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(data, format="PNG")
    bucket = FakeBucket(data.getvalue())
    repository = MediaRepository(db)
    service = MediaService(repository, make_storage(bucket))
    try:
        await DurableMultimodalRepository(
            db, AesGcmPayloadCipher(b"x" * 32, "v1")
        ).create_conversation(identity, conversation)
        args = {
            "identity": identity,
            "conversation_id": conversation,
            "idempotency_key": key,
            "content_type": "image/png",
            "size": len(bucket.data),
        }
        first = await service.initiate(**args)
        repeated = await service.initiate(**args)
        assert first.record.upload_id == repeated.record.upload_id
        assert first.policy and first.policy.form_data["key"] == first.record.staging_key
        with pytest.raises(RunConflict):
            await service.initiate(**{**args, "size": len(bucket.data) + 1})
        with pytest.raises(MediaAccessError):
            await repository.get_upload(replace(identity, subject="other"), first.record.upload_id)
        asset = await service.complete(identity, first.record.upload_id)
        assert asset and asset.content_type == "image/png" and asset.final_version == "final-v1"
        again = await service.complete(identity, first.record.upload_id)
        assert again == asset and len(bucket.copied) == 1
        assert bucket.copied[0][2] == {"versionId": "source-v1"}
        assert (await repository.get_upload(identity, first.record.upload_id)).status == "READY"
        assert "signature=hidden" in await service.signed_preview(identity, asset.asset_id)
        with pytest.raises(MediaAccessError):
            await service.signed_preview(replace(identity, subject="other"), asset.asset_id)
    finally:
        await db.close()


async def test_invalid_image_never_reaches_final_storage():
    db = database()
    identity = AgentIdentity(
        "media-user-2", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation = str(uuid4())
    bucket = FakeBucket(b"not a valid image")
    repository = MediaRepository(db)
    service = MediaService(repository, make_storage(bucket))
    try:
        await DurableMultimodalRepository(
            db, AesGcmPayloadCipher(b"x" * 32, "v1")
        ).create_conversation(identity, conversation)
        result = await service.initiate(
            identity=identity,
            conversation_id=conversation,
            idempotency_key=str(uuid4()),
            content_type="image/png",
            size=len(bucket.data),
        )
        with pytest.raises(MediaValidationError):
            await service.complete(identity, result.record.upload_id)
        assert not bucket.copied
        assert (await repository.get_upload(identity, result.record.upload_id)).status == "REJECTED"
    finally:
        await db.close()


async def test_expired_asset_is_tombstoned_and_enqueued_once():
    from sqlalchemy import text

    from app.media.deletion import MediaDeletionRepository

    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
    identity = AgentIdentity(
        "expiry-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation = str(uuid4())
    data = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(data, format="PNG")
    service = MediaService(MediaRepository(db), make_storage(FakeBucket(data.getvalue())))
    deletion = MediaDeletionRepository(db, cipher)
    try:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE agent_chat_conversations,agent_chat_runs CASCADE")
            )
        await DurableMultimodalRepository(db, cipher).create_conversation(identity, conversation)
        issued = await service.initiate(
            identity=identity,
            conversation_id=conversation,
            idempotency_key=str(uuid4()),
            content_type="image/png",
            size=len(data.getvalue()),
        )
        asset = await service.complete(identity, issued.record.upload_id)
        assert asset is not None and not await deletion.expire_one()
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_media_assets SET expires_at=CURRENT_TIMESTAMP-INTERVAL '1 minute' WHERE asset_id=:id"
                ),
                {"id": asset.asset_id},
            )
        assert await deletion.expire_one()
        assert not await deletion.expire_one()
        with pytest.raises(MediaAccessError):
            await service.repository.ready_asset(identity, asset.asset_id)
        async with db.engine.connect() as connection:
            assert (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM agent_media_deletions WHERE asset_id=:id AND status='PENDING'"
                    ),
                    {"id": asset.asset_id},
                )
            ).scalar_one() == 1
    finally:
        await db.close()


async def test_staging_and_abandoned_upload_cleanup_keep_ready_final():
    from sqlalchemy import text

    from app.media.deletion import MediaDeletionRepository
    from app.media.deletion_worker import MediaDeletionWorker

    db = database()
    cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
    identity = AgentIdentity(
        "cleanup-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    data = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(data, format="PNG")
    service = MediaService(MediaRepository(db), make_storage(FakeBucket(data.getvalue())))
    deletion = MediaDeletionRepository(db, cipher)
    removed = []

    class CleanupStorage:
        def delete_all_versions(self, key):
            removed.append(key)

    cleaner = MediaDeletionWorker(db, CleanupStorage())
    try:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE agent_chat_conversations,agent_chat_runs CASCADE")
            )
        conversation = str(uuid4())
        await DurableMultimodalRepository(db, cipher).create_conversation(identity, conversation)
        args = {
            "identity": identity,
            "conversation_id": conversation,
            "content_type": "image/png",
            "size": len(data.getvalue()),
        }
        ready = await service.initiate(**args, idempotency_key=str(uuid4()))
        abandoned = await service.initiate(**args, idempotency_key=str(uuid4()))
        final = await service.complete(identity, ready.record.upload_id)
        assert final is not None
        async with db.engine.begin() as connection:
            await connection.execute(
                text("""UPDATE agent_media_uploads SET expires_at=CURRENT_TIMESTAMP-INTERVAL '10 minutes'
                WHERE upload_id IN (:ready,:abandoned)"""),
                {"ready": ready.record.upload_id, "abandoned": abandoned.record.upload_id},
            )
        assert await cleaner.cleanup_staging_once()
        assert removed == [ready.record.staging_key]
        assert not await cleaner.cleanup_staging_once()
        assert (
            await service.repository.ready_asset(identity, final.asset_id)
        ).final_version == "final-v1"
        assert await deletion.expire_abandoned_upload()
        assert not await deletion.expire_abandoned_upload()
        with pytest.raises(MediaAccessError):
            await service.repository.get_upload(identity, abandoned.record.upload_id)
        assert await cleaner.run_once()
        assert abandoned.record.staging_key in removed
        assert any(key.startswith("final/") for key in removed)
        assert (
            await service.repository.ready_asset(identity, final.asset_id)
        ).final_version == "final-v1"
    finally:
        await db.close()


async def test_validator_outage_preserves_upload_for_retry(tmp_path):
    from app.media.metadata import MediaInspectionUnavailable

    db = database()
    identity = AgentIdentity(
        "validator-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation = str(uuid4())
    data = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(data, format="PNG")
    bucket = FakeBucket(data.getvalue())
    repository = MediaRepository(db)
    storage = make_storage(bucket)
    service = MediaService(
        repository,
        storage,
        validator_socket=str(tmp_path / "missing.sock"),
        validator_temp_dir=str(tmp_path),
    )
    try:
        await DurableMultimodalRepository(
            db, AesGcmPayloadCipher(b"x" * 32, "v1")
        ).create_conversation(identity, conversation)
        issued = await service.initiate(
            identity=identity,
            conversation_id=conversation,
            idempotency_key=str(uuid4()),
            content_type="image/png",
            size=len(data.getvalue()),
        )
        with pytest.raises(MediaInspectionUnavailable):
            await service.complete(identity, issued.record.upload_id)
        assert (await repository.get_upload(identity, issued.record.upload_id)).status == "ISSUED"
        restored = MediaService(repository, storage)
        assert await restored.complete(identity, issued.record.upload_id) is not None
        assert (await repository.get_upload(identity, issued.record.upload_id)).status == "READY"
    finally:
        await db.close()
