import asyncio
import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from app.core.config import Settings


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


class ModelGateway:
    """DeepSeek LLM 与 Embedding 的统一模型网关。

    业务 Agent 只能依赖该网关，不能自行创建供应商 SDK 客户端。这样可以在同一位置
    控制模型切换、超时、重试、Tracing、Token 成本和日志脱敏，并防止生产
    环境意外回退到本地 Mock。当前使用 OpenAI-compatible 协议，后续可以在不修改业务
    Agent 的情况下增加其他供应商适配器。DeepSeek 使用 OpenAI-compatible API，配置名
    与 learning-langchain-CN 项目保持一致；默认关闭 thinking，保证 Tool Calling 和
    结构化输出的响应格式稳定。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # AsyncOpenAI 构造时要求非空密钥，因此未配置环境使用不可用占位值完成
        # 对象装配。chat/embed 会在任何网络请求发生前检查 configured 并明确抛错，
        # 占位值不会被发送给外部服务，也不会形成“看似成功”的降级结果。
        self._llm = AsyncOpenAI(
            api_key=settings.llm_api_key or "not-configured",
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
        )
        self._embedding = AsyncOpenAI(
            api_key=settings.embedding_effective_api_key or "not-configured",
            base_url=settings.embedding_base_url,
            timeout=settings.llm_timeout_seconds,
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
            raise ModelConfigurationError("LLM provider is not configured")

        try:
            response = await self._llm.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                extra_body=_deepseek_extra_body(self.settings.llm_thinking_enabled),
            )
        except OpenAIError as exc:
            raise ModelResponseError("LLM provider request failed") from exc
        return response.choices[0].message.content or ""

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> str:
        """调用对话模型并要求返回一个 JSON 对象。

        结构化训练计划不能依赖“让模型尽量输出 JSON”的自然语言约定。这里使用
        OpenAI-compatible 接口的 ``response_format`` 让 DeepSeek 在供应商边界执行
        JSON Object 约束；业务层仍必须再用 Pydantic 校验字段、数量和业务规则。
        ``response_format`` 不是权限控制，也不能证明内容正确，只是减少解析失败的
        概率。模型未配置、返回空结果或供应商不符合契约时统一抛错，禁止伪造草案。
        """

        return (await self.chat_json_with_usage(messages, temperature=temperature)).content

    async def chat_json_with_usage(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> JsonModelTurn:
        """结构化生成并保留 Token 用量，供摘要等后台能力做成本监控。"""

        if not self.settings.llm_configured:
            raise ModelConfigurationError("LLM provider is not configured")
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.llm_max_output_tokens,
            "response_format": {"type": "json_object"},
            "extra_body": _deepseek_extra_body(self.settings.llm_thinking_enabled),
        }
        try:
            response = await self._llm.chat.completions.create(**request)
        except OpenAIError as exc:
            raise ModelResponseError("LLM provider request failed") from exc
        if not response.choices:
            raise ModelResponseError("LLM returned no choices")
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise ModelResponseError("LLM returned an empty JSON response")
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
        temperature: float = 0.2,
    ) -> ModelTurn:
        """调用支持 Tool Calling 的模型，并规范化为 Runtime 自有协议。

        OpenAI-compatible 供应商的响应对象不能直接泄漏到业务编排层，否则更换模型
        供应商会导致整个 Agent 图改动。这里严格解析 tool name、call id 和 JSON 参数；
        任一工具参数不是合法 JSON 都会失败，Runtime 不会猜测或修复模型意图。
        """

        if not self.settings.llm_configured:
            raise ModelConfigurationError("LLM provider is not configured")
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.settings.llm_max_output_tokens,
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
        try:
            response = await self._llm.chat.completions.create(**request)
        except OpenAIError as exc:
            raise ModelResponseError("LLM provider request failed") from exc
        if not response.choices:
            raise ModelResponseError("LLM returned no choices")

        message = response.choices[0].message
        parsed_calls: list[ModelToolCall] = []
        for raw_call in message.tool_calls or []:
            try:
                arguments = json.loads(raw_call.function.arguments)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ModelResponseError("LLM returned invalid tool arguments") from exc
            if not isinstance(arguments, dict):
                raise ModelResponseError("LLM tool arguments must be a JSON object")
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
            raise ModelConfigurationError("Embedding provider is not configured")

        if self.settings.embedding_backend == "local":
            return await asyncio.to_thread(self._embed_local, texts)

        response = await self._embedding.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

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
