"""隔离 PostgreSQL/Redis 与模拟 OSS/模型组成的移动端接口端到端验证。"""

from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from PIL import Image
from sqlalchemy import text

from app.agent.movement_models import KnowledgeReport, MovementReport
from app.api.routes.auth import auth_error
from app.api.routes.mobile_auth import router as auth_router
from app.api.routes.mobile_media import router as media_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import conversation_thread_id
from app.infrastructure.browser_auth import BrowserAuthError
from app.media.deletion import MediaDeletionRepository
from app.media.repository import MediaRepository
from app.media.service import MediaService
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import DurableMultimodalRepository
from app.streaming.legacy_bridge import LegacyConversationBridge
from app.streaming.multimodal_worker import MultimodalWorker
from app.streaming.repository import ChatRunRepository
from tests.test_media_service_postgres import FakeBucket, make_storage
from tests.test_mobile_auth_redis import make_service
from tests.test_multimodal_dispatch_postgres import database

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MULTIMODAL_PG_TESTS") != "1"
    or not os.getenv("AGENT_MOBILE_TEST_REDIS_URL"),
    reason="需要显式指定隔离 PostgreSQL 和 Redis",
)


async def test_mobile_upload_submit_worker_history_and_delete() -> None:
    db = database()
    auth, redis, _ = await make_service()
    cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
    repository = DurableMultimodalRepository(db, cipher)
    media_repository = MediaRepository(db)
    data = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(data, format="PNG")
    bucket = FakeBucket(data.getvalue())
    storage = make_storage(bucket)
    captured = []
    change_revision = [False]

    class Analysis:
        async def analyze(self, **kwargs):
            captured.append(kwargs)
            if change_revision[0]:
                async with db.engine.begin() as connection:
                    await connection.execute(
                        text("""UPDATE agent_memory_subject_fences SET revision=revision+1
                        WHERE subject_user_id='user-1'""")
                    )
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

        async def analyze_knowledge(self, **kwargs):
            captured.append(kwargs)
            return KnowledgeReport.model_validate(
                {
                    "summary": "画面中的训练知识",
                    "key_points": [{"text": "注意训练节奏", "basis_type": "MEDIA"}],
                    "source_assets": [
                        {
                            "asset_id": kwargs["media"][0].asset_id,
                            "revision": kwargs["media"][0].revision,
                        }
                    ],
                    "model_version": "model-v1",
                    "prompt_version": "v1",
                    "knowledge_status": "EMPTY",
                }
            )

    runs = ChatRunRepository(db, cipher, timeout=60, retention_hours=1)

    async def cancel(run_id, owner, request_id):
        return await runs.request_cancel(run_id, owner, request_id)

    saver = InMemorySaver()
    legacy_bridge = LegacyConversationBridge(saver, runs)
    app = FastAPI()
    app.state.legacy_bridge = legacy_bridge
    app.state.settings = SimpleNamespace(
        multimodal_api_enabled=True,
        multimodal_knowledge_enabled=True,
        multimodal_model="model-v1",
        multimodal_prompt_version="v1",
        chat_rate_limit_enabled=False,
    )
    app.state.browser_auth = None
    app.state.mobile_auth = auth
    app.state.database = db
    app.state.confirmation_cipher = cipher
    app.state.multimodal_repository = repository
    app.state.media_service = MediaService(media_repository, storage)
    app.state.media_deletion = MediaDeletionRepository(db, cipher)
    app.state.chat_runs = SimpleNamespace(repository=runs, cancel=cancel)
    app.add_exception_handler(BrowserAuthError, auth_error)
    app.include_router(auth_router)
    app.include_router(media_router)
    worker = MultimodalWorker(
        repository,
        media_repository,
        storage,
        Analysis(),
        auth.identity,
        legacy_bridge=legacy_bridge,
    )  # type: ignore[arg-type]
    marker = uuid4().hex
    try:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("TRUNCATE agent_chat_conversations,agent_chat_runs CASCADE")
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://app.test"
        ) as client:
            assert (await client.get("/api/v1/mobile/agent/conversations")).status_code == 401
            login = await client.post(
                "/api/v1/mobile-auth/login",
                json={
                    "username": marker,
                    "password": "test-only",
                    "organization_id": "org-1",
                },
            )
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            _, actor = await auth.authenticate(login.json()["access_token"])
            thread = conversation_thread_id("legacy-chat-1", actor)
            revision = await repository.memory_revision(actor.subject)
            snapshot = empty_checkpoint()
            snapshot["channel_values"] = {
                "turn_run_id": "legacy-run-1",
                "memory_revision": revision,
                "messages": [
                    {"role": "user", "content": "我练深蹲"},
                    {"role": "assistant", "content": "注意稳定"},
                ],
            }
            snapshot["channel_versions"] = {key: 1 for key in snapshot["channel_values"]}
            await saver.aput(
                {"configurable": {"thread_id": thread, "checkpoint_ns": ""}},
                snapshot,
                {"source": "input", "step": 0, "parents": {}},
                snapshot["channel_versions"],
            )
            async with db.engine.begin() as connection:
                await connection.execute(
                    text("""INSERT INTO agent_chat_history_commits
                    (thread_id,owner_hash,subject_user_id,run_id,version) VALUES (:thread,:owner,:subject,'legacy-run-1',1)
                    ON CONFLICT(thread_id,owner_hash) DO UPDATE SET run_id=EXCLUDED.run_id"""),
                    {"thread": thread, "owner": run_owner(actor), "subject": actor.subject},
                )
            async with db.engine.begin() as connection:
                await connection.execute(
                    text("""INSERT INTO agent_chat_history_commits
                    (thread_id,owner_hash,subject_user_id,run_id,version)
                    VALUES (:thread,:owner,'different-user','other-run',1)
                    ON CONFLICT(thread_id,owner_hash) DO NOTHING"""),
                    {"thread": "fitness:" + "0" * 64, "owner": "different-owner"},
                )
            visible = (
                await client.get(
                    "/api/v1/mobile/agent/conversations/legacy-available", headers=headers
                )
            ).json()["conversations"]
            assert any(item["thread_id"] == thread for item in visible)
            assert all(item["thread_id"] != "fitness:" + "0" * 64 for item in visible)
            missing = await client.post(
                "/api/v1/mobile/agent/conversations/legacy-link",
                headers=headers,
                json={"thread_id": "fitness:" + "0" * 64},
            )
            assert missing.status_code == 404
            linked = await client.post(
                "/api/v1/mobile/agent/conversations/legacy-link",
                headers=headers,
                json={"thread_id": thread},
            )
            assert linked.status_code == 201
            linked_id = linked.json()["conversation_id"]
            repeated_link = await client.post(
                "/api/v1/mobile/agent/conversations/legacy-link",
                headers=headers,
                json={"thread_id": thread},
            )
            assert repeated_link.json()["conversation_id"] == linked_id
            concurrent = await asyncio.gather(
                *(
                    client.post(
                        "/api/v1/mobile/agent/conversations/legacy-link",
                        headers=headers,
                        json={"thread_id": thread},
                    )
                    for _ in range(6)
                )
            )
            assert {item.json()["conversation_id"] for item in concurrent} == {linked_id}
            available = (
                await client.get(
                    "/api/v1/mobile/agent/conversations/legacy-available", headers=headers
                )
            ).json()["conversations"]
            assert not any(item["thread_id"] == thread for item in available)
            linked_history = await client.get(
                f"/api/v1/mobile/agent/conversations/{linked_id}/messages", headers=headers
            )
            assert [x["source"] for x in linked_history.json()["messages"]] == ["legacy", "legacy"]
            linked_upload = await client.post(
                "/api/v1/mobile/agent/media/uploads",
                headers=headers,
                json={
                    "conversation_id": linked_id,
                    "idempotency_key": str(uuid4()),
                    "content_type": "image/png",
                    "size": len(bucket.data),
                },
            )
            assert linked_upload.status_code == 201
            uploaded = linked_upload.json()
            assert (
                await client.post(
                    f"/api/v1/mobile/agent/media/uploads/{uploaded['upload_id']}/complete",
                    headers=headers,
                )
            ).status_code == 200
            linked_run = {
                "conversation_id": linked_id,
                "run_id": str(uuid4()),
                "client_message_id": str(uuid4()),
                "message": "帮我看图片",
                "media_ids": [uploaded["asset_id"]],
            }
            assert (
                await client.post("/api/v1/mobile/agent/runs", headers=headers, json=linked_run)
            ).status_code == 202
            assert await worker.run_once() and captured[0]["history"][0]["content"] == "我练深蹲"
            captured.clear()
            assert (
                await client.delete(
                    f"/api/v1/mobile/agent/conversations/{linked_id}/legacy-link", headers=headers
                )
            ).json()["legacy_linked"] is False
            unlinked = await client.get(
                f"/api/v1/mobile/agent/conversations/{linked_id}/messages", headers=headers
            )
            assert all(x.get("source") != "legacy" for x in unlinked.json()["messages"])
            assert (
                await client.delete(
                    f"/api/v1/mobile/agent/conversations/{linked_id}", headers=headers
                )
            ).status_code == 202
            created = await client.post("/api/v1/mobile/agent/conversations", headers=headers)
            assert created.status_code == 201
            conversation = created.json()["conversation_id"]
            upload = await client.post(
                "/api/v1/mobile/agent/media/uploads",
                headers=headers,
                json={
                    "conversation_id": conversation,
                    "idempotency_key": str(uuid4()),
                    "content_type": "image/png",
                    "size": len(bucket.data),
                },
            )
            assert upload.status_code == 201 and upload.json()["upload"]["url"].startswith(
                "https://"
            )
            asset, upload_id = upload.json()["asset_id"], upload.json()["upload_id"]
            completed = await client.post(
                f"/api/v1/mobile/agent/media/uploads/{upload_id}/complete", headers=headers
            )
            assert completed.json() == {"status": "READY", "asset_id": asset}
            run_id = str(uuid4())
            body = {
                "conversation_id": conversation,
                "run_id": run_id,
                "client_message_id": str(uuid4()),
                "message": "动作标准吗？",
                "media_ids": [asset],
            }
            submitted = await client.post("/api/v1/mobile/agent/runs", headers=headers, json=body)
            assert submitted.status_code == 202 and submitted.json()["created"]
            repeated = await client.post("/api/v1/mobile/agent/runs", headers=headers, json=body)
            assert repeated.status_code == 202 and not repeated.json()["created"]
            assert await worker.run_once() and captured[0]["media"][0].asset_id == asset
            run = await client.get(f"/api/v1/mobile/agent/runs/{run_id}", headers=headers)
            assert (
                run.json()["status"] == "COMPLETED"
                and run.json()["response"]["report"]["summary"] == "画面不足"
            )
            history = await client.get(
                f"/api/v1/mobile/agent/conversations/{conversation}/messages", headers=headers
            )
            assert [item["role"] for item in history.json()["messages"]] == ["user", "assistant"]
            assert history.json()["messages"][0]["media"][0]["asset_id"] == asset
            followup = {
                **body,
                "run_id": str(uuid4()),
                "client_message_id": str(uuid4()),
                "message": "刚才哪里需要改？",
            }
            assert (
                await client.post("/api/v1/mobile/agent/runs", headers=headers, json=followup)
            ).status_code == 202
            assert await worker.run_once()
            assert captured[1]["history"] == [
                {"role": "user", "content": "动作标准吗？"},
                {"role": "assistant", "content": "画面不足"},
            ]
            async with db.engine.begin() as connection:
                await connection.execute(
                    text("""UPDATE agent_memory_subject_fences SET revision=revision+1
                    WHERE subject_user_id='user-1'""")
                )
            third = {
                **body,
                "run_id": str(uuid4()),
                "client_message_id": str(uuid4()),
                "message": "再看看",
            }
            assert (
                await client.post("/api/v1/mobile/agent/runs", headers=headers, json=third)
            ).status_code == 202
            assert await worker.run_once()
            assert captured[2]["history"] == []
            change_revision[0] = True
            fourth = {
                **body,
                "run_id": str(uuid4()),
                "client_message_id": str(uuid4()),
                "message": "再分析",
            }
            assert (
                await client.post("/api/v1/mobile/agent/runs", headers=headers, json=fourth)
            ).status_code == 202
            assert await worker.run_once()
            fenced = (
                await client.get(f"/api/v1/mobile/agent/runs/{fourth['run_id']}", headers=headers)
            ).json()
            assert fenced["status"] == "FAILED" and fenced["error_code"] == "MEMORY_CHANGED"
            assert fenced["response"] is None
            change_revision[0] = False
            knowledge = {
                **body,
                "run_id": str(uuid4()),
                "client_message_id": str(uuid4()),
                "message": "解读图片知识",
                "analysis_mode": "knowledge",
            }
            assert (
                await client.post("/api/v1/mobile/agent/runs", headers=headers, json=knowledge)
            ).status_code == 202
            assert await worker.run_once()
            knowledge_run = (
                await client.get(
                    f"/api/v1/mobile/agent/runs/{knowledge['run_id']}", headers=headers
                )
            ).json()
            assert knowledge_run["response"]["report"]["schema_version"] == "fitness-knowledge.v1"
            knowledge_messages = (
                await client.get(
                    f"/api/v1/mobile/agent/conversations/{conversation}/messages",
                    headers=headers,
                    params={"run_id": knowledge["run_id"]},
                )
            ).json()["messages"]
            assert knowledge_messages[-1]["content"]["key_points"][0]["text"] == "注意训练节奏"
            deleted = await client.delete(f"/api/v1/mobile/agent/media/{asset}", headers=headers)
            assert deleted.status_code == 202
            after = await client.get(f"/api/v1/mobile/agent/runs/{run_id}", headers=headers)
            assert after.json()["response"] == {"content_deleted": True}
            history = await client.get(
                f"/api/v1/mobile/agent/conversations/{conversation}/messages", headers=headers
            )
            assert history.json()["messages"] == []
            removed = await client.delete(
                f"/api/v1/mobile/agent/conversations/{conversation}", headers=headers
            )
            assert removed.status_code == 202
            assert (
                await client.get(
                    f"/api/v1/mobile/agent/conversations/{conversation}/messages", headers=headers
                )
            ).status_code == 404
            assert (await client.get("/api/v1/mobile/agent/conversations", headers=headers)).json()[
                "conversations"
            ] == []
    finally:
        await auth.upstream.close()
        await redis.aclose()
        await db.close()
