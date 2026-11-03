"""候选提取的加密来源；任务只引用来源 ID，不复制对话正文。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database


class MemorySourceConflict(RuntimeError):
    """同一来源请求标识不能重新绑定不同的用户输入。"""


@dataclass(frozen=True)
class MemorySource:
    id: str
    subject_user_id: str
    organization_id: str
    thread_id: str
    source_request_id: str
    write_epoch: int


class MemorySourceRepository:
    """保存已认证的用户原话，并在可信完成事务中建立提取任务。"""

    def __init__(
        self, database: Database, cipher: AesGcmPayloadCipher, *, retention_days: int = 30
    ) -> None:
        if retention_days < 1:
            raise ValueError("来源保留天数必须大于零")
        self.database = database
        self.cipher = cipher
        self.retention_days = retention_days

    @staticmethod
    def associated_data(source_id: str, subject: str, organization: str) -> str:
        return f"memory-source:{source_id}:{subject}:{organization}"

    async def capture(
        self,
        *,
        identity: AgentIdentity,
        thread_id: str,
        source_request_id: str,
        message: str,
    ) -> MemorySource | None:
        """多机构没有明确来源时不猜测归属；相同来源请求不得换绑正文。"""
        if len(identity.organization_ids) != 1:
            return None
        organization = next(iter(identity.organization_ids))
        source_id = str(uuid4())
        payload = self.cipher.encrypt(
            message.encode("utf-8"),
            associated_data=self.associated_data(source_id, identity.subject, organization),
        )
        params = {
            "id": source_id,
            "subject": identity.subject,
            "organization": organization,
            "thread": thread_id,
            "request": source_request_id,
            "cipher": payload,
            "key": self.cipher.key_version,
            "days": self.retention_days,
        }
        async with self.database.engine.begin() as connection:
            await connection.execute(
                text("""
                    INSERT INTO agent_memory_profiles (subject_user_id, organization_id)
                    VALUES (:subject, :organization)
                    ON CONFLICT (subject_user_id, organization_id) DO NOTHING
                """),
                params,
            )
            profile = (
                (
                    await connection.execute(
                        text("""
                        SELECT write_epoch, extraction_enabled FROM agent_memory_profiles
                        WHERE subject_user_id=:subject AND organization_id=:organization
                        FOR UPDATE
                    """),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            if not profile["extraction_enabled"]:
                return None
            params["epoch"] = int(profile["write_epoch"])
            result = await connection.execute(
                text("""
                    INSERT INTO agent_memory_source_messages
                      (id,subject_user_id,organization_id,thread_id,source_request_id,
                       message_ciphertext,key_version,write_epoch,retain_until)
                    VALUES (:id,:subject,:organization,:thread,:request,:cipher,:key,:epoch,
                            CURRENT_TIMESTAMP + :days * INTERVAL '1 day')
                    ON CONFLICT (subject_user_id,organization_id,thread_id,source_request_id)
                    DO NOTHING
                    RETURNING *
                """),
                params,
            )
            row = result.mappings().first()
            if row is None:
                row = (
                    (
                        await connection.execute(
                            text("""
                            SELECT * FROM agent_memory_source_messages
                            WHERE subject_user_id=:subject AND organization_id=:organization
                              AND thread_id=:thread AND source_request_id=:request
                        """),
                            params,
                        )
                    )
                    .mappings()
                    .one()
                )
                existing = self.cipher.decrypt(
                    bytes(row["message_ciphertext"]),
                    associated_data=self.associated_data(
                        str(row["id"]), identity.subject, organization
                    ),
                    key_version=str(row["key_version"]),
                ).decode("utf-8")
                if existing != message:
                    raise MemorySourceConflict("同一来源请求不能绑定不同输入")
            return MemorySource(
                id=str(row["id"]),
                subject_user_id=identity.subject,
                organization_id=organization,
                thread_id=thread_id,
                source_request_id=source_request_id,
                write_epoch=int(row["write_epoch"]),
            )

    async def complete_on_connection(
        self, connection: AsyncConnection, source: MemorySource
    ) -> bool:
        """只能在可信完成提交中调用；旧 epoch 的来源不能复活。"""
        params: dict[str, Any] = {
            "id": source.id,
            "subject": source.subject_user_id,
            "organization": source.organization_id,
            "thread": source.thread_id,
            "request": source.source_request_id,
            "epoch": source.write_epoch,
            "job_id": str(uuid4()),
        }
        profile = (
            (
                await connection.execute(
                    text("""
                    SELECT write_epoch, extraction_enabled FROM agent_memory_profiles
                    WHERE subject_user_id=:subject AND organization_id=:organization FOR UPDATE
                """),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if profile is None or not profile["extraction_enabled"]:
            return False
        if int(profile["write_epoch"]) != source.write_epoch:
            return False
        source_row = await connection.execute(
            text("""
                UPDATE agent_memory_source_messages SET status='COMPLETED',
                  completed_at=COALESCE(completed_at,CURRENT_TIMESTAMP)
                WHERE id=:id AND subject_user_id=:subject AND organization_id=:organization
                  AND thread_id=:thread AND source_request_id=:request AND write_epoch=:epoch
                  AND retain_until > CURRENT_TIMESTAMP
                RETURNING id
            """),
            params,
        )
        if source_row.first() is None:
            return False
        await connection.execute(
            text("""
                INSERT INTO agent_memory_extraction_jobs
                  (id,subject_user_id,organization_id,thread_id,source_request_id,
                   source_message_id,write_epoch)
                VALUES (:job_id,:subject,:organization,:thread,:request,:id,:epoch)
                ON CONFLICT (subject_user_id,organization_id,source_message_id) DO NOTHING
            """),
            params,
        )
        return True

    async def complete(self, source: MemorySource) -> bool:
        """非流式请求的来源完成记录和任务在同一事务中提交。"""
        async with self.database.engine.begin() as connection:
            return await self.complete_on_connection(connection, source)

    async def cleanup_expired(self, *, limit: int = 500) -> int:
        """有界清除过期来源密文；先失效在途任务，避免失效来源被迟到任务发表。

        工作任务先锁 job 后锁 source，这里也先锁 job；跳过被别的事务占用的行。
        过期来源关联任务尚未完成处理时暂不删除，下一轮重试。来源删除后关联的终态
        任务由外键级联删除。此方法需要由保留 Worker 定期调用。
        """
        if not 1 <= limit <= 5000:
            raise ValueError("来源清理批次限制必须在 1 到 5000 之间")
        async with self.database.engine.begin() as connection:
            # 取消失效来源上的租约。发布阶段必须验证 RUNNING/lease token；即使模型
            # 正在运行，提交阶段也无法再产生候选。
            await connection.execute(
                text("""
                    WITH jobs AS (
                        SELECT j.id FROM agent_memory_extraction_jobs AS j
                        JOIN agent_memory_source_messages AS s ON s.id=j.source_message_id
                        WHERE s.retain_until <= CURRENT_TIMESTAMP
                          AND j.status IN ('PENDING', 'RUNNING')
                        ORDER BY s.retain_until, j.id
                        FOR UPDATE OF j SKIP LOCKED LIMIT :limit
                    )
                    UPDATE agent_memory_extraction_jobs AS j
                    SET status='FAILED', lease_token=NULL, lease_until=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    FROM jobs WHERE j.id=jobs.id
                """),
                {"limit": limit},
            )
            result = await connection.execute(
                text("""
                    WITH sources AS (
                        SELECT s.id FROM agent_memory_source_messages AS s
                        WHERE s.retain_until <= CURRENT_TIMESTAMP
                          AND NOT EXISTS (
                            SELECT 1 FROM agent_memory_extraction_jobs AS j
                            WHERE j.source_message_id=s.id AND j.status IN ('PENDING','RUNNING')
                          )
                        ORDER BY s.retain_until, s.id
                        FOR UPDATE OF s SKIP LOCKED LIMIT :limit
                    )
                    DELETE FROM agent_memory_source_messages AS s
                    USING sources WHERE s.id=sources.id RETURNING s.id
                """),
                {"limit": limit},
            )
            return len(result.all())
