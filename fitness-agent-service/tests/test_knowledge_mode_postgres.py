"""Durable knowledge requests use the same ownership, fencing and deletion boundaries."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.agent.movement_analysis import MovementAnalysisService
from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.multimodal_gateway import AuthorizedMediaRef
from app.media.deletion import MediaDeletionRepository
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import DurableMultimodalRepository
from app.streaming.repository import ChatRunRepository, RunConflict
from tests import test_multimodal_analysis as analysis_helpers
from tests import test_multimodal_dispatch_postgres as pg

pytestmark = pg.pytestmark
isolate_test_data = pg.isolate_test_data


async def test_knowledge_request_roundtrip_idempotency_and_delete():
    db = pg.database()
    try:
        identity = analysis_helpers.identity()
        cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
        repo = DurableMultimodalRepository(db, cipher)
        conversation, run, client = (str(uuid4()) for _ in range(3))
        await repo.create_conversation(identity, conversation)
        asset = await pg.ready_asset(db, identity, conversation)
        args = {
            "identity": identity,
            "conversation_id": conversation,
            "run_id": run,
            "client_message_id": client,
            "question": "介绍什么训练知识？",
            "asset_ids": [asset],
            "auth_session_ref": "a" * 64,
            "model": "vision",
            "prompt_version": "v1",
            "analysis_mode": "knowledge",
        }
        assert (await repo.submit(**args)).created
        assert not (await repo.submit(**args)).created
        with pytest.raises(RunConflict):
            await repo.submit(**{**args, "analysis_mode": "movement"})
        lease = await repo.claim()
        assert lease and lease.payload["analysis_mode"] == "knowledge"
        assert await repo.mark_provider_started(lease)
        raw = analysis_helpers.knowledge_report(
            observations=[
                {
                    "id": "a",
                    "asset_id": asset,
                    "description": "训练说明",
                    "evidence_type": "VIDEO",
                    "time_range": None,
                }
            ]
        )
        service = MovementAnalysisService(
            analysis_helpers.FakeGateway([raw]), analysis_helpers.FakeRag(), prompt_version="v1"
        )
        report = await service.analyze_knowledge(
            identity=identity,
            question=args["question"],
            media=[AuthorizedMediaRef(asset, 1, "video", "https://assets.example/a", 20)],
        )
        assert await repo.publish(lease, report)
        runs = ChatRunRepository(db, cipher, timeout=60, retention_hours=1)
        result = await runs.get(run, run_owner(identity))
        assert result and result.result["report"]["schema_version"] == "fitness-knowledge.v1"
        async with db.engine.connect() as conn:
            schema = (
                await conn.execute(
                    text(
                        "SELECT schema_version FROM agent_chat_messages WHERE run_id=:run AND role='assistant'"
                    ),
                    {"run": run},
                )
            ).scalar_one()
            assert schema == "fitness-knowledge.v1"
        await MediaDeletionRepository(db, cipher).request(identity, asset)
        result = await runs.get(run, run_owner(identity))
        assert result and result.result == {"content_deleted": True}
    finally:
        await db.close()


async def test_worker_dispatches_knowledge_mode_and_preserves_source_fencing():
    from app.media.repository import MediaRepository
    from app.streaming.multimodal_worker import MultimodalWorker

    db = pg.database()
    try:
        identity = analysis_helpers.identity()
        cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
        repo = DurableMultimodalRepository(db, cipher)
        conversation, run = str(uuid4()), str(uuid4())
        await repo.create_conversation(identity, conversation)
        asset = await pg.ready_asset(db, identity, conversation)
        await repo.submit(
            identity=identity,
            conversation_id=conversation,
            run_id=run,
            client_message_id=str(uuid4()),
            question="这是什么知识",
            asset_ids=[asset],
            auth_session_ref="a" * 64,
            model="vision",
            prompt_version="v1",
            analysis_mode="knowledge",
        )
        raw = analysis_helpers.knowledge_report(
            observations=[
                {
                    "id": "a",
                    "asset_id": asset,
                    "description": "训练说明",
                    "evidence_type": "VIDEO",
                    "time_range": None,
                }
            ]
        )
        gateway = analysis_helpers.FakeGateway([raw])
        service = MovementAnalysisService(gateway, analysis_helpers.FakeRag(), prompt_version="v1")

        class Storage:
            def signed_read_url(self, stored, ttl_seconds):
                return "https://assets.example/a"

        async def auth(ref):
            return identity

        worker = MultimodalWorker(repo, MediaRepository(db), Storage(), service, auth)
        assert await worker.run_once()
        result = await ChatRunRepository(db, cipher, timeout=60, retention_hours=1).get(
            run, run_owner(identity)
        )
        assert (
            result
            and result.status == "COMPLETED"
            and result.result["report"]["schema_version"] == "fitness-knowledge.v1"
        )
        assert gateway.calls[0]["kind"] == "knowledge_interpretation"
    finally:
        await db.close()
