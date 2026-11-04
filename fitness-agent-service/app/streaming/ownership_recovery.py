"""Conservative, transactional recovery of a personal multimodal conversation.

Only a freshly authenticated identity from the same subject and exact organization
scope may invoke this. This is not a migration of legacy text checkpoints.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher, ConfirmationPayloadCipherError
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database
from app.streaming.control import run_owner


class RecoveryUnavailable(Exception):
    """Conversation does not exist in the caller's authenticated scope."""


class RecoveryConflict(Exception):
    """Recovery cannot proceed safely without further action."""


class OwnershipRecovery:
    def __init__(self, database: Database, cipher: AesGcmPayloadCipher) -> None:
        self.database = database
        self.cipher = cipher

    async def available(self, identity: AgentIdentity, before: str | None = None) -> dict:
        if len(identity.organization_ids) != 1:
            raise RecoveryUnavailable()
        async with self.database.engine.connect() as conn:
            args = {
                "subject": identity.subject,
                "scope": json.dumps(sorted(identity.organization_ids)),
                "owner": run_owner(identity),
                "before": before,
                "anchor": None,
            }
            if before is not None:
                if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", before):
                    raise RecoveryUnavailable()
                args["anchor"] = (
                    await conn.execute(
                        text("""SELECT updated_at FROM agent_chat_conversations
                    WHERE conversation_id=:before AND subject_user_id=:subject
                      AND organization_scope=:scope AND deleted_at IS NULL"""),
                        args,
                    )
                ).scalar_one_or_none()
                if args["anchor"] is None:
                    raise RecoveryUnavailable()
            rows = (
                (
                    await conn.execute(
                        text("""SELECT conversation_id, updated_at
                FROM agent_chat_conversations WHERE subject_user_id=:subject
                  AND organization_scope=:scope AND owner_hash<>:owner AND deleted_at IS NULL
                  AND (CAST(:anchor AS timestamptz) IS NULL OR (updated_at,conversation_id)<(:anchor,:before))
                ORDER BY updated_at DESC,conversation_id DESC LIMIT 101"""),
                        args,
                    )
                )
                .mappings()
                .all()
            )
        items = [
            {"conversation_id": row["conversation_id"], "updated_at": row["updated_at"].isoformat()}
            for row in rows[:100]
        ]
        return {
            "conversations": items,
            "next_cursor": items[-1]["conversation_id"] if len(rows) > 100 else None,
        }

    async def recover(self, identity: AgentIdentity, conversation_id: str) -> bool:
        if len(identity.organization_ids) != 1 or not re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}", conversation_id
        ):
            raise RecoveryUnavailable()
        target = run_owner(identity)
        async with self.database.engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            await conn.execute(text("SET LOCAL statement_timeout = '30s'"))
            args = {
                "id": conversation_id,
                "subject": identity.subject,
                "scope": json.dumps(sorted(identity.organization_ids)),
            }
            row = (
                (
                    await conn.execute(
                        text("""SELECT owner_hash,legacy_linked,legacy_thread_id,title_ciphertext
                FROM agent_chat_conversations WHERE conversation_id=:id AND subject_user_id=:subject
                  AND organization_scope=:scope AND deleted_at IS NULL FOR UPDATE"""),
                        args,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise RecoveryUnavailable()
            old = row["owner_hash"]
            if old == target:
                return False
            if (
                row["legacy_linked"]
                or row["legacy_thread_id"]
                or row["title_ciphertext"] is not None
            ):
                raise RecoveryConflict("该会话包含旧对话关联或不支持的标题数据，需要人工处理")
            args.update(old=old, target=target)

            async def rows(sql: str) -> list:
                return list((await conn.execute(text(sql), args)).mappings().all())

            assets = await rows("""SELECT asset_id,owner_hash,subject_user_id FROM agent_media_assets
                WHERE conversation_id=:id ORDER BY asset_id LIMIT 10001 FOR UPDATE""")
            messages = await rows("""SELECT message_id,run_id,owner_hash,content_ciphertext,key_version
                FROM agent_chat_messages WHERE conversation_id=:id ORDER BY message_id LIMIT 10001 FOR UPDATE""")
            reports = await rows("""SELECT report_id,run_id,owner_hash,report_ciphertext,key_version
                FROM agent_movement_reports WHERE conversation_id=:id ORDER BY report_id LIMIT 10001 FOR UPDATE""")
            keys = await rows("""SELECT owner_hash,run_id,message_id FROM agent_chat_request_keys
                WHERE conversation_id=:id ORDER BY client_message_id LIMIT 10001 FOR UPDATE""")
            if sum(map(len, (assets, messages, reports, keys))) > 10000:
                raise RecoveryConflict("会话数据过多，需要管理员分批处理")
            if any(
                item["owner_hash"] != old
                for group in (assets, messages, reports, keys)
                for item in group
            ):
                raise RecoveryConflict("会话数据的归属不一致，需要人工检查")
            if any(item["subject_user_id"] != identity.subject for item in assets):
                raise RecoveryConflict("素材归属不一致，需要人工检查")
            args["assets"] = [item["asset_id"] for item in assets]
            args["runs"] = list({item["run_id"] for item in messages if item["run_id"]})
            message_ids = {item["message_id"] for item in messages}
            if any(item["run_id"] not in args["runs"] for item in reports + keys) or any(
                item["message_id"] not in message_ids for item in keys
            ):
                raise RecoveryConflict("会话关联数据不完整，需要人工检查")
            cross = (
                await conn.execute(
                    text("""SELECT EXISTS (
                SELECT 1 FROM agent_chat_message_media d
                JOIN agent_chat_messages m ON m.message_id=d.message_id
                JOIN agent_media_assets a ON a.asset_id=d.asset_id
                WHERE m.conversation_id=:id AND m.conversation_id<>a.conversation_id)
                OR EXISTS (
                SELECT 1 FROM agent_media_assets a
                JOIN agent_chat_message_media d ON d.asset_id=a.asset_id
                JOIN agent_chat_messages m ON m.message_id=d.message_id
                WHERE a.conversation_id=:id AND m.conversation_id<>a.conversation_id)"""),
                    args,
                )
            ).scalar_one()
            if cross:
                raise RecoveryConflict("素材跨会话关联，需要人工检查")
            other = (
                await conn.execute(
                    text("""SELECT EXISTS (SELECT 1 FROM agent_chat_messages
                WHERE run_id=ANY(CAST(:runs AS TEXT[])) AND conversation_id<>:id)"""),
                    args,
                )
            ).scalar_one()
            if other:
                raise RecoveryConflict("任务跨会话关联，需要人工检查")
            runs = await rows("""SELECT run_id,owner_hash,status,executor_kind,result_ciphertext,key_version
                FROM agent_chat_runs WHERE run_id=ANY(CAST(:runs AS TEXT[])) ORDER BY run_id FOR UPDATE""")
            if any(
                item["owner_hash"] != old or item["executor_kind"] != "MULTIMODAL" for item in runs
            ):
                raise RecoveryConflict("任务归属不一致，需要人工检查")
            if any(item["status"] in {"QUEUED", "RUNNING", "CANCEL_REQUESTED"} for item in runs):
                raise RecoveryConflict("会话仍有分析任务进行中，请结束后重试")
            uploads = await rows("""SELECT upload_id,owner_hash,status,
                expires_at+INTERVAL '5 minutes'>CURRENT_TIMESTAMP AS may_be_valid
                FROM agent_media_uploads WHERE asset_id=ANY(CAST(:assets AS TEXT[])) ORDER BY upload_id FOR UPDATE""")
            if any(item["owner_hash"] != old for item in uploads):
                raise RecoveryConflict("上传记录归属不一致，需要人工检查")
            if any(
                item["status"] not in {"READY", "REJECTED", "EXPIRED", "CANCELLED"}
                or item["may_be_valid"]
                for item in uploads
            ):
                raise RecoveryConflict("上传仍在处理或上传凭证尚未失效，请稍后重试")
            deletions = await rows("""SELECT owner_hash,status FROM agent_media_deletions
                WHERE asset_id=ANY(CAST(:assets AS TEXT[])) ORDER BY asset_id FOR UPDATE""")
            if any(
                item["owner_hash"] != old or item["status"] != "COMPLETED" for item in deletions
            ):
                raise RecoveryConflict("素材删除尚未完成或归属不一致，请稍后重试")
            args["uploads"] = [item["upload_id"] for item in uploads]
            attempts = await rows("""SELECT owner_hash FROM agent_media_gateway_attempts
                WHERE upload_id=ANY(CAST(:uploads AS TEXT[])) ORDER BY upload_id FOR UPDATE""")
            if any(item["owner_hash"] != old for item in attempts):
                raise RecoveryConflict("上传记录归属不一致，需要人工检查")
            dispatch = await rows("""SELECT run_id,payload_ciphertext,key_version FROM agent_chat_run_dispatch
                WHERE run_id=ANY(CAST(:runs AS TEXT[])) ORDER BY run_id FOR UPDATE""")
            # Re-encrypt all owner-bound data before committing any ownership changes.
            try:
                for group, table, id_col, cipher_col, prefix in (
                    (
                        messages,
                        "agent_chat_messages",
                        "message_id",
                        "content_ciphertext",
                        "chat-message",
                    ),
                    (
                        reports,
                        "agent_movement_reports",
                        "report_id",
                        "report_ciphertext",
                        "movement-report",
                    ),
                    (runs, "agent_chat_runs", "run_id", "result_ciphertext", "chat-run"),
                    (
                        dispatch,
                        "agent_chat_run_dispatch",
                        "run_id",
                        "payload_ciphertext",
                        "mm-dispatch",
                    ),
                ):
                    updates = []
                    for item in group:
                        value = item[cipher_col]
                        if value is None:
                            continue
                        item_id = item[id_col]
                        plain = self.cipher.decrypt(
                            bytes(value),
                            key_version=item["key_version"],
                            associated_data=f"{prefix}:{item_id}:{old}",
                        )
                        encrypted = self.cipher.encrypt(
                            plain, associated_data=f"{prefix}:{item_id}:{target}"
                        )
                        updates.append(
                            {"cipher": encrypted, "key": self.cipher.key_version, "item": item_id}
                        )
                    if updates:
                        await conn.execute(
                            text(
                                f"UPDATE {table} SET {cipher_col}=:cipher,key_version=:key WHERE {id_col}=:item"
                            ),
                            updates,
                        )
            except ConfirmationPayloadCipherError as exc:
                raise RecoveryConflict("历史数据无法解密，请管理员检查密钥版本") from exc
            for table, where in (
                ("agent_chat_conversations", "conversation_id=:id"),
                ("agent_media_assets", "conversation_id=:id"),
                ("agent_chat_messages", "conversation_id=:id"),
                ("agent_movement_reports", "conversation_id=:id"),
                ("agent_chat_request_keys", "conversation_id=:id"),
                ("agent_chat_runs", "run_id=ANY(CAST(:runs AS TEXT[]))"),
                ("agent_media_uploads", "upload_id=ANY(CAST(:uploads AS TEXT[]))"),
                ("agent_media_gateway_attempts", "upload_id=ANY(CAST(:uploads AS TEXT[]))"),
                ("agent_media_deletions", "asset_id=ANY(CAST(:assets AS TEXT[]))"),
            ):
                await conn.execute(
                    text(
                        f"UPDATE {table} SET owner_hash=:target WHERE {where} AND owner_hash=:old"
                    ),
                    args,
                )
            return True
