from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.confirmation.cipher import AesGcmPayloadCipher
from app.infrastructure.database import Database


class RunConflict(RuntimeError):
    """相同任务 ID 被换绑请求或权限范围。"""


@dataclass(frozen=True)
class ChatRun:
    run_id: str
    status: str
    result: dict[str, Any] | None = None
    error_code: str | None = None


def request_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ChatRunRepository:
    """仅存范围摘要和加密终态，签名身份及输入正文不持久化。"""

    def __init__(
        self,
        database: Database,
        cipher: AesGcmPayloadCipher,
        *,
        timeout: float,
        retention_hours: int,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.timeout = timeout
        self.retention_hours = retention_hours

    async def claim(self, run_id: str, owner: str, digest: str) -> tuple[ChatRun, bool]:
        async with self.database.engine.begin() as connection:
            inserted = await connection.execute(
                text("""
                INSERT INTO agent_chat_runs
                  (run_id, owner_hash, request_digest, execution_deadline, expires_at)
                VALUES (:id, :owner, :digest,
                  CURRENT_TIMESTAMP + :timeout * INTERVAL '1 second',
                  CURRENT_TIMESTAMP + :retention * INTERVAL '1 hour')
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id
            """),
                {
                    "id": run_id,
                    "owner": owner,
                    "digest": digest,
                    "timeout": self.timeout + 15,
                    "retention": self.retention_hours,
                },
            )
            created = inserted.first() is not None
            result = await connection.execute(
                text("""
                SELECT * FROM agent_chat_runs WHERE run_id=:id AND owner_hash=:owner
                  AND expires_at > CURRENT_TIMESTAMP
            """),
                {"id": run_id, "owner": owner},
            )
            row = result.mappings().first()
            if row is None or row["request_digest"] != digest:
                raise RunConflict("任务 ID 已被使用")
            return self._decode(dict(row)), created

    async def get(self, run_id: str, owner: str) -> ChatRun | None:
        async with self.database.engine.begin() as connection:
            # 进程失联后标记未知，永不自动重跑可能产生副作用的原请求。
            await connection.execute(
                text("""
                UPDATE agent_chat_runs SET status='UNKNOWN', error_code='EXECUTION_LOST',
                    updated_at=CURRENT_TIMESTAMP
                WHERE run_id=:id AND owner_hash=:owner AND status='RUNNING'
                  AND execution_deadline < CURRENT_TIMESTAMP
            """),
                {"id": run_id, "owner": owner},
            )
            result = await connection.execute(
                text("""
                SELECT * FROM agent_chat_runs WHERE run_id=:id AND owner_hash=:owner
                  AND expires_at > CURRENT_TIMESTAMP
            """),
                {"id": run_id, "owner": owner},
            )
            row = result.mappings().first()
            return self._decode(dict(row)) if row else None

    async def finish(
        self,
        run_id: str,
        owner: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        if status not in {"COMPLETED", "FAILED", "UNKNOWN"}:
            raise ValueError("无效任务终态")
        ciphertext = (
            self.cipher.encrypt(
                json.dumps(result, ensure_ascii=False).encode(),
                associated_data=f"chat-run:{run_id}:{owner}",
            )
            if result is not None
            else None
        )
        async with self.database.engine.begin() as connection:
            updated = await connection.execute(
                text("""
                UPDATE agent_chat_runs SET status=:status, result_ciphertext=:cipher,
                    key_version=:version, error_code=:error, updated_at=CURRENT_TIMESTAMP
                WHERE run_id=:id AND owner_hash=:owner AND status='RUNNING'
                RETURNING run_id
            """),
                {
                    "id": run_id,
                    "owner": owner,
                    "status": status,
                    "cipher": ciphertext,
                    "version": self.cipher.key_version if ciphertext is not None else None,
                    "error": error_code,
                },
            )
            return updated.first() is not None

    def _decode(self, row: dict[str, Any]) -> ChatRun:
        result = None
        if row["result_ciphertext"] is not None:
            result = json.loads(
                self.cipher.decrypt(
                    bytes(row["result_ciphertext"]),
                    associated_data=f"chat-run:{row['run_id']}:{row['owner_hash']}",
                    key_version=row["key_version"],
                )
            )
        return ChatRun(row["run_id"], row["status"], result, row["error_code"])

    async def cleanup(self, *, batch_size: int = 500) -> int:
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                text("""
                DELETE FROM agent_chat_runs WHERE run_id IN (
                  SELECT run_id FROM agent_chat_runs WHERE expires_at < CURRENT_TIMESTAMP
                  ORDER BY expires_at LIMIT :batch FOR UPDATE SKIP LOCKED
                ) RETURNING run_id
            """),
                {"batch": batch_size},
            )
            return len(result.all())
