"""确认动作创建服务。

该服务串起四个边界：可信 Gateway 资源快照、工具 Schema/动作规范化、AES-GCM 参数密文
和 PostgreSQL 确认单。它不负责用户决定 API，也不签发 Gateway 确认凭证；这些职责分别
在后续确认 API 和凭证服务中实现。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.agent.tool_registry import ToolRegistry
from app.confirmation.cipher import AesGcmPayloadCipher, ConfirmationPayloadCipherError
from app.confirmation.models import ConfirmationDecision, ConfirmationRecord, ConfirmationStateError
from app.confirmation.normalization import (
    ConfirmationNormalizationContext,
    ConfirmationResourceSnapshot,
)
from app.confirmation.repository import ConfirmationRepository
from app.confirmation.token import ConfirmationTokenIssuer
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayClient, GatewayRequestContext


@dataclass(frozen=True)
class ConfirmationExecutionPreparation:
    """一次恢复执行所需的短暂内存上下文，不进入 State 或 Checkpoint。"""

    record: ConfirmationRecord
    tool_input: dict[str, Any]
    confirmation_token: str


class ConfirmationService:
    """创建和读取确认单的应用服务。"""

    def __init__(
        self,
        repository: ConfirmationRepository,
        tools: ToolRegistry,
        gateway: GatewayClient,
        cipher: AesGcmPayloadCipher,
        token_issuer: ConfirmationTokenIssuer,
        *,
        ttl_seconds: int = 600,
    ) -> None:
        self.repository = repository
        self.tools = tools
        self.gateway = gateway
        self.cipher = cipher
        self.token_issuer = token_issuer
        self.ttl_seconds = ttl_seconds

    async def prepare(
        self,
        *,
        tool_id: str,
        raw_input: Mapping[str, Any],
        gateway_context: GatewayRequestContext,
        identity: AgentIdentity,
        thread_id: str,
    ) -> ConfirmationRecord:
        """创建或幂等复用待确认单。

        资源型训练动作会先通过 Java Gateway 读取当前计划，随后把计划版本绑定到动作
        哈希。创建草案没有既有资源，组织 ID来自已经通过工具 Schema 校验的输入。
        """

        definition = self.tools.get(tool_id)
        resource: ConfirmationResourceSnapshot | None = None
        organization_id = _organization_from_input(tool_id, raw_input)
        if (
            definition.confirmation_policy is not None
            and definition.confirmation_policy.resource_required
        ):
            plan_id = _plan_id_from_input(raw_input)
            plan = await self.gateway.get_training_plan(gateway_context, plan_id)
            organization_id = plan.organization_id
            resource = ConfirmationResourceSnapshot(
                organization_id=plan.organization_id,
                resource_id=plan.id,
                version=plan.version,
                attributes=plan.model_dump(mode="json", by_alias=False),
            )

        normalized = self.tools.normalize_confirmation(
            tool_id,
            raw_input,
            context=ConfirmationNormalizationContext(
                request_id=gateway_context.request_id or str(uuid4()),
                thread_id=thread_id,
                subject_user_id=identity.subject,
                actor_roles=tuple(sorted(identity.roles)),
                actor_organization_ids=tuple(sorted(identity.organization_ids)),
                trace_id=gateway_context.trace_id,
            ),
            organization_id=organization_id,
            resource=resource,
        )
        ciphertext = self.cipher.encrypt(
            normalized.canonical_payload,
            associated_data=normalized.payload_hash,
        )
        action = normalized.to_confirmation_action(ciphertext, self.cipher.key_version)
        expires_at = datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        return await self.repository.create(str(uuid4()), action, expires_at)

    async def prepare_execution(
        self, confirmation_id: str, *, identity: AgentIdentity, trace_id: str | None
    ) -> ConfirmationExecutionPreparation:
        """读取批准动作、绑定一次性 JTI 并解密原始工具参数。

        原始参数只在当前请求的 Python 内存和运行时 context 中存在；它不会被写回
        LangGraph State。JTI 先持久化再生成 Token，进程在两步之间重启时可以安全重试。
        """

        record = await self.get_for_subject(confirmation_id, identity)
        if record.authorization_status != "APPROVED":
            raise ConfirmationStateError("only approved confirmation can execute")
        if record.execution_status == "FAILED_RETRYABLE":
            # 网络超时等可恢复失败不能直接复用旧 Token；先清空旧 JTI 和执行时间，
            # 再为下一次尝试重新领取执行权。授权仍保持 APPROVED，不需要用户重复确认。
            record = await self.repository.requeue_retryable(confirmation_id, trace_id)
        if record.execution_status != "NOT_STARTED":
            raise ConfirmationStateError("confirmation execution is not ready to run")
        jti = record.credential_jti or str(uuid4())
        if record.credential_jti is None:
            record = await self.repository.issue_credential_jti(
                confirmation_id, jti, datetime.now(UTC), trace_id
            )
        try:
            plaintext = self.cipher.decrypt(
                record.payload_ciphertext, associated_data=record.payload_hash
            )
            payload = json.loads(plaintext)
        except (ConfirmationPayloadCipherError, ValueError, TypeError) as exc:
            raise ConfirmationStateError("confirmation payload cannot be restored") from exc
        if not isinstance(payload, dict):
            raise ConfirmationStateError("confirmation payload must be an object")

        tool_input = {str(key): value for key, value in payload.items()}
        token = self.token_issuer.issue(
            record,
            resource=_token_resource(record, tool_input),
            jti=jti,
        )
        if record.execution_status == "NOT_STARTED":
            record = await self.repository.claim_execution(
                confirmation_id, datetime.now(UTC), trace_id
            )
        elif record.execution_status != "RUNNING":
            raise ConfirmationStateError("confirmation execution is no longer resumable")
        return ConfirmationExecutionPreparation(record, tool_input, token)

    async def finish_execution(
        self,
        confirmation_id: str,
        *,
        success: bool,
        trace_id: str | None,
        error_code: str | None = None,
        retryable: bool = False,
    ) -> ConfirmationRecord:
        """记录 Gateway 工具的真实结果，保持授权状态和执行状态分离。"""

        return await self.repository.finish_execution(
            confirmation_id,
            success,
            datetime.now(UTC),
            trace_id,
            error_code=error_code,
            retryable=retryable,
        )

    async def get_for_subject(
        self, confirmation_id: str, identity: AgentIdentity
    ) -> ConfirmationRecord:
        """按签名主体和组织范围读取确认单，防止确认 ID 枚举跨租户数据。"""

        record = await self.repository.get_for_subject(
            confirmation_id,
            identity.subject,
            tuple(sorted(identity.organization_ids)),
        )
        _ensure_identity_snapshot(record, identity)
        return record

    async def decide(
        self,
        confirmation_id: str,
        *,
        identity: AgentIdentity,
        decision: ConfirmationDecision,
        decision_request_id: str,
        trace_id: str | None,
    ) -> ConfirmationRecord:
        """提交确认决定，并把签名身份快照交给仓储记录不可变事件。

        ``decision_request_id`` 是客户端重试使用的幂等键，不能拿业务 ``request_id``
        代替。仓储会在 PostgreSQL 行锁事务中再次校验主体、机构、状态和过期时间，
        因此 API 层的身份检查不是最终授权边界。
        """

        if not decision_request_id.strip():
            raise ValueError("decision_request_id is required")

        record = await self.repository.get_for_subject(
            confirmation_id,
            identity.subject,
            tuple(sorted(identity.organization_ids)),
        )
        _ensure_identity_snapshot(record, identity)
        return await self.repository.decide(
            confirmation_id,
            identity.subject,
            tuple(sorted(identity.organization_ids)),
            decision,
            decision_request_id,
            datetime.now(UTC),
            trace_id,
            identity.subject,
            tuple(sorted(identity.roles)),
        )


def _ensure_identity_snapshot(record: ConfirmationRecord, identity: AgentIdentity) -> None:
    """确认阶段必须仍使用创建动作时的角色和完整机构范围。"""

    if record.actor_roles != tuple(
        sorted(identity.roles)
    ) or record.actor_organization_ids != tuple(sorted(identity.organization_ids)):
        # 对外按“不可见”处理，避免通过确认接口探测其他授权快照。
        raise ConfirmationStateError("confirmation identity scope has changed")


def _organization_from_input(tool_id: str, raw_input: Mapping[str, Any]) -> str:
    if tool_id == "fitness.training.plan.create_draft.v1":
        value = raw_input.get("organization_id")
        if isinstance(value, str) and value.strip():
            return value
    # 资源型动作的机构范围必须来自 Gateway 计划快照，不能由模型参数补充。
    return "__resolved_from_gateway__"


def _plan_id_from_input(raw_input: Mapping[str, Any]) -> str:
    value = raw_input.get("plan_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("plan_id is required for a training plan confirmation")
    return value


def _token_resource(record: ConfirmationRecord, payload: Mapping[str, Any]) -> str:
    """生成与 Java Gateway v1 契约一致的资源范围。"""

    if record.resource_id:
        return record.resource_id
    organization_id = payload.get("organization_id")
    student_id = payload.get("student_id")
    if isinstance(organization_id, str) and isinstance(student_id, str):
        return f"{organization_id}:{student_id}"
    raise ConfirmationStateError("confirmation resource scope is incomplete")
