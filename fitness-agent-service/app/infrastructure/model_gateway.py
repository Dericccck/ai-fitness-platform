from typing import Any

from openai import AsyncOpenAI

from app.core.config import Settings


class ModelConfigurationError(RuntimeError):
    """真实模型服务未完成配置时抛出，防止生产流程静默使用假结果。"""


class ModelGateway:
    """LLM 与 Embedding 的统一模型网关。

    业务 Agent 只能依赖该网关，不能自行创建供应商 SDK 客户端。这样可以在同一位置
    控制模型切换、超时、重试、Tracing、Token 成本和日志脱敏，并防止生产
    环境意外回退到本地 Mock。当前使用 OpenAI-compatible 协议，后续可以在不修改业务
    Agent 的情况下增加其他供应商适配器。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # AsyncOpenAI 构造时要求非空密钥，因此未配置环境使用不可用占位值完成
        # 对象装配。chat/embed 会在任何网络请求发生前检查 configured 并明确抛错，
        # 占位值不会被发送给外部服务，也不会形成“看似成功”的降级结果。
        self._llm = AsyncOpenAI(
            api_key=settings.llm_api_key or "not-configured",
            base_url=settings.llm_base_url,
        )
        self._embedding = AsyncOpenAI(
            api_key=settings.embedding_effective_api_key or "not-configured",
            base_url=settings.embedding_base_url,
        )

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

        response = await self._llm.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量生成向量，并保持结果顺序与输入文本一致。

        文档权限和组织元数据应在入库前由 RAG 流程写入结构化字段，不能依靠向量
        表达权限。调用方还需要限制批次大小，防止单次请求超出供应商限制。
        """

        if not self.settings.embedding_configured:
            raise ModelConfigurationError("Embedding provider is not configured")

        response = await self._embedding.embeddings.create(
            model=self.settings.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

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
            "model": settings.embedding_model,
        },
        "reranker": {
            "configured": settings.reranker_configured,
            "model": settings.reranker_model,
        },
    }
