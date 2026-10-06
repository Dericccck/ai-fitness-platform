import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.decision.action_worker import DecisionActionWorker
from app.decision.models import DecisionCaseRecord, DecisionCaseStatus


class Transaction:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *args: object) -> None:
        return None


class Engine:
    def begin(self) -> Transaction:
        return Transaction()


class Database:
    engine = Engine()


class Notifications:
    async def enqueue_on_connection(self, *args: Any, **kwargs: Any) -> None:
        return None


class Repository:
    def __init__(self, action: dict[str, Any], *, fail_execution: bool = False) -> None:
        self.action = action
        self.status = DecisionCaseStatus.APPROVED
        self.fail_execution = fail_execution
        self.transitions: list[DecisionCaseStatus] = []

    async def expire_waiting_cases(self, connection: object, **kwargs: Any) -> int:
        return 0

    async def claim_actions(self, connection: object, **kwargs: Any) -> list[dict[str, Any]]:
        return [self.action]

    async def get_case(self, connection: object, **kwargs: Any) -> DecisionCaseRecord:
        now = datetime.now(UTC)
        return DecisionCaseRecord(
            id="case-1",
            organization_id="org-1",
            objective_id="objective-1",
            policy_id="policy-1",
            anomaly_id="anomaly-1",
            title="经营异常",
            status=self.status.value,
            risk_level="L1",
            diagnosis={},
            selected_option_id="option-1",
            trace_id=None,
            expires_at=now + timedelta(days=1),
            created_at=now,
            updated_at=now,
        )

    async def transition_case(
        self, connection: object, *, target: DecisionCaseStatus, **kwargs: Any
    ) -> None:
        self.status = target
        self.transitions.append(target)

    async def create_operations_task(self, connection: object, **kwargs: Any) -> str:
        if self.fail_execution:
            raise RuntimeError("downstream unavailable")
        return "task-1"

    async def complete_action(self, connection: object, **kwargs: Any) -> bool:
        return True

    async def fail_action(self, connection: object, **kwargs: Any) -> str:
        return "FINAL_FAILED"


def action(payload: dict[str, Any], *, valid_digest: bool = True) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return {
        "id": "action-1",
        "case_id": "case-1",
        "organization_id": "org-1",
        "action_type": "CREATE_OPERATIONS_TASK",
        "payload": payload,
        "payload_digest": digest if valid_digest else "invalid",
        "attempt_count": 1,
    }


async def test_action_worker_advances_case_to_observing_after_success() -> None:
    repository = Repository(action({"title": "跟进异常"}))
    worker = DecisionActionWorker(
        Database(), repository, Notifications(), max_attempts=1, worker_id="test-worker"
    )

    result = await worker.run_once()

    assert result.succeeded == 1
    assert repository.transitions == [
        DecisionCaseStatus.EXECUTING,
        DecisionCaseStatus.OBSERVING,
    ]


async def test_action_worker_marks_case_failed_after_digest_retries_exhausted() -> None:
    repository = Repository(action({"title": "跟进异常"}, valid_digest=False))
    worker = DecisionActionWorker(
        Database(), repository, Notifications(), max_attempts=1, worker_id="test-worker"
    )

    result = await worker.run_once()

    assert result.final_failed == 1
    assert repository.transitions == [
        DecisionCaseStatus.EXECUTING,
        DecisionCaseStatus.EXECUTION_FAILED,
    ]
