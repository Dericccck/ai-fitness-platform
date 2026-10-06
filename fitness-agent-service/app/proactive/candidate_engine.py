"""把训练/预约事件和时间窗口转换成主动服务候选。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from app.infrastructure.database import Database

from .candidate_repository import ProactiveCandidateRepository
from .candidates import CandidateType, candidate_expiry
from .events import ProactiveEventMessage


class ProactiveCandidateEngine:
    def __init__(self, database: Database, repository: ProactiveCandidateRepository) -> None:
        self.database = database
        self.repository = repository

    async def from_event(self, event: ProactiveEventMessage) -> int:
        async with self.database.engine.begin() as connection:
            return await self.enqueue_event(connection, event)

    async def enqueue_event(self, connection: Any, event: ProactiveEventMessage) -> int:
        """在调用方 Inbox 事务内生成候选，避免事件处理与候选双写。"""

        specs = _candidate_specs(event)
        created = 0
        for spec in specs:
            candidate_id = await self.repository.enqueue(
                connection,
                candidate_type=spec["candidate_type"].value,
                reason_code=spec["reason_code"],
                subject_user_id=spec["subject_user_id"],
                organization_id=event.organization_id,
                aggregate_type=spec["aggregate_type"],
                aggregate_id=event.aggregate_id,
                trigger_source="EVENT",
                trigger_event_ids=(event.event_id,),
                facts=spec["facts"],
                dedupe_key=spec["dedupe_key"],
                expires_at=candidate_expiry(spec["candidate_type"]),
                priority=spec.get("priority", "NORMAL"),
            )
            created += int(candidate_id is not None)
        return created


class _CandidateSpec(TypedDict):
    candidate_type: CandidateType
    reason_code: str
    subject_user_id: str
    aggregate_type: str
    dedupe_key: str
    facts: dict[str, Any]
    priority: str


def _candidate_specs(event: ProactiveEventMessage) -> list[_CandidateSpec]:
    payload = event.payload
    student_id = _optional_id(payload, "studentId", "student_id")
    coach_id = _optional_id(payload, "coachId", "coach_id")
    day = datetime.now(UTC).date().isoformat()
    common_facts = {
        "event_type": event.event_type,
        "aggregate_version": event.aggregate_version,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "status": payload.get("status"),
        "plan_id": payload.get("planId") or payload.get("plan_id"),
        "day_id": payload.get("dayId") or payload.get("day_id"),
    }
    specs: list[_CandidateSpec] = []
    if event.event_type == "TRAINING_DAY_SKIPPED" and student_id:
        specs.append(
            {
                "candidate_type": CandidateType.TRAINING_ADHERENCE,
                "reason_code": "TRAINING_DAY_SKIPPED",
                "subject_user_id": student_id,
                "aggregate_type": "TRAINING_PLAN",
                "dedupe_key": f"adherence:{student_id}:{event.aggregate_id}:{day}",
                "facts": common_facts,
                "priority": "NORMAL",
            }
        )
        if coach_id:
            specs.append(
                {
                    "candidate_type": CandidateType.COACH_STUDENT_ATTENTION,
                    "reason_code": "STUDENT_TRAINING_SKIPPED",
                    "subject_user_id": coach_id,
                    "aggregate_type": "TRAINING_PLAN",
                    "dedupe_key": f"coach-attention:{coach_id}:{event.aggregate_id}:{day}",
                    "facts": common_facts,
                    "priority": "HIGH",
                }
            )
    elif event.event_type == "TRAINING_PLAN_COMPLETED" and student_id:
        specs.append(
            {
                "candidate_type": CandidateType.TRAINING_CYCLE_REVIEW,
                "reason_code": "TRAINING_CYCLE_COMPLETED",
                "subject_user_id": student_id,
                "aggregate_type": "TRAINING_PLAN",
                "dedupe_key": f"cycle-review:{student_id}:{event.aggregate_id}:{event.aggregate_version or 1}",
                "facts": common_facts,
                "priority": "NORMAL",
            }
        )
    elif event.event_type == "APPOINTMENT_CANCELLED" and student_id:
        specs.append(
            {
                "candidate_type": CandidateType.APPOINTMENT_FOLLOW_UP,
                "reason_code": "APPOINTMENT_CANCELLED",
                "subject_user_id": student_id,
                "aggregate_type": "APPOINTMENT",
                "dedupe_key": f"appointment-follow-up:{event.event_id}:{student_id}",
                "facts": common_facts,
                "priority": "NORMAL",
            }
        )
    return specs


def _optional_id(payload: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
