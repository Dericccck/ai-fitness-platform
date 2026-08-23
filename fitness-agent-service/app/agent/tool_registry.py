"""版本化 Tool Registry 及其调用审计边界。

Tool Registry 是模型和业务系统之间的唯一工具入口。模型只能选择已经注册的
工具；工具输入必须先通过 Pydantic Schema 校验；工具执行失败也必须回传真实失败，
不能被 Supervisor 或模型包装成“已经成功”。这里暂时只放基础设施和只读工具，后续
写工具必须继续遵守确认凭证、幂等键和 Java Gateway 权限校验。确认凭证来自上游确认流程，
不进入模型的工具参数 Schema。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from app.confirmation.normalization import (
    ConfirmationNormalizationContext,
    ConfirmationPolicy,
    ConfirmationResourceSnapshot,
    NormalizedConfirmationAction,
    normalize_confirmation_action,
)
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.gateway_client import GatewayClientError, GatewayRequestContext

ToolHandler = Callable[[BaseModel, "ToolContext"], Awaitable[Any]]
_TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]+\.v[0-9]+$")

# 工具审计状态只描述一次调用的结果：started 已开始；succeeded 真实执行成功；
# failed 真实执行失败。它不能用来替代确认授权状态或 Java 业务资源状态。
ToolAuditStatus = Literal["started", "succeeded", "failed"]


class ToolRegistryError(RuntimeError):
    """工具注册或调用边界错误的稳定基类。"""


class DuplicateToolError(ToolRegistryError):
    """同一个版本的工具被重复注册。"""


class InvalidToolDefinitionError(ToolRegistryError):
    """工具元数据不满足注册约束。"""


class UnknownToolError(ToolRegistryError):
    """模型请求了未注册的工具。"""


class ToolInputValidationError(ToolRegistryError):
    """工具输入没有通过严格的参数 Schema。"""


class ToolExecutionError(ToolRegistryError):
    """工具内部出现未分类异常，避免把底层异常暴露给模型。"""


class ToolConfirmationRequiredError(ToolRegistryError):
    """写工具缺少上游签发的确认凭证。"""


class ToolRoleForbiddenError(ToolRegistryError):
    """当前签名 AgentContext 的角色不能调用该工具。"""


class ToolContextBindingError(ToolRegistryError):
    """模型参数无法安全绑定到已验证 AgentContext。"""


class ToolConfirmationNormalizationError(ToolRegistryError):
    """写工具参数无法形成确定性确认动作。"""


@dataclass(frozen=True)
class ToolContext:
    """一次工具调用的受控上下文。

    signed_context 由上游认证服务签发，Python Agent 只负责透传给 Java Gateway，不能
    根据自然语言、模型输出或工具参数重新构造主体和组织范围。request_id/trace_id
    只用于跨服务定位，不承载可被模型修改的权限信息。
    """

    gateway_context: GatewayRequestContext
    # 只读 Agent 工具在需要按当前用户做 RAG 权限过滤时使用。该身份来自已验证的
    # AgentContext，不是模型参数；写工具仍由 Gateway 再次完成最终权限判断。
    identity: AgentIdentity | None = None
    # 仅用于查询前的“用户问题-工具参数”一致性校验；不会写入 State、Checkpoint
    # 或工具审计，也不能作为权限依据。
    user_message: str | None = None


@dataclass(frozen=True)
class ToolAuditEvent:
    """工具审计事件的安全最小字段集合。

    审计刻意不保存原始输入、输出、Prompt、签名上下文或 Token。工具参数可能包含
    用户 ID、时间范围等敏感信息，若后续需要业务审计，应由 Java Gateway 按其自身
    脱敏策略记录；Agent 侧只记录调用事实和可定位的错误码。
    """

    tool_id: str
    status: ToolAuditStatus
    request_id: str | None
    trace_id: str | None
    duration_ms: float | None = None
    error_code: str | None = None


class ToolAuditSink(Protocol):
    """审计输出接口，生产默认使用结构化日志，测试可以注入记录器。"""

    def record(self, event: ToolAuditEvent) -> None:
        """接收一个不含敏感参数的工具审计事件。"""


class LoggingToolAuditSink:
    """把工具调用元数据输出到统一结构化日志。"""

    def __init__(self) -> None:
        self._logger = structlog.get_logger("agent.tools")

    def record(self, event: ToolAuditEvent) -> None:
        fields: dict[str, Any] = {
            "tool_id": event.tool_id,
            "tool_status": event.status,
            "tool_request_id": event.request_id,
            "tool_trace_id": event.trace_id,
        }
        if event.duration_ms is not None:
            fields["tool_duration_ms"] = event.duration_ms
        if event.error_code is not None:
            fields["tool_error_code"] = event.error_code
        self._logger.info("agent_tool_call", **fields)


@dataclass(frozen=True)
class ToolDefinition:
    """一个可被 Agent Runtime 发现和调用的工具定义。"""

    tool_id: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    allowed_roles: frozenset[str]
    read_only: bool
    requires_confirmation: bool
    # 写工具必须绑定固定策略，避免新增工具忘记定义资源、摘要和风险边界。
    confirmation_policy: ConfirmationPolicy | None = None

    @property
    def version(self) -> str:
        """从版本化工具 ID 提取版本，避免定义和注册表出现两份状态。"""

        return self.tool_id.rsplit(".", maxsplit=1)[-1]

    @property
    def model_name(self) -> str:
        """返回供应商 Tool Calling 可接受的模型侧名称。

        内部工具 ID 保留命名空间和版本号，例如
        ``fitness.operations.metric.query.v1``，便于审计、确认单和代码检索。
        但 OpenAI-compatible 接口要求函数名只包含字母、数字、下划线和连字符，
        因此只在供应商边界把点号转换成下划线；业务内部仍始终使用原始 ID。
        """

        return self.tool_id.replace(".", "_")

    def public_spec(self) -> dict[str, Any]:
        """返回可提供给 Supervisor/模型的工具描述，不包含 Python handler。"""

        return {
            "name": self.tool_id,
            "model_name": self.model_name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "version": self.version,
            "allowed_roles": sorted(self.allowed_roles),
            "read_only": self.read_only,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_action": (
                self.confirmation_policy.action if self.confirmation_policy is not None else None
            ),
        }


class ToolRegistry:
    """集中管理工具定义，并强制执行 Schema、审计和错误边界。"""

    def __init__(self, audit_sink: ToolAuditSink | None = None) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._audit_sink = audit_sink or LoggingToolAuditSink()

    def register(self, definition: ToolDefinition) -> None:
        """注册工具并检查版本、角色和写操作安全元数据。"""

        if not _TOOL_ID_PATTERN.fullmatch(definition.tool_id):
            raise InvalidToolDefinitionError("tool_id must use a namespaced name ending with .vN")
        if not definition.description.strip():
            raise InvalidToolDefinitionError("tool description is required")
        if not definition.allowed_roles:
            raise InvalidToolDefinitionError("at least one allowed role is required")
        if not definition.read_only and not definition.requires_confirmation:
            raise InvalidToolDefinitionError("write tools must require explicit confirmation")
        if (
            not definition.read_only
            and definition.requires_confirmation
            and definition.confirmation_policy is None
        ):
            raise InvalidToolDefinitionError(
                "confirmed write tools must declare a confirmation policy"
            )
        if definition.tool_id in self._definitions:
            raise DuplicateToolError(f"tool already registered: {definition.tool_id}")
        if any(item.model_name == definition.model_name for item in self._definitions.values()):
            raise DuplicateToolError(f"model tool name already registered: {definition.model_name}")
        self._definitions[definition.tool_id] = definition

    def get(self, tool_id: str) -> ToolDefinition:
        """按精确版本获取工具；不允许模糊匹配或自动降级到旧版本。"""

        try:
            return self._definitions[tool_id]
        except KeyError as exc:
            for definition in self._definitions.values():
                if definition.model_name == tool_id:
                    return definition
            raise UnknownToolError(f"unknown tool: {tool_id}") from exc

    def public_specs(self) -> list[dict[str, Any]]:
        """返回稳定排序的工具 Schema，供 Supervisor 构建受控 Tool Calling。"""

        return [
            definition.public_spec()
            for definition in sorted(self._definitions.values(), key=lambda item: item.tool_id)
        ]

    def bind_context_input(
        self,
        tool_id: str,
        raw_input: Mapping[str, Any],
        identity: AgentIdentity | None,
    ) -> dict[str, Any]:
        """把模型参数中的租户和当前主体字段绑定到已验签身份。

        模型可以提出“明天上午、瑜伽课、王教练”等业务意图，但不能决定自己属于
        哪个机构或替哪个学员读取/写入数据。此前仅依赖 Gateway 最终拒绝虽然安全，
        却会把模型随机构 ID 一起猜错的输入变成 403/503。这里在 Agent 工具边界
        先做确定性绑定，并让确认单创建、只读查询和确认恢复共用同一份结果。
        """

        definition = self.get(tool_id)
        bound = dict(raw_input)
        if identity is None:
            return bound

        organization_field = self._model_field_key(definition, "organization_id")
        if organization_field is not None:
            if len(identity.organization_ids) != 1:
                raise ToolContextBindingError(
                    "当前 AgentContext 包含多个机构，不能在模型参数中猜测机构"
                )
            self._remove_model_alias(definition, bound, organization_field)
            bound[organization_field] = next(iter(identity.organization_ids))

        # 只有纯学员上下文才强制绑定当前主体。管理员或教练可能在授权范围内
        # 为其他学员处理业务，仍由 Gateway 校验组织成员关系和教练绑定关系。
        elevated_roles = {"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH"}
        if "STUDENT" in identity.roles and not identity.roles.intersection(elevated_roles):
            for field_name in ("student_id", "user_id"):
                field_key = self._model_field_key(definition, field_name)
                if field_key is not None:
                    self._remove_model_alias(definition, bound, field_key)
                    bound[field_key] = identity.subject
        return bound

    @staticmethod
    def _remove_model_alias(
        definition: ToolDefinition, bound: dict[str, Any], field_name: str
    ) -> None:
        """把 alias 输入归一化为 Agent 内部字段，避免 strict Schema 看到重复键。"""

        field = definition.input_model.model_fields.get(field_name)
        if field is not None and field.alias and field.alias != field_name:
            bound.pop(field.alias, None)

    @staticmethod
    def _model_field_key(definition: ToolDefinition, field_name: str) -> str | None:
        """返回 Agent Schema 的 Python 字段名，兼容跨服务 camelCase 别名。

        Pydantic 的 ``populate_by_name`` 允许模型输入使用 snake_case，并在最终
        ``model_dump(by_alias=True)`` 时转换成 Java 需要的 camelCase。如果这里把
        绑定值写到 alias 字段，会同时保留模型原始的 snake_case 字段，严格的
        ``extra='forbid'`` 就会把同一个合法请求误判为多余参数。因此租户绑定必须
        写回 Python 字段名；跨服务命名转换只能发生在 Gateway Payload 生成处。
        """

        field = definition.input_model.model_fields.get(field_name)
        if field is None:
            return None
        return field_name

    def normalize_confirmation(
        self,
        tool_id: str,
        raw_input: Mapping[str, Any],
        *,
        context: ConfirmationNormalizationContext,
        organization_id: str,
        resource: ConfirmationResourceSnapshot | None = None,
    ) -> NormalizedConfirmationAction:
        """把写工具输入转换为确认动作，不执行任何外部副作用。

        该方法是后续 ``interrupt()`` 的前置边界：先校验和规范化，再创建确认单；只有
        用户批准、签发窄范围凭证并恢复图后，``invoke`` 才能继续调用 Gateway。
        """

        definition = self.get(tool_id)
        if definition.read_only or not definition.requires_confirmation:
            raise ToolConfirmationNormalizationError("only confirmed write tools can be normalized")
        if definition.confirmation_policy is None:
            raise ToolConfirmationNormalizationError("tool confirmation policy is missing")
        try:
            validated_input = definition.input_model.model_validate(dict(raw_input))
            return normalize_confirmation_action(
                tool_id=definition.tool_id,
                input_data=validated_input,
                policy=definition.confirmation_policy,
                context=context,
                organization_id=organization_id,
                resource=resource,
            )
        except ValueError as exc:
            raise ToolConfirmationNormalizationError(
                f"cannot normalize confirmation for tool: {definition.tool_id}"
            ) from exc

    async def invoke(
        self,
        tool_id: str,
        raw_input: Mapping[str, Any],
        context: ToolContext,
    ) -> Any:
        """校验并调用一个工具，返回 JSON 兼容的 Tool View。

        这里不接受任意可调用对象，也不允许工具名称映射到动态导入路径。这样模型即使
        输出了未知名称、额外参数或越界 limit，也只能得到明确的工具错误，不能触达
        数据库或任意 Python 函数。
        """

        definition = self._get_or_audit_unknown(tool_id, context)
        started_at = perf_counter()
        request_id = context.gateway_context.request_id
        trace_id = context.gateway_context.trace_id
        # 模型可能传回经过供应商边界转换的 model_name；审计必须落原始内部
        # tool_id，保证运营排障、指标统计和确认单引用的是同一个稳定标识。
        self._audit_sink.record(ToolAuditEvent(definition.tool_id, "started", request_id, trace_id))

        # allowed_roles 是 Agent 层的第一道工具暴露边界，必须在实际调用入口复核，
        # 不能只把它作为模型描述字段。Java Gateway 仍会根据签名上下文、组织范围和
        # 资源关系做最终授权；这里的提前拒绝可以避免学员触发教练专属的 LLM/RAG
        # 计算，也防止未来新增纯 Python 工具遗漏下游权限层。直接单测若没有身份，
        # 保持兼容；生产 Supervisor 和确认恢复链路始终注入已验证身份。
        if context.identity is not None and not definition.allowed_roles.intersection(
            context.identity.roles
        ):
            self._record_failure(
                definition.tool_id, request_id, trace_id, "ROLE_FORBIDDEN", started_at
            )
            raise ToolRoleForbiddenError(f"role is not allowed for tool: {definition.tool_id}")

        if not definition.read_only and not context.gateway_context.confirmation_token:
            self._record_failure(
                definition.tool_id, request_id, trace_id, "CONFIRMATION_REQUIRED", started_at
            )
            raise ToolConfirmationRequiredError(
                f"confirmation is required for tool: {definition.tool_id}"
            )

        try:
            bound_input = self.bind_context_input(tool_id, raw_input, context.identity)
            validated_input = definition.input_model.model_validate(bound_input)
        except ValidationError as exc:
            self._record_failure(
                definition.tool_id, request_id, trace_id, "INVALID_INPUT", started_at
            )
            raise ToolInputValidationError(f"invalid input for tool: {definition.tool_id}") from exc
        except ToolContextBindingError as exc:
            self._record_failure(
                definition.tool_id, request_id, trace_id, "CONTEXT_BINDING_FAILED", started_at
            )
            raise ToolInputValidationError(
                f"tool input cannot be bound to verified context: {definition.tool_id}"
            ) from exc

        try:
            result = await definition.handler(validated_input, context)
        except GatewayClientError as exc:
            error_code = exc.code or type(exc).__name__
            self._record_failure(definition.tool_id, request_id, trace_id, error_code, started_at)
            # 保留 Gateway 的稳定错误类型，后续 API 层可映射为可恢复的用户提示。
            raise
        except Exception as exc:
            self._record_failure(
                definition.tool_id, request_id, trace_id, "TOOL_EXECUTION_FAILED", started_at
            )
            raise ToolExecutionError("tool execution failed") from exc

        self._audit_sink.record(
            ToolAuditEvent(
                definition.tool_id,
                "succeeded",
                request_id,
                trace_id,
                duration_ms=self._duration_ms(started_at),
            )
        )
        return _to_json_compatible(result)

    def _get_or_audit_unknown(self, tool_id: str, context: ToolContext) -> ToolDefinition:
        try:
            return self.get(tool_id)
        except UnknownToolError:
            # 未知工具没有可信的定义。只有符合工具 ID 语法的名称才写入日志，避免把
            # 模型输出中的任意长文本或潜在敏感内容原样带进审计系统。
            self._audit_sink.record(
                ToolAuditEvent(
                    tool_id=tool_id if _TOOL_ID_PATTERN.fullmatch(tool_id) else "<invalid>",
                    status="failed",
                    request_id=context.gateway_context.request_id,
                    trace_id=context.gateway_context.trace_id,
                    error_code="UNKNOWN_TOOL",
                )
            )
            raise

    def _record_failure(
        self,
        tool_id: str,
        request_id: str | None,
        trace_id: str | None,
        error_code: str,
        started_at: float,
    ) -> None:
        self._audit_sink.record(
            ToolAuditEvent(
                tool_id,
                "failed",
                request_id,
                trace_id,
                duration_ms=self._duration_ms(started_at),
                error_code=error_code,
            )
        )

    @staticmethod
    def _duration_ms(started_at: float) -> float:
        return round((perf_counter() - started_at) * 1000, 2)


def _to_json_compatible(value: Any) -> Any:
    """把 Gateway Pydantic View 转成可直接交给模型/HTTP 层的 JSON 数据。"""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [_to_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_to_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_json_compatible(item) for key, item in value.items()}
    return value


class EmptyToolInput(BaseModel):
    """无参数工具仍使用严格模型，拒绝模型偷偷传入未定义字段。"""

    model_config = ConfigDict(extra="forbid")
