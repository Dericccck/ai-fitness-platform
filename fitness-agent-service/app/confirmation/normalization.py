"""写操作确认前的确定性动作规范化。

本模块只做一件事：把已经通过工具 Schema 校验的输入，转换成可以展示、哈希和后续
持久化的动作信封。它不创建确认单，也不签发确认 Token，更不执行 Java Gateway。

设计上刻意区分两类数据：

* ``canonical_payload`` 是实际会交给 Gateway 的业务请求参数，下一步会在应用层加密；
* ``display_summary`` 是给用户确认的固定字段投影，不能由 LLM 自由编写。

计划类写操作还必须提供由可信 Gateway 查询得到的资源快照。快照中的资源版本、当前
状态和展示字段会一起参与哈希，避免用户确认旧计划后，Agent 静默改用新计划执行。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from .models import ConfirmationAction


class ConfirmationNormalizationError(ValueError):
    """动作无法形成稳定、完整且可核对的确认信封。"""


@dataclass(frozen=True)
class ConfirmationResourceSnapshot:
    """由受信任业务 Gateway 返回的待操作资源快照。

    ``attributes`` 不能来自用户自然语言或模型工具参数。它应当来自 Gateway 的
    Pydantic Tool View，并在进入本模块前完成权限过滤。资源型确认必须绑定当前版本，
    因为等待用户确认期间，计划可能被教练修改、驳回或发布。
    """

    organization_id: str
    resource_id: str
    version: int
    attributes: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.organization_id.strip():
            raise ConfirmationNormalizationError("resource organization_id is required")
        if not self.resource_id.strip():
            raise ConfirmationNormalizationError("resource_id is required")
        if self.version < 0:
            raise ConfirmationNormalizationError("resource version cannot be negative")


@dataclass(frozen=True)
class ConfirmationNormalizationContext:
    """创建确认动作所需的运行时身份和线程信息。

    这些字段只在 Agent 进程内存中使用，不进入 LangGraph State。主体、组织和角色应由
    最新签名 AgentContext 派生，不能从模型参数或前端隐藏字段读取。
    """

    request_id: str
    thread_id: str
    subject_user_id: str
    actor_roles: tuple[str, ...]
    actor_organization_ids: tuple[str, ...]
    trace_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("request_id", "thread_id", "subject_user_id"):
            if not getattr(self, field_name).strip():
                raise ConfirmationNormalizationError(f"{field_name} is required")


PayloadBuilder = Callable[[BaseModel], Mapping[str, Any]]
SummaryBuilder = Callable[[BaseModel, Mapping[str, Any] | None], Mapping[str, Any]]
ResourceIdBuilder = Callable[[BaseModel], str | None]
OrganizationIdBuilder = Callable[[BaseModel], str | None]


@dataclass(frozen=True)
class ConfirmationPolicy:
    """一个写工具的固定确认策略。

    ``payload_builder`` 必须和真正调用 Gateway 使用同一套转换函数；摘要构造器只能
    读取已校验输入和受信任资源快照，不能接收模型自由生成的解释文本。
    """

    action: str
    resource_type: str
    risk_level: str
    operation: str
    target_status: str
    payload_builder: PayloadBuilder
    summary_builder: SummaryBuilder
    resource_required: bool = False
    resource_id_builder: ResourceIdBuilder | None = None
    organization_id_builder: OrganizationIdBuilder | None = None


@dataclass(frozen=True)
class NormalizedConfirmationAction:
    """规范化后的内存动作信封。

    该对象还不是数据库确认单。``canonical_payload`` 是明文但只存在于当前调用内存，
    调用方必须先通过版本化密钥边界加密，再调用 ``to_confirmation_action``；本对象不应
    写入 Checkpoint、普通日志或模型消息。
    """

    tool_id: str
    organization_id: str
    action: str
    resource_type: str
    resource_id: str | None
    expected_resource_version: int | None
    request_id: str
    payload_hash: str
    risk_level: str
    display_summary: Mapping[str, Any]
    canonical_payload: bytes
    thread_id: str
    subject_user_id: str
    actor_roles: tuple[str, ...]
    actor_organization_ids: tuple[str, ...]
    trace_id: str | None = None

    def to_confirmation_action(
        self, payload_ciphertext: bytes, payload_key_version: str
    ) -> ConfirmationAction:
        """把已加密参数装配为可交给 PostgreSQL 仓储的领域对象。"""

        if not payload_ciphertext:
            raise ConfirmationNormalizationError("encrypted payload is required")
        if not payload_key_version.strip():
            raise ConfirmationNormalizationError("payload key version is required")
        return ConfirmationAction(
            tool_id=self.tool_id,
            organization_id=self.organization_id,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            expected_resource_version=self.expected_resource_version,
            request_id=self.request_id,
            payload_hash=self.payload_hash,
            risk_level=self.risk_level,
            display_summary=self.display_summary,
            payload_ciphertext=payload_ciphertext,
            payload_key_version=payload_key_version,
            thread_id=self.thread_id,
            subject_user_id=self.subject_user_id,
            actor_roles=self.actor_roles,
            actor_organization_ids=self.actor_organization_ids,
            trace_id=self.trace_id,
        )


def normalize_confirmation_action(
    *,
    tool_id: str,
    input_data: BaseModel,
    policy: ConfirmationPolicy,
    context: ConfirmationNormalizationContext,
    organization_id: str,
    resource: ConfirmationResourceSnapshot | None = None,
) -> NormalizedConfirmationAction:
    """根据固定策略生成确定性摘要和 SHA-256 动作指纹。

    资源型动作必须传入快照；创建草案没有既有资源，因此其组织 ID直接来自已经校验的
    创建参数。哈希材料同时包含执行参数和摘要绑定字段，展示内容与实际动作任一处变化
    都会得到新的指纹。
    """

    if not organization_id.strip():
        raise ConfirmationNormalizationError("organization_id is required")
    if policy.resource_required and resource is None:
        raise ConfirmationNormalizationError("trusted resource snapshot is required")
    declared_resource_id = (
        policy.resource_id_builder(input_data) if policy.resource_id_builder is not None else None
    )
    declared_organization_id = (
        policy.organization_id_builder(input_data)
        if policy.organization_id_builder is not None
        else None
    )
    if declared_organization_id is not None and declared_organization_id != organization_id:
        raise ConfirmationNormalizationError("input organization does not match action")
    if resource is not None and resource.organization_id != organization_id:
        raise ConfirmationNormalizationError("resource organization does not match action")

    payload = _json_compatible_mapping(policy.payload_builder(input_data))
    resource_id = resource.resource_id if resource else None
    if resource_id != declared_resource_id:
        raise ConfirmationNormalizationError("resource snapshot does not match tool input")
    expected_version = resource.version if resource else None
    resource_projection = _resource_projection(policy, resource)
    summary = _json_compatible_mapping(policy.summary_builder(input_data, resource_projection))
    operation = summary.get("operation")
    if operation != policy.operation:
        raise ConfirmationNormalizationError("display summary operation is not policy controlled")
    target_status = summary.get("target_status")
    if policy.target_status != "DYNAMIC" and target_status != policy.target_status:
        raise ConfirmationNormalizationError(
            "display summary target status is not policy controlled"
        )
    if not isinstance(target_status, str) or not target_status.strip():
        raise ConfirmationNormalizationError("display summary target status is required")

    # 这些边界字段由规范化器统一覆盖，而不是信任每个模板自行填写，防止模板漏掉
    # 组织、资源版本或真实 Payload。业务模板仍负责 operation 和可读的 details。
    summary = {
        **summary,
        "action": policy.action,
        "organization_id": organization_id,
        "resource_type": policy.resource_type,
        "resource_id": resource_id,
        "expected_resource_version": expected_version,
        "details": payload,
        "resource": resource_projection,
    }
    _validate_summary_binding(
        summary,
        organization_id=organization_id,
        resource_type=policy.resource_type,
        resource_id=resource_id,
        expected_version=expected_version,
        action=policy.action,
        target_status=target_status,
        payload=payload,
        resource_projection=resource_projection,
    )

    hash_material = {
        "tool_id": tool_id,
        "organization_id": organization_id,
        "action": policy.action,
        "resource_type": policy.resource_type,
        "resource_id": resource_id,
        "expected_resource_version": expected_version,
        "payload": payload,
        "resource_projection": resource_projection,
        "target_status": target_status,
    }
    canonical_material = canonical_json_bytes(hash_material)
    return NormalizedConfirmationAction(
        tool_id=tool_id,
        organization_id=organization_id,
        action=policy.action,
        resource_type=policy.resource_type,
        resource_id=resource_id,
        expected_resource_version=expected_version,
        request_id=context.request_id,
        payload_hash=hashlib.sha256(canonical_material).hexdigest(),
        risk_level=policy.risk_level,
        display_summary=summary,
        canonical_payload=canonical_json_bytes(payload),
        thread_id=context.thread_id,
        subject_user_id=context.subject_user_id,
        actor_roles=context.actor_roles,
        actor_organization_ids=context.actor_organization_ids,
        trace_id=context.trace_id,
    )


def canonical_json_bytes(value: Any) -> bytes:
    """以固定键顺序和 UTF-8 编码生成不可变 JSON 字节。

    JSON 数组顺序不能排序，因为训练日和动作处方的顺序本身就是业务语义；对象键则
    统一排序。禁止 NaN/Infinity，避免不同语言对非标准 JSON 的序列化结果不一致。
    """

    normalized = _canonicalize(value)
    try:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfirmationNormalizationError("value cannot be canonically serialized") from exc


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json", by_alias=True))
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfirmationNormalizationError("NaN and Infinity are not valid action values")
    return value


def _json_compatible_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfirmationNormalizationError("confirmation builder must return an object")
    canonical = _canonicalize(value)
    if not isinstance(canonical, dict):
        raise ConfirmationNormalizationError("confirmation builder must return an object")
    return canonical


def _resource_projection(
    policy: ConfirmationPolicy, resource: ConfirmationResourceSnapshot | None
) -> dict[str, Any] | None:
    if resource is None:
        return None
    # 资源快照的原始字段来自 Gateway，但摘要只允许业务策略声明的字段，避免把内部
    # DTO、权限字段或未来新增的敏感字段意外展示并纳入确认范围。
    allowed = {
        "title",
        "status",
        "student_id",
        "coach_id",
        "goal_type",
        "days",
        "period_start",
        "period_end",
    }
    return {
        key: _canonicalize(resource.attributes[key])
        for key in sorted(allowed)
        if key in resource.attributes
    }


def _validate_summary_binding(
    summary: Mapping[str, Any],
    *,
    organization_id: str,
    resource_type: str,
    resource_id: str | None,
    expected_version: int | None,
    action: str,
    target_status: str,
    payload: Mapping[str, Any],
    resource_projection: Mapping[str, Any] | None,
) -> None:
    """校验确定性模板没有漏掉授权边界字段或实际执行参数。"""

    expected = {
        "organization_id": organization_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "expected_resource_version": expected_version,
        "action": action,
        "target_status": target_status,
        "details": payload,
        "resource": resource_projection,
    }
    for key, value in expected.items():
        if key not in summary or _canonicalize(summary[key]) != _canonicalize(value):
            raise ConfirmationNormalizationError(f"display summary is not bound to {key}")
