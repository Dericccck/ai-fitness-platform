"""主动式 Agent 候选、决策、消息快照和反馈的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from .candidates import CandidateRecord, ProactiveAgentOutput, ProactiveMessageSnapshot


class ProactiveCandidateRepository:
    async def upsert_subscription(
        self,
        connection: Any,
        *,
        subject_user_id: str,
        organization_id: str,
        audience: str,
        enabled: bool,
        timezone: str,
    ) -> None:
        await connection.execute(
            text(
                """INSERT INTO agent_proactive_subscriptions (
                    subject_user_id, organization_id, audience, enabled, timezone
                ) VALUES (:subject, :organization, :audience, :enabled, :timezone)
                ON CONFLICT (subject_user_id, organization_id, audience) DO UPDATE SET
                    enabled=EXCLUDED.enabled, timezone=EXCLUDED.timezone,
                    updated_at=CURRENT_TIMESTAMP"""
            ),
            {
                "subject": subject_user_id,
                "organization": organization_id,
                "audience": audience,
                "enabled": enabled,
                "timezone": timezone,
            },
        )

    async def list_dead(
        self,
        connection: Any,
        *,
        limit: int = 50,
        organization_ids: tuple[str, ...] | None = None,
    ) -> list[CandidateRecord]:
        organization_filter = (
            ""
            if organization_ids is None
            else " AND organization_id = ANY(CAST(:organizations AS TEXT[]))"
        )
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT * FROM agent_proactive_candidates WHERE status='DEAD'"
                        + organization_filter
                        + " ORDER BY updated_at DESC LIMIT :limit"
                    ),
                    {"limit": limit, "organizations": list(organization_ids or ())},
                )
            )
            .mappings()
            .all()
        )
        return [_from_row(row) for row in rows]

    async def replay_dead(
        self,
        connection: Any,
        *,
        candidate_id: str,
        operator_id: str,
        reason: str,
        organization_ids: tuple[str, ...] | None = None,
    ) -> bool:
        organization_filter = (
            ""
            if organization_ids is None
            else " AND organization_id = ANY(CAST(:organizations AS TEXT[]))"
        )
        result = await connection.execute(
            text(
                """UPDATE agent_proactive_candidates SET status='PENDING', attempt_count=0,
                   available_at=CURRENT_TIMESTAMP, locked_by=NULL, locked_at=NULL,
                   last_error_code=NULL, last_replayed_by=:operator,
                   last_replay_reason=:reason, replayed_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP WHERE id=:id AND status='DEAD'"""
                + organization_filter
            ),
            {
                "id": candidate_id,
                "operator": operator_id,
                "reason": reason[:256],
                "organizations": list(organization_ids or ()),
            },
        )
        return int(result.rowcount or 0) == 1

    async def enqueue(
        self,
        connection: Any,
        *,
        candidate_type: str,
        reason_code: str,
        subject_user_id: str,
        organization_id: str,
        aggregate_type: str,
        aggregate_id: str,
        trigger_source: str,
        trigger_event_ids: tuple[str, ...],
        facts: dict[str, Any],
        dedupe_key: str,
        expires_at: datetime,
        priority: str = "NORMAL",
        available_at: datetime | None = None,
    ) -> str | None:
        candidate_id = uuid4().hex
        result = await connection.execute(
            text(
                """
                INSERT INTO agent_proactive_candidates (
                    id, candidate_type, reason_code, subject_user_id, organization_id,
                    aggregate_type, aggregate_id, trigger_source, trigger_event_ids,
                    facts, dedupe_key, available_at, expires_at, priority
                ) VALUES (
                    :id, :candidate_type, :reason_code, :subject_user_id, :organization_id,
                    :aggregate_type, :aggregate_id, :trigger_source,
                    CAST(:trigger_event_ids AS JSONB), CAST(:facts AS JSONB),
                    :dedupe_key, COALESCE(:available_at, CURRENT_TIMESTAMP), :expires_at, :priority
                ) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id
                """
            ),
            {
                "id": candidate_id,
                "candidate_type": candidate_type,
                "reason_code": reason_code,
                "subject_user_id": subject_user_id,
                "organization_id": organization_id,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "trigger_source": trigger_source,
                "trigger_event_ids": json.dumps(trigger_event_ids),
                "facts": json.dumps(facts, ensure_ascii=False),
                "dedupe_key": dedupe_key,
                "expires_at": expires_at,
                "priority": priority,
                "available_at": available_at,
            },
        )
        value = result.scalar_one_or_none()
        return str(value) if value is not None else None

    async def save_context_snapshot(
        self,
        connection: Any,
        *,
        candidate_id: str,
        context: dict[str, Any],
        context_version: str,
    ) -> str:
        snapshot_id = uuid4().hex
        await connection.execute(
            text(
                """INSERT INTO agent_proactive_context_snapshots (
                    id, candidate_id, context_version, context
                ) VALUES (:id, :candidate_id, :context_version, CAST(:context AS JSONB))"""
            ),
            {
                "id": snapshot_id,
                "candidate_id": candidate_id,
                "context_version": context_version,
                "context": json.dumps(context, ensure_ascii=False),
            },
        )
        return snapshot_id

    async def claim_batch(
        self, connection: Any, *, worker_id: str, limit: int, lock_timeout_seconds: int = 300
    ) -> list[CandidateRecord]:
        rows = (
            (
                await connection.execute(
                    text(
                        """
                        WITH picked AS (
                            SELECT id FROM agent_proactive_candidates
                            WHERE expires_at > CURRENT_TIMESTAMP AND (
                                (status IN ('PENDING','DEFERRED','RETRYABLE_FAILED')
                                 AND available_at <= CURRENT_TIMESTAMP)
                                OR (status = 'PROCESSING' AND locked_at <= CURRENT_TIMESTAMP
                                    - (:lock_timeout * INTERVAL '1 second'))
                            )
                            ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END,
                                     created_at, id
                            FOR UPDATE SKIP LOCKED LIMIT :limit
                        )
                        UPDATE agent_proactive_candidates AS candidate
                        SET status='PROCESSING', locked_by=:worker_id,
                            locked_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                        FROM picked WHERE candidate.id=picked.id RETURNING candidate.*
                        """
                    ),
                    {"worker_id": worker_id, "limit": limit, "lock_timeout": lock_timeout_seconds},
                )
            )
            .mappings()
            .all()
        )
        await connection.execute(
            text(
                """UPDATE agent_proactive_candidates SET status='EXPIRED',
                   completed_at=CURRENT_TIMESTAMP, locked_by=NULL, locked_at=NULL,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE status IN ('PENDING','DEFERRED','RETRYABLE_FAILED')
                     AND expires_at <= CURRENT_TIMESTAMP"""
            )
        )
        return [_from_row(row) for row in rows]

    async def save_decision(
        self,
        connection: Any,
        *,
        candidate: CandidateRecord,
        output: ProactiveAgentOutput,
        model_name: str,
        prompt_version: str,
        policy_version: str,
        input_digest: str,
        output_digest: str,
        safety_status: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> int:
        result = await connection.execute(
            text(
                """
                INSERT INTO agent_proactive_decisions (
                    candidate_id, decision_version, decision, reason_code, strategy,
                    recommended_action, confidence, evidence_refs, model_name,
                    prompt_version, policy_version, input_digest, output_digest,
                    safety_status, input_tokens, output_tokens
                ) VALUES (
                    :candidate_id,
                    (SELECT COALESCE(MAX(existing.decision_version), 0) + 1
                       FROM agent_proactive_decisions AS existing
                      WHERE existing.candidate_id=:candidate_id),
                    :decision, :reason_code, :strategy,
                    :recommended_action, :confidence, CAST(:evidence_refs AS JSONB),
                    :model_name, :prompt_version, :policy_version, :input_digest,
                    :output_digest, :safety_status, :input_tokens, :output_tokens
                )
                RETURNING id
                """
            ),
            {
                "candidate_id": candidate.id,
                "decision": output.decision.value,
                "reason_code": output.reason_code,
                "strategy": output.strategy,
                "recommended_action": output.recommended_action.value,
                "confidence": output.confidence,
                "evidence_refs": json.dumps(output.evidence_refs),
                "model_name": model_name,
                "prompt_version": prompt_version,
                "policy_version": policy_version,
                "input_digest": input_digest,
                "output_digest": output_digest,
                "safety_status": safety_status,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
        return int(result.scalar_one())

    async def create_message_snapshot(
        self,
        connection: Any,
        *,
        candidate: CandidateRecord,
        output: ProactiveAgentOutput,
        template_key: str,
        cta_type: str | None,
        content_source: str,
    ) -> ProactiveMessageSnapshot:
        snapshot_id = uuid4().hex
        cta_payload = {"candidate_id": candidate.id, "aggregate_id": candidate.aggregate_id}
        row = (
            (
                await connection.execute(
                    text(
                        """
                        INSERT INTO agent_proactive_message_snapshots (
                            id, candidate_id, channel, template_key, title, body,
                            cta_type, cta_payload, content_source
                        ) VALUES (
                            :id, :candidate_id, 'IN_APP', :template_key, :title, :body,
                            :cta_type, CAST(:cta_payload AS JSONB), :content_source
                        ) ON CONFLICT (candidate_id, channel) DO UPDATE SET
                            title=EXCLUDED.title, body=EXCLUDED.body,
                            cta_type=EXCLUDED.cta_type, cta_payload=EXCLUDED.cta_payload,
                            content_source=EXCLUDED.content_source
                        RETURNING *
                        """
                    ),
                    {
                        "id": snapshot_id,
                        "candidate_id": candidate.id,
                        "template_key": template_key,
                        "title": output.title,
                        "body": output.body,
                        "cta_type": cta_type,
                        "cta_payload": json.dumps(cta_payload),
                        "content_source": content_source,
                    },
                )
            )
            .mappings()
            .one()
        )
        return _snapshot_from_row(row)

    async def get_message_snapshot(
        self, connection: Any, *, snapshot_id: str
    ) -> ProactiveMessageSnapshot | None:
        row = (
            (
                await connection.execute(
                    text("SELECT * FROM agent_proactive_message_snapshots WHERE id=:id"),
                    {"id": snapshot_id},
                )
            )
            .mappings()
            .first()
        )
        return _snapshot_from_row(row) if row else None

    async def complete(
        self, connection: Any, *, candidate_id: str, worker_id: str, status: str
    ) -> bool:
        result = await connection.execute(
            text(
                """UPDATE agent_proactive_candidates SET status=:status,
                   completed_at=CASE WHEN :status IN ('COMPLETED','IGNORED','EXPIRED')
                                     THEN CURRENT_TIMESTAMP ELSE completed_at END,
                   locked_by=NULL, locked_at=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id AND status='PROCESSING' AND locked_by=:worker_id"""
            ),
            {"id": candidate_id, "worker_id": worker_id, "status": status},
        )
        return int(result.rowcount or 0) == 1

    async def mark_notification_outcome(
        self,
        connection: Any,
        *,
        candidate_id: str,
        status: str,
        error_code: str | None = None,
    ) -> bool:
        if status not in {"COMPLETED", "IGNORED", "DEAD"}:
            raise ValueError("主动候选通知结果状态无效")
        result = await connection.execute(
            text(
                """UPDATE agent_proactive_candidates
                   SET status=:status, completed_at=CURRENT_TIMESTAMP,
                       last_error_code=:error_code, updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id AND status='NOTIFICATION_QUEUED'"""
            ),
            {"id": candidate_id, "status": status, "error_code": error_code},
        )
        return int(result.rowcount or 0) == 1

    async def defer(
        self, connection: Any, *, candidate_id: str, worker_id: str, minutes: int
    ) -> bool:
        result = await connection.execute(
            text(
                """UPDATE agent_proactive_candidates SET status='DEFERRED',
                   available_at=CURRENT_TIMESTAMP + (:minutes * INTERVAL '1 minute'),
                   locked_by=NULL, locked_at=NULL, updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id AND status='PROCESSING' AND locked_by=:worker_id"""
            ),
            {"id": candidate_id, "worker_id": worker_id, "minutes": minutes},
        )
        return int(result.rowcount or 0) == 1

    async def retry(
        self,
        connection: Any,
        *,
        candidate: CandidateRecord,
        worker_id: str,
        error_code: str,
        max_attempts: int,
        delay_seconds: int,
    ) -> bool:
        result = await connection.execute(
            text(
                """UPDATE agent_proactive_candidates
                   SET attempt_count=attempt_count+1,
                       status=CASE WHEN attempt_count+1 >= :max_attempts
                                   THEN 'DEAD' ELSE 'RETRYABLE_FAILED' END,
                       available_at=CURRENT_TIMESTAMP + (:delay * INTERVAL '1 second'),
                       last_error_code=:error_code, locked_by=NULL, locked_at=NULL,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE id=:id AND status='PROCESSING' AND locked_by=:worker_id"""
            ),
            {
                "id": candidate.id,
                "worker_id": worker_id,
                "error_code": error_code[:128],
                "max_attempts": max_attempts,
                "delay": delay_seconds,
            },
        )
        return int(result.rowcount or 0) == 1

    async def record_response(
        self,
        connection: Any,
        *,
        candidate_id: str,
        subject_user_id: str,
        response_type: str,
        response_value: str | None,
        conversation_id: str | None,
        notification_id: str | None,
    ) -> bool:
        result = await connection.execute(
            text(
                """INSERT INTO agent_proactive_responses (
                    candidate_id, notification_id, subject_user_id, response_type,
                    response_value, conversation_id
                ) VALUES (:candidate_id, :notification_id, :subject_user_id,
                          :response_type, :response_value, :conversation_id)
                ON CONFLICT (candidate_id, subject_user_id, response_type) DO NOTHING"""
            ),
            {
                "candidate_id": candidate_id,
                "notification_id": notification_id,
                "subject_user_id": subject_user_id,
                "response_type": response_type,
                "response_value": response_value,
                "conversation_id": conversation_id,
            },
        )
        return int(result.rowcount or 0) == 1

    async def get_for_subject(
        self, connection: Any, *, candidate_id: str, subject_user_id: str
    ) -> CandidateRecord | None:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT * FROM agent_proactive_candidates WHERE id=:id AND subject_user_id=:subject"
                    ),
                    {"id": candidate_id, "subject": subject_user_id},
                )
            )
            .mappings()
            .first()
        )
        return _from_row(row) if row else None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _from_row(row: Any) -> CandidateRecord:
    return CandidateRecord(
        id=str(row["id"]),
        candidate_type=str(row["candidate_type"]),
        reason_code=str(row["reason_code"]),
        subject_user_id=str(row["subject_user_id"]),
        organization_id=str(row["organization_id"]),
        aggregate_type=str(row["aggregate_type"]),
        aggregate_id=str(row["aggregate_id"]),
        trigger_source=str(row["trigger_source"]),
        trigger_event_ids=tuple(row["trigger_event_ids"]),
        facts=dict(row["facts"]),
        dedupe_key=str(row["dedupe_key"]),
        priority=str(row["priority"]),
        status=str(row["status"]),
        available_at=_utc(row["available_at"]),
        expires_at=_utc(row["expires_at"]),
        attempt_count=int(row["attempt_count"]),
        locked_by=str(row["locked_by"]) if row["locked_by"] else None,
        locked_at=_utc(row["locked_at"]) if row["locked_at"] else None,
        last_error_code=str(row["last_error_code"]) if row["last_error_code"] else None,
        created_at=_utc(row["created_at"]),
        updated_at=_utc(row["updated_at"]),
    )


def _snapshot_from_row(row: Any) -> ProactiveMessageSnapshot:
    return ProactiveMessageSnapshot(
        id=str(row["id"]),
        candidate_id=str(row["candidate_id"]),
        channel=str(row["channel"]),
        template_key=str(row["template_key"]),
        template_version=int(row["template_version"]),
        title=str(row["title"]),
        body=str(row["body"]),
        cta_type=str(row["cta_type"]) if row["cta_type"] else None,
        cta_payload=dict(row["cta_payload"] or {}),
        content_source=str(row["content_source"]),
    )
