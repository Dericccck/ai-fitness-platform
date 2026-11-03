"""异步删除 OSS 版本；暂存直传凭证过期后再次清理，避免迟到写入。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text

from app.core.multimodal_metrics import MultimodalMetrics
from app.infrastructure.database import Database
from app.media.storage import OssMediaStorage

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DeletionLease:
    deletion_id: str
    token: str = field(repr=False)
    staging_key: str = field(repr=False)
    final_key: str = field(repr=False)


class MediaDeletionWorker:
    def __init__(
        self,
        database: Database,
        storage: OssMediaStorage,
        *,
        metrics: MultimodalMetrics | None = None,
    ) -> None:
        self.database, self.storage = database, storage
        self.metrics = metrics

    async def claim(self) -> DeletionLease | None:
        async with self.database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("""SELECT d.deletion_id,d.asset_id,u.upload_id,u.staging_key
                      FROM agent_media_deletions d JOIN agent_media_uploads u ON u.asset_id=d.asset_id
                      WHERE (d.status='PENDING' AND d.retry_at<=CURRENT_TIMESTAMP)
                         OR (d.status='RUNNING' AND d.lease_until<=CURRENT_TIMESTAMP)
                      ORDER BY d.retry_at,d.deletion_id FOR UPDATE OF d SKIP LOCKED LIMIT 1""")
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            token = uuid4().hex
            await connection.execute(
                text("""UPDATE agent_media_deletions SET status='RUNNING',lease_token=:token,
                  lease_until=CURRENT_TIMESTAMP+INTERVAL '2 minutes',attempt=attempt+1,
                  updated_at=CURRENT_TIMESTAMP WHERE deletion_id=:id"""),
                {"token": token, "id": row["deletion_id"]},
            )
            final_key = f"final/{UUID(str(row['asset_id'])).hex}/{UUID(str(row['upload_id'])).hex}"
            return DeletionLease(str(row["deletion_id"]), token, str(row["staging_key"]), final_key)

    async def finish(self, lease: DeletionLease) -> bool:
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                text("""UPDATE agent_media_deletions d SET
                  status=CASE WHEN u.expires_at+INTERVAL '5 minutes'<=CURRENT_TIMESTAMP
                    THEN 'COMPLETED' ELSE 'PENDING' END,
                  retry_at=GREATEST(CURRENT_TIMESTAMP+INTERVAL '1 minute',u.expires_at+INTERVAL '5 minutes'),
                  cleared_once=true,lease_token=NULL,lease_until=NULL,
                  completed_at=CASE WHEN u.expires_at+INTERVAL '5 minutes'<=CURRENT_TIMESTAMP
                    THEN CURRENT_TIMESTAMP ELSE NULL END,updated_at=CURRENT_TIMESTAMP
                  FROM agent_media_uploads u WHERE d.deletion_id=:id AND u.asset_id=d.asset_id
                    AND d.status='RUNNING' AND d.lease_token=:token AND d.lease_until>CURRENT_TIMESTAMP
                  RETURNING d.deletion_id"""),
                {"id": lease.deletion_id, "token": lease.token},
            )
            return result.first() is not None

    async def fail(self, lease: DeletionLease) -> None:
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("""UPDATE agent_media_deletions SET status='PENDING',
                  retry_at=CURRENT_TIMESTAMP+INTERVAL '1 minute',lease_token=NULL,lease_until=NULL,
                  updated_at=CURRENT_TIMESTAMP WHERE deletion_id=:id AND lease_token=:token
                  AND status='RUNNING'"""),
                {"id": lease.deletion_id, "token": lease.token},
            )

    async def cleanup_staging_once(self) -> bool:
        """已成功上传的暂存版本在签名到期后清理，不触碰最终素材。"""
        token = uuid4().hex
        async with self.database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("""SELECT upload_id,staging_key
                FROM agent_media_uploads WHERE status='READY' AND staging_cleared_at IS NULL
                  AND expires_at+INTERVAL '5 minutes'<=CURRENT_TIMESTAMP
                  AND (staging_cleanup_deadline IS NULL OR staging_cleanup_deadline<=CURRENT_TIMESTAMP)
                ORDER BY expires_at,upload_id FOR UPDATE SKIP LOCKED LIMIT 1""")
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            await connection.execute(
                text("""UPDATE agent_media_uploads SET staging_cleanup_token=:token,
                staging_cleanup_deadline=CURRENT_TIMESTAMP+INTERVAL '2 minutes'
                WHERE upload_id=:id"""),
                {"token": token, "id": row["upload_id"]},
            )
        try:
            await asyncio.to_thread(self.storage.delete_all_versions, str(row["staging_key"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("media_staging_cleanup_retry", error_type=type(exc).__name__)
            if self.metrics:
                self.metrics.record_cleanup("staging", "retry")
            return True  # 有限租约到期后重试。
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("""UPDATE agent_media_uploads
                SET staging_cleared_at=CURRENT_TIMESTAMP,staging_cleanup_token=NULL,
                    staging_cleanup_deadline=NULL WHERE upload_id=:id AND staging_cleanup_token=:token
                    AND staging_cleanup_deadline>CURRENT_TIMESTAMP"""),
                {"id": row["upload_id"], "token": token},
            )
        if self.metrics:
            self.metrics.record_cleanup("staging", "success")
        return True

    async def run_once(self) -> bool:
        lease = await self.claim()
        if lease is None:
            return False
        try:
            await asyncio.to_thread(self.storage.delete_all_versions, lease.staging_key)
            await asyncio.to_thread(self.storage.delete_all_versions, lease.final_key)
            await self.finish(lease)
            if self.metrics:
                self.metrics.record_cleanup("deletion", "success")
        except Exception as exc:  # noqa: BLE001 - 只记录稳定类型，不记录私有 key 或供应商响应
            logger.warning("media_deletion_retry", error_type=type(exc).__name__)
            if self.metrics:
                self.metrics.record_cleanup("deletion", "retry")
            await self.fail(lease)
        return True
