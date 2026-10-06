"""从可靠 Inbox 中聚合时间/状态信号，生成主动服务候选。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from app.infrastructure.database import Database

from .candidate_repository import ProactiveCandidateRepository
from .candidates import CandidateType, candidate_expiry


@dataclass(frozen=True)
class SchedulerRunResult:
    inactivity: int
    mismatch: int
    weekly_review: int
    coach_digest: int
    operations_digest: int


class ProactiveCandidateScheduler:
    """时间任务只产生幂等候选，不直接调用 LLM 或发送通知。"""

    def __init__(
        self,
        database: Database,
        repository: ProactiveCandidateRepository,
        *,
        inactivity_days: int = 3,
        mismatch_skip_count: int = 2,
        default_timezone: str = "Asia/Shanghai",
        digest_hour_local: int = 8,
    ) -> None:
        self.database = database
        self.repository = repository
        self.inactivity_days = inactivity_days
        self.mismatch_skip_count = mismatch_skip_count
        try:
            self.default_timezone = ZoneInfo(default_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("主动调度默认时区无效") from exc
        if digest_hour_local < 0 or digest_hour_local > 23:
            raise ValueError("主动摘要小时必须在 0 到 23 之间")
        self.digest_hour_local = digest_hour_local

    async def run_once(self, now: datetime | None = None) -> SchedulerRunResult:
        current = now or datetime.now(UTC)
        local_current = current.astimezone(self.default_timezone)
        counters = {
            "inactivity": 0,
            "mismatch": 0,
            "weekly_review": 0,
            "coach_digest": 0,
            "operations_digest": 0,
        }
        async with self.database.engine.begin() as connection:
            inactivity_rows = (
                (
                    await connection.execute(
                        text(
                            """
                        WITH latest AS (
                            SELECT DISTINCT ON (organization_id, payload->>'studentId')
                                   event_id, organization_id, aggregate_id,
                                   payload->>'studentId' AS student_id,
                                   payload->>'coachId' AS coach_id,
                                   COALESCE(occurred_at, created_at) AS happened_at
                            FROM agent_proactive_event_inbox
                            WHERE event_type IN ('TRAINING_DAY_COMPLETED','TRAINING_DAY_SKIPPED')
                              AND payload->>'studentId' IS NOT NULL
                            ORDER BY organization_id, payload->>'studentId',
                                     COALESCE(occurred_at, created_at) DESC
                        )
                        SELECT * FROM latest
                        WHERE NOT EXISTS (
                            SELECT 1 FROM agent_proactive_event_inbox AS completed
                            WHERE completed.organization_id=latest.organization_id
                              AND completed.aggregate_id=latest.aggregate_id
                              AND completed.event_type='TRAINING_PLAN_COMPLETED'
                        )
                        """
                        )
                    )
                )
                .mappings()
                .all()
            )
            for row in inactivity_rows:
                happened_at = row["happened_at"]
                if happened_at.tzinfo is None:
                    happened_at = happened_at.replace(tzinfo=UTC)
                if happened_at > current - timedelta(days=self.inactivity_days):
                    continue
                week = local_current.strftime("%G-W%V")
                candidate_id = await self.repository.enqueue(
                    connection,
                    candidate_type=CandidateType.TRAINING_ADHERENCE.value,
                    reason_code="TRAINING_INACTIVE",
                    subject_user_id=str(row["student_id"]),
                    organization_id=str(row["organization_id"]),
                    aggregate_type="TRAINING_PLAN",
                    aggregate_id=str(row["aggregate_id"]),
                    trigger_source="SCHEDULE",
                    trigger_event_ids=(str(row["event_id"]),),
                    facts={
                        "last_training_at": happened_at.isoformat(),
                        "inactive_days": self.inactivity_days,
                    },
                    dedupe_key=f"inactivity:{row['organization_id']}:{row['student_id']}:{week}",
                    expires_at=candidate_expiry(CandidateType.TRAINING_ADHERENCE, current),
                )
                counters["inactivity"] += int(candidate_id is not None)

            skip_rows = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT organization_id, aggregate_id,
                               payload->>'studentId' AS student_id,
                               payload->>'coachId' AS coach_id,
                               COUNT(*) AS skip_count, array_agg(event_id ORDER BY created_at) AS event_ids
                        FROM agent_proactive_event_inbox
                        WHERE event_type='TRAINING_DAY_SKIPPED'
                          AND created_at >= CURRENT_TIMESTAMP - INTERVAL '14 days'
                          AND NOT EXISTS (
                              SELECT 1 FROM agent_proactive_event_inbox AS completed
                              WHERE completed.organization_id=agent_proactive_event_inbox.organization_id
                                AND completed.aggregate_id=agent_proactive_event_inbox.aggregate_id
                                AND completed.event_type='TRAINING_PLAN_COMPLETED'
                          )
                        GROUP BY organization_id, aggregate_id,
                                 payload->>'studentId', payload->>'coachId'
                        HAVING COUNT(*) >= :minimum
                        """
                        ),
                        {"minimum": self.mismatch_skip_count},
                    )
                )
                .mappings()
                .all()
            )
            window = local_current.strftime("%G-W%V")
            for row in skip_rows:
                student_id = row["student_id"]
                if not student_id:
                    continue
                candidate_id = await self.repository.enqueue(
                    connection,
                    candidate_type=CandidateType.TRAINING_PLAN_MISMATCH.value,
                    reason_code="REPEATED_TRAINING_SKIPS",
                    subject_user_id=str(student_id),
                    organization_id=str(row["organization_id"]),
                    aggregate_type="TRAINING_PLAN",
                    aggregate_id=str(row["aggregate_id"]),
                    trigger_source="SCHEDULE",
                    trigger_event_ids=tuple(row["event_ids"]),
                    facts={"skip_count": int(row["skip_count"]), "lookback_days": 14},
                    dedupe_key=f"plan-mismatch:{row['aggregate_id']}:{window}",
                    expires_at=candidate_expiry(CandidateType.TRAINING_PLAN_MISMATCH, current),
                    priority="HIGH",
                )
                counters["mismatch"] += int(candidate_id is not None)

                coach_id = row["coach_id"]
                if coach_id:
                    coach_candidate = await self.repository.enqueue(
                        connection,
                        candidate_type=CandidateType.COACH_STUDENT_ATTENTION.value,
                        reason_code="STUDENT_REPEATED_TRAINING_SKIPS",
                        subject_user_id=str(coach_id),
                        organization_id=str(row["organization_id"]),
                        aggregate_type="TRAINING_PLAN",
                        aggregate_id=str(row["aggregate_id"]),
                        trigger_source="SCHEDULE",
                        trigger_event_ids=tuple(row["event_ids"]),
                        facts={"skip_count": int(row["skip_count"]), "lookback_days": 14},
                        dedupe_key=f"coach-student-attention:{coach_id}:{row['aggregate_id']}:{window}",
                        expires_at=candidate_expiry(CandidateType.COACH_STUDENT_ATTENTION, current),
                        priority="HIGH",
                    )
                    counters["coach_digest"] += int(coach_candidate is not None)

            # 周一汇总滚动七天；计划完成事件仍会即时生成周期复盘。
            review_rows = (
                []
                if local_current.weekday() != 0
                else (
                    (
                        await connection.execute(
                            text(
                                """
                        SELECT organization_id, aggregate_id,
                               payload->>'studentId' AS student_id,
                               COUNT(*) FILTER (WHERE event_type='TRAINING_DAY_COMPLETED') AS completed,
                               COUNT(*) FILTER (WHERE event_type='TRAINING_DAY_SKIPPED') AS skipped,
                               array_agg(event_id ORDER BY created_at) AS event_ids
                        FROM agent_proactive_event_inbox
                        WHERE event_type IN ('TRAINING_DAY_COMPLETED','TRAINING_DAY_SKIPPED')
                          AND created_at >= CURRENT_TIMESTAMP - INTERVAL '7 days'
                        GROUP BY organization_id, aggregate_id, payload->>'studentId'
                        """
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            )
            for row in review_rows:
                if not row["student_id"]:
                    continue
                candidate_id = await self.repository.enqueue(
                    connection,
                    candidate_type=CandidateType.TRAINING_CYCLE_REVIEW.value,
                    reason_code="WEEKLY_TRAINING_REVIEW",
                    subject_user_id=str(row["student_id"]),
                    organization_id=str(row["organization_id"]),
                    aggregate_type="TRAINING_PLAN",
                    aggregate_id=str(row["aggregate_id"]),
                    trigger_source="SCHEDULE",
                    trigger_event_ids=tuple(row["event_ids"]),
                    facts={
                        "completed": int(row["completed"]),
                        "skipped": int(row["skipped"]),
                        "window_days": 7,
                    },
                    dedupe_key=f"weekly-review:{row['organization_id']}:{row['student_id']}:{window}",
                    expires_at=candidate_expiry(CandidateType.TRAINING_CYCLE_REVIEW, current),
                )
                counters["weekly_review"] += int(candidate_id is not None)

            digest_rows = (
                []
                if local_current.hour != self.digest_hour_local
                else (
                    (
                        await connection.execute(
                            text(
                                """SELECT organization_id, payload->>'coachId' AS coach_id,
                               COUNT(*) FILTER (WHERE event_type='TRAINING_DAY_SKIPPED') AS skipped,
                               COUNT(*) FILTER (WHERE event_type='TRAINING_PLAN_REVIEW_REQUIRED') AS reviews,
                               array_agg(event_id ORDER BY created_at) AS event_ids
                               FROM agent_proactive_event_inbox
                               WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                                 AND payload->>'coachId' IS NOT NULL
                               GROUP BY organization_id, payload->>'coachId'"""
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            )
            day = local_current.date().isoformat()
            for row in digest_rows:
                candidate_id = await self.repository.enqueue(
                    connection,
                    candidate_type=CandidateType.COACH_DAILY_DIGEST.value,
                    reason_code="COACH_DAILY_SUMMARY",
                    subject_user_id=str(row["coach_id"]),
                    organization_id=str(row["organization_id"]),
                    aggregate_type="COACH_DASHBOARD",
                    aggregate_id=str(row["coach_id"]),
                    trigger_source="SCHEDULE",
                    trigger_event_ids=tuple(row["event_ids"]),
                    facts={"skipped": int(row["skipped"]), "reviews": int(row["reviews"])},
                    dedupe_key=f"coach-digest:{row['organization_id']}:{row['coach_id']}:{day}",
                    expires_at=candidate_expiry(CandidateType.COACH_DAILY_DIGEST, current),
                )
                counters["coach_digest"] += int(candidate_id is not None)

            operation_rows = (
                (
                    await connection.execute(
                        text(
                            """SELECT subscription.subject_user_id, subscription.organization_id,
                               subscription.timezone,
                               COUNT(inbox.event_id) AS event_count,
                               COUNT(*) FILTER (WHERE inbox.event_type='APPOINTMENT_CANCELLED') AS cancellations,
                               COUNT(*) FILTER (WHERE inbox.event_type='TRAINING_DAY_SKIPPED') AS skipped,
                               array_remove(array_agg(inbox.event_id), NULL) AS event_ids
                               FROM agent_proactive_subscriptions AS subscription
                               LEFT JOIN agent_proactive_event_inbox AS inbox
                                 ON inbox.organization_id=subscription.organization_id
                                AND inbox.created_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                               WHERE subscription.audience='OPERATIONS' AND subscription.enabled=TRUE
                               GROUP BY subscription.subject_user_id, subscription.organization_id"""
                        )
                    )
                )
                .mappings()
                .all()
            )
            for row in operation_rows:
                if int(row["event_count"]) == 0:
                    continue
                try:
                    operation_local = current.astimezone(ZoneInfo(str(row["timezone"])))
                except ZoneInfoNotFoundError:
                    operation_local = local_current
                if operation_local.hour != self.digest_hour_local:
                    continue
                operation_day = operation_local.date().isoformat()
                candidate_id = await self.repository.enqueue(
                    connection,
                    candidate_type=CandidateType.OPERATIONS_DAILY_DIGEST.value,
                    reason_code="OPERATIONS_DAILY_SUMMARY",
                    subject_user_id=str(row["subject_user_id"]),
                    organization_id=str(row["organization_id"]),
                    aggregate_type="ORGANIZATION",
                    aggregate_id=str(row["organization_id"]),
                    trigger_source="SCHEDULE",
                    trigger_event_ids=tuple(row["event_ids"] or ()),
                    facts={
                        "event_count": int(row["event_count"]),
                        "cancellations": int(row["cancellations"]),
                        "skipped": int(row["skipped"]),
                    },
                    dedupe_key=f"operations-digest:{row['organization_id']}:{row['subject_user_id']}:{operation_day}",
                    expires_at=candidate_expiry(CandidateType.OPERATIONS_DAILY_DIGEST, current),
                )
                counters["operations_digest"] += int(candidate_id is not None)
        return SchedulerRunResult(**counters)
