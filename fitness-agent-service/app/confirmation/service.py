"""确认动作创建服务。

该服务串起四个边界：可信 Gateway 资源快照、工具 Schema/动作规范化、AES-GCM 参数密文
和 PostgreSQL 确认单。它不负责用户决定 API，也不签发 Gateway 确认凭证；这些职责分别
在后续确认 API 和凭证服务中实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.agent.tool_registry import ToolRegistry
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.models import ConfirmationRecord
from app.confirmation.normalization import (
    ConfirmationNormalizationContext,
    ConfirmationResourceSnapshot,
)
from app.confirmation.repository import ConfirmationRepository
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayClient, GatewayRequestContext


class ConfirmationService:
    """创建和读取确认单的应用服务。"""

    def __init__(
        self,
        repository: ConfirmationRepository,
        tools: ToolRegistry,
        gateway: GatewayClient,
        cipher: AesGcmPayloadCipher,
        *,
        ttl_seconds: int = 600,
    ) -> None:
        self.repository = repository
        self.tools = tools
        self.gateway = gateway
        self.cipher = cipher
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

    async def get_for_subject(
        self, confirmation_id: str, identity: AgentIdentity
    ) -> ConfirmationRecord:
        """按签名主体和组织范围读取确认单，防止确认 ID 枚举跨租户数据。"""

        return await self.repository.get_for_subject(
            confirmation_id,
            identity.subject,
            tuple(sorted(identity.organization_ids)),
        )


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
