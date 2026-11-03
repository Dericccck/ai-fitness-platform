"""多模态运行持久投递与租约；公开运行状态仍由 agent_chat_runs 唯一管理。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from app.agent.movement_models import MovementReport
from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity, conversation_thread_id
from app.infrastructure.database import Database
from app.streaming.control import run_owner
from app.streaming.repository import RunConflict, RunSessionBusy, request_digest


class DurableAccessError(RuntimeError):
    """会话或素材不存在、不可见或已失效。"""


class MemoryRevisionChanged(RuntimeError):
    """用户记忆在分析期间变化，旧上下文不能再发布。"""


@dataclass(frozen=True)
class SubmitResult:
    run_id: str
    message_id: str
    created: bool
    content_deleted: bool = False


@dataclass(frozen=True)
class DispatchLease:
    run_id: str
    owner_hash: str
    lease_token: str = field(repr=False)
    auth_session_ref: str = field(repr=False)
    payload: dict[str, Any] = field(repr=False)
    attempt: int = 0


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("标识必须是 UUID") from exc


def _conversation(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise ValueError("会话标识无效")
    return value


class DurableMultimodalRepository:
    def __init__(self, database: Database, cipher: AesGcmPayloadCipher) -> None:
        self.database = database
        self.cipher = cipher

    async def create_conversation(self, identity: AgentIdentity, conversation_id: str) -> None:
        conversation_id = _conversation(conversation_id)
        owner = run_owner(identity)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO agent_chat_conversations
                      (conversation_id, subject_user_id, organization_scope, owner_hash)
                    VALUES (:id, :subject, :orgs, :owner) ON CONFLICT DO NOTHING
                """),
                {
                    "id": conversation_id,
                    "subject": identity.subject,
                    "orgs": json.dumps(sorted(identity.organization_ids)),
                    "owner": owner,
                },
            )
            result = await connection.execute(
                text("""SELECT 1 FROM agent_chat_conversations WHERE conversation_id=:id
                    AND owner_hash=:owner AND subject_user_id=:subject AND deleted_at IS NULL"""),
                {"id": conversation_id, "owner": owner, "subject": identity.subject},
            )
            if result.first() is None:
                raise DurableAccessError("会话不存在或无法访问")

    async def set_legacy_link(
        self, identity: AgentIdentity, conversation_id: str, enabled: bool
    ) -> None:
        conversation_id = _conversation(conversation_id)
        owner = run_owner(identity)
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                text("""UPDATE agent_chat_conversations
                SET legacy_linked=:enabled,legacy_thread_id=CASE WHEN :enabled THEN legacy_thread_id ELSE NULL END,
                content_epoch=content_epoch+CASE WHEN legacy_linked IS DISTINCT FROM :enabled THEN 1 ELSE 0 END,updated_at=CURRENT_TIMESTAMP
                WHERE conversation_id=:id AND owner_hash=:owner AND deleted_at IS NULL RETURNING conversation_id"""),
                {"enabled": enabled, "id": conversation_id, "owner": owner},
            )
            if result.first() is None:
                raise DurableAccessError("会话不存在或无法访问")

    async def legacy_thread(self, identity: AgentIdentity, conversation_id: str) -> str | None:
        async with self.database.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""SELECT legacy_linked,legacy_thread_id
                FROM agent_chat_conversations WHERE conversation_id=:id AND owner_hash=:owner
                  AND deleted_at IS NULL"""),
                        {"id": _conversation(conversation_id), "owner": run_owner(identity)},
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise DurableAccessError("会话不存在或无法访问")
            if not row["legacy_linked"]:
                return None
            return (
                str(row["legacy_thread_id"])
                if row["legacy_thread_id"]
                else conversation_thread_id(conversation_id, identity)
            )

    async def link_legacy_thread(self, identity: AgentIdentity, thread: str) -> str:
        if not re.fullmatch(r"fitness:[0-9a-f]{64}", thread):
            raise ValueError("历史对话标识无效")
        owner = run_owner(identity)
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"legacy-link:{owner}:{thread}"},
            )
            existing = (
                await connection.execute(
                    text("""SELECT conversation_id FROM agent_chat_conversations
                WHERE owner_hash=:owner AND legacy_thread_id=:thread AND deleted_at IS NULL"""),
                    {"owner": owner, "thread": thread},
                )
            ).scalar_one_or_none()
            if existing:
                return str(existing)
            conversation = str(uuid4())
            await connection.execute(
                text("""INSERT INTO agent_chat_conversations
                (conversation_id,subject_user_id,organization_scope,owner_hash,legacy_linked,legacy_thread_id)
                VALUES (:id,:subject,:orgs,:owner,true,:thread)"""),
                {
                    "id": conversation,
                    "subject": identity.subject,
                    "orgs": json.dumps(sorted(identity.organization_ids)),
                    "owner": owner,
                    "thread": thread,
                },
            )
            return conversation

    async def submit(
        self,
        *,
        identity: AgentIdentity,
        conversation_id: str,
        run_id: str,
        client_message_id: str,
        question: str,
        asset_ids: list[str],
        auth_session_ref: str,
        model: str,
        prompt_version: str,
        exercise_hint: str = "",
        retry_of: str | None = None,
    ) -> SubmitResult:
        conversation_id = _conversation(conversation_id)
        run_id, client_message_id = _uuid(run_id), _uuid(client_message_id)
        assets = [_uuid(value) for value in asset_ids]
        if not 1 <= len(assets) <= 3 or len(set(assets)) != len(assets):
            raise ValueError("素材数量无效或包含重复项")
        if not question.strip() or len(question) > 4000 or len(exercise_hint) > 100:
            raise ValueError("问题或动作名称长度无效")
        if not re.fullmatch(r"[0-9a-f]{64}", auth_session_ref):
            raise ValueError("必须提供服务端授权会话引用摘要")
        if not model or len(model) > 128 or not prompt_version or len(prompt_version) > 128:
            raise ValueError("模型或提示词版本无效")
        if retry_of is not None:
            retry_of = _uuid(retry_of)
        owner = run_owner(identity)
        input_data = {
            "question": question,
            "assets": assets,
            "exercise_hint": exercise_hint,
            "retry_of": retry_of,
        }
        input_digest = request_digest(input_data)
        scope = request_digest(
            {"owner": owner, "thread": conversation_thread_id(conversation_id, identity)}
        )
        async with self.database.engine.begin() as connection:
            conversation = (
                (
                    await connection.execute(
                        text("""SELECT content_epoch FROM agent_chat_conversations
                    WHERE conversation_id=:id AND owner_hash=:owner AND deleted_at IS NULL FOR UPDATE"""),
                        {"id": conversation_id, "owner": owner},
                    )
                )
                .mappings()
                .first()
            )
            if conversation is None:
                raise DurableAccessError("会话不存在或无法访问")
            existing = (
                (
                    await connection.execute(
                        text("""SELECT run_id,message_id,input_digest,content_deleted FROM agent_chat_request_keys
                    WHERE owner_hash=:owner AND conversation_id=:conversation AND client_message_id=:client"""),
                        {
                            "owner": owner,
                            "conversation": conversation_id,
                            "client": client_message_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["input_digest"] != input_digest:
                    raise RunConflict("客户端消息标识已绑定其他请求")
                return SubmitResult(
                    str(existing["run_id"]),
                    str(existing["message_id"]),
                    False,
                    bool(existing["content_deleted"]),
                )
            rows = (
                (
                    await connection.execute(
                        text("""SELECT asset_id, revision, final_version, kind FROM agent_media_assets
                    WHERE asset_id=ANY(CAST(:assets AS TEXT[])) AND owner_hash=:owner
                      AND conversation_id=:conversation AND status='READY' AND deleted_at IS NULL
                      AND expires_at>CURRENT_TIMESTAMP FOR SHARE"""),
                        {"assets": assets, "owner": owner, "conversation": conversation_id},
                    )
                )
                .mappings()
                .all()
            )
            by_id = {str(row["asset_id"]): row for row in rows}
            if len(by_id) != len(assets):
                raise DurableAccessError("素材不存在、不可访问或已过期")
            if len(assets) > 1 and any(by_id[item]["kind"] == "video" for item in assets):
                raise ValueError("一个请求只能包含单个视频或多张图片")
            manifest = [
                {
                    "asset_id": item,
                    "revision": int(by_id[item]["revision"]),
                    "version": str(by_id[item]["final_version"]),
                }
                for item in assets
            ]
            payload = {
                **input_data,
                "manifest": manifest,
                "model": model,
                "prompt_version": prompt_version,
                "conversation_id": conversation_id,
            }
            digest = request_digest(payload)
            inserted = await connection.execute(
                text("""INSERT INTO agent_chat_runs
                    (run_id,owner_hash,request_digest,conversation_scope_hash,status,executor_kind,
                     queued_at,execution_deadline,expires_at,content_epoch,retry_of,stage)
                    VALUES (:id,:owner,:digest,:scope,'QUEUED','MULTIMODAL',CURRENT_TIMESTAMP,
                      CURRENT_TIMESTAMP+INTERVAL '300 seconds',CURRENT_TIMESTAMP+INTERVAL '24 hours',
                      :epoch,:retry,'QUEUED') ON CONFLICT DO NOTHING RETURNING run_id"""),
                {
                    "id": run_id,
                    "owner": owner,
                    "digest": digest,
                    "scope": scope,
                    "epoch": conversation["content_epoch"],
                    "retry": retry_of,
                },
            )
            if inserted.first() is None:
                active = await connection.execute(
                    text("""SELECT 1 FROM agent_chat_runs WHERE conversation_scope_hash=:scope
                        AND status IN ('QUEUED','RUNNING','CANCEL_REQUESTED')"""),
                    {"scope": scope},
                )
                if active.first() is not None:
                    raise RunSessionBusy("会话已有进行中的任务")
                raise RunConflict("任务标识已被使用")
            message_id, assistant_id = str(uuid4()), str(uuid4())
            encrypted = self.cipher.encrypt(
                question.encode(), associated_data=f"chat-message:{message_id}:{owner}"
            )
            await connection.execute(
                text("""INSERT INTO agent_chat_messages
                    (message_id,conversation_id,owner_hash,run_id,role,status,content_ciphertext,key_version,schema_version)
                    VALUES (:message,:conversation,:owner,:run,'user','COMMITTED',:content,:key,'text.v1'),
                           (:assistant,:conversation,:owner,:run,'assistant','PENDING',NULL,NULL,'movement-analysis.v1')"""),
                {
                    "message": message_id,
                    "assistant": assistant_id,
                    "conversation": conversation_id,
                    "owner": owner,
                    "run": run_id,
                    "content": encrypted,
                    "key": self.cipher.key_version,
                },
            )
            for index, asset in enumerate(manifest):
                for message, dependency in ((message_id, "ATTACHMENT"), (assistant_id, "ANALYSIS")):
                    await connection.execute(
                        text("""INSERT INTO agent_chat_message_media
                            (message_id,asset_id,asset_revision,ordinal,dependency_type)
                            VALUES (:message,:asset,:revision,:ordinal,:dependency)"""),
                        {
                            "message": message,
                            "asset": asset["asset_id"],
                            "revision": asset["revision"],
                            "ordinal": index,
                            "dependency": dependency,
                        },
                    )
            await connection.execute(
                text("""INSERT INTO agent_chat_request_keys
                    (owner_hash,conversation_id,client_message_id,run_id,message_id,input_digest,request_digest,expires_at)
                    VALUES (:owner,:conversation,:client,:run,:message,:input,:digest,
                      CURRENT_TIMESTAMP+INTERVAL '90 days')"""),
                {
                    "owner": owner,
                    "conversation": conversation_id,
                    "client": client_message_id,
                    "run": run_id,
                    "message": message_id,
                    "input": input_digest,
                    "digest": digest,
                },
            )
            encrypted_payload = self.cipher.encrypt(
                json.dumps(payload, ensure_ascii=False).encode(),
                associated_data=f"mm-dispatch:{run_id}:{owner}",
            )
            await connection.execute(
                text("""INSERT INTO agent_chat_run_dispatch (run_id,payload_ciphertext,key_version,auth_session_ref)
                    VALUES (:id,:payload,:key,:auth)"""),
                {
                    "id": run_id,
                    "payload": encrypted_payload,
                    "key": self.cipher.key_version,
                    "auth": auth_session_ref,
                },
            )
            await connection.execute(
                text("""UPDATE agent_chat_conversations SET updated_at=CURRENT_TIMESTAMP
                  WHERE conversation_id=:id AND owner_hash=:owner"""),
                {"id": conversation_id, "owner": owner},
            )
            return SubmitResult(run_id, message_id, True)

    async def claim(self, *, lease_seconds: int = 30) -> DispatchLease | None:
        if not 5 <= lease_seconds <= 120:
            raise ValueError("租约时长无效")
        async with self.database.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text("""
                SELECT d.run_id,d.payload_ciphertext,d.key_version,d.auth_session_ref,d.attempt,r.owner_hash
                FROM agent_chat_run_dispatch d JOIN agent_chat_runs r ON r.run_id=d.run_id
                WHERE r.status='QUEUED' AND r.executor_kind='MULTIMODAL'
                  AND r.execution_deadline>CURRENT_TIMESTAMP AND d.available_at<=CURRENT_TIMESTAMP
                  AND d.lease_token IS NULL
                ORDER BY d.available_at,d.run_id FOR UPDATE OF d,r SKIP LOCKED LIMIT 1
            """)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            lease = uuid4().hex
            await connection.execute(
                text("""UPDATE agent_chat_run_dispatch SET lease_token=:lease,
                lease_until=CURRENT_TIMESTAMP+:seconds*INTERVAL '1 second',attempt=attempt+1,
                updated_at=CURRENT_TIMESTAMP WHERE run_id=:id"""),
                {"lease": lease, "seconds": lease_seconds, "id": row["run_id"]},
            )
            await connection.execute(
                text("""UPDATE agent_chat_runs SET status='RUNNING',stage='ANALYZING',
                stage_version=stage_version+1,started_at=COALESCE(started_at,CURRENT_TIMESTAMP),
                updated_at=CURRENT_TIMESTAMP WHERE run_id=:id AND status='QUEUED'"""),
                {"id": row["run_id"]},
            )
            payload = json.loads(
                self.cipher.decrypt(
                    bytes(row["payload_ciphertext"]),
                    associated_data=f"mm-dispatch:{row['run_id']}:{row['owner_hash']}",
                    key_version=str(row["key_version"]),
                )
            )
            return DispatchLease(
                str(row["run_id"]),
                str(row["owner_hash"]),
                lease,
                str(row["auth_session_ref"]),
                payload,
                int(row["attempt"]) + 1,
            )

    async def memory_revision(self, subject: str) -> int:
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("""INSERT INTO agent_memory_subject_fences(subject_user_id)
                VALUES (:subject) ON CONFLICT DO NOTHING"""),
                {"subject": subject},
            )
            revision = (
                await connection.execute(
                    text("""SELECT revision FROM agent_memory_subject_fences
                WHERE subject_user_id=:subject"""),
                    {"subject": subject},
                )
            ).scalar_one()
            return int(revision)

    async def recent_context(
        self, lease: DispatchLease, *, memory_revision: int
    ) -> list[dict[str, str]]:
        """只读取同一授权会话已完成且未删除的近期消息，避免将历史写入新报告。"""
        conversation = _conversation(lease.payload["conversation_id"])
        async with self.database.engine.begin() as connection:
            valid = (
                await connection.execute(
                    text("""SELECT 1 FROM agent_chat_conversations c
                JOIN agent_chat_runs r ON r.run_id=:run AND r.owner_hash=c.owner_hash
                JOIN agent_chat_run_dispatch d ON d.run_id=r.run_id
                WHERE c.conversation_id=:conversation AND c.owner_hash=:owner
                  AND c.deleted_at IS NULL AND c.content_epoch=r.content_epoch
                  AND r.status='RUNNING' AND d.lease_token=:lease AND d.lease_until>CURRENT_TIMESTAMP
                FOR SHARE OF c,r,d"""),
                    {
                        "run": lease.run_id,
                        "conversation": conversation,
                        "owner": lease.owner_hash,
                        "lease": lease.lease_token,
                    },
                )
            ).first()
            if valid is None:
                raise DurableAccessError("会话或任务已失效")
            rows = (
                (
                    await connection.execute(
                        text("""SELECT m.message_id,m.role,m.content_ciphertext,m.key_version
                FROM agent_chat_messages m JOIN agent_chat_runs r ON r.run_id=m.run_id
                WHERE m.conversation_id=:conversation AND m.owner_hash=:owner AND m.run_id!=:run
                  AND m.deleted_at IS NULL AND m.status='COMMITTED' AND r.status='COMPLETED'
                  AND m.content_ciphertext IS NOT NULL AND r.multimodal_memory_revision=:revision
                ORDER BY m.created_at DESC, CASE m.role WHEN 'assistant' THEN 0 ELSE 1 END, m.message_id DESC LIMIT 8"""),
                        {
                            "conversation": conversation,
                            "owner": lease.owner_hash,
                            "run": lease.run_id,
                            "revision": memory_revision,
                        },
                    )
                )
                .mappings()
                .all()
            )
            result = []
            for row in reversed(rows):
                value = self.cipher.decrypt(
                    bytes(row["content_ciphertext"]),
                    associated_data=f"chat-message:{row['message_id']}:{lease.owner_hash}",
                    key_version=row["key_version"],
                ).decode()
                if row["role"] == "assistant":
                    value = str(json.loads(value).get("summary", ""))
                if value:
                    result.append({"role": str(row["role"]), "content": value[:500]})
            return result[-8:]

    async def heartbeat(self, lease: DispatchLease, *, lease_seconds: int = 30) -> bool:
        if not 5 <= lease_seconds <= 120:
            raise ValueError("租约时长无效")
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                text("""UPDATE agent_chat_run_dispatch d SET
                lease_until=LEAST(CURRENT_TIMESTAMP+:seconds*INTERVAL '1 second',r.execution_deadline),
                updated_at=CURRENT_TIMESTAMP FROM agent_chat_runs r
                WHERE d.run_id=:id AND r.run_id=d.run_id AND r.owner_hash=:owner
                  AND r.status='RUNNING' AND d.lease_token=:token
                  AND d.lease_until>CURRENT_TIMESTAMP AND r.execution_deadline>CURRENT_TIMESTAMP
                RETURNING d.run_id"""),
                {
                    "id": lease.run_id,
                    "owner": lease.owner_hash,
                    "token": lease.lease_token,
                    "seconds": lease_seconds,
                },
            )
            return result.first() is not None

    async def mark_provider_started(
        self, lease: DispatchLease, *, memory_revision: int | None = None
    ) -> bool:
        async with self.database.engine.begin() as connection:
            manifest = lease.payload["manifest"]
            expected = {item["asset_id"]: (item["revision"], item["version"]) for item in manifest}
            if len(expected) != len(manifest):
                return False
            conversation = (
                (
                    await connection.execute(
                        text("""SELECT content_epoch,subject_user_id FROM agent_chat_conversations
                      WHERE conversation_id=:id AND owner_hash=:owner AND deleted_at IS NULL FOR UPDATE"""),
                        {"id": lease.payload["conversation_id"], "owner": lease.owner_hash},
                    )
                )
                .mappings()
                .first()
            )
            if conversation is None:
                return False
            assets = (
                (
                    await connection.execute(
                        text("""SELECT asset_id,revision,final_version FROM agent_media_assets
                      WHERE asset_id=ANY(CAST(:assets AS TEXT[])) AND owner_hash=:owner
                        AND conversation_id=:conversation AND status='READY' AND deleted_at IS NULL
                        AND expires_at>CURRENT_TIMESTAMP ORDER BY asset_id FOR UPDATE"""),
                        {
                            "assets": sorted(expected),
                            "owner": lease.owner_hash,
                            "conversation": lease.payload["conversation_id"],
                        },
                    )
                )
                .mappings()
                .all()
            )
            if {
                str(item["asset_id"]): (int(item["revision"]), str(item["final_version"]))
                for item in assets
            } != expected:
                return False
            run = (
                (
                    await connection.execute(
                        text("""SELECT content_epoch FROM agent_chat_runs WHERE run_id=:id
                      AND owner_hash=:owner AND status='RUNNING' AND execution_deadline>CURRENT_TIMESTAMP
                      FOR UPDATE"""),
                        {"id": lease.run_id, "owner": lease.owner_hash},
                    )
                )
                .mappings()
                .first()
            )
            if run is None or run["content_epoch"] != conversation["content_epoch"]:
                return False
            if memory_revision is not None:
                current = (
                    await connection.execute(
                        text("""SELECT revision FROM agent_memory_subject_fences
                    WHERE subject_user_id=:subject FOR UPDATE"""),
                        {"subject": conversation["subject_user_id"]},
                    )
                ).scalar_one_or_none()
                if current is None or int(current) != memory_revision:
                    raise MemoryRevisionChanged("记忆已改变")
                await connection.execute(
                    text("""UPDATE agent_chat_runs SET multimodal_memory_revision=:revision
                    WHERE run_id=:id"""),
                    {"id": lease.run_id, "revision": memory_revision},
                )
            result = await connection.execute(
                text("""UPDATE agent_chat_run_dispatch d SET
                provider_dispatch_started=true,updated_at=CURRENT_TIMESTAMP FROM agent_chat_runs r
                WHERE d.run_id=:id AND r.run_id=d.run_id AND r.owner_hash=:owner
                  AND r.status='RUNNING' AND d.lease_token=:token AND d.lease_until>CURRENT_TIMESTAMP
                  AND r.execution_deadline>CURRENT_TIMESTAMP RETURNING d.run_id"""),
                {"id": lease.run_id, "owner": lease.owner_hash, "token": lease.lease_token},
            )
            return result.first() is not None

    async def fail(self, lease: DispatchLease, *, outcome_unknown: bool, code: str) -> bool:
        """凭有效租约记录终态；错误码只能使用内部预定义值。"""
        allowed = {
            "AUTH_EXPIRED",
            "MEMORY_CHANGED",
            "MEDIA_UNAVAILABLE",
            "MODEL_FAILED",
            "PROVIDER_OUTCOME_UNKNOWN",
            "INTERNAL_ERROR",
        }
        if code not in allowed:
            raise ValueError("错误码无效")
        state = "UNKNOWN" if outcome_unknown else "FAILED"
        async with self.database.engine.begin() as connection:
            updated = await connection.execute(
                text("""UPDATE agent_chat_runs r SET status=:state,error_code=:code,stage=:state,
                  stage_version=stage_version+1,updated_at=CURRENT_TIMESTAMP
                  FROM agent_chat_run_dispatch d WHERE r.run_id=:id AND r.owner_hash=:owner
                    AND r.status='RUNNING' AND d.run_id=r.run_id AND d.lease_token=:token
                    AND d.lease_until>CURRENT_TIMESTAMP AND r.execution_deadline>CURRENT_TIMESTAMP
                  RETURNING r.run_id"""),
                {
                    "id": lease.run_id,
                    "owner": lease.owner_hash,
                    "token": lease.lease_token,
                    "state": state,
                    "code": code,
                },
            )
            if updated.first() is None:
                return False
            await connection.execute(
                text("""UPDATE agent_chat_run_dispatch SET lease_token=NULL,lease_until=NULL,
                  updated_at=CURRENT_TIMESTAMP WHERE run_id=:id"""),
                {"id": lease.run_id},
            )
            return True

    async def publish(self, lease: DispatchLease, report: MovementReport) -> bool:
        """只允许持有效租约的执行者发布仍属于原授权版本的结果。"""
        conversation_id = _conversation(lease.payload["conversation_id"])
        manifest = lease.payload["manifest"]
        expected = {item["asset_id"]: (item["revision"], item["version"]) for item in manifest}
        if len(expected) != len(manifest) or {
            (asset.asset_id, asset.revision) for asset in report.source_assets
        } != {(asset, data[0]) for asset, data in expected.items()}:
            raise ValueError("报告来源与原始素材不一致")
        if (
            report.model_version != lease.payload["model"]
            or report.prompt_version != lease.payload["prompt_version"]
        ):
            raise ValueError("报告版本与任务不一致")
        body = report.model_dump(mode="json")
        encoded = json.dumps(body, ensure_ascii=False).encode()
        async with self.database.engine.begin() as connection:
            # 删除操作必须采用同一锁顺序：会话、素材、任务、投递记录。
            conversation = (
                (
                    await connection.execute(
                        text("""SELECT content_epoch,subject_user_id FROM agent_chat_conversations
                      WHERE conversation_id=:conversation AND owner_hash=:owner
                        AND deleted_at IS NULL FOR UPDATE"""),
                        {"conversation": conversation_id, "owner": lease.owner_hash},
                    )
                )
                .mappings()
                .first()
            )
            if conversation is None:
                return False
            assets = (
                (
                    await connection.execute(
                        text("""SELECT asset_id,revision,final_version FROM agent_media_assets
                      WHERE asset_id=ANY(CAST(:assets AS TEXT[])) AND owner_hash=:owner
                        AND conversation_id=:conversation AND status='READY' AND deleted_at IS NULL
                        AND expires_at>CURRENT_TIMESTAMP ORDER BY asset_id FOR UPDATE"""),
                        {
                            "assets": sorted(expected),
                            "owner": lease.owner_hash,
                            "conversation": conversation_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
            actual = {
                str(item["asset_id"]): (int(item["revision"]), str(item["final_version"]))
                for item in assets
            }
            if actual != expected:
                return False
            run = (
                (
                    await connection.execute(
                        text("""SELECT r.content_epoch,r.multimodal_memory_revision FROM agent_chat_runs r
                      WHERE r.run_id=:id AND r.owner_hash=:owner AND r.status='RUNNING'
                        AND r.executor_kind='MULTIMODAL' AND r.execution_deadline>CURRENT_TIMESTAMP
                      FOR UPDATE"""),
                        {"id": lease.run_id, "owner": lease.owner_hash},
                    )
                )
                .mappings()
                .first()
            )
            if run is None or run["content_epoch"] != conversation["content_epoch"]:
                return False
            if run["multimodal_memory_revision"] is not None:
                current = (
                    await connection.execute(
                        text("""SELECT revision FROM agent_memory_subject_fences
                    WHERE subject_user_id=:subject FOR UPDATE"""),
                        {"subject": conversation["subject_user_id"]},
                    )
                ).scalar_one_or_none()
                if current is None or int(current) != int(run["multimodal_memory_revision"]):
                    raise MemoryRevisionChanged("记忆已改变")
            dispatch = (
                await connection.execute(
                    text("""SELECT 1 FROM agent_chat_run_dispatch
                      WHERE run_id=:id AND lease_token=:token AND lease_until>CURRENT_TIMESTAMP
                        AND provider_dispatch_started=true FOR UPDATE"""),
                    {"id": lease.run_id, "token": lease.lease_token},
                )
            ).first()
            if dispatch is None:
                return False
            assistant = (
                await connection.execute(
                    text("""SELECT message_id FROM agent_chat_messages WHERE run_id=:id
                      AND owner_hash=:owner AND role='assistant' AND status='PENDING'
                      AND deleted_at IS NULL FOR UPDATE"""),
                    {"id": lease.run_id, "owner": lease.owner_hash},
                )
            ).scalar_one_or_none()
            if assistant is None:
                return False
            report_id = str(uuid4())
            encrypted_report = self.cipher.encrypt(
                encoded, associated_data=f"movement-report:{report_id}:{lease.owner_hash}"
            )
            encrypted_message = self.cipher.encrypt(
                encoded, associated_data=f"chat-message:{assistant}:{lease.owner_hash}"
            )
            result = json.dumps(
                {"status": "COMPLETED", "report": body}, ensure_ascii=False
            ).encode()
            encrypted_result = self.cipher.encrypt(
                result, associated_data=f"chat-run:{lease.run_id}:{lease.owner_hash}"
            )
            await connection.execute(
                text("""INSERT INTO agent_movement_reports
                  (report_id,run_id,owner_hash,conversation_id,schema_version,report_ciphertext,
                   key_version,model_version,prompt_version,media_manifest_hash,content_epoch)
                  VALUES (:report,:run,:owner,:conversation,:schema,:cipher,:key,:model,:prompt,:manifest,:epoch)"""),
                {
                    "report": report_id,
                    "run": lease.run_id,
                    "owner": lease.owner_hash,
                    "conversation": conversation_id,
                    "schema": report.schema_version,
                    "cipher": encrypted_report,
                    "key": self.cipher.key_version,
                    "model": report.model_version,
                    "prompt": report.prompt_version,
                    "manifest": request_digest({"manifest": manifest}),
                    "epoch": run["content_epoch"],
                },
            )
            await connection.execute(
                text("""UPDATE agent_chat_messages SET status='COMMITTED',content_ciphertext=:content,
                  key_version=:key,updated_at=CURRENT_TIMESTAMP WHERE message_id=:id"""),
                {"content": encrypted_message, "key": self.cipher.key_version, "id": assistant},
            )
            await connection.execute(
                text("""UPDATE agent_chat_runs SET status='COMPLETED',stage='COMPLETED',
                  stage_version=stage_version+1,result_ciphertext=:result,key_version=:key,
                  updated_at=CURRENT_TIMESTAMP WHERE run_id=:id"""),
                {"result": encrypted_result, "key": self.cipher.key_version, "id": lease.run_id},
            )
            await connection.execute(
                text("""UPDATE agent_chat_run_dispatch SET lease_token=NULL,lease_until=NULL,
                  updated_at=CURRENT_TIMESTAMP WHERE run_id=:id"""),
                {"id": lease.run_id},
            )
            return True

    async def reap(self, *, batch_size: int = 100) -> int:
        if not 1 <= batch_size <= 1000:
            raise ValueError("批次大小无效")
        async with self.database.engine.begin() as connection:
            rows = (
                (
                    await connection.execute(
                        text("""
                SELECT r.run_id,r.status,r.execution_deadline,d.provider_dispatch_started,
                       r.execution_deadline<=CURRENT_TIMESTAMP AS deadline_expired
                FROM agent_chat_run_dispatch d JOIN agent_chat_runs r ON r.run_id=d.run_id
                WHERE r.executor_kind='MULTIMODAL' AND
                  ((r.status='QUEUED' AND r.execution_deadline<=CURRENT_TIMESTAMP)
                   OR (r.status IN ('RUNNING','CANCEL_REQUESTED') AND d.lease_until<=CURRENT_TIMESTAMP))
                ORDER BY r.execution_deadline FOR UPDATE OF r,d SKIP LOCKED LIMIT :batch
            """),
                        {"batch": batch_size},
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                if row["status"] == "CANCEL_REQUESTED" and not row["provider_dispatch_started"]:
                    state, error = "CANCELLED", None
                elif row["status"] == "QUEUED" or (
                    row["deadline_expired"] and not row["provider_dispatch_started"]
                ):
                    state, error = "FAILED", "EXECUTION_TIMEOUT"
                elif row["provider_dispatch_started"]:
                    state, error = "UNKNOWN", "PROVIDER_OUTCOME_UNKNOWN"
                else:
                    state, error = "QUEUED", None
                await connection.execute(
                    text("""UPDATE agent_chat_runs SET status=:state,error_code=:error,
                    stage=:state,stage_version=stage_version+1,updated_at=CURRENT_TIMESTAMP,
                    cancelled_at=CASE WHEN :state='CANCELLED' THEN CURRENT_TIMESTAMP ELSE cancelled_at END
                    WHERE run_id=:id"""),
                    {"id": row["run_id"], "state": state, "error": error},
                )
                await connection.execute(
                    text("""UPDATE agent_chat_run_dispatch SET lease_token=NULL,
                    lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE run_id=:id"""),
                    {"id": row["run_id"]},
                )
            return len(rows)
