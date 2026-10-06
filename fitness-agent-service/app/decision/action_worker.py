"""可靠执行经过策略授权的经营动作。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import structlog

from app.core.metrics import HttpMetrics
from app.infrastructure.database import Database
from app.notifications.outbox import NotificationOutboxRepository

from .models import DecisionCaseStatus
from .repository import DecisionRepository

_logger = structlog.get_logger("decision.action_worker")


@dataclass(frozen=True)
class ActionWorkerResult:
    claimed: int
    succeeded: int
    retried: int
    final_failed: int
    expired_cases: int


class DecisionActionWorker:
    def __init__(
        self,
        database: Database,
        repository: DecisionRepository,
        notifications: NotificationOutboxRepository,
        *,
        batch_size: int = 30,
        max_attempts: int = 5,
        worker_id: str | None = None,
        metrics: HttpMetrics | None = None,
    ) -> None:
        self.database = database
        self.repository = repository
        self.notifications = notifications
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.worker_id = worker_id or f"decision-action:{uuid4()}"
        self.metrics = metrics

    async def run_once(self) -> ActionWorkerResult:
        async with self.database.engine.begin() as connection:
            expired_cases = await self.repository.expire_waiting_cases(
                connection, limit=self.batch_size
            )
            actions = await self.repository.claim_actions(
                connection, worker_id=self.worker_id, limit=self.batch_size
            )
        counters = {"succeeded": 0, "retried": 0, "final_failed": 0}
        for action in actions:
            try:
                await self._mark_case_executing(action)
                await self._execute(action)
                counters["succeeded"] += 1
            except Exception as exc:  # noqa: BLE001
                delay = min(1800, 30 * (2 ** min(int(action["attempt_count"]), 6)))
                async with self.database.engine.begin() as connection:
                    outcome = await self.repository.fail_action(
                        connection,
                        action_id=str(action["id"]),
                        worker_id=self.worker_id,
                        error_code=_stable_error(exc),
                        max_attempts=self.max_attempts,
                        retry_delay_seconds=delay,
                    )
                    if outcome == "FINAL_FAILED":
                        case = await self.repository.get_case(
                            connection,
                            case_id=str(action["case_id"]),
                            organization_ids=(str(action["organization_id"]),),
                        )
                        if case.status == DecisionCaseStatus.EXECUTING.value:
                            await self.repository.transition_case(
                                connection,
                                case=case,
                                target=DecisionCaseStatus.EXECUTION_FAILED,
                                actor_id=f"system:{self.worker_id}",
                                reason_code="ACTION_FINAL_FAILED",
                                metadata={"action_run_id": str(action["id"])},
                            )
                counters["final_failed" if outcome == "FINAL_FAILED" else "retried"] += 1
                _logger.warning(
                    "decision_action_failed",
                    action_type=action["action_type"],
                    error_type=type(exc).__name__,
                    final=outcome == "FINAL_FAILED",
                )
        if self.metrics is not None:
            self.metrics.maintenance_runs_total.labels(
                worker="decision_action", status="succeeded"
            ).inc()
            for outcome, count in counters.items():
                self.metrics.maintenance_items_total.labels(
                    worker="decision_action", outcome=outcome
                ).inc(count)
            self.metrics.maintenance_items_total.labels(
                worker="decision_action", outcome="expired_cases"
            ).inc(expired_cases)
        return ActionWorkerResult(claimed=len(actions), expired_cases=expired_cases, **counters)

    async def _mark_case_executing(self, action: dict[str, Any]) -> None:
        async with self.database.engine.begin() as connection:
            case = await self.repository.get_case(
                connection,
                case_id=str(action["case_id"]),
                organization_ids=(str(action["organization_id"]),),
            )
            if case.status in {
                DecisionCaseStatus.APPROVED.value,
                DecisionCaseStatus.EXECUTION_FAILED.value,
            }:
                await self.repository.transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.EXECUTING,
                    actor_id=f"system:{self.worker_id}",
                    reason_code="ACTION_EXECUTION_STARTED",
                    metadata={"action_run_id": str(action["id"])},
                )

    async def _execute(self, action: dict[str, Any]) -> None:
        payload = dict(action["payload"])
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(encoded.encode()).hexdigest() != action["payload_digest"]:
            raise ValueError("ACTION_PAYLOAD_DIGEST_MISMATCH")
        action_type = str(action["action_type"])
        async with self.database.engine.begin() as connection:
            if action_type in {"CREATE_OPERATIONS_TASK", "CREATE_COACH_FOLLOW_UP"}:
                task_id = await self.repository.create_operations_task(
                    connection,
                    action_id=str(action["id"]),
                    organization_id=str(action["organization_id"]),
                    title=str(payload.get("title") or "经营异常待跟进"),
                    assignee_id=_optional_text(payload.get("assignee_id")),
                )
                result = {"resource_type": "OPERATIONS_TASK", "resource_id": task_id}
            elif action_type == "SEND_IN_APP_REMINDER":
                subject_id = _required_text(payload, "subject_user_id")
                notification_type = _required_text(payload, "notification_type")
                await self.notifications.enqueue_on_connection(
                    connection,
                    notification_type=notification_type,
                    subject_user_id=subject_id,
                    organization_id=str(action["organization_id"]),
                    aggregate_type="DECISION_CASE",
                    aggregate_id=str(action["case_id"]),
                    dedupe_key=f"decision-action:{action['id']}:IN_APP",
                    payload={"decision_case_id": str(action["case_id"])},
                )
                result = {"resource_type": "NOTIFICATION_OUTBOX"}
            else:
                raise ValueError("ACTION_ADAPTER_NOT_REGISTERED")
            if not await self.repository.complete_action(
                connection,
                action_id=str(action["id"]),
                worker_id=self.worker_id,
                result=result,
            ):
                raise RuntimeError("完成动作时丢失动作锁")
            case = await self.repository.get_case(
                connection,
                case_id=str(action["case_id"]),
                organization_ids=(str(action["organization_id"]),),
            )
            if case.status == DecisionCaseStatus.EXECUTING.value:
                await self.repository.transition_case(
                    connection,
                    case=case,
                    target=DecisionCaseStatus.OBSERVING,
                    actor_id=f"system:{self.worker_id}",
                    reason_code="ACTION_SUCCEEDED",
                    metadata={"action_run_id": str(action["id"])},
                )


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ACTION_PAYLOAD_MISSING_{key.upper()}")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _stable_error(exc: Exception) -> str:
    text = str(exc)
    if text.startswith("ACTION_"):
        return text[:128]
    return "ACTION_EXECUTION_FAILED"
