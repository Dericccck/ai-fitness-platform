import asyncio
import json
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from time import perf_counter
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import structlog
from openai import APIConnectionError, AsyncOpenAI, OpenAIError

from app.core.config import Settings
from app.core.metrics import HttpMetrics
from app.evaluation.telemetry import TruLensTelemetry
from app.infrastructure.model_protection import CircuitBreaker, ModelCapacityError, ModelProtection
from app.infrastructure.model_usage import (
    ModelUsage,
    estimate_cost,
    extract_usage,
    record_usage,
    usage_context,
)
from app.streaming.events import AnswerFilter, emit_event, streaming_enabled

_logger = structlog.get_logger("agent.model_gateway")


class ModelConfigurationError(RuntimeError):
    """真实模型服务未完成配置时抛出，防止生产流程静默使用假结果。"""


class ModelResponseError(RuntimeError):
    """模型返回无法被 Agent Runtime 解释的结果时抛出。"""


@dataclass(frozen=True)
class ModelToolCall:
    """模型请求调用一个已注册工具的结构化意图。"""

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    """一次模型回合的文本、工具调用和可选用量信息。"""

    content: str
    tool_calls: tuple[ModelToolCall, ...]
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class JsonModelTurn:
    """结构化 JSON 模型回合及供应商返回的 Token 用量。"""

    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


def _provider_status_code(error: OpenAIError) -> int | None:
    """提取供应商 HTTP 状态码；异常对象不一定携带该字段。"""

    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) and 100 <= value <= 599 else None


class ModelGateway:
    """DeepSeek LLM 与 Embedding 的统一模型网关。

    业务 Agent 只能依赖该网关，不能自行创建供应商 SDK 客户端。这样可以在同一位置
    控制模型切换、超时、重试、Tracing、Token 成本和日志脱敏，并防止生产
    环境意外回退到本地 Mock。当前使用 OpenAI-compatible 协议，后续可以在不修改业务
    Agent 的情况下增加其他供应商适配器。DeepSeek 使用 OpenAI-compatible API，配置名
    与 learning-langchain-CN 项目保持一致；默认关闭 thinking，保证 Tool Calling 和
    结构化输出的响应格式稳定。
    """

    def __init__(
        self,
        settings: Settings,
        telemetry: TruLensTelemetry | None = None,
        metrics: HttpMetrics | None = None,
        usage_sink: Callable[[ModelUsage], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self.telemetry = telemetry or TruLensTelemetry.disabled()
        self.metrics = metrics
        self.usage_sink = usage_sink
        # 覆盖账本、所有尝试和退避，早于共享并发租约到期，避免数据库阻塞时租约先失效。
        self.logical_timeout_seconds = settings.llm_timeout_seconds * settings.llm_max_attempts + 10
        self.protection = ModelProtection(
            settings.llm_max_concurrency,
            settings.llm_queue_timeout_seconds,
            CircuitBreaker(
                settings.llm_circuit_failure_threshold, settings.llm_circuit_cooldown_seconds
            ),
        )

        # AsyncOpenAI 构造时要求非空密钥，因此未配置环境使用不可用占位值完成
        # 对象装配。chat/embed 会在任何网络请求发生前检查 configured 并明确抛错，
        # 占位值不会被发送给外部服务，也不会形成“看似成功”的降级结果。
        self._llm = AsyncOpenAI(
            api_key=settings.llm_api_key or "not-configured",
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )
        self._embedding = AsyncOpenAI(
            api_key=settings.embedding_effective_api_key or "not-configured",
            base_url=settings.embedding_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )
        self._local_embedding: Any = None

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        """调用配置的对话模型并返回首个文本结果。

        ``messages`` 由上层 Agent Runtime 根据版本化 Prompt 和受信任上下文构造。
        本方法只负责供应商调用，不负责授权，也不能根据模型文本认定业务操作成功。
        后续的超时、重试、Tracing 和用量统计也应集中加在该边界。
        """

        if not self.settings.llm_configured:
            raise ModelConfigurationError("LLM 服务未配置")

        response = await self._create_completion(
            {
                "model": self.settings.llm_model,
                "messages": messages,
                "temperature": temperature,
                "extra_body": _deepseek_extra_body(self.settings.llm_thinking_enabled),
            },
            kind="chat",
        )
        return response.choices[0].message.content or ""

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str:
        """调用对话模型并要求返回一个 JSON 对象。

        结构化训练计划不能依赖“让模型尽量输出 JSON”的自然语言约定。这里使用
        OpenAI-compatible 接口的 ``response_format`` 让 DeepSeek 在供应商边界执行
        JSON Object 约束；业务层仍必须再用 Pydantic 校验字段、数量和业务规则。
        ``response_format`` 不是权限控制，也不能证明内容正确，只是减少解析失败的
        概率。模型未配置、返回空结果或供应商不符合契约时统一抛错，禁止伪造草案。
        """

        return (
            await self.chat_json_with_usage(
                messages,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
        ).content

    async def chat_json_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> JsonModelTurn:
        """结构化生成并保留 Token 用量，供摘要等后台能力做成本监控。"""

        if not self.settings.llm_configured:
            raise ModelConfigurationError("LLM 服务未配置")
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens or self.settings.llm_max_output_tokens,
            "response_format": {"type": "json_object"},
            "extra_body": _deepseek_extra_body(self.settings.llm_thinking_enabled),
        }
        response = await self._create_completion(request, kind="json")
        if not response.choices:
            raise ModelResponseError("LLM 未返回任何候选结果")
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise ModelResponseError("LLM 返回了空 JSON 响应")
        usage = getattr(response, "usage", None)
        return JsonModelTurn(
            content=content,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        force_tool_name: str | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> ModelTurn:
        """调用支持 Tool Calling 的模型，并规范化为 Runtime 自有协议。

        OpenAI-compatible 供应商的响应对象不能直接泄漏到业务编排层，否则更换模型
        供应商会导致整个 Agent 图改动。这里严格解析 tool name、call id 和 JSON 参数；
        任一工具参数不是合法 JSON 都会失败，Runtime 不会猜测或修复模型意图。
        """

        if not self.settings.llm_configured:
            raise ModelConfigurationError("LLM 服务未配置")
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens or self.settings.llm_max_output_tokens,
            "extra_body": _deepseek_extra_body(self.settings.llm_thinking_enabled),
        }
        # 工具预算耗尽后仍需要让模型基于最后一次真实工具结果生成最终答复，
        # 此时显式不传 tools，避免模型继续发起新的业务调用。
        if tools:
            request["tools"] = tools
            # 对明确的业务写意图，Supervisor 可以要求模型必须从当前路由白名单中
            # 选择指定工具。这样模型不能先查询一次资料后自行结束，写操作仍然会在
            # Supervisor 的确认节点暂停；该参数只控制“选哪个工具”，不绕过权限、
            # 参数绑定或确认机制。
            request["tool_choice"] = (
                {
                    "type": "function",
                    "function": {"name": force_tool_name},
                }
                if force_tool_name
                else "auto"
            )
        response = await self._create_completion(request, kind="tool_calling")
        if not response.choices:
            raise ModelResponseError("LLM 未返回任何候选结果")

        message = response.choices[0].message
        parsed_calls: list[ModelToolCall] = []
        for raw_call in message.tool_calls or []:
            try:
                arguments = json.loads(raw_call.function.arguments)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ModelResponseError("LLM 返回了无效的工具参数") from exc
            if not isinstance(arguments, dict):
                raise ModelResponseError("LLM 工具参数必须是 JSON 对象")
            parsed_calls.append(
                ModelToolCall(
                    call_id=raw_call.id,
                    name=raw_call.function.name,
                    arguments=arguments,
                )
            )

        usage = response.usage
        return ModelTurn(
            content=message.content or "",
            tool_calls=tuple(parsed_calls),
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成向量，并保持结果顺序与输入文本一致。

        文档权限和组织元数据应在入库前由 RAG 流程写入结构化字段，不能依靠向量
        表达权限。调用方还需要限制批次大小，防止单次请求超出供应商限制。
        """

        if not self.settings.embedding_configured:
            raise ModelConfigurationError("Embedding 服务未配置")

        if self.settings.embedding_backend == "local":
            return await asyncio.to_thread(self._embed_local, texts)

        response = await self._embedding.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    async def warmup_local_embedding(self) -> None:
        """在服务接收流量前加载本地 Embedding 并完成一次最小推理。

        远程 Embedding 不做预热，避免启动时产生外部请求和计费。这个方法
        不改变模型或向量算法，只是将首次加载成本从用户请求移动到启动阶段。
        """

        if self.settings.embedding_backend != "local" or not self.settings.embedding_configured:
            return
        await asyncio.to_thread(self._embed_local, ["健身检索模型预热"])

    async def _create_completion(self, request: dict[str, Any], *, kind: str) -> Any:
        """计量每一次实际尝试；统一截止时间、有限重试、共享并发与实例熔断。"""

        request = _stable_request(request)
        request = _bounded_request(request, max_chars=self.settings.agent_context_max_chars)
        if "max_tokens" not in request:
            request["max_tokens"] = self.settings.llm_max_output_tokens
        with self.telemetry.span(
            "fitness.agent.generation",
            attributes={
                "fitness.agent.generation_kind": kind,
                "fitness.agent.model": self.settings.llm_model,
                "fitness.agent.input_message_count": len(request.get("messages", [])),
            },
        ) as generation_span:
            try:
                async with self.protection.capacity():
                    async with asyncio.timeout(self.logical_timeout_seconds):
                        response = await self._attempt_completion(request, kind=kind)
            except ModelCapacityError as exc:
                if self.metrics:
                    self.metrics.record_model_request(kind, "REJECTED")
                raise ModelResponseError("LLM 服务繁忙，请稍后重试") from exc
            except TimeoutError as exc:
                raise ModelResponseError("LLM 调用已超过总时间预算") from exc
            if self.metrics is not None:
                self.metrics.record_model_request(kind, "SUCCEEDED")
            usage = getattr(response, "usage", None)
            self.telemetry.set_attributes(
                generation_span,
                {
                    "fitness.agent.generation_status": "succeeded",
                    "fitness.agent.input_tokens": getattr(usage, "prompt_tokens", None),
                    "fitness.agent.output_tokens": getattr(usage, "completion_tokens", None),
                },
            )
            if response.choices:
                self.telemetry.set_text(
                    generation_span,
                    "fitness.agent.generation.output",
                    response.choices[0].message.content,
                )
            return response

    async def _persist_usage(self, usage: ModelUsage) -> None:
        if self.usage_sink is None:
            return
        try:
            await self.usage_sink(usage)
        except Exception as exc:
            if self.metrics:
                self.metrics.usage_ledger_failures_total.inc()
            _logger.error("model_usage_persistence_failed", error_type=type(exc).__name__)
            # STARTED 已提交但终态更新失败时仍能被识别为未知；不谎报完整计费。
            raise ModelResponseError("模型用量记录暂时不可用") from exc

    async def _attempt_completion(self, request: dict[str, Any], *, kind: str) -> Any:
        request_id, run_id = usage_context()
        provider = urlparse(self.settings.llm_base_url).hostname or "configured-provider"
        for attempt in range(1, self.settings.llm_max_attempts + 1):
            probe = self.protection.breaker.enter()
            usage = ModelUsage(
                call_id=uuid4().hex,
                request_id=request_id,
                run_id=run_id,
                model=self.settings.llm_model,
                provider=provider,
                kind=kind,
                attempt=attempt,
                status="STARTED",
                prompt_version=self.settings.prompt_version,
            )
            started = perf_counter()
            ttft: float | None = None
            raw_usage: Any = None
            try:
                await self._persist_usage(usage)
                async with asyncio.timeout(self.settings.llm_timeout_seconds):
                    if streaming_enabled() and kind == "tool_calling":
                        response, ttft = await self._stream_completion(request, started=started)
                    else:
                        response = await self._llm.chat.completions.create(**request)
                raw_usage = getattr(response, "usage", None)
            except (OpenAIError, TimeoutError) as exc:
                status_code = _provider_status_code(exc) if isinstance(exc, OpenAIError) else None
                transient = isinstance(exc, (APIConnectionError, TimeoutError)) or (
                    status_code is not None and (status_code == 429 or status_code >= 500)
                )
                if transient:
                    self.protection.breaker.failure(probe=probe)
                else:
                    self.protection.breaker.abandon(probe=probe)
                failed = replace(usage, status="FAILED", duration_seconds=perf_counter() - started)
                record_usage(failed)
                await self._persist_usage(failed)
                if self.metrics:
                    self.metrics.record_model_request(kind, "FAILED")
                    self.metrics.observe_model_usage(failed)
                _logger.warning(
                    "model_provider_request_failed",
                    generation_kind=kind,
                    provider_error_type=type(exc).__name__,
                    provider_status_code=status_code,
                )
                # 流式已可能暴露部分输出，禁止自动重试拼接两次生成；半开失败也不重试。
                if (
                    not transient
                    or probe
                    or streaming_enabled()
                    or attempt == self.settings.llm_max_attempts
                ):
                    raise ModelResponseError("LLM 服务请求失败") from exc
                retry_after = _retry_after(exc)
                if retry_after is not None and retry_after > 5:
                    raise ModelResponseError("LLM 服务请求受限，请稍后重试") from exc
                await asyncio.sleep(
                    retry_after if retry_after is not None else random.uniform(0.1, 0.5) * attempt
                )
                continue
            except BaseException:
                self.protection.breaker.abandon(probe=probe)
                failed = replace(usage, status="UNKNOWN", duration_seconds=perf_counter() - started)
                record_usage(failed)
                # 取消不能吞掉；有限时间内保存未知用量，随后继续向上传播取消。
                try:
                    async with asyncio.timeout(2):
                        await asyncio.shield(self._persist_usage(failed))
                except Exception as persistence_error:  # noqa: BLE001 - 不能覆盖原始取消
                    _logger.warning(
                        "cancelled_model_usage_unknown", error_type=type(persistence_error).__name__
                    )
                raise
            self.protection.breaker.success()
            counts = extract_usage(raw_usage)
            cost = estimate_cost(self.settings, counts)
            succeeded = replace(
                usage,
                status="SUCCEEDED",
                input_tokens=counts["input_tokens"],
                output_tokens=counts["output_tokens"],
                cache_hit_tokens=counts["cache_hit_tokens"],
                cache_miss_tokens=counts["cache_miss_tokens"],
                duration_seconds=perf_counter() - started,
                ttft_seconds=ttft,
                estimated_cost=cost,
                currency=self.settings.llm_pricing_currency if cost is not None else None,
                pricing_version=self.settings.llm_pricing_version if cost is not None else None,
            )
            record_usage(succeeded)
            await self._persist_usage(succeeded)
            if self.metrics:
                self.metrics.observe_model_usage(succeeded)
            return response
        raise ModelResponseError("LLM 服务请求失败")

    async def _stream_completion(
        self, request: dict[str, Any], *, started: float
    ) -> tuple[Any, float | None]:
        """聚合完整工具参数；只有无工具回合允许增量文本对外展示。"""

        stream = await self._llm.chat.completions.create(
            **request, stream=True, stream_options={"include_usage": True}
        )
        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: Any = None
        ttft: float | None = None
        filter_ = AnswerFilter()
        visible = not request.get("tools")
        finish_reason: str | None = None
        try:
            async for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                for choice in chunk.choices:
                    if choice.index != 0:
                        continue
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    delta = choice.delta
                    if delta.content:
                        if ttft is None:
                            ttft = perf_counter() - started
                        content.append(delta.content)
                        if visible:
                            safe = filter_.push(delta.content)
                            if safe:
                                emit_event("answer.delta", {"text": safe})
                    for call in delta.tool_calls or []:
                        stored = calls.setdefault(
                            call.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if call.id:
                            stored["id"] += call.id
                        if call.function:
                            stored["name"] += call.function.name or ""
                            stored["arguments"] += call.function.arguments or ""
        finally:
            await stream.close()
        if finish_reason not in {"stop", "tool_calls"}:
            raise ModelResponseError("LLM 流式响应未完整结束")
        if visible:
            if calls:
                raise ModelResponseError("无工具回合出现工具调用")
            tail = filter_.push("", final=True)
            if tail:
                emit_event("answer.delta", {"text": tail})
        message = SimpleNamespace(
            content="".join(content),
            tool_calls=[
                SimpleNamespace(
                    id=item["id"],
                    function=SimpleNamespace(name=item["name"], arguments=item["arguments"]),
                )
                for _, item in sorted(calls.items())
            ],
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage), ttft

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """在线程池中执行本地 BGE-M3，避免阻塞 FastAPI 事件循环。"""

        if self._local_embedding is None:
            from sentence_transformers import SentenceTransformer

            self._local_embedding = SentenceTransformer(
                self.settings.embedding_model_path,
                device="cpu",
            )
        vectors = self._local_embedding.encode(
            texts,
            batch_size=self.settings.rag_embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]

    async def close(self) -> None:
        """关闭底层 HTTP 连接池，在 FastAPI lifespan 结束时调用。"""

        await self._llm.close()
        await self._embedding.close()


def redact_provider_config(settings: Settings) -> dict[str, Any]:
    """返回可公开诊断的模型状态，绝不暴露密钥或完整服务配置。"""

    return {
        "llm": {"configured": settings.llm_configured, "model": settings.llm_model},
        "embedding": {
            "configured": settings.embedding_configured,
            "backend": settings.embedding_backend,
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
        },
        "reranker": {
            "configured": settings.reranker_configured,
            "backend": settings.reranker_backend,
            "model": settings.reranker_model,
        },
    }


def _deepseek_extra_body(thinking_enabled: bool) -> dict[str, Any]:
    """生成与 learning-langchain-CN 一致的 DeepSeek thinking 配置。"""

    return {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}


def _retry_after(error: BaseException) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    try:
        raw = headers.get("retry-after")
        value = float(raw) if raw is not None else None
        if value is None or not math.isfinite(value):
            return None
        return max(0.0, value)
    except (TypeError, ValueError):
        return None


def _stable_request(request: dict[str, Any]) -> dict[str, Any]:
    """固定 Schema 序列化与工具顺序，不改变 RAG 相关性排序或业务消息顺序。"""

    result = dict(request)
    tools = result.get("tools")
    if tools:
        result["tools"] = sorted(
            json.loads(json.dumps(tools, ensure_ascii=False, sort_keys=True)),
            key=lambda item: item.get("function", {}).get("name", ""),
        )
    messages = result.get("messages", [])
    merged: list[dict[str, Any]] = []
    for message in messages:
        current = dict(message)
        if (
            merged
            and current.get("role") == merged[-1].get("role") == "system"
            and isinstance(current.get("content"), str)
            and isinstance(merged[-1].get("content"), str)
            and set(current) == set(merged[-1]) == {"role", "content"}
        ):
            merged[-1]["content"] += "\n\n" + current["content"]
        else:
            merged.append(current)
    result["messages"] = merged
    return result


def _bounded_request(request: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    """只淘汰完整的旧用户回合，保留工具调用与结果配对、全部系统规则和当前回合。"""
    result = dict(request)
    messages = list(result.get("messages", []))
    while len(json.dumps({**result, "messages": messages}, ensure_ascii=False)) > max_chars:
        users = [i for i, message in enumerate(messages) if message.get("role") == "user"]
        if len(users) < 2:
            raise ModelResponseError("当前模型上下文超过安全预算，请缩小请求范围")
        start, end = users[:2]
        messages = (
            messages[:start]
            + [m for m in messages[start:end] if m.get("role") == "system"]
            + messages[end:]
        )
    result["messages"] = messages
    return result
