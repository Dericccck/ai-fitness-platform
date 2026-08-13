from datetime import UTC, datetime, timedelta
from typing import Any, cast

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor, SupervisorRequest
from app.confirmation.cipher import AesGcmPayloadCipher, ConfirmationPayloadCipherError
from app.confirmation.models import ConfirmationRecord
from app.confirmation.service import ConfirmationExecutionPreparation
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayClient, GatewayRequestContext
from app.infrastructure.model_gateway import ModelGateway, ModelToolCall, ModelTurn

from .test_tool_registry import FakeGateway


class OneWriteModel:
    """只提出一次创建草案的模型桩，确认前不允许进入第二个模型回合。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]
    ) -> ModelTurn:
        self.calls += 1
        return ModelTurn(
            content="",
            tool_calls=(
                ModelToolCall(
                    call_id="call-create",
                    name="fitness.training.plan.create_draft.v1",
                    arguments={
                        "organization_id": "org-1",
                        "student_id": "student-1",
                        "coach_id": "coach-1",
                        "title": "基础力量",
                        "goal_type": "力量",
                        "days": [
                            {
                                "day_number": 1,
                                "title": "下肢",
                                "items": [
                                    {
                                        "exercise_name": "深蹲",
                                        "sort_order": 1,
                                        "sets": 3,
                                        "reps": "8-10",
                                    }
                                ],
                            }
                        ],
                    },
                ),
            ),
        )


class FakeConfirmationService:
    def __init__(self, record: ConfirmationRecord) -> None:
        self.record = record
        self.prepared: list[dict[str, Any]] = []
        self.execution_preparations = 0
        self.finished: list[bool] = []

    async def prepare(self, **kwargs: Any) -> ConfirmationRecord:
        self.prepared.append(kwargs)
        return self.record

    async def get_for_subject(
        self, confirmation_id: str, identity: AgentIdentity
    ) -> ConfirmationRecord:
        assert confirmation_id == self.record.id
        assert identity.subject == self.record.subject_user_id
        return self.record

    async def prepare_execution(
        self, confirmation_id: str, *, identity: AgentIdentity, trace_id: str | None
    ) -> ConfirmationExecutionPreparation:
        assert confirmation_id == self.record.id
        assert identity.subject == self.record.subject_user_id
        assert self.record.authorization_status == "APPROVED"
        self.execution_preparations += 1
        self.record = self.record.issue_credential("test-jti", datetime.now(UTC)).claim_execution(
            datetime.now(UTC)
        )
        return ConfirmationExecutionPreparation(
            record=self.record,
            tool_input={
                "organization_id": "org-1",
                "student_id": "student-1",
                "coach_id": "coach-1",
                "title": "基础力量",
                "goal_type": "力量",
                "days": [
                    {
                        "day_number": 1,
                        "title": "下肢",
                        "items": [
                            {
                                "exercise_name": "深蹲",
                                "sort_order": 1,
                                "sets": 3,
                                "reps": "8-10",
                            }
                        ],
                    }
                ],
            },
            confirmation_token="server-issued-token",
        )

    async def finish_execution(
        self,
        confirmation_id: str,
        *,
        success: bool,
        trace_id: str | None,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> ConfirmationRecord:
        assert confirmation_id == self.record.id
        self.finished.append(success)
        self.record = (
            self.record.finish_success(datetime.now(UTC))
            if success
            else self.record.finish_failure(
                datetime.now(UTC), error_code or "TEST_FAILURE", retryable
            )
        )
        return self.record


class RecordingGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.confirmation_tokens: list[str | None] = []

    async def create_training_draft(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.confirmation_tokens.append(context.confirmation_token)
        return await super().create_training_draft(context, payload)


def pending_record() -> ConfirmationRecord:
    now = datetime.now(UTC)
    return ConfirmationRecord(
        id="confirmation-1",
        protocol_version=1,
        thread_id="thread-1",
        subject_user_id="coach-1",
        organization_id="org-1",
        tool_id="fitness.training.plan.create_draft.v1",
        risk_level="WRITE",
        action="CREATE_TRAINING_DRAFT",
        resource_type="training_plan",
        resource_id=None,
        expected_resource_version=None,
        request_id="request-1",
        payload_hash="a" * 64,
        display_summary={
            "operation": "创建训练计划草案",
            "organization_id": "org-1",
            "details": {"title": "基础力量"},
        },
        payload_ciphertext=b"ciphertext",
        payload_key_version="test-v1",
        authorization_status="PENDING",
        execution_status="NOT_STARTED",
        version=0,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
        actor_roles=("COACH",),
        actor_organization_ids=("org-1",),
    )


def test_aes_gcm_binds_ciphertext_to_payload_hash() -> None:
    cipher = AesGcmPayloadCipher(key=b"0" * 32, key_version="test-v1")
    plaintext = '{"title":"基础力量"}'.encode()
    ciphertext = cipher.encrypt(plaintext, associated_data="hash-1")

    assert cipher.decrypt(ciphertext, associated_data="hash-1") == plaintext
    try:
        cipher.decrypt(ciphertext, associated_data="hash-2")
    except ConfirmationPayloadCipherError:
        pass
    else:
        raise AssertionError("changed payload hash must invalidate ciphertext")


async def test_write_tool_creates_confirmation_interrupt_without_gateway_execution() -> None:
    models = OneWriteModel()
    gateway = FakeGateway()
    registry = build_fitness_tool_registry(cast(GatewayClient, gateway))
    confirmation_service = FakeConfirmationService(pending_record())
    supervisor = Supervisor(
        cast(ModelGateway, models),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=cast(Any, confirmation_service),
    )
    request = SupervisorRequest(
        user_message="制定一个基础力量计划",
        gateway_context=GatewayRequestContext(
            signed_context="signed-context",
            request_id="request-1",
            trace_id="trace-1",
        ),
        conversation_id="conversation-1",
        thread_id="thread-1",
        identity=AgentIdentity(
            subject="coach-1",
            organization_ids=frozenset({"org-1"}),
            roles=frozenset({"COACH"}),
            issued_at=1,
            expires_at=2,
        ),
    )

    response = await supervisor.invoke(request)

    assert response.status == "CONFIRMATION_REQUIRED"
    assert response.confirmation_id == "confirmation-1"
    assert confirmation_service.prepared
    assert gateway.current_user_calls == 0
    state = await supervisor._graph.aget_state({"configurable": {"thread_id": "thread-1"}})
    messages = state.values["messages"]
    assert messages[-1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert state.values["pending_confirmation_id"] == "confirmation-1"
    assert "深蹲" not in str(state.values)


async def test_approved_confirmation_resumes_server_side_and_executes_once() -> None:
    models = OneWriteModel()
    gateway = RecordingGateway()
    registry = build_fitness_tool_registry(cast(GatewayClient, gateway))
    confirmation_service = FakeConfirmationService(pending_record())
    supervisor = Supervisor(
        cast(ModelGateway, models),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=cast(Any, confirmation_service),
    )
    request = SupervisorRequest(
        user_message="制定一个基础力量计划",
        gateway_context=GatewayRequestContext(
            signed_context="signed-context",
            request_id="request-1",
            trace_id="trace-1",
        ),
        conversation_id="conversation-1",
        thread_id="thread-1",
        identity=AgentIdentity(
            subject="coach-1",
            organization_ids=frozenset({"org-1"}),
            roles=frozenset({"COACH"}),
            issued_at=1,
            expires_at=2,
        ),
    )

    first_response = await supervisor.invoke(request)
    assert first_response.status == "CONFIRMATION_REQUIRED"

    confirmation_service.record = confirmation_service.record.approve(
        datetime.now(UTC), "decision-1"
    )
    resumed_response = await supervisor.resume_confirmation(
        "confirmation-1",
        identity=request.identity,
        gateway_context=request.gateway_context,
        thread_id="thread-1",
    )

    assert resumed_response.status == "COMPLETED"
    assert resumed_response.answer == "已完成创建训练计划草案。"
    assert models.calls == 1
    assert confirmation_service.execution_preparations == 1
    assert confirmation_service.finished == [True]
    assert gateway.confirmation_tokens == ["server-issued-token"]
    state = await supervisor._graph.aget_state({"configurable": {"thread_id": "thread-1"}})
    assert state.values["pending_confirmation_id"] is None
