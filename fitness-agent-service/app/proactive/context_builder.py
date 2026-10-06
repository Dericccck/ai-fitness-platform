"""为主动决策构建最小化、可审计的第一方上下文。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import text

from app.infrastructure.database import Database

from .candidates import CandidateRecord

CONTEXT_VERSION = "proactive-context-v1"


class ProactiveContextBuilder:
    """只读取 Agent 自有数据，不在后台伪造用户身份调用业务接口。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def build(self, candidate: CandidateRecord) -> tuple[CandidateRecord, dict[str, Any]]:
        async with self.database.engine.connect() as connection:
            history = (
                (
                    await connection.execute(
                        text(
                            """SELECT event_id, event_type, aggregate_id,
                                      COALESCE(occurred_at, created_at) AS happened_at
                               FROM agent_proactive_event_inbox
                               WHERE organization_id=:organization_id
                                 AND (payload->>'studentId'=:subject_id
                                      OR payload->>'coachId'=:subject_id)
                               ORDER BY COALESCE(occurred_at, created_at) DESC
                               LIMIT 12"""
                        ),
                        {
                            "organization_id": candidate.organization_id,
                            "subject_id": candidate.subject_user_id,
                        },
                    )
                )
                .mappings()
                .all()
            )
            engagement = (
                (
                    await connection.execute(
                        text(
                            """SELECT response_type, COUNT(*) AS count
                               FROM agent_proactive_responses
                               WHERE subject_user_id=:subject_id
                               GROUP BY response_type"""
                        ),
                        {"subject_id": candidate.subject_user_id},
                    )
                )
                .mappings()
                .all()
            )
            notification_count = await connection.scalar(
                text(
                    """SELECT COUNT(*) FROM agent_notification_outbox
                       WHERE subject_user_id=:subject_id
                         AND created_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
                         AND status='PUBLISHED'"""
                ),
                {"subject_id": candidate.subject_user_id},
            )

        context: dict[str, Any] = {
            "context_version": CONTEXT_VERSION,
            "recent_events": [
                {
                    "event_ref": f"event:{row['event_id']}",
                    "event_type": str(row["event_type"]),
                    "aggregate_id": str(row["aggregate_id"]),
                    "happened_at": row["happened_at"].isoformat(),
                }
                for row in history
            ],
            "engagement_counts": {
                str(row["response_type"]): int(row["count"]) for row in engagement
            },
            "published_notifications_last_7d": int(notification_count or 0),
        }
        return replace(candidate, facts={**candidate.facts, "agent_context": context}), context
