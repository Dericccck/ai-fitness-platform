"""Pagination contract; requires the explicitly designated disposable test database."""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import mobile_media
from app.infrastructure.agent_context import AgentIdentity
from app.rag.models import RetrievalScope
from app.rag.repository import KnowledgeRepository
from app.streaming.control import run_owner
from tests.test_multimodal_dispatch_postgres import database

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MULTIMODAL_PG_TESTS") != "1", reason="requires disposable database"
)


async def test_pagination_uses_stable_ties_and_is_owner_scoped(monkeypatch):
    db = database()
    identity = AgentIdentity(
        "pagination-user", frozenset({"org-1"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    owner = run_owner(identity)
    conversation = str(uuid4())
    run_id = str(uuid4())
    app = FastAPI()
    app.state.database = db
    app.state.confirmation_cipher = SimpleNamespace()
    app.include_router(mobile_media.router)

    async def auth(request, response):
        return "test", identity

    monkeypatch.setattr(mobile_media, "_auth", auth)
    try:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO agent_chat_conversations
              (conversation_id,subject_user_id,organization_scope,owner_hash)
              VALUES (:id,:subject,'org-1',:owner)"""),
                {"id": conversation, "subject": identity.subject, "owner": owner},
            )
            await connection.execute(
                text("""INSERT INTO agent_chat_messages
              (message_id,conversation_id,owner_hash,run_id,role,status,schema_version,created_at)
              SELECT 'page-' || lpad(n::text, 3, '0'),:conversation,:owner,
                CASE WHEN n=1 THEN :run ELSE NULL END,'user','PENDING','test',
                '2026-01-01 00:00:00+00'::timestamptz FROM generate_series(1,205) n"""),
                {"conversation": conversation, "owner": owner, "run": run_id},
            )
            await connection.execute(
                text("""INSERT INTO agent_chat_conversations
              (conversation_id,subject_user_id,organization_scope,owner_hash,updated_at)
              SELECT 'page-conversation-' || lpad(n::text, 3, '0'),:subject,'org-1',:owner,
                '2026-01-01 00:00:00+00'::timestamptz FROM generate_series(1,101) n"""),
                {"subject": identity.subject, "owner": owner},
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            base = f"/api/v1/mobile/agent/conversations/{conversation}/messages"
            first = (await client.get(base)).json()
            assert len(first["messages"]) == 200 and first["next_cursor"]
            second = (await client.get(base, params={"before": first["next_cursor"]})).json()
            ids = [m["message_id"] for m in second["messages"] + first["messages"]]
            assert ids == [f"page-{i:03}" for i in range(1, 206)]
            assert second["next_cursor"] is None
            assert len((await client.get(base, params={"run_id": run_id})).json()["messages"]) == 1
            assert (await client.get(base, params={"before": "foreign"})).status_code == 422
            listing = (await client.get("/api/v1/mobile/agent/conversations")).json()
            assert len(listing["conversations"]) == 100 and listing["next_cursor"]
            tail = (
                await client.get(
                    "/api/v1/mobile/agent/conversations", params={"before": listing["next_cursor"]}
                )
            ).json()
            assert len(tail["conversations"]) == 2 and tail["next_cursor"] is None
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_chat_messages WHERE owner_hash=:owner"), {"owner": owner}
            )
            await connection.execute(
                text("DELETE FROM agent_chat_conversations WHERE owner_hash=:owner"),
                {"owner": owner},
            )
        await db.close()


async def test_training_type_filter_is_applied_before_recall_without_weakening_acl():
    db = database()
    marker = uuid4().hex
    ids = [f"{marker}-training", f"{marker}-other", f"{marker}-private"]
    vector = [1.0] + [0.0] * 1023
    try:
        async with db.engine.begin() as connection:
            for index, document_id in enumerate(ids):
                kind = "TRAINING_GUIDE" if index != 1 else "OTHER"
                visibility = "PRIVATE" if index == 2 else "GLOBAL"
                await connection.execute(
                    text("""INSERT INTO knowledge_documents
                  (id,title,source_uri,document_type,visibility,version,status,checksum,effective_from,owner_user_id)
                  VALUES (:id,'test',:uri,:kind,:visibility,1,'PUBLISHED','test',CURRENT_TIMESTAMP,'other')"""),
                    {
                        "id": document_id,
                        "uri": "internal://" + document_id,
                        "kind": kind,
                        "visibility": visibility,
                    },
                )
                await connection.execute(
                    text("""INSERT INTO knowledge_chunks
                  (id,document_id,chunk_index,content,content_hash,embedding,visibility,owner_user_id,
                   allowed_roles,document_type,effective_from)
                  VALUES (:id,:id,0,'squat exercise','hash',CAST(:vector AS vector),:visibility,'other',
                    ARRAY['STUDENT']::text[],:kind,CURRENT_TIMESTAMP)"""),
                    {
                        "id": document_id,
                        "vector": "[" + ",".join(map(str, vector)) + "]",
                        "kind": kind,
                        "visibility": visibility,
                    },
                )
        repo = KnowledgeRepository(db)
        scope = RetrievalScope(
            "test-user", frozenset({"org-1"}), frozenset({"STUDENT"}), frozenset({"TRAINING_GUIDE"})
        )
        vector_results = await repo.search_candidates(vector, scope, limit=10)
        keyword_results = await repo.search_keyword_candidates("squat", scope, limit=10)
        assert {item.id for item in vector_results if item.id.startswith(marker)} == {ids[0]}
        assert {item.id for item in keyword_results if item.id.startswith(marker)} == {ids[0]}
        empty_scope = RetrievalScope(
            "test-user", frozenset({"org-1"}), frozenset({"STUDENT"}), frozenset()
        )
        assert await repo.search_candidates(vector, empty_scope, limit=10) == []
        assert await repo.search_keyword_candidates("squat", empty_scope, limit=10) == []
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM knowledge_documents WHERE id=ANY(CAST(:ids AS TEXT[]))"),
                {"ids": ids},
            )
        await db.close()
