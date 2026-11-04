"""The history audit returns aggregate candidates without changing the source data."""

from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import DurableMultimodalRepository
from scripts.audit_multimodal_history import audit
from tests.test_multimodal_dispatch_postgres import database, ready_asset

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MULTIMODAL_PG_TESTS") != "1", reason="isolated DB only"
)


async def test_audit_reports_ambiguous_history_and_changed_owner_without_mutation():
    db = database()
    user = AgentIdentity(
        "audit-" + uuid4().hex, frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    owner = run_owner(user)
    repo = DurableMultimodalRepository(db, AesGcmPayloadCipher(b"x" * 32, "v1"))
    conversation, run = str(uuid4()), str(uuid4())
    try:
        baseline = await audit(db)
        await repo.create_conversation(user, conversation)
        await repo.create_conversation(replace(user, roles=frozenset({"COACH"})), str(uuid4()))
        asset = await ready_asset(db, user, conversation)
        await repo.submit(
            identity=user,
            conversation_id=conversation,
            run_id=run,
            client_message_id=str(uuid4()),
            question="test",
            asset_ids=[asset],
            auth_session_ref="a" * 64,
            model="v1",
            prompt_version="v1",
        )
        async with db.engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE agent_chat_runs SET status='COMPLETED',result_ciphertext=:content,key_version='v1' WHERE run_id=:run"
                ),
                {"run": run, "content": b"synthetic"},
            )
            await connection.execute(
                text("""UPDATE agent_chat_messages SET status='COMMITTED',
                    content_ciphertext=:content,key_version='v1' WHERE run_id=:run"""),
                {"run": run, "content": b"synthetic"},
            )
            prior = str(uuid4())
            await connection.execute(
                text("""INSERT INTO agent_chat_messages
                (message_id,conversation_id,owner_hash,run_id,role,status,schema_version,created_at)
                VALUES (:id,:conversation,:owner,:prior,'user','COMMITTED','test',CURRENT_TIMESTAMP-INTERVAL '1 minute')"""),
                {"id": prior, "conversation": conversation, "owner": owner, "prior": str(uuid4())},
            )
            await connection.execute(
                text("""INSERT INTO agent_chat_message_media
                (message_id,asset_id,asset_revision,ordinal,dependency_type)
                VALUES (:id,:asset,1,0,'ATTACHMENT')"""),
                {"id": prior, "asset": asset},
            )
        observed = await audit(db)
        assert observed["split_owner_groups"] == baseline["split_owner_groups"] + 1
        assert (
            observed["possible_missing_lineage_reports"]
            == baseline["possible_missing_lineage_reports"] + 1
        )
        assert await audit(db) == observed
        # The run and dispatch expire sooner than the visible report; the audit
        # must continue to count this candidate after that retention cleanup.
        async with db.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_chat_runs WHERE run_id=:run"), {"run": run}
            )
        assert await audit(db) == observed
    finally:
        await db.close()
