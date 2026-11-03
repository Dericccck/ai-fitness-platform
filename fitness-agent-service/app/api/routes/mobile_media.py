"""移动端媒体与任务 API；身份、私有存储及模型密钥始终在服务端。"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, bindparam, text
from starlette.responses import StreamingResponse

from app.api.chat_protection import enforce_chat_quota
from app.api.routes.mobile_auth import bearer
from app.api.routes.mobile_auth import service as auth_service
from app.infrastructure.agent_context import AgentIdentity, conversation_thread_id
from app.media.metadata import MediaInspectionUnavailable, MediaValidationError
from app.media.repository import MediaAccessError, MediaQuotaExceeded, MediaTransferAlreadyStarted
from app.media.storage import MediaObjectNotFound, MediaStorageError, StoredVersion
from app.streaming.control import run_owner
from app.streaming.durable_multimodal import DurableAccessError
from app.streaming.legacy_bridge import LegacyConversationUnavailable
from app.streaming.repository import RunConflict, RunSessionBusy

router = APIRouter(prefix="/api/v1/mobile/agent", tags=["mobile-multimodal"])


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadInput(StrictInput):
    conversation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    idempotency_key: UUID
    content_type: str = Field(min_length=3, max_length=100)
    size: int = Field(gt=0, le=100 * 1024 * 1024)


class RunInput(StrictInput):
    conversation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    run_id: UUID
    client_message_id: UUID
    message: str = Field(min_length=1, max_length=4000)
    media_ids: list[UUID] = Field(min_length=1, max_length=3)
    exercise_hint: str = Field(default="", max_length=100)


class LinkLegacyInput(StrictInput):
    thread_id: str = Field(pattern=r"^fitness:[0-9a-f]{64}$")


class CancelInput(StrictInput):
    cancel_request_id: UUID


async def _auth(request: Request, response: Response) -> tuple[str, AgentIdentity]:
    response.headers["Cache-Control"] = "no-store"
    if not getattr(request.app.state.settings, "multimodal_api_enabled", False):
        raise HTTPException(503, "动作分析服务尚未启用")
    return await auth_service(request).authenticate(bearer(request))


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, MediaQuotaExceeded):
        return HTTPException(429, str(exc))
    if isinstance(exc, MediaTransferAlreadyStarted):
        return HTTPException(409, str(exc))
    if isinstance(exc, MediaObjectNotFound):
        return HTTPException(404, "暂存素材尚未上传")
    if isinstance(exc, (MediaAccessError, DurableAccessError)):
        return HTTPException(404, "会话或素材不存在、不可访问或已过期")
    if isinstance(exc, (RunConflict, RunSessionBusy)):
        return HTTPException(409, str(exc))
    if isinstance(exc, (MediaValidationError, ValueError)):
        return HTTPException(422, str(exc))
    if isinstance(exc, (MediaStorageError, MediaInspectionUnavailable)):
        return HTTPException(503, "素材服务暂时不可用，请稍后重试")
    return HTTPException(503, "动作分析服务暂时不可用")


@router.get("/capabilities")
async def capabilities(request: Request, response: Response) -> dict[str, Any]:
    await _auth(request, response)
    return {
        "image_types": ["image/jpeg", "image/png", "image/webp"],
        "video_types": ["video/mp4", "video/quicktime"],
        "max_images": 3,
        "max_image_bytes": 10 * 1024 * 1024,
        "max_video_bytes": 100 * 1024 * 1024,
        "video_min_seconds": 2,
        "video_max_seconds": 120,
        "audio_analysis": False,
    }


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request, response: Response) -> dict[str, str]:
    _, identity = await _auth(request, response)
    await enforce_chat_quota(request, identity)
    conversation_id = str(uuid4())
    await request.app.state.multimodal_repository.create_conversation(identity, conversation_id)
    return {"conversation_id": conversation_id}


@router.get("/conversations/legacy-available")
async def available_legacy_conversations(request: Request, response: Response) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    if getattr(request.app.state, "legacy_bridge", None) is None:
        raise HTTPException(503, "历史对话服务暂不可用")
    async with request.app.state.database.engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text("""SELECT h.thread_id,h.updated_at
            FROM agent_chat_history_commits h WHERE h.owner_hash=:owner AND h.subject_user_id=:subject
              AND h.thread_id ~ '^fitness:[0-9a-f]{64}$' AND NOT EXISTS
                (SELECT 1 FROM agent_chat_conversations c WHERE c.owner_hash=h.owner_hash
                 AND c.legacy_thread_id=h.thread_id AND c.deleted_at IS NULL)
            ORDER BY h.updated_at DESC LIMIT 100"""),
                    {"owner": run_owner(identity), "subject": identity.subject},
                )
            )
            .mappings()
            .all()
        )
    return {
        "conversations": [
            {"thread_id": str(row["thread_id"]), "updated_at": row["updated_at"].isoformat()}
            for row in rows
        ]
    }


@router.post("/conversations/legacy-link", status_code=201)
async def link_legacy_conversation(
    payload: LinkLegacyInput, request: Request, response: Response
) -> dict[str, str]:
    _, identity = await _auth(request, response)
    await enforce_chat_quota(request, identity)
    bridge = getattr(request.app.state, "legacy_bridge", None)
    if bridge is None:
        raise HTTPException(503, "历史对话服务暂不可用")
    try:
        await bridge.history_by_thread(identity, payload.thread_id, memory_revision=None)
        repo = request.app.state.multimodal_repository
        conversation_id = await repo.link_legacy_thread(identity, payload.thread_id)
    except LegacyConversationUnavailable:
        raise HTTPException(404, "未找到可关联的历史对话") from None
    except DurableAccessError as exc:
        raise _error(exc) from exc
    return {"conversation_id": conversation_id}


@router.delete("/conversations/{conversation_id}/legacy-link")
async def unlink_legacy_conversation(
    conversation_id: str, request: Request, response: Response
) -> dict[str, bool]:
    _, identity = await _auth(request, response)
    try:
        await request.app.state.multimodal_repository.set_legacy_link(
            identity, conversation_id, False
        )
    except (DurableAccessError, ValueError) as exc:
        raise _error(exc) from exc
    return {"legacy_linked": False}


@router.get("/conversations")
async def list_conversations(
    request: Request, response: Response, before: str | None = None
) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    if before is not None and not 1 <= len(before) <= 128:
        raise HTTPException(422, "无效分页标识")
    async with request.app.state.database.engine.connect() as connection:
        owner = run_owner(identity)
        if before:
            anchor = (
                await connection.execute(
                    text(
                        "SELECT updated_at FROM agent_chat_conversations WHERE conversation_id=:id AND owner_hash=:owner"
                    ),
                    {"id": before, "owner": owner},
                )
            ).scalar_one_or_none()
            if anchor is None:
                raise HTTPException(422, "分页标识已失效，请刷新")
        else:
            anchor = None
        rows = (
            (
                await connection.execute(
                    text("""SELECT conversation_id,created_at,updated_at,legacy_linked FROM agent_chat_conversations
              WHERE owner_hash=:owner AND deleted_at IS NULL
                AND (CAST(:anchor AS timestamptz) IS NULL OR (updated_at,conversation_id) < (:anchor,:before))
              ORDER BY updated_at DESC,conversation_id DESC LIMIT 101"""),
                    {"owner": owner, "anchor": anchor, "before": before},
                )
            )
            .mappings()
            .all()
        )
    has_more = len(rows) > 100
    rows = rows[:100]
    return {
        "next_cursor": str(rows[-1]["conversation_id"]) if has_more else None,
        "conversations": [
            {
                "conversation_id": row["conversation_id"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
                "legacy_linked": bool(row["legacy_linked"]),
            }
            for row in rows
        ],
    }


@router.delete("/conversations/{conversation_id}", status_code=202)
async def delete_conversation(
    conversation_id: str, request: Request, response: Response
) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    if not 1 <= len(conversation_id) <= 128:
        raise HTTPException(404, "会话不存在或无法访问")
    try:
        count = await request.app.state.media_deletion.request_conversation(
            identity, conversation_id
        )
    except MediaAccessError as exc:
        raise _error(exc) from exc
    return {"status": "PENDING", "media_count": count}


@router.post("/media/uploads", status_code=201)
async def create_upload(
    payload: UploadInput, request: Request, response: Response
) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    await enforce_chat_quota(request, identity)
    try:
        service = request.app.state.media_service
        mode = getattr(request.app.state.settings, "multimodal_upload_mode", "direct")
        if mode == "gateway":
            from app.media.service import UploadResult

            record = await service.repository.create_upload(
                identity=identity,
                conversation_id=payload.conversation_id,
                idempotency_key=str(payload.idempotency_key),
                content_type=payload.content_type,
                size=payload.size,
            )
            result = UploadResult(record, None)
        else:
            result = await service.initiate(
                identity=identity,
                conversation_id=payload.conversation_id,
                idempotency_key=str(payload.idempotency_key),
                content_type=payload.content_type,
                size=payload.size,
            )
    except (
        MediaAccessError,
        MediaQuotaExceeded,
        MediaStorageError,
        RunConflict,
        ValueError,
    ) as exc:
        raise _error(exc) from exc
    policy = result.policy
    return {
        "upload_id": result.record.upload_id,
        "asset_id": result.record.asset_id,
        "status": result.record.status,
        "upload_mode": mode,
        "upload": {
            "url": policy.host,
            "form_data": policy.form_data,
            "file_field_name": policy.file_field_name,
            "expires_at": policy.expires_at.isoformat(),
        }
        if policy
        else None,
    }


@router.put("/media/uploads/{upload_id}/content")
async def gateway_upload(upload_id: UUID, request: Request, response: Response) -> dict[str, Any]:
    """Receive a bounded body once; the application alone holds OSS write credentials."""
    _, identity = await _auth(request, response)
    settings = request.app.state.settings
    if getattr(settings, "multimodal_upload_mode", "direct") != "gateway":
        raise HTTPException(404, "上传方式不可用")
    await enforce_chat_quota(request, identity)
    service = request.app.state.media_service
    try:
        current = await service.repository.get_upload(identity, str(upload_id))
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != current.content_type:
            raise HTTPException(415, "上传类型与素材声明不一致")
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) != current.expected_size):
            raise HTTPException(422, "上传大小与素材声明不一致")
        semaphore = request.app.state.media_gateway_semaphore
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=1)
        except TimeoutError:
            raise HTTPException(503, "上传服务繁忙，请稍后重试") from None
        try:
            record = await service.repository.claim_gateway_transfer(identity, str(upload_id))
            with tempfile.TemporaryFile() as temporary:
                count = 0
                try:
                    async with asyncio.timeout(settings.multimodal_gateway_upload_timeout_seconds):
                        async for chunk in request.stream():
                            count += len(chunk)
                            if count > record.expected_size:
                                raise HTTPException(413, "上传内容超过声明大小")
                            temporary.write(chunk)
                except TimeoutError:
                    raise HTTPException(408, "上传超时，请新建素材后重试") from None
                if count != record.expected_size:
                    raise HTTPException(422, "上传内容不完整，请新建素材后重试")
                temporary.seek(0)
                transfer = asyncio.create_task(
                    asyncio.to_thread(
                        service.storage.put_gateway_upload,
                        record.staging_key,
                        temporary,
                        size=record.expected_size,
                        content_type=record.content_type,
                    )
                )
                try:
                    await asyncio.shield(transfer)
                except asyncio.CancelledError:
                    # Keep the temporary file alive until the underlying storage write finishes.
                    await asyncio.shield(transfer)
                    raise
            asset = await service.complete(identity, str(upload_id))
            return {
                "status": "READY" if asset else "VERIFYING",
                "asset_id": asset.asset_id if asset else None,
            }
        finally:
            semaphore.release()
    except (
        MediaAccessError,
        MediaTransferAlreadyStarted,
        MediaStorageError,
        MediaValidationError,
        MediaInspectionUnavailable,
    ) as exc:
        raise _error(exc) from exc


@router.get("/media/uploads/{upload_id}")
async def upload_status(upload_id: UUID, request: Request, response: Response) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    try:
        record = await request.app.state.media_service.repository.get_upload(
            identity, str(upload_id)
        )
    except MediaAccessError as exc:
        raise _error(exc) from exc
    return {"upload_id": record.upload_id, "asset_id": record.asset_id, "status": record.status}


@router.post("/media/uploads/{upload_id}/complete")
async def complete_upload(upload_id: UUID, request: Request, response: Response) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    try:
        asset = await request.app.state.media_service.complete(identity, str(upload_id))
    except (
        MediaValidationError,
        MediaStorageError,
        MediaAccessError,
        MediaInspectionUnavailable,
    ) as exc:
        raise _error(exc) from exc
    return {
        "status": "READY" if asset else "VERIFYING",
        "asset_id": asset.asset_id if asset else None,
    }


@router.post("/media/{asset_id}/preview")
async def preview(asset_id: UUID, request: Request, response: Response) -> dict[str, str]:
    _, identity = await _auth(request, response)
    try:
        if getattr(request.app.state.settings, "multimodal_upload_mode", "direct") == "gateway":
            await request.app.state.media_service.repository.ready_asset(identity, str(asset_id))
            return {"mode": "gateway", "url": f"/api/v1/mobile/agent/media/{asset_id}/content"}
        url = await request.app.state.media_service.signed_preview(identity, str(asset_id))
    except (MediaAccessError, MediaStorageError) as exc:
        raise _error(exc) from exc
    return {"mode": "direct", "url": url}


def _preview_range(value: str | None, size: int) -> tuple[int, int, bool]:
    if value is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", value)
    if not match or not any(match.groups()):
        raise ValueError("无效读取范围")
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("无效读取范围")
        return max(0, size - suffix), size - 1, True
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ValueError("无效读取范围")
    return start, end, True


@router.get("/media/{asset_id}/content")
async def preview_content(
    asset_id: UUID, request: Request, response: Response
) -> StreamingResponse:
    ref, identity = await _auth(request, response)
    if getattr(request.app.state.settings, "multimodal_upload_mode", "direct") != "gateway":
        raise HTTPException(404, "预览方式不可用")
    service = request.app.state.media_service
    try:
        asset = await service.repository.ready_asset(identity, str(asset_id))
        try:
            start, end, partial = _preview_range(request.headers.get("range"), asset.size)
        except ValueError:
            raise HTTPException(
                416, "无效读取范围", headers={"Content-Range": f"bytes */{asset.size}"}
            ) from None
        version = StoredVersion(
            asset.final_key, asset.final_version, asset.size, asset.content_type
        )
        source = await asyncio.to_thread(service.storage.open_version_range, version, start, end)
    except (MediaAccessError, MediaStorageError) as exc:
        raise _error(exc) from exc

    async def body():
        remaining = end - start + 1
        try:
            while remaining:
                current_identity = await auth_service(request).identity(ref)
                if current_identity is None or run_owner(current_identity) != run_owner(identity):
                    return
                try:
                    current = await service.repository.ready_asset(current_identity, str(asset_id))
                except MediaAccessError:
                    return
                if (
                    current.revision != asset.revision
                    or current.final_version != asset.final_version
                ):
                    return
                chunk = await asyncio.to_thread(source.read, min(1024 * 1024, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(source.close)

    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
    }
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{asset.size}"
    return StreamingResponse(
        body(), status_code=206 if partial else 200, media_type=asset.content_type, headers=headers
    )


@router.delete("/media/{asset_id}", status_code=202)
async def delete_media(asset_id: UUID, request: Request, response: Response) -> dict[str, str]:
    _, identity = await _auth(request, response)
    try:
        deletion_id = await request.app.state.media_deletion.request(identity, str(asset_id))
    except MediaAccessError as exc:
        raise _error(exc) from exc
    return {"deletion_id": deletion_id, "status": "PENDING"}


@router.get("/deletions/{deletion_id}")
async def deletion_status(
    deletion_id: UUID, request: Request, response: Response
) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    async with request.app.state.database.engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("""SELECT status,cleared_once FROM agent_media_deletions
              WHERE deletion_id=:id AND owner_hash=:owner"""),
                    {"id": str(deletion_id), "owner": run_owner(identity)},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(404, "删除任务不存在")
    return {
        "deletion_id": str(deletion_id),
        "status": row["status"],
        "cleared_once": row["cleared_once"],
    }


@router.post("/runs", status_code=202)
async def submit_run(payload: RunInput, request: Request, response: Response) -> dict[str, Any]:
    ref, identity = await _auth(request, response)
    await enforce_chat_quota(request, identity)
    try:
        result = await request.app.state.multimodal_repository.submit(
            identity=identity,
            conversation_id=payload.conversation_id,
            run_id=str(payload.run_id),
            client_message_id=str(payload.client_message_id),
            question=payload.message,
            asset_ids=[str(item) for item in payload.media_ids],
            auth_session_ref=ref,
            model=request.app.state.settings.multimodal_model,
            prompt_version=request.app.state.settings.multimodal_prompt_version,
            exercise_hint=payload.exercise_hint,
        )
    except (DurableAccessError, RunConflict, RunSessionBusy, ValueError) as exc:
        raise _error(exc) from exc
    return {
        "run_id": result.run_id,
        "message_id": result.message_id,
        "created": result.created,
        "content_deleted": result.content_deleted,
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, request: Request, response: Response) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    owner = run_owner(identity)
    async with request.app.state.database.engine.begin() as connection:
        row = (
            (
                await connection.execute(
                    text("""SELECT r.* FROM agent_chat_runs r
                  JOIN agent_chat_messages m ON m.run_id=r.run_id AND m.role='user'
                  JOIN agent_chat_conversations c ON c.conversation_id=m.conversation_id
                  WHERE r.run_id=:id AND r.owner_hash=:owner AND r.executor_kind='MULTIMODAL'
                    AND r.expires_at>CURRENT_TIMESTAMP AND c.owner_hash=:owner AND c.deleted_at IS NULL
                  FOR SHARE OF c"""),
                    {"id": str(run_id), "owner": owner},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise HTTPException(404, "任务不存在或已过期")
        run = request.app.state.chat_runs.repository._decode(dict(row))
        return {
            "run_id": run.run_id,
            "status": run.status,
            "response": run.result,
            "error_code": run.error_code,
            "stop_reason": run.stop_reason,
        }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: UUID, payload: CancelInput, request: Request, response: Response
) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    run = await request.app.state.chat_runs.cancel(
        str(run_id), run_owner(identity), str(payload.cancel_request_id)
    )
    if run is None:
        raise HTTPException(404, "任务不存在或已过期")
    return {"run_id": run.run_id, "status": run.status}


@router.get("/conversations/{conversation_id}/messages")
async def messages(
    conversation_id: str,
    request: Request,
    response: Response,
    before: str | None = None,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    _, identity = await _auth(request, response)
    if before is not None and (not 1 <= len(before) <= 128 or run_id is not None):
        raise HTTPException(422, "无效分页标识")
    owner = run_owner(identity)
    async with request.app.state.database.engine.begin() as connection:
        exists = (
            await connection.execute(
                text("""SELECT legacy_linked,legacy_thread_id FROM agent_chat_conversations WHERE conversation_id=:id
              AND owner_hash=:owner AND deleted_at IS NULL FOR SHARE"""),
                {"id": conversation_id, "owner": owner},
            )
        ).first()
        if exists is None:
            raise HTTPException(404, "会话不存在或无法访问")
        anchor = None
        if before:
            anchor = (
                (
                    await connection.execute(
                        text("""SELECT created_at,role,message_id
                FROM agent_chat_messages WHERE message_id=:before AND conversation_id=:id AND owner_hash=:owner"""),
                        {"before": before, "id": conversation_id, "owner": owner},
                    )
                )
                .mappings()
                .first()
            )
            if anchor is None:
                raise HTTPException(422, "分页标识已失效，请刷新")
        rows = (
            (
                await connection.execute(
                    text("""SELECT message_id,run_id,role,status,content_ciphertext,key_version,schema_version,created_at
              FROM agent_chat_messages WHERE conversation_id=:id AND owner_hash=:owner AND deleted_at IS NULL
                AND (CAST(:run AS text) IS NULL OR run_id=:run)
                AND (CAST(:anchor_time AS timestamptz) IS NULL OR (created_at,CASE role WHEN 'user' THEN 0 ELSE 1 END,message_id)
                    < (:anchor_time,:anchor_role,:before))
              ORDER BY created_at DESC,CASE role WHEN 'user' THEN 0 ELSE 1 END DESC,message_id DESC LIMIT 201"""),
                    {
                        "id": conversation_id,
                        "owner": owner,
                        "run": str(run_id) if run_id else None,
                        "anchor_time": anchor["created_at"] if anchor else None,
                        "anchor_role": (0 if anchor["role"] == "user" else 1) if anchor else None,
                        "before": before,
                    },
                )
            )
            .mappings()
            .all()
        )
        has_more = len(rows) > 200
        rows = list(reversed(rows[:200]))
        next_cursor = str(rows[0]["message_id"]) if has_more else None
        media = (
            (
                await connection.execute(
                    text("""SELECT mm.message_id,a.asset_id,a.kind,a.content_type,a.status
              FROM agent_chat_message_media mm JOIN agent_chat_messages m ON m.message_id=mm.message_id
              JOIN agent_media_assets a ON a.asset_id=mm.asset_id
              WHERE m.conversation_id=:id AND m.owner_hash=:owner AND a.owner_hash=:owner
                AND m.deleted_at IS NULL AND a.deleted_at IS NULL
                AND mm.dependency_type IN ('ATTACHMENT','ANALYSIS')
                AND m.message_id IN :message_ids ORDER BY mm.ordinal""").bindparams(
                        bindparam("message_ids", expanding=True, type_=String())
                    ),
                    {
                        "id": conversation_id,
                        "owner": owner,
                        "message_ids": [str(row["message_id"]) for row in rows],
                    },
                )
            )
            .mappings()
            .all()
        )
        grouped: dict[str, list[dict[str, str]]] = {}
        for item in media:
            grouped.setdefault(str(item["message_id"]), []).append(
                {
                    "asset_id": str(item["asset_id"]),
                    "kind": str(item["kind"]),
                    "content_type": str(item["content_type"]),
                    "status": str(item["status"]),
                }
            )
        result = []
        for row in rows:
            content: Any = None
            if row["content_ciphertext"] is not None:
                plain = request.app.state.confirmation_cipher.decrypt(
                    bytes(row["content_ciphertext"]),
                    associated_data=f"chat-message:{row['message_id']}:{owner}",
                    key_version=row["key_version"],
                ).decode()
                content = json.loads(plain) if row["role"] == "assistant" else plain
            result.append(
                {
                    "message_id": row["message_id"],
                    "run_id": row["run_id"],
                    "role": row["role"],
                    "status": row["status"],
                    "content": content,
                    "schema_version": row["schema_version"],
                    "created_at": row["created_at"].isoformat(),
                    "media": grouped.get(str(row["message_id"]), []),
                }
            )
        if bool(exists[0]) and not has_more and run_id is None:
            bridge = getattr(request.app.state, "legacy_bridge", None)
            if bridge is None:
                raise HTTPException(503, "历史对话服务暂不可用")
            try:
                await connection.execute(
                    text("""INSERT INTO agent_memory_subject_fences(subject_user_id)
                        VALUES (:subject) ON CONFLICT DO NOTHING"""),
                    {"subject": identity.subject},
                )
                revision = int(
                    (
                        await connection.execute(
                            text("""SELECT revision FROM agent_memory_subject_fences
                        WHERE subject_user_id=:subject FOR SHARE"""),
                            {"subject": identity.subject},
                        )
                    ).scalar_one()
                )
                thread = (
                    str(exists[1])
                    if exists[1]
                    else conversation_thread_id(conversation_id, identity)
                )
                legacy = await bridge.history_by_thread(
                    identity, thread, memory_revision=revision, connection=connection
                )
            except LegacyConversationUnavailable:
                raise HTTPException(503, "历史对话暂不可用，可解除关联后继续") from None
            result = [
                {
                    "message_id": f"legacy-{index}",
                    "run_id": "legacy",
                    "role": item["role"],
                    "status": "COMMITTED",
                    "content": item["content"],
                    "schema_version": "legacy-text.v1",
                    "created_at": None,
                    "media": [],
                    "source": "legacy",
                }
                for index, item in enumerate(legacy)
            ] + result
        return {"conversation_id": conversation_id, "messages": result, "next_cursor": next_cursor}
