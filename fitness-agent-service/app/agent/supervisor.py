"""基于 LangGraph 的 Supervisor Runtime。

Supervisor 负责维护一次对话的状态、让模型选择已注册工具、执行工具并把真实结果
放回模型上下文。它不直接访问数据库，也不把自然语言中的用户 ID 当作权限依据。
当前版本先完成稳定的 Runtime 边界，专业 Fitness/Booking/Operations Agent 会在此
基础上逐步接入；赛事、作品和活动运营请求明确返回不在本项目范围内。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app.confirmation.models import ConfirmationStateError
from app.confirmation.service import ConfirmationService
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.cache import SessionLockManager, SessionLockUnavailable
from app.infrastructure.gateway_client import GatewayRequestContext, GatewayUnavailableError
from app.infrastructure.model_gateway import (
    ModelConfigurationError,
    ModelGateway,
    ModelResponseError,
    ModelToolCall,
)
from app.rag.models import RetrievalScope
from app.rag.service import RagSearchError, RagService

from .tool_registry import ToolContext, ToolRegistry, ToolRegistryError

# SupervisorRoute 是意图路由，不是业务执行状态：FITNESS_COACHING 健身指导；BOOKING 预约；
# OPERATIONS 经营分析；UNSUPPORTED_LEGACY 明确拒绝赛事、作品和活动运营遗留范围。
SupervisorRoute = Literal[
    "FITNESS_COACHING",
    "BOOKING",
    "OPERATIONS",
    "UNSUPPORTED_LEGACY",
]
SupervisorRunStatus = Literal["COMPLETED", "CONFIRMATION_REQUIRED"]


class SupervisorRuntimeError(RuntimeError):
    """Supervisor 无法安全完成当前请求时抛出。"""


class SupervisorSessionBusy(SupervisorRuntimeError):
    """同一会话正在执行另一个请求。"""


class SupervisorCheckpointIncompatible(SupervisorRuntimeError):
    """Checkpoint 仍包含旧版敏感运行上下文，必须重新建立会话。"""


class ToolStepLimitExceeded(SupervisorRuntimeError):
    """模型连续请求工具超过预算，防止循环调用消耗失控。"""


class UnsupportedLegacyRequest(SupervisorRuntimeError):
    """请求命中被明确排除的旧赛事/作品/活动运营范围。"""


@dataclass(frozen=True)
class SupervisorRequest:
    """一次 Agent 请求的入口参数和非持久化运行上下文。"""

    user_message: str
    gateway_context: GatewayRequestContext
    conversation_id: str
    # identity/thread_id 只存在于本次请求上下文，不属于可持久化 State。
    thread_id: str | None = None
    locale: str = "zh-CN"
    identity: AgentIdentity | None = None


@dataclass(frozen=True)
class SupervisorRuntimeContext:
    """只在本次图执行期间存在的敏感上下文。

    <p>LangGraph 的 State 会写入 PostgreSQL Checkpoint，不能把签名 AgentContext、确认
    Token 或 Gateway 请求对象放进去。节点通过 LangGraph 的 ``context`` 接收本对象，图
    暂停、恢复或服务重启时必须由新的 HTTP 请求重新验证并注入。</p>
    """

    gateway_context: GatewayRequestContext
    # 签名身份和 thread 只通过 LangGraph runtime context 注入，不能写入 State。
    identity: AgentIdentity | None = None
    thread_id: str | None = None


@dataclass(frozen=True)
class SupervisorResponse:
    """对外返回的稳定响应，不暴露 LangGraph 或供应商对象。"""

    answer: str
    route: SupervisorRoute
    tool_steps: int
    input_tokens: int | None
    output_tokens: int | None
    status: SupervisorRunStatus = "COMPLETED"
    confirmation_id: str | None = None
    confirmation_summary: dict[str, Any] | None = None
    confirmation_expires_at: str | None = None


class SupervisorState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    route: SupervisorRoute
    tool_steps: int
    final_answer: str
    input_tokens: int
    output_tokens: int
    error: str
    model_tool_calls: list[ModelToolCall]
    # 只保存确认单 ID；精确参数保存在加密确认单，绝不进入 Checkpoint。
    pending_confirmation_id: str | None


class Supervisor:
    """统一的模型-工具循环和最小路由策略。"""

    def __init__(
        self,
        models: ModelGateway,
        tools: ToolRegistry,
        *,
        max_tool_steps: int = 4,
        checkpointer: Any | None = None,
        session_lock: SessionLockManager | None = None,
        rag_service: RagService | None = None,
        confirmation_service: ConfirmationService | None = None,
    ) -> None:
        self.models = models
        self.tools = tools
        self.max_tool_steps = max_tool_steps
        self._checkpointer = checkpointer
        self.session_lock = session_lock
        self.rag_service = rag_service
        self.confirmation_service = confirmation_service
        self._graph = self._build_graph()

    async def invoke(self, request: SupervisorRequest) -> SupervisorResponse:
        """执行一次可恢复的 Supervisor 状态图。"""

        route = classify_route(request.user_message)
        if route == "UNSUPPORTED_LEGACY":
            raise UnsupportedLegacyRequest("赛事、作品和活动运营不属于当前健身 Agent 的业务范围")

        knowledge_context = ""
        if route == "FITNESS_COACHING" and self.rag_service is not None and request.identity:
            try:
                rag_result = await self.rag_service.search(
                    request.user_message,
                    RetrievalScope(
                        subject=request.identity.subject,
                        organization_ids=request.identity.organization_ids,
                        roles=request.identity.roles,
                    ),
                )
                knowledge_context = rag_result.as_prompt_context()
            except RagSearchError as exc:
                # 检索失败必须对调用方可见；模型不能收到未标记或伪造的回退上下文。
                raise SupervisorRuntimeError("knowledge retrieval failed") from exc

        system_prompt = _system_prompt(route, request.locale)
        if knowledge_context:
            system_prompt = f"{system_prompt}\n\n{knowledge_context}"
        initial: SupervisorState = {
            "route": route,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.user_message},
            ],
            "tool_steps": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        thread_id = request.thread_id or request.conversation_id
        config = {"configurable": {"thread_id": thread_id}}

        async def run_with_persisted_history() -> Any:
            # Checkpoint 只会保存状态，不会自动把本次新消息拼接到业务上下文中。
            # 每次请求都先读取同一 thread 的最新状态，再追加当前 user message，
            # 确保跨 HTTP 请求的多轮对话真正连续，同时避免重复写入 system prompt。
            previous_values: dict[str, Any] = {}
            if self._checkpointer is not None:
                # 先读取原始 Checkpoint，再让 LangGraph 按当前 Schema 映射 values。旧版
                # 的 ``request`` 不是当前 State 字段，若只检查 values 会被框架静默丢弃，
                # 这会掩盖历史签名上下文已经落盘的事实。
                raw_checkpoint = await self._checkpointer.aget_tuple(config)
                if raw_checkpoint is not None and "request" in raw_checkpoint.checkpoint.get(
                    "channel_values", {}
                ):
                    raise SupervisorCheckpointIncompatible(
                        "checkpoint uses an incompatible sensitive runtime state"
                    )
                previous = await self._graph.aget_state(config)
                previous_values = previous.values if previous else {}
            if "request" in previous_values:
                # 旧版本把签名上下文和潜在确认凭证放进 State。不能继续反序列化或
                # 自动迁移这类状态，避免敏感对象在恢复链路中继续传播；调用方应重建会话。
                raise SupervisorCheckpointIncompatible(
                    "checkpoint uses an incompatible sensitive runtime state"
                )
            previous_messages = previous_values.get("messages", [])
            if previous_messages:
                initial["messages"] = [
                    *previous_messages,
                    {"role": "user", "content": request.user_message},
                ]
                initial["input_tokens"] = previous_values.get("input_tokens", 0)
                initial["output_tokens"] = previous_values.get("output_tokens", 0)
            return await self._graph.ainvoke(
                initial,
                config=config,
                context=SupervisorRuntimeContext(
                    gateway_context=request.gateway_context,
                    identity=request.identity,
                    thread_id=thread_id,
                ),
            )

        try:
            if self.session_lock is None:
                final_state = await run_with_persisted_history()
            else:
                async with self.session_lock.hold(thread_id):
                    final_state = await run_with_persisted_history()
        except SessionLockUnavailable as exc:
            raise SupervisorSessionBusy("conversation is already being processed") from exc
        except (ModelConfigurationError, ModelResponseError, ToolRegistryError) as exc:
            raise SupervisorRuntimeError("supervisor execution failed") from exc

        interrupts = final_state.get("__interrupt__", [])
        if interrupts:
            interrupt_value = interrupts[0].value
            if not isinstance(interrupt_value, dict):
                raise SupervisorRuntimeError("supervisor returned an invalid confirmation prompt")
            return SupervisorResponse(
                answer="",
                route=route,
                tool_steps=final_state.get("tool_steps", 0),
                input_tokens=_optional_int(final_state.get("input_tokens")),
                output_tokens=_optional_int(final_state.get("output_tokens")),
                status="CONFIRMATION_REQUIRED",
                confirmation_id=_optional_text(interrupt_value.get("confirmation_id")),
                confirmation_summary=(
                    interrupt_value.get("summary")
                    if isinstance(interrupt_value.get("summary"), dict)
                    else None
                ),
                confirmation_expires_at=_optional_text(interrupt_value.get("expires_at")),
            )
        answer = final_state.get("final_answer", "").strip()
        if not answer:
            raise SupervisorRuntimeError("supervisor produced an empty answer")
        return SupervisorResponse(
            answer=answer,
            route=final_state["route"],
            tool_steps=final_state.get("tool_steps", 0),
            input_tokens=_optional_int(final_state.get("input_tokens")),
            output_tokens=_optional_int(final_state.get("output_tokens")),
        )

    async def resume_confirmation(
        self,
        confirmation_id: str,
        *,
        identity: AgentIdentity,
        gateway_context: GatewayRequestContext,
        thread_id: str,
    ) -> SupervisorResponse:
        """由服务端恢复已批准确认，不接受客户端直接注入执行参数或 Token。

        ``Command(resume=...)`` 只携带服务端生成的确认 ID，节点会再次从 PostgreSQL 读取
        已批准事实，并在恢复节点内临时解密参数、生成 Token 和调用 Registry。Token 与
        明文参数不会进入 State、Checkpoint、消息或 HTTP 响应。
        """

        if self.confirmation_service is None:
            raise SupervisorRuntimeError("confirmation service is not configured")
        config = {"configurable": {"thread_id": thread_id}}
        try:
            if self.session_lock is None:
                final_state = await self._graph.ainvoke(
                    Command(resume={"confirmation_id": confirmation_id}),
                    config=config,
                    context=SupervisorRuntimeContext(
                        gateway_context=gateway_context,
                        identity=identity,
                        thread_id=thread_id,
                    ),
                )
            else:
                async with self.session_lock.hold(thread_id):
                    final_state = await self._graph.ainvoke(
                        Command(resume={"confirmation_id": confirmation_id}),
                        config=config,
                        context=SupervisorRuntimeContext(
                            gateway_context=gateway_context,
                            identity=identity,
                            thread_id=thread_id,
                        ),
                    )
        except SessionLockUnavailable as exc:
            raise SupervisorSessionBusy("conversation is already being processed") from exc
        except (
            ConfirmationStateError,
            ModelConfigurationError,
            ModelResponseError,
            ToolRegistryError,
        ) as exc:
            raise SupervisorRuntimeError("confirmation execution failed") from exc
        return self._response_from_state(final_state)

    @staticmethod
    def _response_from_state(final_state: dict[str, Any]) -> SupervisorResponse:
        """把恢复后的图状态转换成稳定完成响应。"""

        answer = str(final_state.get("final_answer", "")).strip()
        if not answer:
            raise SupervisorRuntimeError("confirmed execution produced an empty answer")
        return SupervisorResponse(
            answer=answer,
            route=final_state["route"],
            tool_steps=final_state.get("tool_steps", 0),
            input_tokens=_optional_int(final_state.get("input_tokens")),
            output_tokens=_optional_int(final_state.get("output_tokens")),
        )

    def _build_graph(self) -> Any:
        graph = StateGraph(SupervisorState, context_schema=SupervisorRuntimeContext)
        graph.add_node("model", self._model_node)
        graph.add_node("tools", self._tool_node)
        graph.add_node("confirmation", self._confirmation_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model",
            self._after_model,
            {"tools": "tools", "confirmation": "confirmation", "finish": END},
        )
        graph.add_edge("tools", "model")
        graph.add_edge("confirmation", END)
        return graph.compile(checkpointer=self._checkpointer)

    async def _model_node(
        self, state: SupervisorState, runtime: Runtime[SupervisorRuntimeContext]
    ) -> dict[str, Any]:
        # 中断恢复会从该节点重新执行。只要 State 已有待确认 ID，就不能再次调用 LLM，
        # 否则模型可能重新生成一套与原确认单不同的参数。
        if state.get("pending_confirmation_id"):
            return {"model_tool_calls": []}
        tool_schemas = (
            _model_tools(self.tools) if state.get("tool_steps", 0) < self.max_tool_steps else []
        )
        turn = await self.models.chat_with_tools(
            state["messages"],
            tools=tool_schemas,
        )
        tool_calls = list(turn.tool_calls)
        if any(not self.tools.get(call.name).read_only for call in tool_calls):
            if len(tool_calls) != 1:
                raise SupervisorRuntimeError(
                    "a confirmation is required for each write tool call separately"
                )
            if runtime.context is None or runtime.context.identity is None:
                raise SupervisorRuntimeError("signed identity is required for write confirmation")
            if self.confirmation_service is None:
                raise SupervisorRuntimeError("confirmation service is not configured")
            call = tool_calls[0]
            confirmation = await self.confirmation_service.prepare(
                tool_id=call.name,
                raw_input=call.arguments,
                gateway_context=runtime.context.gateway_context,
                identity=runtime.context.identity,
                thread_id=runtime.context.thread_id or "unknown-thread",
            )
            # Checkpoint 只保存空参数占位和确认 ID；精确参数已在确认单中加密保存。
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": turn.content,
                "tool_calls": [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": "{}"},
                    }
                ],
            }
            return {
                "messages": [*state["messages"], assistant_message],
                "model_tool_calls": [],
                "pending_confirmation_id": confirmation.id,
                "final_answer": "",
                "input_tokens": state.get("input_tokens", 0) + (turn.input_tokens or 0),
                "output_tokens": state.get("output_tokens", 0) + (turn.output_tokens or 0),
            }
        regular_assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": turn.content,
        }
        if tool_calls:
            regular_assistant_message["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in tool_calls
            ]
        return {
            "messages": [*state["messages"], regular_assistant_message],
            "model_tool_calls": tool_calls,
            "final_answer": turn.content if not tool_calls else "",
            "input_tokens": state.get("input_tokens", 0) + (turn.input_tokens or 0),
            "output_tokens": state.get("output_tokens", 0) + (turn.output_tokens or 0),
        }

    async def _confirmation_node(
        self, state: SupervisorState, runtime: Runtime[SupervisorRuntimeContext]
    ) -> dict[str, Any]:
        """展示确认卡片，或在服务端批准后恢复并执行唯一写工具。"""

        confirmation_id = state.get("pending_confirmation_id")
        if not confirmation_id or runtime.context is None or runtime.context.identity is None:
            raise SupervisorRuntimeError("confirmation runtime context is incomplete")
        if self.confirmation_service is None:
            raise SupervisorRuntimeError("confirmation service is not configured")
        record = await self.confirmation_service.get_for_subject(
            confirmation_id, runtime.context.identity
        )
        if record.authorization_status == "PENDING":
            # 首次进入节点只展示脱敏摘要并暂停；resume 的内容不会被当作批准事实。
            resume_value = interrupt(
                {
                    "type": "CONFIRMATION_REQUIRED",
                    "confirmation_id": record.id,
                    "status": record.authorization_status,
                    "tool_id": record.tool_id,
                    "action": record.action,
                    "risk_level": record.risk_level,
                    "summary": dict(record.display_summary),
                    "expires_at": record.expires_at.isoformat(),
                }
            )
            if (
                not isinstance(resume_value, dict)
                or resume_value.get("confirmation_id") != confirmation_id
            ):
                raise SupervisorRuntimeError("confirmation resume scope does not match")
            record = await self.confirmation_service.get_for_subject(
                confirmation_id, runtime.context.identity
            )
        if record.authorization_status != "APPROVED":
            raise ConfirmationStateError("confirmation is not approved for execution")

        prepared = await self.confirmation_service.prepare_execution(
            confirmation_id,
            identity=runtime.context.identity,
            trace_id=runtime.context.gateway_context.trace_id,
        )
        execution_context = ToolContext(
            gateway_context=replace(
                runtime.context.gateway_context,
                request_id=prepared.record.request_id,
                confirmation_token=prepared.confirmation_token,
            ),
            identity=runtime.context.identity,
        )
        try:
            result = await self.tools.invoke(
                prepared.record.tool_id, prepared.tool_input, execution_context
            )
        except Exception as exc:
            retryable = isinstance(exc, GatewayUnavailableError)
            await self.confirmation_service.finish_execution(
                confirmation_id,
                success=False,
                trace_id=runtime.context.gateway_context.trace_id,
                error_code=type(exc).__name__,
                retryable=retryable,
            )
            raise SupervisorRuntimeError("confirmed training operation failed") from exc
        await self.confirmation_service.finish_execution(
            confirmation_id,
            success=True,
            trace_id=runtime.context.gateway_context.trace_id,
        )
        return {
            "messages": [
                *state["messages"],
                {
                    "role": "tool",
                    "tool_call_id": "confirmed-execution",
                    "content": json.dumps(result, ensure_ascii=False),
                },
            ],
            "pending_confirmation_id": None,
            "model_tool_calls": [],
            "tool_steps": state.get("tool_steps", 0) + 1,
            "final_answer": f"已完成{record.display_summary.get('operation', '确认写操作')}。",
        }

    async def _tool_node(
        self, state: SupervisorState, runtime: Runtime[SupervisorRuntimeContext]
    ) -> dict[str, Any]:
        if runtime.context is None:
            raise SupervisorRuntimeError("supervisor runtime context is missing")
        context = ToolContext(
            gateway_context=runtime.context.gateway_context,
            identity=runtime.context.identity,
        )
        tool_messages: list[dict[str, Any]] = []
        calls = state.get("model_tool_calls", [])
        for call in calls:
            result = await self.tools.invoke(call.name, call.arguments, context)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        return {
            "messages": [*state["messages"], *tool_messages],
            "tool_steps": state.get("tool_steps", 0) + 1,
        }

    @staticmethod
    def _after_model(state: SupervisorState) -> str:
        if state.get("pending_confirmation_id"):
            return "confirmation"
        return "tools" if state.get("model_tool_calls") else "finish"


def classify_route(user_message: str) -> SupervisorRoute:
    """执行业务范围护栏，不把角色或权限判断交给自然语言分类。

    这只是 Supervisor 的第一层路由标签，后续会替换为可评测的意图分类节点；真实
    数据权限仍由签名 AgentContext 和 Java Gateway 决定。旧模块被明确拦截，避免模型
    误把遗留赛事代码当成健身业务能力。
    """

    text = user_message.lower()
    if any(keyword in text for keyword in ("赛事", "比赛", "作品", "活动运营", "报名活动")):
        return "UNSUPPORTED_LEGACY"
    if any(keyword in text for keyword in ("预约", "改约", "取消预约", "课表", "课程")):
        return "BOOKING"
    if any(keyword in text for keyword in ("营收", "收入", "经营", "报表", "sql")):
        return "OPERATIONS"
    return "FITNESS_COACHING"


def _system_prompt(route: SupervisorRoute, locale: str) -> str:
    return (
        "你是健身平台的 Supervisor Agent。只处理健身训练、课程、合同、课时和预约相关业务。"
        "动态业务事实必须通过已注册工具查询，不能猜测；工具失败时必须明确告知失败，不能宣称成功。"
        "不得根据自然语言中的 user_id、organization_id 或角色改变权限。"
        f"当前路由={route}，语言={locale}。回答应简洁、准确，并区分已查询事实与一般建议。"
    )


def _model_tools(registry: ToolRegistry) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["input_schema"],
            },
        }
        for spec in registry.public_specs()
    ]


def _optional_int(value: int | None) -> int | None:
    return value if value else None


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
