"""素材删除的数据库失效事务；实际对象版本清理由持久删除任务执行。"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.media.repository import MediaAccessError
from app.streaming.control import run_owner


class MediaDeletionRepository:
    def __init__(self, database: Database, cipher: AesGcmPayloadCipher) -> None:
        self.database = database
        self.cipher = cipher

    async def request_conversation(self, identity: AgentIdentity, conversation_id: str) -> int:
        """一次事务隐藏整个会话、关联结果和素材，随后异步清理 OSS 版本。"""
        owner = run_owner(identity)
        async with self.database.engine.begin() as connection:
            conversation = (
                (
                    await connection.execute(
                        text("""SELECT content_epoch,deleted_at FROM agent_chat_conversations
                      WHERE conversation_id=:id AND owner_hash=:owner FOR UPDATE"""),
                        {"id": conversation_id, "owner": owner},
                    )
                )
                .mappings()
                .first()
            )
            if conversation is None:
                raise MediaAccessError("会话不存在或无法访问")
            if conversation["deleted_at"] is not None:
                return 0
            epoch = int(conversation["content_epoch"]) + 1
            assets = (
                (
                    await connection.execute(
                        text("""SELECT asset_id FROM agent_media_assets WHERE conversation_id=:id
                      AND owner_hash=:owner ORDER BY asset_id FOR UPDATE"""),
                        {"id": conversation_id, "owner": owner},
                    )
                )
                .scalars()
                .all()
            )
            await connection.execute(
                text("""UPDATE agent_chat_conversations SET deleted_at=CURRENT_TIMESTAMP,
                  content_epoch=:epoch,title_ciphertext=NULL,key_version=NULL,updated_at=CURRENT_TIMESTAMP
                  WHERE conversation_id=:id AND owner_hash=:owner"""),
                {"epoch": epoch, "id": conversation_id, "owner": owner},
            )
            for asset in assets:
                await connection.execute(
                    text("""UPDATE agent_media_assets SET status='DELETED',
                      deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP),revision=revision+1,
                      updated_at=CURRENT_TIMESTAMP WHERE asset_id=:asset AND owner_hash=:owner"""),
                    {"asset": asset, "owner": owner},
                )
                await connection.execute(
                    text("""UPDATE agent_media_uploads SET status='CANCELLED',verification_token=NULL,
                      verification_deadline=NULL,updated_at=CURRENT_TIMESTAMP WHERE asset_id=:asset
                      AND status NOT IN ('READY','REJECTED','EXPIRED','CANCELLED')"""),
                    {"asset": asset},
                )
                await connection.execute(
                    text("""INSERT INTO agent_media_deletions
                      (deletion_id,owner_hash,asset_id,request_epoch,status)
                      VALUES (:id,:owner,:asset,:epoch,'PENDING') ON CONFLICT (asset_id) DO NOTHING"""),
                    {"id": str(uuid4()), "owner": owner, "asset": asset, "epoch": epoch},
                )
            runs = (
                (
                    await connection.execute(
                        text("""SELECT r.run_id,r.status,r.result_ciphertext FROM agent_chat_runs r
                      WHERE r.owner_hash=:owner AND r.run_id IN
                        (SELECT run_id FROM agent_chat_messages WHERE conversation_id=:conversation AND owner_hash=:owner)
                      ORDER BY r.run_id FOR UPDATE"""),
                        {"owner": owner, "conversation": conversation_id},
                    )
                )
                .mappings()
                .all()
            )
            for run in runs:
                run_id = str(run["run_id"])
                if run["result_ciphertext"] is not None:
                    tombstone = self.cipher.encrypt(
                        b'{"content_deleted":true}', associated_data=f"chat-run:{run_id}:{owner}"
                    )
                    await connection.execute(
                        text("""UPDATE agent_chat_runs SET result_ciphertext=:cipher,key_version=:key,
                          updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                        {"cipher": tombstone, "key": self.cipher.key_version, "run": run_id},
                    )
                elif run["status"] == "QUEUED":
                    await connection.execute(
                        text("""UPDATE agent_chat_runs SET status='CANCELLED',stage='CANCELLED',
                          cancelled_at=CURRENT_TIMESTAMP,stop_reason='CONTENT_DELETED',
                          stage_version=stage_version+1,updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                        {"run": run_id},
                    )
                elif run["status"] == "RUNNING":
                    await connection.execute(
                        text("""UPDATE agent_chat_runs SET status='CANCEL_REQUESTED',
                          cancel_requested_at=CURRENT_TIMESTAMP,cancel_deadline=CURRENT_TIMESTAMP+INTERVAL '10 seconds',
                          stop_reason='CONTENT_DELETED',updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                        {"run": run_id},
                    )
                payload = self.cipher.encrypt(
                    b'{"content_deleted":true}', associated_data=f"mm-dispatch:{run_id}:{owner}"
                )
                await connection.execute(
                    text("""UPDATE agent_chat_run_dispatch SET payload_ciphertext=:cipher,key_version=:key,
                      updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                    {"cipher": payload, "key": self.cipher.key_version, "run": run_id},
                )
            await connection.execute(
                text("""UPDATE agent_chat_messages SET content_ciphertext=NULL,key_version=NULL,status='DELETED',
                  deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP),updated_at=CURRENT_TIMESTAMP
                  WHERE conversation_id=:id AND owner_hash=:owner"""),
                {"id": conversation_id, "owner": owner},
            )
            await connection.execute(
                text("""UPDATE agent_movement_reports SET report_ciphertext=NULL,key_version=NULL,
                  deleted_at=COALESCE(deleted_at,CURRENT_TIMESTAMP)
                  WHERE conversation_id=:id AND owner_hash=:owner"""),
                {"id": conversation_id, "owner": owner},
            )
            await connection.execute(
                text("""UPDATE agent_chat_request_keys SET content_deleted=true
                  WHERE conversation_id=:id AND owner_hash=:owner"""),
                {"id": conversation_id, "owner": owner},
            )
            return len(assets)

    async def request(self, identity: AgentIdentity, asset_id: str) -> str:
        try:
            asset_id = str(UUID(asset_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("素材标识必须是 UUID") from exc
        return await self._request_for_owner(run_owner(identity), asset_id)

    async def expire_one(self) -> bool:
        """过期素材与主动删除共用事务围栏和物理清理队列。"""
        async with self.database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""SELECT asset_id,owner_hash FROM agent_media_assets
                      WHERE deleted_at IS NULL AND expires_at<=CURRENT_TIMESTAMP
                      ORDER BY expires_at,asset_id LIMIT 1""")
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return False
        await self._request_for_owner(
            str(row["owner_hash"]), str(row["asset_id"]), expired_only=True
        )
        return True

    async def expire_abandoned_upload(self) -> bool:
        """回收过期未完成上传及可能已复制但未提交的最终版本。"""
        async with self.database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""SELECT a.asset_id,a.owner_hash
                FROM agent_media_uploads u JOIN agent_media_assets a ON a.asset_id=u.asset_id
                WHERE a.deleted_at IS NULL AND u.status != 'READY'
                  AND u.expires_at+INTERVAL '5 minutes'<=CURRENT_TIMESTAMP
                  AND (u.verification_deadline IS NULL OR u.verification_deadline<=CURRENT_TIMESTAMP)
                ORDER BY u.expires_at,u.upload_id LIMIT 1""")
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return False
        await self._request_for_owner(
            str(row["owner_hash"]), str(row["asset_id"]), abandoned_only=True
        )
        return True

    async def _request_for_owner(
        self,
        owner: str,
        asset_id: str,
        *,
        expired_only: bool = False,
        abandoned_only: bool = False,
    ) -> str:
        async with self.database.engine.begin() as connection:
            # 顺序与报告发布及上传验证一致，防止并发死锁。
            conversation = (
                (
                    await connection.execute(
                        text("""SELECT c.conversation_id,c.content_epoch FROM agent_chat_conversations c
                      JOIN agent_media_assets a ON a.conversation_id=c.conversation_id
                      WHERE a.asset_id=:asset AND a.owner_hash=:owner AND c.owner_hash=:owner
                      FOR UPDATE OF c"""),
                        {"asset": asset_id, "owner": owner},
                    )
                )
                .mappings()
                .first()
            )
            if conversation is None:
                raise MediaAccessError("素材不存在或无法访问")
            asset = (
                (
                    await connection.execute(
                        text("""SELECT deleted_at,expires_at<=CURRENT_TIMESTAMP AS expired
                      FROM agent_media_assets WHERE asset_id=:asset
                      AND owner_hash=:owner FOR UPDATE"""),
                        {"asset": asset_id, "owner": owner},
                    )
                )
                .mappings()
                .first()
            )
            if asset is None:
                raise MediaAccessError("素材不存在或无法访问")
            if expired_only and (asset["deleted_at"] is not None or not asset["expired"]):
                return ""
            if abandoned_only:
                upload = (
                    (
                        await connection.execute(
                            text("""SELECT status,
                    expires_at+INTERVAL '5 minutes'<=CURRENT_TIMESTAMP AS expired,
                    verification_deadline IS NULL OR verification_deadline<=CURRENT_TIMESTAMP AS lease_expired
                    FROM agent_media_uploads WHERE asset_id=:asset FOR UPDATE"""),
                            {"asset": asset_id},
                        )
                    )
                    .mappings()
                    .first()
                )
                if (
                    asset["deleted_at"] is not None
                    or upload is None
                    or upload["status"] == "READY"
                    or not upload["expired"]
                    or not upload["lease_expired"]
                ):
                    return ""
            existing = (
                await connection.execute(
                    text("SELECT deletion_id FROM agent_media_deletions WHERE asset_id=:asset"),
                    {"asset": asset_id},
                )
            ).scalar_one_or_none()
            if existing:
                return str(existing)
            epoch = int(conversation["content_epoch"]) + 1
            await connection.execute(
                text("""UPDATE agent_chat_conversations SET content_epoch=:epoch,updated_at=CURRENT_TIMESTAMP
                  WHERE conversation_id=:id"""),
                {"epoch": epoch, "id": conversation["conversation_id"]},
            )
            await connection.execute(
                text("""UPDATE agent_media_assets SET status='DELETED',deleted_at=CURRENT_TIMESTAMP,
                  revision=revision+1,updated_at=CURRENT_TIMESTAMP WHERE asset_id=:asset"""),
                {"asset": asset_id},
            )
            await connection.execute(
                text("""UPDATE agent_media_uploads SET status='CANCELLED',verification_token=NULL,
                  verification_deadline=NULL,updated_at=CURRENT_TIMESTAMP WHERE asset_id=:asset
                  AND status NOT IN ('READY','REJECTED','EXPIRED','CANCELLED')"""),
                {"asset": asset_id},
            )
            runs = (
                (
                    await connection.execute(
                        text("""SELECT r.run_id,r.status FROM agent_chat_runs r
                      WHERE r.owner_hash=:owner AND r.run_id IN
                        (SELECT m.run_id FROM agent_chat_messages m JOIN agent_chat_message_media mm
                          ON mm.message_id=m.message_id WHERE mm.asset_id=:asset AND m.owner_hash=:owner)
                      ORDER BY r.run_id FOR UPDATE"""),
                        {"asset": asset_id, "owner": owner},
                    )
                )
                .mappings()
                .all()
            )
            for run in runs:
                run_id = str(run["run_id"])
                if run["status"] == "COMPLETED":
                    tombstone = self.cipher.encrypt(
                        json.dumps({"content_deleted": True}).encode(),
                        associated_data=f"chat-run:{run_id}:{owner}",
                    )
                    await connection.execute(
                        text("""UPDATE agent_chat_runs SET result_ciphertext=:cipher,key_version=:key,
                          updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                        {"cipher": tombstone, "key": self.cipher.key_version, "run": run_id},
                    )
                elif run["status"] == "QUEUED":
                    await connection.execute(
                        text("""UPDATE agent_chat_runs SET status='CANCELLED',stage='CANCELLED',
                          cancelled_at=CURRENT_TIMESTAMP,stop_reason='CONTENT_DELETED',
                          stage_version=stage_version+1,updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                        {"run": run_id},
                    )
                elif run["status"] == "RUNNING":
                    await connection.execute(
                        text("""UPDATE agent_chat_runs SET status='CANCEL_REQUESTED',
                          cancel_requested_at=CURRENT_TIMESTAMP,cancel_deadline=CURRENT_TIMESTAMP+INTERVAL '10 seconds',
                          stop_reason='CONTENT_DELETED',updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                        {"run": run_id},
                    )
                payload = self.cipher.encrypt(
                    b'{"content_deleted":true}', associated_data=f"mm-dispatch:{run_id}:{owner}"
                )
                await connection.execute(
                    text("""UPDATE agent_chat_run_dispatch SET payload_ciphertext=:cipher,key_version=:key,
                      updated_at=CURRENT_TIMESTAMP WHERE run_id=:run"""),
                    {"cipher": payload, "key": self.cipher.key_version, "run": run_id},
                )
                await connection.execute(
                    text("""UPDATE agent_chat_messages SET content_ciphertext=NULL,key_version=NULL,
                      status='DELETED',deleted_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                      WHERE run_id=:run AND owner_hash=:owner"""),
                    {"run": run_id, "owner": owner},
                )
                await connection.execute(
                    text("""UPDATE agent_movement_reports SET report_ciphertext=NULL,key_version=NULL,
                      deleted_at=CURRENT_TIMESTAMP WHERE run_id=:run AND owner_hash=:owner"""),
                    {"run": run_id, "owner": owner},
                )
                await connection.execute(
                    text("""UPDATE agent_chat_request_keys SET content_deleted=true
                      WHERE run_id=:run AND owner_hash=:owner"""),
                    {"run": run_id, "owner": owner},
                )
            deletion_id = str(uuid4())
            await connection.execute(
                text("""INSERT INTO agent_media_deletions
                  (deletion_id,owner_hash,asset_id,request_epoch,status)
                  VALUES (:id,:owner,:asset,:epoch,'PENDING')"""),
                {"id": deletion_id, "owner": owner, "asset": asset_id, "epoch": epoch},
            )
            return deletion_id
