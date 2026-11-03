"""用户媒体元数据仓储；上传幂等及验证租约均以 PostgreSQL 为准。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.media.metadata import MediaMetadata
from app.streaming.control import run_owner
from app.streaming.repository import RunConflict, request_digest


class MediaAccessError(RuntimeError):
    """不存在或不属于当前授权范围。"""


class MediaQuotaExceeded(RuntimeError):
    """The account has reached the configured logical media quota."""


class MediaTransferAlreadyStarted(RuntimeError):
    """The one-shot transfer was previously claimed; do not replay it."""


@dataclass(frozen=True)
class UploadRecord:
    upload_id: str
    asset_id: str
    conversation_id: str
    status: str
    content_type: str
    expected_size: int
    staging_key: str = field(repr=False)
    source_version: str | None = field(default=None, repr=False)
    verification_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ReadyAsset:
    asset_id: str
    revision: int
    kind: str
    content_type: str
    size: int
    duration_seconds: float | None
    final_key: str = field(repr=False)
    final_version: str = field(repr=False)


_TYPES = {
    "image/jpeg": ("image", 10 * 1024 * 1024),
    "image/png": ("image", 10 * 1024 * 1024),
    "image/webp": ("image", 10 * 1024 * 1024),
    "video/mp4": ("video", 100 * 1024 * 1024),
    "video/quicktime": ("video", 100 * 1024 * 1024),
}


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("标识必须是 UUID") from exc


def _upload(row: Mapping[Any, Any]) -> UploadRecord:
    values = dict(row)
    return UploadRecord(
        upload_id=str(values["upload_id"]),
        asset_id=str(values["asset_id"]),
        conversation_id=str(values["conversation_id"]),
        status=str(values["status"]),
        content_type=str(values["content_type"]),
        expected_size=int(values["size_bytes"]),
        staging_key=str(values["staging_key"]),
        source_version=values["source_version"],
        verification_token=values["verification_token"],
        expires_at=values["expires_at"],
    )


class MediaRepository:
    def __init__(
        self, database: Database, *, max_assets: int = 200, max_bytes: int = 2 * 1024**3
    ) -> None:
        if max_assets < 1 or max_bytes < 1:
            raise ValueError("媒体配额必须为正数")
        self.database = database
        self.max_assets = max_assets
        self.max_bytes = max_bytes

    async def _lock_upload_asset(self, connection: AsyncConnection, upload_id: str) -> None:
        """与发布、删除保持会话→素材→上传记录的锁顺序。"""
        await connection.execute(
            text("""SELECT c.conversation_id FROM agent_chat_conversations c
              JOIN agent_media_assets a ON a.conversation_id=c.conversation_id
              JOIN agent_media_uploads u ON u.asset_id=a.asset_id
              WHERE u.upload_id=:id FOR UPDATE OF c"""),
            {"id": upload_id},
        )
        await connection.execute(
            text("""SELECT a.asset_id FROM agent_media_assets a
              JOIN agent_media_uploads u ON u.asset_id=a.asset_id
              WHERE u.upload_id=:id FOR UPDATE OF a"""),
            {"id": upload_id},
        )

    async def create_upload(
        self,
        *,
        identity: AgentIdentity,
        conversation_id: str,
        idempotency_key: str,
        content_type: str,
        size: int,
    ) -> UploadRecord:
        key = _uuid(idempotency_key)
        if content_type not in _TYPES or not 0 < size <= _TYPES[content_type][1]:
            raise ValueError("媒体类型或大小不受支持")
        owner = run_owner(identity)
        digest = request_digest(
            {"conversation_id": conversation_id, "content_type": content_type, "size": size}
        )
        async with self.database.engine.begin() as connection:
            # 不同会话可能复用幂等键，按用户+key序列化，避免资产行孤儿。
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"media-upload:{owner}:{key}"},
            )
            existing = (
                (
                    await connection.execute(
                        text("""
                SELECT u.*,a.conversation_id,a.content_type,a.size_bytes,
                  a.deleted_at AS asset_deleted_at,c.deleted_at AS conversation_deleted_at
                FROM agent_media_uploads u JOIN agent_media_assets a ON a.asset_id=u.asset_id
                JOIN agent_chat_conversations c ON c.conversation_id=a.conversation_id
                WHERE u.owner_hash=:owner AND u.idempotency_key=:key
            """),
                        {"owner": owner, "key": key},
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["request_digest"] != digest:
                    raise RunConflict("上传标识已绑定其他文件")
                if (
                    existing["asset_deleted_at"] is not None
                    or existing["conversation_deleted_at"] is not None
                ):
                    raise MediaAccessError("上传任务已失效")
                return _upload(existing)
            # Lock on stable subject so concurrent uploads in different conversations
            # cannot each pass the aggregate quota independently.
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"media-user-quota:{identity.subject}"},
            )
            usage = (
                (
                    await connection.execute(
                        text("""SELECT count(*) AS count,COALESCE(sum(size_bytes),0) AS bytes
                  FROM agent_media_assets WHERE subject_user_id=:subject AND deleted_at IS NULL"""),
                        {"subject": identity.subject},
                    )
                )
                .mappings()
                .one()
            )
            if (
                int(usage["count"]) >= self.max_assets
                or int(usage["bytes"]) + size > self.max_bytes
            ):
                raise MediaQuotaExceeded("素材数量或容量已达上限，请删除不需要的素材后重试")
            conversation = (
                await connection.execute(
                    text("""
                SELECT 1 FROM agent_chat_conversations WHERE conversation_id=:conversation
                  AND owner_hash=:owner AND subject_user_id=:subject AND deleted_at IS NULL FOR SHARE
            """),
                    {"conversation": conversation_id, "owner": owner, "subject": identity.subject},
                )
            ).first()
            if conversation is None:
                raise MediaAccessError("会话不存在或无法访问")
            asset_id, upload_id = str(uuid4()), str(uuid4())
            staging_key = f"staging/{owner[:24]}/{UUID(asset_id).hex}"
            await connection.execute(
                text("""
                INSERT INTO agent_media_assets
                  (asset_id,owner_hash,subject_user_id,organization_scope,conversation_id,kind,
                   content_type,size_bytes,status,expires_at)
                VALUES (:asset,:owner,:subject,:orgs,:conversation,:kind,:type,:size,'UPLOADING',
                        CURRENT_TIMESTAMP+INTERVAL '30 days')
            """),
                {
                    "asset": asset_id,
                    "owner": owner,
                    "subject": identity.subject,
                    "orgs": ",".join(sorted(identity.organization_ids)),
                    "conversation": conversation_id,
                    "kind": _TYPES[content_type][0],
                    "type": content_type,
                    "size": size,
                },
            )
            row = (
                (
                    await connection.execute(
                        text("""
                INSERT INTO agent_media_uploads
                  (upload_id,asset_id,owner_hash,idempotency_key,request_digest,staging_key,max_bytes,status,expires_at)
                VALUES (:id,:asset,:owner,:key,:digest,:staging,:size,'ISSUED',
                        CURRENT_TIMESTAMP+INTERVAL '24 hours') RETURNING *
            """),
                        {
                            "id": upload_id,
                            "asset": asset_id,
                            "owner": owner,
                            "key": key,
                            "digest": digest,
                            "staging": staging_key,
                            "size": size,
                        },
                    )
                )
                .mappings()
                .one()
            )
            return _upload(
                {
                    **row,
                    "conversation_id": conversation_id,
                    "content_type": content_type,
                    "size_bytes": size,
                }
            )

    async def get_upload(self, identity: AgentIdentity, upload_id: str) -> UploadRecord:
        async with self.database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT u.*,a.conversation_id,a.content_type,a.size_bytes FROM agent_media_uploads u
                JOIN agent_media_assets a ON a.asset_id=u.asset_id
                WHERE u.upload_id=:id AND u.owner_hash=:owner AND a.deleted_at IS NULL
            """),
                        {"id": _uuid(upload_id), "owner": run_owner(identity)},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise MediaAccessError("上传任务不存在或无法访问")
            return _upload(row)

    async def claim_gateway_transfer(self, identity: AgentIdentity, upload_id: str) -> UploadRecord:
        """Claim exactly one storage write per upload session, including across crashes."""
        upload_id, owner = _uuid(upload_id), run_owner(identity)
        async with self.database.engine.begin() as connection:
            await self._lock_upload_asset(connection, upload_id)
            row = (
                (
                    await connection.execute(
                        text("""SELECT u.*,a.conversation_id,a.content_type,a.size_bytes
                FROM agent_media_uploads u JOIN agent_media_assets a ON a.asset_id=u.asset_id
                JOIN agent_chat_conversations c ON c.conversation_id=a.conversation_id
                WHERE u.upload_id=:id AND u.owner_hash=:owner AND a.owner_hash=:owner
                  AND c.owner_hash=:owner AND c.deleted_at IS NULL AND a.deleted_at IS NULL
                  AND a.expires_at>CURRENT_TIMESTAMP AND u.expires_at>CURRENT_TIMESTAMP
                  AND u.status='ISSUED' FOR UPDATE OF u"""),
                        {"id": upload_id, "owner": owner},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise MediaAccessError("上传任务不可用或正在处理")
            inserted = (
                await connection.execute(
                    text("""INSERT INTO agent_media_gateway_attempts
                (upload_id,owner_hash) VALUES (:id,:owner) ON CONFLICT DO NOTHING RETURNING upload_id"""),
                    {"id": upload_id, "owner": owner},
                )
            ).first()
            if inserted is None:
                raise MediaTransferAlreadyStarted(
                    "上传已开始或结果未确认，请检查状态；如需重传请新建素材"
                )
            return _upload(row)

    async def claim_verification(
        self, identity: AgentIdentity, upload_id: str
    ) -> UploadRecord | None:
        owner, token = run_owner(identity), uuid4().hex
        async with self.database.engine.begin() as connection:
            await self._lock_upload_asset(connection, _uuid(upload_id))
            row = (
                (
                    await connection.execute(
                        text("""
                UPDATE agent_media_uploads SET status='VERIFYING',verification_token=:token,
                  verification_deadline=CURRENT_TIMESTAMP+INTERVAL '180 seconds',updated_at=CURRENT_TIMESTAMP
                WHERE upload_id=:id AND owner_hash=:owner AND expires_at>CURRENT_TIMESTAMP
                  AND (status IN ('ISSUED','UPLOADED') OR (status='VERIFYING'
                       AND verification_deadline<CURRENT_TIMESTAMP))
                RETURNING *
            """),
                        {"id": _uuid(upload_id), "owner": owner, "token": token},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            asset = (
                (
                    await connection.execute(
                        text("""
                UPDATE agent_media_assets SET status='VERIFYING',updated_at=CURRENT_TIMESTAMP
                WHERE asset_id=:asset AND owner_hash=:owner AND status IN ('UPLOADING','VERIFYING')
                  AND deleted_at IS NULL RETURNING conversation_id,content_type,size_bytes
            """),
                        {"asset": row["asset_id"], "owner": owner},
                    )
                )
                .mappings()
                .first()
            )
            if asset is None:
                raise MediaAccessError("素材已失效")
            return _upload({**row, **asset})

    async def bind_source_version(self, record: UploadRecord, version: str) -> str:
        if not record.verification_token or not version:
            raise ValueError("验证租约或版本无效")
        async with self.database.engine.begin() as connection:
            row = (
                await connection.execute(
                    text("""
                UPDATE agent_media_uploads SET source_version=COALESCE(source_version,:version),
                  updated_at=CURRENT_TIMESTAMP
                WHERE upload_id=:id AND status='VERIFYING' AND verification_token=:token
                  AND verification_deadline>CURRENT_TIMESTAMP RETURNING source_version
            """),
                    {
                        "id": record.upload_id,
                        "token": record.verification_token,
                        "version": version,
                    },
                )
            ).scalar_one_or_none()
            if row is None:
                raise MediaAccessError("上传验证租约已失效")
            return str(row)

    async def finish_verification(
        self,
        record: UploadRecord,
        metadata: MediaMetadata,
        *,
        final_key: str,
        final_version: str,
    ) -> bool:
        if not record.verification_token:
            raise ValueError("验证租约无效")
        expected_key = f"final/{UUID(record.asset_id).hex}/{UUID(record.upload_id).hex}"
        if final_key != expected_key or not final_version or final_version == "null":
            raise ValueError("最终媒体版本无效")
        if metadata.content_type != record.content_type or metadata.size != record.expected_size:
            raise ValueError("验证后的媒体与上传契约不一致")
        async with self.database.engine.begin() as connection:
            await self._lock_upload_asset(connection, record.upload_id)
            upload = (
                (
                    await connection.execute(
                        text("""
                UPDATE agent_media_uploads SET status='READY',verification_token=NULL,
                  verification_deadline=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE upload_id=:id AND asset_id=:asset AND status='VERIFYING' AND verification_token=:token
                  AND verification_deadline>CURRENT_TIMESTAMP AND source_version IS NOT NULL
                  RETURNING asset_id,owner_hash
            """),
                        {
                            "id": record.upload_id,
                            "asset": record.asset_id,
                            "token": record.verification_token,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if upload is None:
                return False
            row = (
                await connection.execute(
                    text("""
                UPDATE agent_media_assets SET status='READY',size_bytes=:size,width=:width,height=:height,
                  duration_seconds=:duration,sha256=:sha,final_key=:key,final_version=:version,
                  updated_at=CURRENT_TIMESTAMP WHERE asset_id=:asset AND owner_hash=:owner
                  AND status='VERIFYING' AND deleted_at IS NULL RETURNING asset_id
            """),
                    {
                        "asset": record.asset_id,
                        "owner": upload["owner_hash"],
                        "size": metadata.size,
                        "width": metadata.width,
                        "height": metadata.height,
                        "duration": metadata.duration_seconds,
                        "sha": metadata.sha256,
                        "key": final_key,
                        "version": final_version,
                    },
                )
            ).first()
            if row is None:
                raise MediaAccessError("素材已失效")
            return True

    async def release_verification(self, record: UploadRecord, *, rejected: bool = False) -> bool:
        if not record.verification_token:
            raise ValueError("验证租约无效")
        async with self.database.engine.begin() as connection:
            await self._lock_upload_asset(connection, record.upload_id)
            row = (
                (
                    await connection.execute(
                        text("""
                UPDATE agent_media_uploads SET status=:status,verification_token=NULL,
                  verification_deadline=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE upload_id=:id AND status='VERIFYING' AND verification_token=:token
                  RETURNING asset_id,owner_hash
            """),
                        {
                            "id": record.upload_id,
                            "token": record.verification_token,
                            "status": "REJECTED" if rejected else "ISSUED",
                        },
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            await connection.execute(
                text("""UPDATE agent_media_assets SET status=:status,
                updated_at=CURRENT_TIMESTAMP WHERE asset_id=:asset AND owner_hash=:owner
                AND status='VERIFYING' AND deleted_at IS NULL"""),
                {
                    "asset": row["asset_id"],
                    "owner": row["owner_hash"],
                    "status": "REJECTED" if rejected else "UPLOADING",
                },
            )
            return True

    async def ready_asset(self, identity: AgentIdentity, asset_id: str) -> ReadyAsset:
        async with self.database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT * FROM agent_media_assets WHERE asset_id=:id AND owner_hash=:owner
                  AND status='READY' AND deleted_at IS NULL AND expires_at>CURRENT_TIMESTAMP
            """),
                        {"id": _uuid(asset_id), "owner": run_owner(identity)},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise MediaAccessError("素材不存在、不可访问或已过期")
            return ReadyAsset(
                str(row["asset_id"]),
                int(row["revision"]),
                str(row["kind"]),
                str(row["content_type"]),
                int(row["size_bytes"]),
                row["duration_seconds"],
                str(row["final_key"]),
                str(row["final_version"]),
            )
