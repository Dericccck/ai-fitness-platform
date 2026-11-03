"""独立视觉模型网关：只接收服务端授权的媒体引用，不保存或记录素材地址。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import structlog
from opentelemetry.instrumentation.utils import suppress_http_instrumentation

from app.core.config import Settings
from app.infrastructure.model_protection import (
    CircuitBreaker,
    ModelCapacityError,
    ModelProtection,
    RedisDailyModelBudget,
)
from app.infrastructure.model_usage import ModelUsage, record_usage, usage_context
from app.streaming.control import cancellation_checkpoint


class MultimodalError(RuntimeError):
    """不包含供应商请求体、签名地址或用户数据的稳定错误。"""


class MultimodalUnavailable(MultimodalError):
    pass


class MultimodalOutcomeUnknown(MultimodalError):
    """请求可能已经到达供应商，不能自动重放或假定未产生费用。"""


class MultimodalResponseError(MultimodalError):
    pass


@dataclass(frozen=True)
class AuthorizedMediaRef:
    """由媒体服务在鉴权后创建；外部 API 不得直接接收这个对象。"""

    asset_id: str
    revision: int
    kind: Literal["image", "video"]
    url: str = field(repr=False)
    duration_seconds: float | None = None


@dataclass(frozen=True)
class MultimodalTurn:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    provider_request_id: str | None


UsageSink = Callable[[ModelUsage], Awaitable[None]]
logger = structlog.get_logger(__name__)


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class MultimodalGateway:
    """一次请求最多调用一次供应商；未知结果不能静默重试。"""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=settings.multimodal_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self.usage_sink = usage_sink
        self.call_budget: RedisDailyModelBudget | None = None
        self.protection = ModelProtection(
            settings.multimodal_max_concurrency,
            settings.multimodal_queue_timeout_seconds,
            CircuitBreaker(
                settings.llm_circuit_failure_threshold, settings.llm_circuit_cooldown_seconds
            ),
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _media_parts(self, media: Sequence[AuthorizedMediaRef]) -> list[dict[str, Any]]:
        if not 1 <= len(media) <= 3:
            raise MultimodalResponseError("每次请求需要一至三个已授权素材")
        if any(item.kind == "video" for item in media) and len(media) != 1:
            raise MultimodalResponseError("视频分析每次只能使用一个视频")
        allowed = {host.lower() for host in self.settings.multimodal_media_hosts}
        parts: list[dict[str, Any]] = []
        for item in media:
            try:
                url = urlsplit(item.url)
                valid = (
                    item.asset_id
                    and item.revision > 0
                    and item.kind in {"image", "video"}
                    and url.scheme == "https"
                    and url.hostname in allowed
                    and url.port in {None, 443}
                    and not url.username
                    and not url.password
                    and not url.fragment
                )
            except ValueError:
                valid = False
            if not valid:
                raise MultimodalResponseError("媒体引用无效或不属于受信任的存储")
            if item.kind == "video":
                # 百炼 OpenAI 兼容协议要求 fps 与 video_url 同级。
                parts.append(
                    {
                        "type": "video_url",
                        "video_url": {"url": item.url},
                        "fps": self.settings.multimodal_video_fps,
                    }
                )
            else:
                parts.append({"type": "image_url", "image_url": {"url": item.url}})
        return parts

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        media: Sequence[AuthorizedMediaRef],
        kind: str = "movement_analysis",
    ) -> MultimodalTurn:
        if not self.settings.multimodal_enabled:
            raise MultimodalUnavailable("多模态模型尚未启用")
        if not system_prompt.strip() or not user_prompt.strip():
            raise MultimodalResponseError("多模态请求缺少必要文字")
        if len(system_prompt) + len(user_prompt) > self.settings.multimodal_max_text_chars:
            raise MultimodalResponseError("多模态文字上下文超出限制")
        parts = self._media_parts(media)
        parts.append({"type": "text", "text": user_prompt})
        body = {
            "model": self.settings.multimodal_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": parts},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "max_tokens": self.settings.multimodal_max_output_tokens,
        }
        request_id, run_id = usage_context()
        usage = ModelUsage(
            call_id=uuid4().hex,
            request_id=request_id,
            run_id=run_id,
            model=self.settings.multimodal_model,
            provider=urlsplit(self.settings.multimodal_base_url).hostname or "configured-provider",
            kind=kind,
            attempt=1,
            status="STARTED",
            prompt_version=self.settings.multimodal_prompt_version,
        )
        started = perf_counter()
        await cancellation_checkpoint()
        try:
            async with self.protection.capacity():
                probe = self.protection.breaker.enter()
                try:
                    if self.call_budget is not None:
                        await asyncio.wait_for(self.call_budget.reserve(), timeout=5)
                    if self.usage_sink:
                        await asyncio.wait_for(self.usage_sink(usage), timeout=5)
                    await cancellation_checkpoint()
                    with suppress_http_instrumentation():
                        async with asyncio.timeout(self.settings.multimodal_timeout_seconds):
                            async with self.client.stream(
                                "POST",
                                self.settings.multimodal_base_url.rstrip("/") + "/chat/completions",
                                headers={
                                    "Authorization": f"Bearer {self.settings.multimodal_api_key}"
                                },
                                json=body,
                            ) as response:
                                status_code = response.status_code
                                raw_request_id = response.headers.get("x-request-id")
                                response_body = bytearray()
                                if status_code == 200:
                                    async for chunk in response.aiter_bytes():
                                        response_body.extend(chunk)
                                        if len(response_body) > 2 * 1024 * 1024:
                                            raise MultimodalResponseError("视觉模型返回内容过大")
                    if status_code != 200:
                        logger.warning("multimodal_provider_rejected", status_code=status_code)
                        if status_code == 429 or status_code >= 500:
                            self.protection.breaker.failure(probe=probe)
                        else:
                            self.protection.breaker.abandon(probe=probe)
                        # 5xx 不能证明供应商未完成或未计费。
                        failed = replace(
                            usage,
                            status="UNKNOWN" if status_code >= 500 else "FAILED",
                            duration_seconds=perf_counter() - started,
                        )
                        record_usage(failed)
                        if self.usage_sink:
                            await self.usage_sink(failed)
                        if status_code >= 500:
                            raise MultimodalOutcomeUnknown("视觉模型请求结果未确认，请手动重试")
                        raise MultimodalUnavailable("视觉模型请求失败，请稍后重试")
                    self.protection.breaker.success()
                except (httpx.TransportError, TimeoutError) as exc:
                    self.protection.breaker.failure(probe=probe)
                    unknown = replace(
                        usage, status="UNKNOWN", duration_seconds=perf_counter() - started
                    )
                    record_usage(unknown)
                    if self.usage_sink:
                        await self.usage_sink(unknown)
                    raise MultimodalOutcomeUnknown("视觉模型请求结果未确认，请手动重试") from exc
                except MultimodalResponseError:
                    # 已收到 HTTP 200，但响应过大而中止读取；供应商可能已经计费。
                    self.protection.breaker.abandon(probe=probe)
                    unknown = replace(
                        usage, status="UNKNOWN", duration_seconds=perf_counter() - started
                    )
                    record_usage(unknown)
                    if self.usage_sink:
                        await self.usage_sink(unknown)
                    raise
                except asyncio.CancelledError:
                    self.protection.breaker.abandon(probe=probe)
                    unknown = replace(
                        usage, status="UNKNOWN", duration_seconds=perf_counter() - started
                    )
                    record_usage(unknown)
                    if self.usage_sink:
                        try:
                            async with asyncio.timeout(2):
                                await self.usage_sink(unknown)
                        except Exception as exc:  # noqa: BLE001 - 不覆盖取消信号
                            logger.warning(
                                "multimodal_usage_persist_failed", error_type=type(exc).__name__
                            )
                    raise
                except BaseException:
                    self.protection.breaker.abandon(probe=probe)
                    raise
        except ModelCapacityError as exc:
            raise MultimodalUnavailable("视觉模型服务繁忙，请稍后重试") from exc

        try:
            payload = json.loads(response_body)
            raw_usage = payload.get("usage") or {}
            input_tokens = _nonnegative_int(raw_usage.get("prompt_tokens"))
            output_tokens = _nonnegative_int(raw_usage.get("completion_tokens"))
        except (ValueError, AttributeError, TypeError):
            payload, input_tokens, output_tokens = None, None, None
        succeeded = replace(
            usage,
            status="SUCCEEDED",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_seconds=perf_counter() - started,
        )
        record_usage(succeeded)
        if self.usage_sink:
            await self.usage_sink(succeeded)
        await cancellation_checkpoint()
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
            if choice.get("finish_reason") != "stop" or message.get("refusal"):
                raise ValueError("incomplete")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty")
            if not isinstance(json.loads(content), dict):
                raise TypeError("not-object")
        except (TypeError, ValueError, KeyError, IndexError):
            raise MultimodalResponseError("视觉模型返回的结构化结果无效或不完整") from None
        provider_id = raw_request_id[:128] if raw_request_id else None
        return MultimodalTurn(
            content, self.settings.multimodal_model, input_tokens, output_tokens, provider_id
        )
