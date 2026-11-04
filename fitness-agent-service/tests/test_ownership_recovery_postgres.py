"""Recovery integration tests run only against an explicitly isolated test database."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.agent.movement_models import MovementReport
from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import DurableMultimodalRepository
from app.streaming.ownership_recovery import (
    OwnershipRecovery,
    RecoveryConflict,
    RecoveryUnavailable,
)
from app.streaming.repository import ChatRunRepository
from tests import test_multimodal_dispatch_postgres as pg_helpers

pytestmark = pg_helpers.pytestmark
isolate_test_data = pg_helpers.isolate_test_data
database = pg_helpers.database
ready_asset = pg_helpers.ready_asset


async def build(db, *, complete=True):
    cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
    repo = DurableMultimodalRepository(db, cipher)
    old = AgentIdentity("recover-user", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999)
    new = replace(old, roles=frozenset({"MEMBER"}))
    conversation, run, client = str(uuid4()), str(uuid4()), str(uuid4())
    await repo.create_conversation(old, conversation)
    asset = await ready_asset(db, old, conversation)
    await repo.submit(
        identity=old,
        conversation_id=conversation,
        run_id=run,
        client_message_id=client,
        question="敏感训练问题",
        asset_ids=[asset],
        auth_session_ref="a" * 64,
        model="model-v1",
        prompt_version="v1",
    )
    if complete:
        lease = await repo.claim()
        assert lease and await repo.mark_provider_started(lease)
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
        assert await repo.publish(lease, report)
    return cipher, repo, old, new, conversation, run, asset, client


async def test_recover_reencrypts_and_preserves_idempotency_and_scope():
    db = database()
    try:
        cipher, repo, old, new, conversation, run, asset, client = await build(db)
        recovery = OwnershipRecovery(db, cipher)
        assert [x["conversation_id"] for x in (await recovery.available(new))["conversations"]] == [
            conversation
        ]
        with pytest.raises(RecoveryUnavailable):
            await recovery.recover(replace(new, subject="other"), conversation)
        with pytest.raises(RecoveryUnavailable):
            await recovery.recover(
                replace(new, organization_ids=frozenset({"other"})), conversation
            )
        async with db.engine.connect() as conn:
            before = (
                await conn.execute(
                    text(
                        "SELECT message_id,content_ciphertext FROM agent_chat_messages WHERE conversation_id=:id AND role='user'"
                    ),
                    {"id": conversation},
                )
            ).one()
        assert await recovery.recover(new, conversation)
        assert not await recovery.recover(new, conversation)
        assert (await recovery.available(new))["conversations"] == []
        async with db.engine.connect() as conn:
            after = (
                await conn.execute(
                    text("SELECT content_ciphertext FROM agent_chat_messages WHERE message_id=:id"),
                    {"id": before.message_id},
                )
            ).scalar_one()
            assert bytes(after) != bytes(before.content_ciphertext)
            assert (
                cipher.decrypt(
                    bytes(after),
                    associated_data=f"chat-message:{before.message_id}:{run_owner(new)}",
                    key_version="v1",
                ).decode()
                == "敏感训练问题"
            )
            report = (
                await conn.execute(
                    text(
                        "SELECT report_id,report_ciphertext,key_version FROM agent_movement_reports WHERE conversation_id=:id"
                    ),
                    {"id": conversation},
                )
            ).one()
            assert (
                "画面不足"
                in cipher.decrypt(
                    bytes(report.report_ciphertext),
                    associated_data=f"movement-report:{report.report_id}:{run_owner(new)}",
                    key_version=report.key_version,
                ).decode()
            )
        runs = ChatRunRepository(db, cipher, timeout=60, retention_hours=1)
        assert await runs.get(run, run_owner(old)) is None
        assert (await runs.get(run, run_owner(new))).status == "COMPLETED"
        repeated = await repo.submit(
            identity=new,
            conversation_id=conversation,
            run_id=run,
            client_message_id=client,
            question="敏感训练问题",
            asset_ids=[asset],
            auth_session_ref="a" * 64,
            model="model-v1",
            prompt_version="v1",
        )
        assert not repeated.created and repeated.run_id == run
    finally:
        await db.close()


async def test_active_task_and_undecryptable_data_leave_ownership_unchanged():
    db = database()
    try:
        cipher, _repo, old, new, conversation, run, _asset, _client = await build(
            db, complete=False
        )
        recovery = OwnershipRecovery(db, cipher)
        with pytest.raises(RecoveryConflict, match="进行中"):
            await recovery.recover(new, conversation)
        async with db.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agent_chat_runs SET status='FAILED' WHERE run_id=:run"), {"run": run}
            )
        wrong_cipher = AesGcmPayloadCipher(b"y" * 32, "v1")
        with pytest.raises(RecoveryConflict, match="解密"):
            await OwnershipRecovery(db, wrong_cipher).recover(new, conversation)
        async with db.engine.connect() as conn:
            owner = (
                await conn.execute(
                    text(
                        "SELECT owner_hash FROM agent_chat_conversations WHERE conversation_id=:id"
                    ),
                    {"id": conversation},
                )
            ).scalar_one()
        assert owner == run_owner(old)
        assert await recovery.recover(new, conversation)
    finally:
        await db.close()


async def test_legacy_link_and_live_upload_prevent_recovery():
    db = database()
    try:
        cipher, _repo, old, new, conversation, _run, asset, _client = await build(db)
        recovery = OwnershipRecovery(db, cipher)
        async with db.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_chat_conversations SET legacy_linked=true WHERE conversation_id=:id"
                ),
                {"id": conversation},
            )
        with pytest.raises(RecoveryConflict, match="旧对话"):
            await recovery.recover(new, conversation)
        async with db.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_chat_conversations SET legacy_linked=false WHERE conversation_id=:id"
                ),
                {"id": conversation},
            )
            await conn.execute(
                text("""INSERT INTO agent_media_uploads (upload_id,asset_id,owner_hash,idempotency_key,request_digest,staging_key,max_bytes,status,expires_at)
                VALUES (:upload,:asset,:owner,:key,'digest',:staging,10,'READY',CURRENT_TIMESTAMP+INTERVAL '1 hour')"""),
                {
                    "upload": str(uuid4()),
                    "asset": asset,
                    "owner": run_owner(old),
                    "key": str(uuid4()),
                    "staging": str(uuid4()),
                },
            )
        with pytest.raises(RecoveryConflict, match="上传"):
            await recovery.recover(new, conversation)
        async with db.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_media_uploads SET expires_at=CURRENT_TIMESTAMP-INTERVAL '6 minutes' WHERE asset_id=:asset"
                ),
                {"asset": asset},
            )
        assert await recovery.recover(new, conversation)
    finally:
        await db.close()


async def test_concurrent_recovery_is_idempotent_and_retained_messages_survive_run_purge():
    import asyncio

    db = database()
    try:
        cipher, _repo, old, new, conversation, run, _asset, _client = await build(db)
        # Run/result retention is shorter than the conversation retention.
        async with db.engine.begin() as conn:
            await conn.execute(text("DELETE FROM agent_chat_runs WHERE run_id=:id"), {"id": run})
        service = OwnershipRecovery(db, cipher)
        results = await asyncio.gather(
            service.recover(new, conversation), service.recover(new, conversation)
        )
        assert sorted(results) == [False, True]
        async with db.engine.connect() as conn:
            owners = (
                (
                    await conn.execute(
                        text(
                            "SELECT DISTINCT owner_hash FROM agent_chat_messages WHERE conversation_id=:id"
                        ),
                        {"id": conversation},
                    )
                )
                .scalars()
                .all()
            )
            assert owners == [run_owner(new)]
            assert run_owner(old) != owners[0]
    finally:
        await db.close()


async def test_http_recovery_enforces_auth_scope_and_reports_conflict(monkeypatch):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI

    from app.api.routes import mobile_media

    db = database()
    try:
        _cipher, repo, _old, new, conversation, run, _asset, _client = await build(
            db, complete=False
        )
        app = FastAPI()
        app.state.database = db
        app.state.multimodal_repository = repo
        app.state.settings = SimpleNamespace(multimodal_owner_recovery_enabled=False)
        app.include_router(mobile_media.router)
        identity = new

        async def auth(request, response):
            response.headers["Cache-Control"] = "no-store"
            return "test", identity

        async def quota(request, identity):
            return None

        monkeypatch.setattr(mobile_media, "_auth", auth)
        monkeypatch.setattr(mobile_media, "enforce_chat_quota", quota)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            base = "/api/v1/mobile/agent/conversations"
            assert (await client.get(base + "/recoverable")).status_code == 503
            assert (await client.post(f"{base}/{conversation}/recover")).status_code == 503
            assert (await client.get("/api/v1/mobile/agent/capabilities")).json()[
                "owner_recovery_enabled"
            ] is False
            app.state.settings.multimodal_owner_recovery_enabled = True
            assert (await client.get("/api/v1/mobile/agent/capabilities")).json()[
                "owner_recovery_enabled"
            ] is True
            listing = await client.get(base + "/recoverable")
            assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
            assert [x["conversation_id"] for x in listing.json()["conversations"]] == [conversation]
            assert (await client.post(f"{base}/{conversation}/recover")).status_code == 409
            identity = replace(new, subject="other")
            assert (await client.get(base + "/recoverable")).json()["conversations"] == []
            assert (await client.post(f"{base}/{conversation}/recover")).status_code == 404
            identity = new
            async with db.engine.begin() as conn:
                await conn.execute(
                    text("UPDATE agent_chat_runs SET status='FAILED' WHERE run_id=:id"), {"id": run}
                )
            assert (await client.post(f"{base}/{conversation}/recover")).json() == {
                "recovered": True
            }
            assert (await client.post(f"{base}/{conversation}/recover")).json() == {
                "recovered": False
            }
    finally:
        await db.close()


async def test_late_decryption_failure_rolls_back_prior_cipher_updates():
    db = database()
    try:
        cipher, _repo, old, new, conversation, run, _asset, _client = await build(db)
        async with db.engine.begin() as conn:
            before = (
                await conn.execute(
                    text(
                        "SELECT message_id,content_ciphertext FROM agent_chat_messages WHERE conversation_id=:id AND role='user'"
                    ),
                    {"id": conversation},
                )
            ).one()
            await conn.execute(
                text("UPDATE agent_chat_run_dispatch SET key_version='missing' WHERE run_id=:id"),
                {"id": run},
            )
        with pytest.raises(RecoveryConflict, match="解密"):
            await OwnershipRecovery(db, cipher).recover(new, conversation)
        async with db.engine.connect() as conn:
            message = (
                await conn.execute(
                    text(
                        "SELECT owner_hash,content_ciphertext FROM agent_chat_messages WHERE message_id=:id"
                    ),
                    {"id": before.message_id},
                )
            ).one()
            owner = (
                await conn.execute(
                    text(
                        "SELECT owner_hash FROM agent_chat_conversations WHERE conversation_id=:id"
                    ),
                    {"id": conversation},
                )
            ).scalar_one()
        assert owner == message.owner_hash == run_owner(old)
        assert bytes(message.content_ciphertext) == bytes(before.content_ciphertext)
    finally:
        await db.close()


async def test_recoverable_pagination_is_scope_checked_and_survives_recovering_anchor():
    db = database()
    try:
        cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
        old = AgentIdentity("page-recovery", frozenset({"org"}), frozenset({"OLD"}), 1, 9999999999)
        new = replace(old, roles=frozenset({"NEW"}))
        async with db.engine.begin() as conn:
            await conn.execute(
                text("""INSERT INTO agent_chat_conversations (conversation_id,subject_user_id,organization_scope,owner_hash,updated_at)
                SELECT 'recovery-page-' || lpad(n::text,3,'0'),:subject,'["org"]',:owner,'2026-01-01 00:00:00+00'::timestamptz
                FROM generate_series(1,101) n"""),
                {"subject": old.subject, "owner": run_owner(old)},
            )
        service = OwnershipRecovery(db, cipher)
        first = await service.available(new)
        assert len(first["conversations"]) == 100 and first["next_cursor"]
        await service.recover(new, first["next_cursor"])
        second = await service.available(new, first["next_cursor"])
        assert len(second["conversations"]) == 1 and second["next_cursor"] is None
        with pytest.raises(RecoveryUnavailable):
            await service.available(replace(new, subject="other"), first["next_cursor"])
    finally:
        await db.close()


async def test_successful_recovery_is_audited_atomically_and_retention_is_bounded():
    import hashlib

    db = database()
    try:
        cipher, _repo, old, new, conversation, _run, _asset, _client = await build(db)
        service = OwnershipRecovery(db, cipher)
        assert await service.recover(new, conversation)
        assert not await service.recover(new, conversation)
        async with db.engine.begin() as conn:
            rows = (
                (
                    await conn.execute(
                        text("""SELECT actor_hash,conversation_hash,previous_owner_hash,outcome
                FROM agent_recovery_audit WHERE conversation_hash=:hash ORDER BY created_at,outcome"""),
                        {"hash": hashlib.sha256(conversation.encode()).hexdigest()},
                    )
                )
                .mappings()
                .all()
            )
            assert {r["outcome"] for r in rows} == {"RECOVERED", "NOOP"}
            recovered = next(r for r in rows if r["outcome"] == "RECOVERED")
            assert recovered["actor_hash"] == run_owner(new) and recovered[
                "previous_owner_hash"
            ] == run_owner(old)
            assert conversation not in str(rows)
            await conn.execute(
                text(
                    "UPDATE agent_recovery_audit SET created_at=CURRENT_TIMESTAMP-INTERVAL '91 days' WHERE conversation_hash=:hash"
                ),
                {"hash": hashlib.sha256(conversation.encode()).hexdigest()},
            )
        assert await service.cleanup_audit(90) >= 2
    finally:
        await db.close()
