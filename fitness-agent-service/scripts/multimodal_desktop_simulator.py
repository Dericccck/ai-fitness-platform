"""Local-only integration simulator. Never use for real credentials or deployment.

Requires the isolated test PostgreSQL/Redis used by the multimodal tests. It
uses the real API, database, validation, and worker with in-memory object
storage and a clearly labelled no-model response.
"""

from __future__ import annotations

import asyncio
import io
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy.engine import make_url

from app.agent.movement_models import MovementReport
from app.api.routes.auth import auth_error
from app.api.routes.mobile_auth import router as auth_router
from app.api.routes.mobile_media import router as media_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.browser_auth import BrowserAuthError
from app.media.deletion import MediaDeletionRepository
from app.media.deletion_worker import MediaDeletionWorker
from app.media.repository import MediaRepository
from app.media.service import MediaService
from app.media.storage import MediaObjectNotFound, MediaStorageError, PostPolicy, StoredVersion
from app.streaming.durable_multimodal import DurableMultimodalRepository
from app.streaming.multimodal_worker import MultimodalWorker
from app.streaming.repository import ChatRunRepository
from tests.test_mobile_auth_redis import make_service
from tests.test_multimodal_dispatch_postgres import database

if os.getenv("FITNESS_DESKTOP_SIMULATOR") != "1":
    raise RuntimeError(
        "Simulator requires FITNESS_DESKTOP_SIMULATOR=1 and must only bind to loopback"
    )
_db = make_url(os.environ.get("AGENT_DATABASE_URL", ""))
if _db.host != "127.0.0.1" or _db.port != 57323 or _db.database != "fitness_multimodal_test":
    raise RuntimeError("Simulator requires the isolated localhost test database on port 57323")
if os.environ.get("AGENT_MOBILE_TEST_REDIS_URL") != "redis://127.0.0.1:57043/15":
    raise RuntimeError("Simulator requires the isolated localhost Redis on port 57043 database 15")


class LocalStorage:
    def __init__(self):
        self.objects = {}
        self.uploads = {}
        self.reads = {}

    def post_policy(self, key, *, max_bytes, content_type, ttl_seconds=900):
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        self.uploads[token] = (key, max_bytes, content_type, expires)
        return PostPolicy("http://127.0.0.1:8099/mock-oss", {"token": token}, expires)

    def head(self, key, *, version_id=None):
        item = self.objects.get(key)
        if item is None:
            raise MediaObjectNotFound("素材尚未上传")
        data, content_type, version = item
        if version_id is not None and version != version_id:
            raise MediaStorageError("素材版本不可用")
        return StoredVersion(key, version, len(data), content_type)

    def open_version(self, version):
        self.head(version.key, version_id=version.version_id)
        return io.BytesIO(self.objects[version.key][0])

    def copy_to_final(self, source, final_key):
        self.head(source.key, version_id=source.version_id)
        data, content_type, _ = self.objects[source.key]
        self.objects[final_key] = (data, content_type, secrets.token_hex(16))
        return self.head(final_key)

    def signed_read_url(self, version, *, ttl_seconds):
        self.head(version.key, version_id=version.version_id)
        token = secrets.token_urlsafe(32)
        self.reads[token] = (
            version.key,
            version.version_id,
            datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        return f"http://127.0.0.1:8099/mock-oss/read/{token}"

    def delete_version(self, key, version_id):
        item = self.objects.get(key)
        if item and item[2] == version_id:
            self.objects.pop(key, None)

    def delete_all_versions(self, key):
        self.objects.pop(key, None)


storage = LocalStorage()


class SimulatedAnalysis:
    async def analyze(self, **kwargs):
        refs = kwargs["media"]
        async with httpx.AsyncClient(timeout=10) as client:
            for ref in refs:
                response = await client.get(ref.url)
                response.raise_for_status()
                if not response.content:
                    raise ValueError("empty fixture")
        return MovementReport.model_validate(
            {
                "exercise": {"name": "", "identification": "UNCERTAIN"},
                "assessment": "INSUFFICIENT_EVIDENCE",
                "summary": "本机模拟：已收到并读取素材，但没有调用真实视觉模型，无法判断动作是否标准。",
                "limitations": ["这是联调模拟结果，不是健身动作评价。"],
                "source_assets": [
                    {"asset_id": ref.asset_id, "revision": ref.revision} for ref in refs
                ],
                "model_version": "desktop-simulator",
                "prompt_version": "desktop-v1",
                "knowledge_status": "EMPTY",
            }
        )


@asynccontextmanager
async def lifespan(app):
    db = database()
    auth, redis, _ = await make_service()
    cipher = AesGcmPayloadCipher(b"x" * 32, "v1")
    repo = DurableMultimodalRepository(db, cipher)
    media_repo = MediaRepository(db)
    runs = ChatRunRepository(db, cipher, timeout=60, retention_hours=1)

    async def cancel(run_id, owner, request_id):
        return await runs.request_cancel(run_id, owner, request_id)

    app.state.settings = SimpleNamespace(
        multimodal_api_enabled=True,
        multimodal_model="desktop-simulator",
        multimodal_prompt_version="desktop-v1",
        chat_rate_limit_enabled=False,
    )
    app.state.browser_auth = None
    app.state.mobile_auth = auth
    app.state.database = db
    app.state.confirmation_cipher = cipher
    app.state.multimodal_repository = repo
    app.state.media_service = MediaService(media_repo, storage)
    app.state.media_deletion = MediaDeletionRepository(db, cipher)
    app.state.chat_runs = SimpleNamespace(repository=runs, cancel=cancel)
    worker = MultimodalWorker(repo, media_repo, storage, SimulatedAnalysis(), auth.identity)
    deletion = MediaDeletionWorker(db, storage)

    async def poll():
        while True:
            await worker.run_once()
            await deletion.run_once()
            await asyncio.sleep(0.5)

    task = asyncio.create_task(poll())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await auth.upstream.close()
        await redis.aclose()
        await db.close()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8086", "http://127.0.0.1:8086"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_exception_handler(BrowserAuthError, auth_error)
app.include_router(auth_router)
app.include_router(media_router)


@app.post("/mock-oss", status_code=201)
async def upload(request: Request):
    form = await request.form()
    policy = storage.uploads.pop(str(form.get("token")), None)
    file = form.get("file")
    if not policy or file is None or not hasattr(file, "read"):
        raise HTTPException(403, "invalid local upload")
    key, maximum, content_type, expires = policy
    if expires <= datetime.now(UTC):
        raise HTTPException(403, "expired local upload")
    data = await file.read(maximum + 1)
    if not data or len(data) > maximum:
        raise HTTPException(400, "invalid local upload size")
    storage.objects[key] = (data, content_type, secrets.token_hex(16))
    return Response(status_code=201)


@app.get("/mock-oss/read/{token}")
async def read(token: str):
    policy = storage.reads.get(token)
    if not policy:
        raise HTTPException(404)
    key, version, expires = policy
    if expires <= datetime.now(UTC):
        raise HTTPException(404)
    try:
        item = storage.head(key, version_id=version)
    except MediaStorageError:
        raise HTTPException(404) from None
    return Response(storage.objects[key][0], media_type=item.content_type)
