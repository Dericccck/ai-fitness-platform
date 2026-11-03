"""私有素材上传编排；供应商存储操作永不信任客户端返回的对象版本。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from app.infrastructure.agent_context import AgentIdentity
from app.media.metadata import MediaInspectionUnavailable, MediaValidationError, inspect_stream
from app.media.repository import MediaAccessError, MediaRepository, ReadyAsset, UploadRecord
from app.media.storage import MediaStorageError, OssMediaStorage, PostPolicy, StoredVersion

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class UploadResult:
    record: UploadRecord
    policy: PostPolicy | None


class MediaService:
    def __init__(
        self,
        repository: MediaRepository,
        storage: OssMediaStorage,
        *,
        validator_socket: str = "",
        validator_temp_dir: str = "",
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.validator_socket = validator_socket
        self.validator_temp_dir = validator_temp_dir

    async def initiate(
        self,
        *,
        identity: AgentIdentity,
        conversation_id: str,
        idempotency_key: str,
        content_type: str,
        size: int,
    ) -> UploadResult:
        record = await self.repository.create_upload(
            identity=identity,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            content_type=content_type,
            size=size,
        )
        if record.status != "ISSUED":
            return UploadResult(record, None)
        if record.expires_at is None:
            raise MediaStorageError("上传有效期不可用")
        remaining = int((record.expires_at - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            raise MediaAccessError("上传任务已过期")
        policy = self.storage.post_policy(
            record.staging_key,
            max_bytes=record.expected_size,
            content_type=record.content_type,
            ttl_seconds=min(remaining, 900),
        )
        return UploadResult(record, policy)

    async def complete(self, identity: AgentIdentity, upload_id: str) -> ReadyAsset | None:
        current = await self.repository.get_upload(identity, upload_id)
        if current.status == "READY":
            return await self.repository.ready_asset(identity, current.asset_id)
        if current.status in {"REJECTED", "CANCELLED", "EXPIRED"}:
            raise MediaAccessError("上传任务已失效")
        record = await self.repository.claim_verification(identity, upload_id)
        if record is None:
            # 另一个请求已在验证；调用者应查询原上传任务，而不是新建文件。
            return None
        final: StoredVersion | None = None
        try:
            source = await asyncio.to_thread(
                self.storage.head, record.staging_key, version_id=record.source_version
            )
            fixed_version = await self.repository.bind_source_version(record, source.version_id)
            if fixed_version != source.version_id:
                source = await asyncio.to_thread(
                    self.storage.head, record.staging_key, version_id=fixed_version
                )
            if source.size != record.expected_size:
                raise MediaValidationError("实际媒体大小与声明不一致")

            def inspect() -> Any:
                stream = self.storage.open_version(source)
                try:
                    return inspect_stream(
                        stream,
                        declared_type=record.content_type,
                        expected_size=record.expected_size,
                        max_bytes=record.expected_size,
                        validator_socket=self.validator_socket,
                        validator_temp_dir=self.validator_temp_dir,
                    )
                finally:
                    close = getattr(stream, "close", None)
                    if close:
                        close()

            metadata = await asyncio.to_thread(inspect)
            # final key由服务端生成，客户端从未获得其写入权限。
            final_key = f"final/{UUID(record.asset_id).hex}/{UUID(record.upload_id).hex}"
            final = await asyncio.to_thread(self.storage.copy_to_final, source, final_key)
            committed = await self.repository.finish_verification(
                record, metadata, final_key=final.key, final_version=final.version_id
            )
            if not committed:
                await asyncio.to_thread(self.storage.delete_version, final.key, final.version_id)
                final = None
                raise MediaAccessError("上传验证已过期，请重试")
            return await self.repository.ready_asset(identity, record.asset_id)
        except MediaValidationError:
            await self.repository.release_verification(record, rejected=True)
            raise
        except (MediaStorageError, MediaAccessError, MediaInspectionUnavailable):
            if final is not None:
                try:
                    await asyncio.to_thread(
                        self.storage.delete_version, final.key, final.version_id
                    )
                except MediaStorageError:
                    # 孤立最终版本不可由客户端访问；清理任务必须按上传会话回收。
                    logger.warning("media_orphan_cleanup_failed", upload_id=record.upload_id)
            await self.repository.release_verification(record)
            raise

    async def signed_preview(self, identity: AgentIdentity, asset_id: str) -> str:
        asset = await self.repository.ready_asset(identity, asset_id)
        version = StoredVersion(
            asset.final_key, asset.final_version, asset.size, asset.content_type
        )
        return await asyncio.to_thread(self.storage.signed_read_url, version, ttl_seconds=120)
