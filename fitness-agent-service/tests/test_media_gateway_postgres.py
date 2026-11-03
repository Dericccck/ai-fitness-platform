"""Isolated DB tests for the one-shot authenticated media gateway."""

from __future__ import annotations

import asyncio
import io
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import text

from app.api.routes import mobile_media
from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.media.repository import MediaRepository
from app.media.service import MediaService
from app.media.storage import MediaStorageError
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import DurableMultimodalRepository
from tests.test_media_service_postgres import FakeBucket, make_storage
from tests.test_multimodal_dispatch_postgres import database

pytestmark = pytest.mark.skipif(
    os.getenv("AGENT_RUN_MULTIMODAL_PG_TESTS") != "1", reason="isolated DB only"
)


async def test_gateway_one_write_and_no_direct_policy(monkeypatch):
    db = database()
    identity = AgentIdentity(
        "gateway-" + uuid4().hex, frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation = str(uuid4())
    repository = MediaRepository(db)
    writes = []
    actor = [identity]

    class Storage:
        fail = False

        def put_gateway_upload(self, key, stream, *, size, content_type):
            writes.append((size, content_type, stream.read()))
            time.sleep(0.05)
            if self.fail:
                raise MediaStorageError("unknown")

    async def auth(request, response):
        return "test-session", actor[0]

    async def complete(user, upload_id):
        return SimpleNamespace(asset_id="ready-asset")

    monkeypatch.setattr(mobile_media, "_auth", auth)
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        multimodal_upload_mode="gateway",
        multimodal_gateway_upload_timeout_seconds=30,
        chat_rate_limit_enabled=False,
    )
    app.state.media_gateway_semaphore = asyncio.Semaphore(2)
    app.state.media_service = SimpleNamespace(
        repository=repository, storage=Storage(), complete=complete
    )
    app.include_router(mobile_media.router)
    try:
        await DurableMultimodalRepository(
            db, AesGcmPayloadCipher(b"x" * 32, "v1")
        ).create_conversation(identity, conversation)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            initial = await client.post(
                "/api/v1/mobile/agent/media/uploads",
                json={
                    "conversation_id": conversation,
                    "idempotency_key": str(uuid4()),
                    "content_type": "image/png",
                    "size": 4,
                },
            )
            assert initial.status_code == 201
            session = initial.json()
            assert session["upload_mode"] == "gateway" and session["upload"] is None
            path = f"/api/v1/mobile/agent/media/uploads/{session['upload_id']}/content"
            actor[0] = AgentIdentity(
                "other-user", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
            )
            assert (
                await client.put(path, content=b"data", headers={"Content-Type": "image/png"})
            ).status_code == 404
            assert not writes
            actor[0] = identity
            bad = await client.put(path, content=b"abc", headers={"Content-Type": "image/png"})
            assert bad.status_code == 422 and not writes
            responses = await asyncio.gather(
                *[
                    client.put(path, content=b"data", headers={"Content-Type": "image/png"})
                    for _ in range(2)
                ]
            )
            assert sorted(item.status_code for item in responses) == [200, 409]
            assert writes == [(4, "image/png", b"data")]
            assert (
                await client.put(path, content=b"data", headers={"Content-Type": "image/png"})
            ).status_code == 409
            session2 = (
                await client.post(
                    "/api/v1/mobile/agent/media/uploads",
                    json={
                        "conversation_id": conversation,
                        "idempotency_key": str(uuid4()),
                        "content_type": "image/png",
                        "size": 4,
                    },
                )
            ).json()
            path2 = f"/api/v1/mobile/agent/media/uploads/{session2['upload_id']}/content"
            app.state.media_service.storage.fail = True
            assert (
                await client.put(path2, content=b"data", headers={"Content-Type": "image/png"})
            ).status_code == 503
            assert (
                await client.put(path2, content=b"data", headers={"Content-Type": "image/png"})
            ).status_code == 409
            assert len(writes) == 2
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("""DELETE FROM agent_media_gateway_attempts WHERE owner_hash=:owner"""),
                {"owner": run_owner(identity)},
            )
            await connection.execute(
                text("""DELETE FROM agent_media_uploads WHERE owner_hash=:owner"""),
                {"owner": run_owner(identity)},
            )
            await connection.execute(
                text("""DELETE FROM agent_media_assets WHERE owner_hash=:owner"""),
                {"owner": run_owner(identity)},
            )
            await connection.execute(
                text("""DELETE FROM agent_chat_conversations WHERE owner_hash=:owner"""),
                {"owner": run_owner(identity)},
            )
        await db.close()


async def test_gateway_upload_validates_and_finalizes_real_service(monkeypatch):
    db = database()
    identity = AgentIdentity(
        "gateway-real-" + uuid4().hex, frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999
    )
    conversation = str(uuid4())
    image = io.BytesIO()
    Image.new("RGB", (8, 6), "red").save(image, format="PNG")
    data = image.getvalue()

    class Bucket(FakeBucket):
        def put_object(self, key, stream, headers):
            assert headers == {"Content-Type": "image/png"}
            assert stream.read() == data
            return SimpleNamespace(headers={"x-oss-version-id": "source-v1"})

    bucket = Bucket(data)
    repo = MediaRepository(db)

    async def auth(request, response):
        return "session", identity

    monkeypatch.setattr(mobile_media, "_auth", auth)
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        multimodal_upload_mode="gateway",
        multimodal_gateway_upload_timeout_seconds=30,
        chat_rate_limit_enabled=False,
    )
    app.state.media_gateway_semaphore = asyncio.Semaphore(2)
    app.state.media_service = MediaService(repo, make_storage(bucket))
    app.include_router(mobile_media.router)
    try:
        await DurableMultimodalRepository(
            db, AesGcmPayloadCipher(b"x" * 32, "v1")
        ).create_conversation(identity, conversation)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            session = (
                await client.post(
                    "/api/v1/mobile/agent/media/uploads",
                    json={
                        "conversation_id": conversation,
                        "idempotency_key": str(uuid4()),
                        "content_type": "image/png",
                        "size": len(data),
                    },
                )
            ).json()
            path = f"/api/v1/mobile/agent/media/uploads/{session['upload_id']}/content"
            response = await client.put(path, content=data, headers={"Content-Type": "image/png"})
            assert response.status_code == 200 and response.json()["status"] == "READY"
            assert (await repo.get_upload(identity, session["upload_id"])).status == "READY"
            assert len(bucket.copied) == 1
    finally:
        async with db.engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM agent_media_gateway_attempts WHERE owner_hash=:owner"),
                {"owner": run_owner(identity)},
            )
            await connection.execute(
                text("DELETE FROM agent_media_uploads WHERE owner_hash=:owner"),
                {"owner": run_owner(identity)},
            )
            await connection.execute(
                text("DELETE FROM agent_media_assets WHERE owner_hash=:owner"),
                {"owner": run_owner(identity)},
            )
            await connection.execute(
                text("DELETE FROM agent_chat_conversations WHERE owner_hash=:owner"),
                {"owner": run_owner(identity)},
            )
        await db.close()
