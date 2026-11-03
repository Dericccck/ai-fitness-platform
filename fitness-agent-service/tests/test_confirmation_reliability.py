"""确认执行不能因传输故障、取消或发布回退而重放业务写入。"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor, SupervisorRuntimeError
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.models import ConfirmationAction, ConfirmationRecord, ConfirmationStateError
from app.confirmation.request_body import gateway_confirmation_body
from app.confirmation.service import JAVA_WRITE_TOOL_IDS, ConfirmationService, _execution_tool_input
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import (
    GatewayClient,
    GatewayExecutionUnknownError,
    GatewayForbiddenError,
    GatewayProtocolError,
    GatewayRequestContext,
    GatewayToolOperation,
)

from .test_confirmation_runtime import (
    FakeConfirmationService,
    OneWriteModel,
    RecordingGateway,
    pending_record,
)
from .test_tool_registry import FakeGateway

IDENTITY = AgentIdentity(
    subject="coach-1",
    organization_ids=frozenset({"org-1"}),
    roles=frozenset({"COACH"}),
    issued_at=1,
    expires_at=2,
)
CONTEXT = GatewayRequestContext("signed-context", "stable-business-id", "trace-1")


class MemoryRepository:
    def __init__(self, current: ConfirmationRecord | None = None) -> None:
        self.current = current or pending_record()
        self.issued: list[str] = []
        self.finished: list[bool] = []

    async def create(
        self, confirmation_id: str, action: ConfirmationAction, expires_at: datetime
    ) -> ConfirmationRecord:
        values = {key: value for key, value in vars(action).items() if key in vars(self.current)}
        self.current = replace(self.current, **values, id=confirmation_id, expires_at=expires_at)
        return self.current

    async def get_for_subject(self, *_: Any) -> ConfirmationRecord:
        return self.current

    async def mark_unknown(self, *_: Any) -> ConfirmationRecord:
        self.current = self.current.mark_unknown(datetime.now(UTC))
        return self.current

    async def issue_credential_jti(self, _id: str, jti: str, now: datetime, *_: Any):
        self.issued.append(jti)
        self.current = self.current.issue_credential(jti, now)
        return self.current

    async def claim_execution(self, _id: str, now: datetime, *_: Any):
        self.current = self.current.claim_execution(now)
        return self.current

    async def finish_execution(
        self, _id: str, success: bool, now: datetime, _trace: str | None, **kwargs: Any
    ):
        self.finished.append(success)
        self.current = (
            self.current.finish_success(now)
            if success
            else self.current.finish_failure(now, kwargs["error_code"], kwargs["retryable"])
        )
        return self.current


class TokenIssuer:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    def issue(self, _record: ConfirmationRecord, **kwargs: Any) -> str:
        self.bodies.append(kwargs["request_body"])
        return "runtime-only-token"


def service(
    repository: MemoryRepository,
    gateway: Any = None,
    route_selector: Any = None,
) -> ConfirmationService:
    gateway = gateway or FakeGateway()
    return ConfirmationService(
        cast(Any, repository),
        build_fitness_tool_registry(cast(GatewayClient, gateway)),
        cast(GatewayClient, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="test-v1"),
        cast(Any, TokenIssuer()),
        route_selector=route_selector,
    )


async def test_confirmation_pins_transport_and_builder_across_route_rollback() -> None:
    repository = MemoryRepository()
    selected = ["mcp", "1"]
    app = service(repository, route_selector=lambda _: (selected[0], selected[1]))
    record = await app.prepare(
        tool_id="fitness.support.ticket.create.v1",
        raw_input={
            "organization_id": "org-1",
            "subject_user_id": "coach-1",
            "category": "APPOINTMENT",
            "subject": "核对预约",
            "description": "请核对预约的实际状态",
        },
        gateway_context=CONTEXT,
        identity=IDENTITY,
        thread_id="thread-1",
    )
    assert (record.transport, record.contract_version) == ("mcp", "1")
    assert record.request_id == "stable-business-id"
    selected[:] = ["http", "v1"]
    repository.current = record.approve(datetime.now(UTC), "decision-1")

    prepared = await app.prepare_execution(record.id, identity=IDENTITY, trace_id="resume")

    assert (prepared.record.transport, prepared.record.contract_version) == ("mcp", "1")
    assert prepared.record.request_id == record.request_id
    assert len(repository.issued) == 1
    assert "signed-context" not in repr(prepared.record)
    assert "runtime-only-token" not in repr(prepared.record)


async def test_legacy_retryable_record_is_quarantined_without_new_jti_or_send() -> None:
    old = replace(
        pending_record(),
        authorization_status="APPROVED",
        execution_status="FAILED_RETRYABLE",
        credential_jti="original-jti",
        credential_consumed_at=datetime.now(UTC),
    )
    repository = MemoryRepository(old)
    app = service(repository)

    with pytest.raises(ConfirmationStateError, match="必须先对账"):
        await app.prepare_execution(old.id, identity=IDENTITY, trace_id="retry")

    assert repository.current.execution_status == "UNKNOWN"
    assert repository.current.credential_jti == "original-jti"
    assert repository.current.request_id == old.request_id
    assert repository.issued == []
    with pytest.raises(ConfirmationStateError):
        await app.prepare_execution(old.id, identity=IDENTITY, trace_id="retry-again")


def operation(record: ConfirmationRecord, status: str = "SUCCEEDED") -> GatewayToolOperation:
    return GatewayToolOperation.model_validate(
        {
            "operationId": record.request_id,
            "toolId": record.tool_id,
            "confirmationId": record.id,
            "organizationId": record.organization_id,
            "actorId": record.subject_user_id,
            "payloadHash": record.payload_hash,
            "status": status,
            "resultReference": "business-resource-1" if status == "SUCCEEDED" else None,
        }
    )


class OperationGateway(FakeGateway):
    def __init__(self, expected: GatewayToolOperation) -> None:
        super().__init__()
        self.expected = expected
        self.calls: list[dict[str, Any]] = []

    async def query_tool_operation(self, context: GatewayRequestContext, **scope: Any):
        assert context.signed_context == "signed-context"
        self.calls.append(scope)
        return self.expected

    async def reconcile_tool_operation(self, **scope: Any):
        self.calls.append(scope)
        return self.expected


@pytest.mark.parametrize("tool_id", sorted(JAVA_WRITE_TOOL_IDS))
@pytest.mark.parametrize("background", [False, True])
async def test_all_nine_writes_reconcile_original_operation_without_reexecution(
    tool_id: str, background: bool
) -> None:
    record = replace(pending_record(), tool_id=tool_id, execution_status="UNKNOWN")
    repository = MemoryRepository(record)
    gateway = OperationGateway(operation(record))
    app = service(repository, gateway)

    if background:
        result = await app.reconcile_stored_execution(record)
    else:
        result = await app.reconcile_execution(
            record.id, identity=IDENTITY, gateway_context=CONTEXT
        )

    assert result.execution_status == "SUCCEEDED"
    assert repository.issued == []
    assert gateway.calls[0]["operation_id"] == record.request_id
    assert gateway.calls[0]["confirmation_id"] == record.id
    assert gateway.calls[0]["tool_id"] == tool_id
    assert gateway.calls[0]["payload_hash"] == record.payload_hash


@pytest.mark.parametrize(
    "field",
    ["operation_id", "tool_id", "confirmation_id", "organization_id", "actor_id", "payload_hash"],
)
async def test_reconciliation_rejects_every_mismatched_binding(field: str) -> None:
    record = replace(pending_record(), execution_status="UNKNOWN")
    repository = MemoryRepository(record)
    gateway = OperationGateway(operation(record).model_copy(update={field: "wrong-binding"}))
    app = service(repository, gateway)

    with pytest.raises(ConfirmationStateError, match="授权范围不匹配"):
        await app.reconcile_stored_execution(record)

    assert repository.finished == []
    assert repository.current.execution_status == "UNKNOWN"


@pytest.mark.parametrize("status", ["UNKNOWN", "PROCESSING"])
async def test_inconclusive_operation_evidence_never_becomes_final_failure(status: str) -> None:
    record = replace(pending_record(), execution_status="UNKNOWN")
    repository = MemoryRepository(record)
    result = await service(
        repository, OperationGateway(operation(record, status))
    ).reconcile_stored_execution(record)
    assert result.execution_status == "UNKNOWN"
    assert repository.finished == []


async def test_bound_final_failure_evidence_can_converge_unknown() -> None:
    record = replace(pending_record(), execution_status="UNKNOWN")
    repository = MemoryRepository(record)
    result = await service(
        repository, OperationGateway(operation(record, "FAILED"))
    ).reconcile_stored_execution(record)
    assert result.execution_status == "FAILED_FINAL"
    assert repository.issued == []


@pytest.mark.parametrize(
    ("tool_id", "payload", "resource_id", "expected_input", "expected_body"),
    [
        ("fitness.training.plan.submit_review.v1", {}, "plan-1", {"plan_id": "plan-1"}, b"{}"),
        ("fitness.training.plan.publish.v1", {}, "plan-1", {"plan_id": "plan-1"}, b"{}"),
        (
            "fitness.training.plan.review.v1",
            {"decision": "APPROVE", "comment": None},
            "plan-1",
            {"plan_id": "plan-1", "decision": "APPROVE", "comment": None},
            b'{"comment":null,"decision":"APPROVE"}',
        ),
        (
            "fitness.training.day.record_execution.v1",
            {"day_id": "day-1", "status": "COMPLETED", "note": None},
            "plan-1:day-1",
            {"plan_id": "plan-1", "day_id": "day-1", "status": "COMPLETED", "note": None},
            b'{"dayId":"day-1","note":null,"status":"COMPLETED"}',
        ),
        (
            "fitness.training.day.record_execution.v1",
            {"day_id": "d:1", "status": "COMPLETED", "note": None},
            "p:1:d:1",
            {"plan_id": "p:1", "day_id": "d:1", "status": "COMPLETED", "note": None},
            b'{"dayId":"d:1","note":null,"status":"COMPLETED"}',
        ),
    ],
)
def test_training_recovery_restores_bound_route_ids_without_signing_them_into_body(
    tool_id: str,
    payload: dict[str, Any],
    resource_id: str,
    expected_input: dict[str, Any],
    expected_body: bytes,
) -> None:
    record = replace(pending_record(), tool_id=tool_id, resource_id=resource_id)
    invocation = _execution_tool_input(record, payload)
    assert invocation == expected_input
    registry = build_fitness_tool_registry(cast(GatewayClient, FakeGateway()))
    registry.get(tool_id).input_model.model_validate(invocation)
    for version in ("v1", "1"):
        assert (
            gateway_confirmation_body(
                tool_id, payload, resource_id=resource_id, contract_version=version
            )
            == expected_body
        )


@pytest.mark.parametrize(
    "error",
    [
        GatewayExecutionUnknownError("lost response"),
        GatewayProtocolError("invalid response"),
        RuntimeError("lost result"),
    ],
)
async def test_supervisor_keeps_uncertain_write_unknown_and_pins_original_route(
    error: Exception,
) -> None:
    contexts: list[GatewayRequestContext] = []

    class Gateway(RecordingGateway):
        async def create_training_draft(
            self, context: GatewayRequestContext, payload: dict[str, Any]
        ):
            contexts.append(context)
            raise error

    confirmation = FakeConfirmationService(
        replace(pending_record(), transport="mcp", contract_version="1").approve(
            datetime.now(UTC), "decision"
        )
    )
    supervisor = Supervisor(
        cast(Any, OneWriteModel()),
        build_fitness_tool_registry(cast(GatewayClient, Gateway())),
        confirmation_service=cast(Any, confirmation),
    )
    with pytest.raises(SupervisorRuntimeError, match="待核实"):
        await supervisor._confirmation_node(
            cast(
                Any,
                {"pending_confirmation_id": confirmation.record.id, "route": "FITNESS_COACHING"},
            ),
            cast(
                Any,
                SimpleNamespace(
                    context=SimpleNamespace(identity=IDENTITY, gateway_context=CONTEXT)
                ),
            ),
        )
    assert confirmation.record.execution_status == "UNKNOWN"
    assert confirmation.record.credential_jti == "test-jti"
    assert contexts[0].request_id == "request-1"
    assert (contexts[0].transport, contexts[0].contract_version) == ("mcp", "1")


async def test_cancelled_dispatched_write_retains_unknown_for_background_reconciliation() -> None:
    class Gateway(RecordingGateway):
        async def create_training_draft(self, *_: Any):
            raise asyncio.CancelledError("connection stopped after dispatch")

    class Confirmation(FakeConfirmationService):
        async def mark_execution_unknown(self, *_: Any, **__: Any):
            self.record = self.record.mark_unknown(datetime.now(UTC))
            return self.record

    confirmation = Confirmation(pending_record().approve(datetime.now(UTC), "decision"))
    supervisor = Supervisor(
        cast(Any, OneWriteModel()),
        build_fitness_tool_registry(cast(GatewayClient, Gateway())),
        confirmation_service=cast(Any, confirmation),
    )
    with pytest.raises(asyncio.CancelledError):
        await supervisor._confirmation_node(
            cast(
                Any,
                {"pending_confirmation_id": confirmation.record.id, "route": "FITNESS_COACHING"},
            ),
            cast(
                Any,
                SimpleNamespace(
                    context=SimpleNamespace(identity=IDENTITY, gateway_context=CONTEXT)
                ),
            ),
        )
    assert confirmation.record.execution_status == "UNKNOWN"
    assert confirmation.record.credential_consumed_at is not None
    assert confirmation.record.credential_jti == "test-jti"


async def test_explicit_denial_is_final_and_does_not_create_a_retryable_confirmation() -> None:
    class Gateway(RecordingGateway):
        async def create_training_draft(self, *_: Any):
            raise GatewayForbiddenError("definite rejection", status_code=403)

    confirmation = FakeConfirmationService(pending_record().approve(datetime.now(UTC), "decision"))
    supervisor = Supervisor(
        cast(Any, OneWriteModel()),
        build_fitness_tool_registry(cast(GatewayClient, Gateway())),
        confirmation_service=cast(Any, confirmation),
    )
    with pytest.raises(SupervisorRuntimeError, match="被拒绝"):
        await supervisor._confirmation_node(
            cast(
                Any,
                {"pending_confirmation_id": confirmation.record.id, "route": "FITNESS_COACHING"},
            ),
            cast(
                Any,
                SimpleNamespace(
                    context=SimpleNamespace(identity=IDENTITY, gateway_context=CONTEXT)
                ),
            ),
        )
    assert confirmation.record.execution_status == "FAILED_FINAL"
    assert confirmation.execution_preparations == 1


def test_legacy_uncertain_action_without_saved_jti_cannot_issue_a_new_one() -> None:
    legacy = replace(
        pending_record(), authorization_status="APPROVED", execution_status="FAILED_RETRYABLE"
    )
    with pytest.raises(ConfirmationStateError, match="不能签发新凭证"):
        legacy.issue_credential("new-jti", datetime.now(UTC))
