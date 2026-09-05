"""基于 LangGraph 的 Supervisor Runtime。

Supervisor 负责维护一次对话的状态、让模型选择已注册工具、执行工具并把真实结果
放回模型上下文。它不直接访问数据库，也不把自然语言中的用户 ID 当作权限依据。
当前版本先完成稳定的 Runtime 边界，专业健身/预约/经营 Agent 会在此
基础上逐步接入；赛事、作品和活动运营请求明确返回不在本项目范围内。
"""

from __future__ import annotations

import json
from collections.abc import Hashable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, TypedDict

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app.confirmation.models import ConfirmationStateError
from app.confirmation.service import ConfirmationService
from app.evaluation.telemetry import TruLensTelemetry
from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.cache import SessionLockLost, SessionLockManager, SessionLockUnavailable
from app.infrastructure.gateway_client import (
    GatewayClientError,
    GatewayRequestContext,
    GatewayUnavailableError,
)
from app.infrastructure.model_gateway import (
    ModelConfigurationError,
    ModelGateway,
    ModelResponseError,
    ModelToolCall,
)
from app.memory.candidate import (
    MemoryCandidateExtractionError,
    MemoryCandidateExtractionService,
    build_candidate_context,
)
from app.memory.candidate_service import (
    MemoryCandidatePersistenceError,
    MemoryCandidateService,
)
from app.rag.models import RetrievalScope
from app.rag.service import RagSearchError, RagService
from app.session_summary import (
    SessionSummaryError,
    SessionSummaryService,
    build_compacted_messages,
)

from .domain_subgraphs import (
    SupervisorRoute,
    build_domain_subgraph,
    domain_agent_spec,
    executable_domain_specs,
)
from .operations_tools import (
    OperationsQueryPreparationError,
    build_authorized_operations_tool_input,
    operations_prompt_hint,
)
from .tool_registry import ToolContext, ToolRegistry, ToolRegistryError

SupervisorRunStatus = Literal["COMPLETED", "CONFIRMATION_REQUIRED"]
_logger = structlog.get_logger("agent.supervisor")


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
    # 当前问题只传给需要做查询前策略校验的工具，不进入 State 或 Checkpoint。
    user_message: str | None = None


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
    # graph_version=2 表示请求由顶层 Supervisor 路由到领域子图。旧 Checkpoint 没有
    # 该字段，仍可通过父图保留的 legacy 节点完成已经暂停的确认，不会在升级时丢单。
    graph_version: int
    active_domain: str
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
        memory_candidate_service: MemoryCandidateService | None = None,
        session_summary_service: SessionSummaryService | None = None,
        # 保留此参数是为了兼容当前单元测试和旧的内嵌装配；正式应用使用上面的
        # Service，因为它还负责跨请求持久化和用户批准/拒绝。
        memory_candidate_extractor: MemoryCandidateExtractionService | None = None,
        telemetry: TruLensTelemetry | None = None,
    ) -> None:
        self.models = models
        self.tools = tools
        self.max_tool_steps = max_tool_steps
        self._checkpointer = checkpointer
        self.session_lock = session_lock
        self.rag_service = rag_service
        self.confirmation_service = confirmation_service
        self.memory_candidate_service = memory_candidate_service
        self.session_summary_service = session_summary_service
        self.memory_candidate_extractor = memory_candidate_extractor
        self.telemetry = telemetry or TruLensTelemetry.disabled()
        self._domain_graphs: dict[SupervisorRoute, Any] = {}
        self._graph = self._build_graph()

    @property
    def domain_graphs(self) -> Mapping[SupervisorRoute, Any]:
        """返回只读领域子图目录，供能力检查、测试和运行时诊断使用。"""

        return dict(self._domain_graphs)

    async def invoke(self, request: SupervisorRequest) -> SupervisorResponse:
        with self.telemetry.request(
            request_id=request.gateway_context.request_id,
            trace_id=request.gateway_context.trace_id,
            conversation_id=request.conversation_id,
            user_message=request.user_message,
        ) as request_span:
            response = await self._invoke(request)
            self.telemetry.set_attributes(
                request_span,
                {
                    "fitness.agent.route": response.route,
                    "fitness.agent.tool_steps": response.tool_steps,
                    "fitness.agent.input_tokens": response.input_tokens,
                    "fitness.agent.output_tokens": response.output_tokens,
                },
            )
            self.telemetry.finish_request(
                request_span, answer=response.answer, status=response.status
            )
            return response

    async def _invoke(self, request: SupervisorRequest) -> SupervisorResponse:
        """执行一次可恢复的 Supervisor 状态图。"""

        route = classify_route(request.user_message)
        if route == "UNSUPPORTED_LEGACY":
            raise UnsupportedLegacyRequest("赛事、作品和活动运营不属于当前健身 Agent 的业务范围")
        domain_spec = domain_agent_spec(route)

        restricted_answer = _customer_service_restricted_answer(request.user_message)
        if route == "CUSTOMER_SERVICE" and restricted_answer is not None:
            # 医疗、退款和争议问题不能交给模型自由发挥，也不能因为后续新增工具而
            # 意外进入写操作链路。固定回复只表达当前系统边界，不写入业务库或 Memory。
            return SupervisorResponse(
                answer=restricted_answer,
                route=route,
                tool_steps=0,
                input_tokens=None,
                output_tokens=None,
            )

        # API 传入的 thread_id 已经由 conversation_thread_id 脱敏；候选仓储只保存这个
        # 稳定标识，不接触原始会话 ID。没有显式 thread 时的单测回退不代表生产路径。
        thread_id = request.thread_id or request.conversation_id

        knowledge_context = ""
        if (
            domain_spec.uses_rag
            and (
                route != "CUSTOMER_SERVICE"
                or _looks_like_customer_service_knowledge_question(request.user_message)
            )
            and self.rag_service is not None
            and request.identity
        ):
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
                raise SupervisorRuntimeError("知识检索失败") from exc

        memory_candidate_context = ""
        if (
            domain_spec.uses_memory_candidates
            and request.identity is not None
            and (
                self.memory_candidate_service is not None
                or self.memory_candidate_extractor is not None
            )
        ):
            try:
                if self.memory_candidate_service is not None:
                    candidates = await self.memory_candidate_service.propose(
                        user_message=request.user_message,
                        identity=request.identity,
                        thread_id=thread_id,
                        source_request_id=request.gateway_context.request_id,
                    )
                else:
                    # 兼容旧装配时只做当前请求提取；这条路径不会持久化候选。
                    extractor = self.memory_candidate_extractor
                    assert extractor is not None
                    candidates = await extractor.propose(request.user_message)
                memory_candidate_context = build_candidate_context(
                    candidates, request.identity.organization_ids
                )
            except MemoryCandidatePersistenceError as exc:
                # 候选持久化故障不应阻断普通对话，但仍把未确认候选交给主模型，让用户
                # 知道当前不能声称“已记住”；同时通过日志和指标暴露需要修复的基础设施问题。
                memory_candidate_context = build_candidate_context(
                    exc.candidates, request.identity.organization_ids
                )
                _logger.warning(
                    "memory_candidate_persistence_failed",
                    request_id=request.gateway_context.request_id,
                    trace_id=request.gateway_context.trace_id,
                )
            except (MemoryCandidateExtractionError, ModelConfigurationError, ModelResponseError):
                # 候选提取是辅助能力，暂时不可用时不应阻断普通健身问答；主模型仍会
                # 通过公开的 Memory 保存工具和现有确认链路决定是否提出保存动作。
                _logger.warning(
                    "memory_candidate_extraction_failed",
                    request_id=request.gateway_context.request_id,
                    trace_id=request.gateway_context.trace_id,
                )

        system_prompt = _system_prompt(route, request.locale)
        if route == "OPERATIONS":
            system_prompt = f"{system_prompt}\n\n{operations_prompt_hint(request.user_message)}"
        if knowledge_context:
            system_prompt = f"{system_prompt}\n\n{knowledge_context}"
        candidate_message = (
            {"role": "system", "content": memory_candidate_context}
            if memory_candidate_context
            else None
        )
        initial_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if candidate_message is not None:
            initial_messages.append(candidate_message)
        initial_messages.append({"role": "user", "content": request.user_message})
        initial: SupervisorState = {
            "graph_version": 2,
            "active_domain": domain_spec.node_name,
            "route": route,
            "messages": initial_messages,
            "tool_steps": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
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
                    raise SupervisorCheckpointIncompatible("Checkpoint 包含不兼容的敏感运行时状态")
                previous = await self._graph.aget_state(config)
                previous_values = previous.values if previous else {}
            if "request" in previous_values:
                # 旧版本把签名上下文和潜在确认凭证放进 State。不能继续反序列化或
                # 自动迁移这类状态，避免敏感对象在恢复链路中继续传播；调用方应重建会话。
                raise SupervisorCheckpointIncompatible("Checkpoint 包含不兼容的敏感运行时状态")
            previous_messages = previous_values.get("messages", [])
            if previous_messages:
                session_summary = None
                if self.session_summary_service is not None and request.identity is not None:
                    try:
                        session_summary = await self.session_summary_service.load_for_subject(
                            thread_id, request.identity.subject
                        )
                    except Exception:
                        # 摘要是上下文增强能力，读取失败不能让已具备 Checkpoint 的普通
                        # 健身问答直接不可用；安全边界是“失败时不使用摘要”，不是猜测摘要。
                        _logger.exception(
                            "session_summary_load_failed",
                            request_id=request.gateway_context.request_id,
                        )
                # system、RAG 引用和 Memory 候选都是“本轮临时上下文”，不能沿用上一轮
                # 的领域提示。尤其同一会话从健身切换到预约时，如果保留旧
                # system 消息，即使工具白名单已经切换，模型仍可能受旧领域规则影响。
                # 因此先移除历史 system 消息，再由本轮重新构建领域提示、可选短期摘要
                # 和候选上下文；user/assistant/tool 历史继续保留，保证多轮语义连续。
                conversation_messages = [
                    message for message in previous_messages if message.get("role") != "system"
                ]
                current_messages = (
                    build_compacted_messages(
                        system_prompt=system_prompt,
                        summary=session_summary,
                        previous_messages=conversation_messages,
                        keep_recent_messages=self.session_summary_service.keep_recent_messages,
                    )
                    if session_summary and self.session_summary_service is not None
                    else [
                        {"role": "system", "content": system_prompt},
                        *conversation_messages,
                    ]
                )
                if candidate_message is not None:
                    # 短期摘要也是本轮系统上下文，Memory 候选放在所有 system 消息之后。
                    insertion_index = next(
                        (
                            index
                            for index, message in enumerate(current_messages)
                            if message.get("role") != "system"
                        ),
                        len(current_messages),
                    )
                    current_messages.insert(insertion_index, candidate_message)
                current_messages.append({"role": "user", "content": request.user_message})
                initial["messages"] = current_messages
                initial["input_tokens"] = previous_values.get("input_tokens", 0)
                initial["output_tokens"] = previous_values.get("output_tokens", 0)
            return await self._graph.ainvoke(
                initial,
                config=config,
                context=SupervisorRuntimeContext(
                    gateway_context=request.gateway_context,
                    identity=request.identity,
                    thread_id=thread_id,
                    user_message=request.user_message,
                ),
            )

        try:
            if self.session_lock is None:
                final_state = await run_with_persisted_history()
            else:
                async with self.session_lock.hold(thread_id) as lease:
                    final_state = await run_with_persisted_history()
                    lease.ensure_owned()
        except SessionLockUnavailable as exc:
            raise SupervisorSessionBusy("会话正在处理中") from exc
        except SessionLockLost as exc:
            raise SupervisorRuntimeError("会话锁租约失效，操作未确认完成") from exc
        except (
            GatewayClientError,
            ModelConfigurationError,
            ModelResponseError,
            ToolRegistryError,
        ) as exc:
            # 对外仍保持统一的 503，避免把模型供应商、Gateway 或工具参数细节泄漏给
            # 客户端；但必须在服务端记录可关联的脱敏故障元数据，否则调用方拿到 503
            # 后无法根据 request_id 定位真正原因。这里刻意只记录异常类型和链路 ID，
            # 不记录 Prompt、模型原文、签名上下文、确认参数或业务明细。
            _logger.exception(
                "supervisor_execution_failed",
                request_id=request.gateway_context.request_id,
                trace_id=request.gateway_context.trace_id,
                error_type=type(exc).__name__,
            )
            raise SupervisorRuntimeError("Supervisor 执行失败") from exc
        except SupervisorRuntimeError as exc:
            # 一些运行时护栏（例如模型工具调用超预算、确认运行上下文不完整或
            # 业务查询准备失败）本身不会落入上面的底层异常集合。若不在这里记录，
            # API 仍会返回 503，但服务端没有 request_id 对应的根因，现场只能反复
            # 猜测。这里只记录稳定类型和链路标识，不记录 Prompt、工具参数或 Token。
            _logger.exception(
                "supervisor_execution_failed",
                request_id=request.gateway_context.request_id,
                trace_id=request.gateway_context.trace_id,
                error_type=type(exc).__name__,
            )
            raise

        interrupts = final_state.get("__interrupt__", [])
        if interrupts:
            interrupt_value = interrupts[0].value
            if not isinstance(interrupt_value, dict):
                raise SupervisorRuntimeError("Supervisor 返回了无效的确认提示")
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
            raise SupervisorRuntimeError("Supervisor 生成了空回答")
        response = SupervisorResponse(
            answer=answer,
            route=final_state["route"],
            tool_steps=final_state.get("tool_steps", 0),
            input_tokens=_optional_int(final_state.get("input_tokens")),
            output_tokens=_optional_int(final_state.get("output_tokens")),
        )
        await self._persist_session_summary(
            final_state,
            config={"configurable": {"thread_id": thread_id}},
            thread_id=thread_id,
            identity=request.identity,
        )
        return response

    async def resume_confirmation(
        self,
        confirmation_id: str,
        *,
        identity: AgentIdentity,
        gateway_context: GatewayRequestContext,
        thread_id: str,
    ) -> SupervisorResponse:
        with self.telemetry.request(
            request_id=gateway_context.request_id,
            trace_id=gateway_context.trace_id,
            conversation_id=thread_id,
            user_message=None,
            route="CONFIRMATION_RESUME",
        ) as request_span:
            response = await self._resume_confirmation(
                confirmation_id,
                identity=identity,
                gateway_context=gateway_context,
                thread_id=thread_id,
            )
            self.telemetry.set_attributes(
                request_span,
                {
                    "fitness.agent.route": response.route,
                    "fitness.agent.tool_steps": response.tool_steps,
                    "fitness.agent.input_tokens": response.input_tokens,
                    "fitness.agent.output_tokens": response.output_tokens,
                },
            )
            self.telemetry.finish_request(
                request_span, answer=response.answer, status=response.status
            )
            return response

    async def _resume_confirmation(
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
            raise SupervisorRuntimeError("确认服务未配置")
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
                async with self.session_lock.hold(thread_id) as lease:
                    final_state = await self._graph.ainvoke(
                        Command(resume={"confirmation_id": confirmation_id}),
                        config=config,
                        context=SupervisorRuntimeContext(
                            gateway_context=gateway_context,
                            identity=identity,
                            thread_id=thread_id,
                        ),
                    )
                    lease.ensure_owned()
        except SessionLockUnavailable as exc:
            raise SupervisorSessionBusy("会话正在处理中") from exc
        except SessionLockLost as exc:
            raise SupervisorRuntimeError("会话锁租约失效，操作未确认完成") from exc
        except (
            ConfirmationStateError,
            GatewayClientError,
            ModelConfigurationError,
            ModelResponseError,
            ToolRegistryError,
        ) as exc:
            _logger.exception(
                "confirmation_execution_failed",
                request_id=gateway_context.request_id,
                trace_id=gateway_context.trace_id,
                error_type=type(exc).__name__,
            )
            raise SupervisorRuntimeError("确认操作执行失败") from exc
        except SupervisorRuntimeError as exc:
            # 恢复路径同样可能触发运行时护栏；否则确认接口会返回 503，却缺少可关联
            # 的服务端错误记录。确认 ID 不写入日志，避免把业务凭证标识扩散到普通日志。
            _logger.exception(
                "confirmation_execution_failed",
                request_id=gateway_context.request_id,
                trace_id=gateway_context.trace_id,
                error_type=type(exc).__name__,
            )
            raise
        response = self._response_from_state(final_state)
        await self._persist_session_summary(
            final_state,
            config=config,
            thread_id=thread_id,
            identity=identity,
        )
        return response

    async def _persist_session_summary(
        self,
        final_state: dict[str, Any],
        *,
        config: dict[str, Any],
        thread_id: str,
        identity: AgentIdentity | None,
    ) -> None:
        """生成摘要并压缩 Checkpoint；摘要故障不覆盖已经完成的业务响应。"""

        if self.session_summary_service is None or identity is None:
            return
        messages = final_state.get("messages", [])
        if not isinstance(messages, list):
            return
        try:
            summary = await self.session_summary_service.maybe_summarize(
                thread_id=thread_id,
                subject_user_id=identity.subject,
                messages=messages,
            )
            if not summary or self._checkpointer is None:
                return
            # 当前 State 的 messages 是完整列表覆盖语义，因此 aupdate_state 会真正替换
            # Checkpoint 中的历史，而不是再追加一份压缩内容。下一轮请求会重新注入最新
            # system prompt、RAG 上下文和候选上下文，历史只保留摘要与最近用户/助手消息。
            compacted = build_compacted_messages(
                system_prompt="当前系统规则将在下一轮请求中重新注入。",
                summary=summary,
                previous_messages=messages,
                keep_recent_messages=self.session_summary_service.keep_recent_messages,
            )
            await self._graph.aupdate_state(
                config,
                {"messages": compacted},
                # v2 的最后一个父图节点是领域 Agent 子图；旧图则仍以 model 作为
                # 完成节点。按图版本选择写入来源，避免压缩摘要后把新会话错误地
                # 送回仅用于兼容旧 Checkpoint 的 model 分支。
                as_node=(
                    domain_agent_spec(final_state["route"]).node_name
                    if final_state.get("graph_version") == 2
                    else "model"
                ),
            )
        except (SessionSummaryError, ModelConfigurationError, ModelResponseError):
            _logger.warning("session_summary_generation_failed", thread_id=thread_id)
        except Exception:
            # 摘要是可恢复的辅助维护动作，不能把已经完成的训练查询或确认执行变成失败。
            _logger.exception("session_summary_persist_failed", thread_id=thread_id)

    @staticmethod
    def _response_from_state(final_state: dict[str, Any]) -> SupervisorResponse:
        """把恢复后的图状态转换成稳定完成响应。"""

        answer = str(final_state.get("final_answer", "")).strip()
        if not answer:
            raise SupervisorRuntimeError("已确认的执行生成了空回答")
        return SupervisorResponse(
            answer=answer,
            route=final_state["route"],
            tool_steps=final_state.get("tool_steps", 0),
            input_tokens=_optional_int(final_state.get("input_tokens")),
            output_tokens=_optional_int(final_state.get("output_tokens")),
        )

    def _build_graph(self) -> Any:
        """构建顶层 Supervisor 图并挂载四个领域 Agent 子图。

        父图只持有一个 Checkpointer。四个子图作为节点嵌入父图，共享同一 State 和
        Runtime Context；因此确认中断可以在 PostgreSQL 中保存完整的父子 namespace，
        服务重启后仍通过父图 ``Command(resume=...)`` 恢复。

        顶层保留 model/tools/confirmation 三个旧节点，仅用于恢复 v1 已暂停 Checkpoint。
        新请求带 ``graph_version=2``，一定经过 supervisor_router 进入领域子图。
        """

        graph = StateGraph(SupervisorState, context_schema=SupervisorRuntimeContext)

        # 新版顶层路由和四个真正独立编译的 LangGraph 子图。
        graph.add_node("supervisor_router", self._supervisor_router_node)
        domain_nodes: list[str] = []
        route_map: dict[Hashable, str] = {"legacy_model": "model"}
        for spec in executable_domain_specs():
            domain_graph = build_domain_subgraph(
                spec,
                state_schema=SupervisorState,
                context_schema=SupervisorRuntimeContext,
                model_node=self._model_node,
                tool_node=self._tool_node,
                confirmation_node=self._confirmation_node,
                after_model=self._after_model,
            )
            self._domain_graphs[spec.route] = domain_graph
            graph.add_node(spec.node_name, domain_graph)
            domain_nodes.append(spec.node_name)
            route_map[spec.node_name] = spec.node_name

        graph.add_edge(START, "supervisor_router")
        graph.add_conditional_edges(
            "supervisor_router",
            self._after_supervisor_router,
            route_map,
        )
        for node_name in domain_nodes:
            graph.add_edge(node_name, END)

        # 旧拓扑兼容分支：不会承接 v2 新请求，但节点名必须保留，才能恢复升级前
        # 已经停在 confirmation interrupt 的 Checkpoint。
        graph.add_node("model", self._model_node)
        graph.add_node("tools", self._tool_node)
        graph.add_node("confirmation", self._confirmation_node)
        graph.add_conditional_edges(
            "model",
            self._after_model,
            {"tools": "tools", "confirmation": "confirmation", "finish": END},
        )
        graph.add_edge("tools", "model")
        graph.add_edge("confirmation", END)
        return graph.compile(checkpointer=self._checkpointer)

    @staticmethod
    def _supervisor_router_node(state: SupervisorState) -> dict[str, Any]:
        """验证顶层路由并记录当前领域 Agent，不执行任何业务工具。"""

        route = state.get("route")
        if route is None or route == "UNSUPPORTED_LEGACY":
            raise SupervisorRuntimeError("Supervisor 路由不可执行")
        spec = domain_agent_spec(route)
        return {"active_domain": spec.node_name}

    @staticmethod
    def _after_supervisor_router(state: SupervisorState) -> str:
        """把 v2 请求分派到领域子图；兼容无版本的历史 Checkpoint。"""

        if state.get("graph_version") != 2:
            return "legacy_model"
        route = state.get("route")
        if route is None:
            raise SupervisorRuntimeError("缺少 Supervisor 路由")
        return domain_agent_spec(route).node_name

    async def _model_node(
        self, state: SupervisorState, runtime: Runtime[SupervisorRuntimeContext]
    ) -> dict[str, Any]:
        spec = domain_agent_spec(state["route"])
        # 中断恢复会从该节点重新执行。只要 State 已有待确认 ID，就不能再次调用 LLM，
        # 否则模型可能重新生成一套与原确认单不同的参数。
        if state.get("pending_confirmation_id"):
            return {"model_tool_calls": []}
        tool_schemas = (
            _model_tools(self.tools, state["route"])
            if state.get("tool_steps", 0) < self.max_tool_steps
            else []
        )
        force_tool_name = _forced_write_tool_name(
            state["route"],
            (
                runtime.context.user_message
                if runtime.context is not None and runtime.context.user_message
                else ""
            ),
        )
        model_kwargs: dict[str, Any] = {"tools": tool_schemas}
        if force_tool_name and tool_schemas:
            model_kwargs["force_tool_name"] = force_tool_name
        # 生成结构化训练计划后，下一回合需要把完整的多日动作明细作为创建草案
        # 工具参数传回模型。这里复用训练计划专用预算，避免第二次 Tool Calling
        # 仍使用普通对话的 1200 tokens 导致参数 JSON 截断；FakeModels 等测试桩
        # 没有 settings 时不传该可选参数，保持基础 Runtime 测试兼容。
        training_plan_budget = getattr(
            getattr(self.models, "settings", None), "training_plan_max_output_tokens", None
        )
        if spec.supports_generated_training_draft and training_plan_budget:
            model_kwargs["max_output_tokens"] = training_plan_budget
        turn = await self.models.chat_with_tools(state["messages"], **model_kwargs)
        tool_calls = list(turn.tool_calls)
        for call in tool_calls:
            definition = self.tools.get(call.name)
            if definition.tool_id not in spec.allowed_tool_ids:
                raise SupervisorRuntimeError("领域 Agent 请求了白名单之外的工具")
        if any(not self.tools.get(call.name).read_only for call in tool_calls):
            if len(tool_calls) != 1:
                raise SupervisorRuntimeError("每次写工具调用都必须单独确认")
            if runtime.context is None or runtime.context.identity is None:
                raise SupervisorRuntimeError("写操作确认需要已签名身份")
            if self.confirmation_service is None:
                raise SupervisorRuntimeError("确认服务未配置")
            call = tool_calls[0]
            # 模型返回的是 model_name，确认单必须保存内部稳定 tool_id，避免供应商
            # 别名进入凭证、审计和后续恢复流程。
            definition = self.tools.get(call.name)
            confirmation = await self._prepare_write_confirmation(
                definition.tool_id,
                call.arguments,
                runtime,
                route=state["route"],
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
            raise SupervisorRuntimeError("确认运行时上下文不完整")
        if self.confirmation_service is None:
            raise SupervisorRuntimeError("确认服务未配置")
        record = await self.confirmation_service.get_for_subject(
            confirmation_id, runtime.context.identity
        )
        if record.tool_id not in domain_agent_spec(state["route"]).allowed_tool_ids:
            raise ConfirmationStateError("确认工具不属于当前领域")
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
                raise SupervisorRuntimeError("确认恢复范围不匹配")
            record = await self.confirmation_service.get_for_subject(
                confirmation_id, runtime.context.identity
            )
        if record.authorization_status != "APPROVED":
            raise ConfirmationStateError("确认单尚未获批准，不能执行")

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
            raise SupervisorRuntimeError("已确认的训练操作失败") from exc
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
            raise SupervisorRuntimeError("缺少 Supervisor 运行时上下文")
        context = ToolContext(
            gateway_context=runtime.context.gateway_context,
            identity=runtime.context.identity,
            user_message=runtime.context.user_message,
        )
        tool_messages: list[dict[str, Any]] = []
        calls = state.get("model_tool_calls", [])
        spec = domain_agent_spec(state["route"])
        for call in calls:
            definition = self.tools.get(call.name)
            if definition.tool_id not in spec.allowed_tool_ids:
                raise SupervisorRuntimeError("工具执行不属于当前领域")
            raw_input = call.arguments
            if (
                spec.deterministic_operations_input
                and definition.tool_id == "fitness.operations.metric.query.v1"
            ):
                if runtime.context.identity is None:
                    raise SupervisorRuntimeError("经营查询需要已签名身份")
                if not runtime.context.user_message:
                    raise SupervisorRuntimeError("经营查询需要原始用户消息")
                try:
                    # 经营查询的组织、指标和日期来自已验证身份及用户原问题。模型只
                    # 选择工具，不能通过参数猜测机构 ID、扩大时间范围或切换指标。
                    raw_input = build_authorized_operations_tool_input(
                        runtime.context.user_message,
                        allowed_organization_ids=(runtime.context.identity.organization_ids),
                    )
                except OperationsQueryPreparationError as exc:
                    raise SupervisorRuntimeError("无法安全准备经营查询") from exc
                _logger.info(
                    "operations_tool_input_prepared",
                    request_id=runtime.context.gateway_context.request_id,
                    trace_id=runtime.context.gateway_context.trace_id,
                )
            bound_input = self.tools.bind_context_input(
                definition.tool_id,
                raw_input,
                runtime.context.identity,
            )
            result = await self.tools.invoke(definition.tool_id, bound_input, context)
            model_result = _safe_model_tool_result(definition.tool_id, result)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "content": json.dumps(model_result, ensure_ascii=False),
                }
            )
            if (
                spec.supports_generated_training_draft
                and definition.tool_id == "fitness.training.plan.generate_draft.v1"
                and _is_explicit_training_plan_creation_request(runtime.context.user_message)
            ):
                # 生成服务已经完成 RAG、结构化 Schema 和语义校验。用户明确要求“创建”时，
                # 这里直接把同一份已校验 Payload 交给确认服务，不再把下一步是否调用写工具
                # 交给模型决定。Payload 只在当前节点内存和加密确认单中存在，不进入 State。
                generated_payload = _generated_draft_payload(result)
                confirmation = await self._prepare_write_confirmation(
                    "fitness.training.plan.create_draft.v1",
                    generated_payload,
                    runtime,
                    route=state["route"],
                )
                return {
                    "messages": [*state["messages"], *tool_messages],
                    "tool_steps": state.get("tool_steps", 0) + 1,
                    "model_tool_calls": [],
                    "pending_confirmation_id": confirmation.id,
                    "final_answer": "",
                }
        return {
            "messages": [*state["messages"], *tool_messages],
            "tool_steps": state.get("tool_steps", 0) + 1,
        }

    async def _prepare_write_confirmation(
        self,
        tool_id: str,
        raw_input: dict[str, Any],
        runtime: Runtime[SupervisorRuntimeContext],
        *,
        route: SupervisorRoute,
    ) -> Any:
        """统一创建写操作确认单，确保模型直写和生成后直写使用同一安全边界。"""

        if runtime.context is None or runtime.context.identity is None:
            raise SupervisorRuntimeError("写操作确认需要已签名身份")
        if self.confirmation_service is None:
            raise SupervisorRuntimeError("确认服务未配置")
        definition = self.tools.get(tool_id)
        if definition.tool_id not in domain_agent_spec(route).allowed_tool_ids:
            raise SupervisorRuntimeError("写操作确认不属于当前领域")
        bound_input = self.tools.bind_context_input(
            definition.tool_id,
            raw_input,
            runtime.context.identity,
        )
        return await self.confirmation_service.prepare(
            tool_id=definition.tool_id,
            raw_input=bound_input,
            gateway_context=runtime.context.gateway_context,
            identity=runtime.context.identity,
            thread_id=runtime.context.thread_id or "unknown-thread",
        )

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
    if _customer_service_restricted_answer(text) is not None:
        return "CUSTOMER_SERVICE"
    # 明确的客服工单写入意图必须优先于“预约状态”等经营指标关键词。
    # 例如“提交客服工单，反馈预约状态异常”虽然包含“预约状态”，但它不是经营报表查询，
    # 而是要求客服受理问题。若先命中 OPERATIONS，模型会拿到错误的指标工具，
    # 既无法创建工单，也会让真实受控验收暴露出路由歧义。
    if _looks_like_customer_service_write_request(text):
        return "CUSTOMER_SERVICE"
    if any(
        keyword in text
        for keyword in (
            "营收",
            "营收金额",
            "收入",
            "营业收入",
            "经营",
            "报表",
            "sql",
            "预约量",
            "预约数",
            "预约总量",
            "预约状态",
            "完课量",
            "完课数",
            "完成课程量",
            "已完成课程",
            "新客量",
            "新客数",
            "新增客户",
            "新增用户",
            "课程利用",
            "课程预约量",
            "教练表现",
            "教练预约量",
            "教练工作量",
            "剩余课时",
            "课时余额",
        )
    ):
        return "OPERATIONS"
    # 只有创建/改约/取消和“可约时间”属于预约操作域；“我的预约是什么”
    # 是客服只读查询，不能因为包含“预约”两个字就暴露预约写工具。
    if any(
        keyword in text
        for keyword in (
            "创建预约",
            "帮我预约",
            "预约一次",
            "预订课程",
            "预定课程",
            "课程预约",
            "安排课程预约",
            "改约",
            "修改预约",
            "调整预约",
            "取消预约",
            "可约时间",
            "空闲时段",
        )
    ):
        return "BOOKING"
    if any(
        keyword in text
        for keyword in (
            "我的预约",
            "查看预约",
            "预约状态",
            "我的课程",
            "课程规则",
            "预约规则",
            "合同",
            "课时",
            "训练计划状态",
            "计划状态",
            "训练执行记录",
            "客服",
            "工单",
        )
    ):
        return "CUSTOMER_SERVICE"
    return "FITNESS_COACHING"


def _system_prompt(route: SupervisorRoute, locale: str) -> str:
    spec = domain_agent_spec(route)
    booking_instruction = (
        "预约规则：用户明确要求创建预约、改约或取消预约时，必须调用对应的预约工具，"
        "不能只用自然语言回复或声称已完成。创建、改约和取消工具会自动生成确认单并暂停，"
        "不要等待用户再次说‘确认’才调用工具；只有用户批准确认单后才会真正写入。"
        if route == "BOOKING"
        else ""
    )
    customer_service_instruction = (
        "客服规则：健身规则问题必须优先参考已提供的知识引用；动态的预约、课程、合同、"
        "课时、训练计划和客服工单状态只能通过只读工具查询，不能猜测；只有用户明确要求提交问题时，"
        "才可以调用创建工单工具，并必须等待 interrupt 确认，不能修改预约、训练计划、Memory 或已有工单。"
        "如果客服服务未部署或写入执行失败，必须明确说明尚未创建工单。涉及医疗、受伤、退款、"
        "赔付或合同争议时，只能说明当前不支持自动处理；不提供诊断、用药或治疗建议，"
        "也不得承诺结果或执行写操作。"
        if route == "CUSTOMER_SERVICE"
        else ""
    )
    return (
        f"你是由健身平台 Supervisor 调度的 {spec.display_name}。"
        "只处理当前领域内的健身训练、课程、合同、课时、预约、经营或客服业务。"
        "动态业务事实必须通过已注册工具查询，不能猜测；工具失败时必须明确告知失败，不能宣称成功。"
        "不得根据自然语言中的 user_id、organization_id 或角色改变权限。"
        "Memory 候选只是模型建议，不是已保存事实；用户未批准前不得声称已经记住。"
        f"{booking_instruction}{customer_service_instruction}当前路由={route}，语言={locale}。"
        "回答应简洁、准确，并区分已查询事实与一般建议。"
    )


def _looks_like_customer_service_knowledge_question(user_message: str) -> bool:
    """判断客服请求是否需要检索规则文档，而不是只查询动态业务事实。"""

    text = "".join(user_message.lower().split())
    return any(
        marker in text
        for marker in (
            "怎么",
            "如何",
            "规则",
            "规定",
            "注意事项",
            "什么意思",
            "可以吗",
            "能不能",
            "课程请假",
            "预约取消",
            "课时规则",
        )
    )


def _customer_service_restricted_answer(user_message: str) -> str | None:
    """对高风险客服问题执行确定性边界，避免依赖模型自觉拒答。"""

    text = "".join(user_message.lower().split())
    if any(
        marker in text
        for marker in (
            "疼痛",
            "受伤",
            "骨折",
            "脱臼",
            "伤口",
            "疾病",
            "诊断",
            "药物",
            "吃药",
            "处方",
            "治疗",
            "医疗",
            "康复",
        )
    ):
        return "这个问题涉及医疗、受伤或治疗判断，当前健身客服 Agent 不提供诊断、用药或治疗建议。请停止可能加重情况的训练，并咨询合格的医疗专业人员。"
    if any(
        marker in text
        for marker in (
            "退款",
            "退费",
            "赔偿",
            "赔付",
            "合同纠纷",
            "合同争议",
        )
    ):
        return "这个问题涉及退款、赔付或合同争议，当前健身客服 Agent 不会承诺处理结果，也不会自动修改合同或执行退款。"
    return None


def _looks_like_customer_service_write_request(user_message: str) -> bool:
    """识别高置信度的客服工单提交意图，避免被业务查询关键词覆盖。"""

    text = user_message.replace(" ", "")
    return _has_affirmative_intent(
        text,
        (
            "提交工单",
            "创建工单",
            "提交客服工单",
            "提交客服问题",
            "帮我提交",
            "帮我反馈问题",
            "联系健身客服",
        ),
    )


def _forced_write_tool_name(route: SupervisorRoute, user_message: str) -> str | None:
    """为明确的预约或客服工单写意图选择强制工具。

    <p>模型仍负责从自然语言提取课程、合同、教练和时间，但不能因为先调用了一个
    只读工具就直接结束。这里只处理高置信度的“创建/改约/取消预约”和“提交客服工单”命令；
    普通的课程查询、可约时间查询和健身问答仍由模型自主选择工具。返回的是供应商侧合法名称，
    内部真实 tool_id 仍由 ToolRegistry 解析和审计。</p>
    """

    if route == "CUSTOMER_SERVICE":
        if _looks_like_customer_service_write_request(user_message):
            return "fitness_support_ticket_create_v1"
        return None
    if route != "BOOKING":
        return None
    text = user_message.replace(" ", "")
    if _has_affirmative_intent(text, ("改约", "修改预约", "调整预约")):
        return "fitness_booking_reschedule_v1"
    if _has_affirmative_intent(text, ("取消预约", "取消我的预约", "撤销预约")):
        return "fitness_booking_cancel_v1"
    if _has_affirmative_intent(
        text,
        ("创建预约", "创建一次预约", "预约一次", "帮我预约", "预订课程", "预定课程"),
    ):
        return "fitness_booking_create_v1"
    return None


def _is_explicit_training_plan_creation_request(user_message: str | None) -> bool:
    """判断用户是否明确要求创建训练计划，而不是只请求建议或预览。"""

    if not user_message:
        return False
    text = "".join(user_message.split())
    creation_verbs = ("创建", "制定", "生成", "安排", "设计", "制作")
    return "训练计划" in text and _has_affirmative_intent(text, creation_verbs)


def _has_affirmative_intent(text: str, keywords: tuple[str, ...]) -> bool:
    """判断动作关键词是否处于肯定命令中，而不是“不要/无需”等否定句中。

    写工具的确定性强制选择只能响应明确肯定意图。这里按中文主句标点切分，只检查
    关键词所在分句中、关键词之前是否出现否定词。例如“不要改约或取消预约”不会
    触发写工具；“不要查询，帮我取消预约”第二个分句仍会被识别为肯定取消请求。

    该函数只是减少误触发确认单，不降低最终安全边界：即使未强制工具，模型主动
    提出写调用仍然必须经过参数校验、确认单、interrupt 和 Java Gateway 授权。
    """

    negations = ("不要", "别", "无需", "不用", "不需要", "禁止", "不想", "不是要", "并非要")
    clause_boundaries = "，。；！？\n"
    for keyword in keywords:
        start = 0
        while (index := text.find(keyword, start)) >= 0:
            boundary = max(text.rfind(marker, 0, index) for marker in clause_boundaries)
            clause_prefix = text[boundary + 1 : index]
            if not any(negation in clause_prefix for negation in negations):
                return True
            start = index + len(keyword)
    return False


def _generated_draft_payload(result: Any) -> dict[str, Any]:
    """读取已通过生成服务校验的创建 Payload，拒绝不完整的内部工具结果。"""

    if not isinstance(result, dict) or result.get("status") != "DRAFT_PREVIEW":
        raise SupervisorRuntimeError("训练草案生成没有返回预览")
    payload = result.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise SupervisorRuntimeError("缺少训练草案预览 Payload")
    return {str(key): value for key, value in payload.items()}


def _safe_model_tool_result(tool_id: str, result: Any) -> Any:
    """移除训练草案精确 Payload，防止模型工具消息把写参数落入 Checkpoint。"""

    if tool_id != "fitness.training.plan.generate_draft.v1" or not isinstance(result, dict):
        return result
    return {key: value for key, value in result.items() if key != "payload"}


def _model_tools(registry: ToolRegistry, route: SupervisorRoute) -> list[dict[str, Any]]:
    allowed_tool_ids = domain_agent_spec(route).allowed_tool_ids
    return [
        {
            "type": "function",
            "function": {
                # 内部保留带命名空间和版本号的 tool_id；模型侧使用不含点号的
                # 别名，满足 DeepSeek/OpenAI-compatible 函数名约束。
                "name": spec["model_name"],
                "description": spec["description"],
                # 经营查询参数全部由服务端根据用户原问题和签名身份生成。模型只
                # 决定是否调用固定指标工具，不接触内部机构 ID、日期或指标参数。
                "parameters": _compact_model_schema(_model_tool_parameters(spec, route)),
            },
        }
        for spec in registry.public_specs()
        if spec["name"] in allowed_tool_ids
    ]


def _compact_model_schema(value: Any) -> Any:
    """移除仅供文档展示、不会改变 Tool Calling 约束的 JSON Schema 元数据。

    Pydantic 仍使用注册表中的完整 Schema 校验真实工具参数；这里只压缩每次发送给
    模型的副本。``title``、``default`` 和 ``examples`` 不参与 required、类型、枚举、
    数值范围或 additionalProperties 等约束，却会在每一次模型回合重复消耗 Token。
    """

    if isinstance(value, dict):
        return {
            key: _compact_model_schema(item)
            for key, item in value.items()
            if key not in {"title", "default", "examples"}
        }
    if isinstance(value, list):
        return [_compact_model_schema(item) for item in value]
    return value


def _model_tool_parameters(spec: dict[str, Any], route: SupervisorRoute) -> dict[str, Any]:
    """生成模型可见 Schema，移除只能由签名上下文决定的机构字段。

    Tool Registry 会在参数校验和调用 Gateway 前，把唯一机构 ID 绑定为已验签
    ``AgentIdentity.organization_ids``。如果仍把 ``organization_id`` 作为模型必填字段，
    模型会先猜机构或反复查询机构，既增加 Token 和工具步数，也违背“模型不决定租户”的
    权限设计。这里仅改变模型可见 Schema，Registry 内部 Pydantic Schema 和 Java Gateway
    契约保持不变；多机构上下文仍由绑定层 fail-closed，不能由模型自行选择。
    """

    if route == "OPERATIONS" and spec["name"] == "fitness.operations.metric.query.v1":
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    parameters = dict(spec["input_schema"])
    properties = dict(parameters.get("properties", {}))
    properties.pop("organization_id", None)
    properties.pop("organizationId", None)
    parameters["properties"] = properties
    required = parameters.get("required")
    if isinstance(required, list):
        parameters["required"] = [
            field for field in required if field not in {"organization_id", "organizationId"}
        ]
    return parameters


def _optional_int(value: int | None) -> int | None:
    return value if value else None


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
