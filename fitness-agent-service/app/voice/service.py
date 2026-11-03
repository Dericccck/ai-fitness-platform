from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import wave
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog
from markdown_it import MarkdownIt
from markdown_it.token import Token
from opentelemetry.instrumentation.utils import suppress_http_instrumentation
from prometheus_client import CollectorRegistry, Counter, Histogram

from app.core.config import Settings

logger = structlog.get_logger(__name__)
T = TypeVar("T")
TTS_CHUNK_CHARS = 480
MAX_AUDIO_BYTES = 12 * 1024 * 1024
SPEECH_VERSION = "speech-v1"


class _AudioUrlFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx 默认 INFO 日志会打印完整下载 URL（含 OSS 签名）。
        return "dashscope-result-" not in record.getMessage()


logging.getLogger("httpx").addFilter(_AudioUrlFilter())
logging.getLogger("httpcore").addFilter(_AudioUrlFilter())


class VoiceError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def validate_recording(data: bytes, max_seconds: int) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (
                audio.getnchannels(),
                audio.getsampwidth(),
                audio.getframerate(),
                audio.getcomptype(),
            ) != (1, 2, 16000, "NONE"):
                raise ValueError()
            frames = audio.getnframes()
            duration = frames / 16000
            if not 0.3 <= duration <= max_seconds:
                raise VoiceError(422, "RECORDING_DURATION", f"录音时长应为 0.3 至 {max_seconds} 秒")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError()
            if not any(pcm):
                raise VoiceError(422, "RECORDING_SILENT", "没有检测到声音，请靠近麦克风后重录")
            return duration
    except (wave.Error, EOFError, ValueError) as exc:
        raise VoiceError(415, "RECORDING_FORMAT", "录音格式无效，请重新录音") from exc


def inline_text(token: Token) -> str:
    result = []
    for child in token.children or []:
        if child.type in {"text", "code_inline"}:
            result.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            result.append(" ")
    return "".join(result)


def speech_chunks(answer: str, max_chars: int) -> list[str]:
    """解析 Markdown，保留表头与数字单位，不依赖模型重写。"""
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(answer)
    parts: list[str] = []
    rows: list[list[str]] | None = None
    row: list[str] = []
    list_numbers: list[int | None] = []
    for token in tokens:
        if token.type == "table_open":
            rows = []
        elif token.type == "tr_open" and rows is not None:
            row = []
        elif token.type == "inline":
            text = inline_text(token)
            if rows is not None:
                row.append(text)
            else:
                parts.append(text)
        elif token.type == "tr_close" and rows is not None:
            rows.append(row)
        elif token.type == "table_close" and rows is not None:
            if rows:
                headers = rows[0]
                for cells in rows[1:]:
                    parts.append(
                        "；".join(
                            f"{headers[i] if i < len(headers) else '内容'}：{value}"
                            for i, value in enumerate(cells)
                        )
                        + "。"
                    )
            rows = None
        elif token.type in {"fence", "code_block"}:
            parts.append("代码内容请查看文字回复。")
        elif token.type == "ordered_list_open":
            list_numbers.append(int(token.attrGet("start") or 1))
        elif token.type == "bullet_list_open":
            list_numbers.append(None)
        elif token.type == "list_item_open" and list_numbers and list_numbers[-1] is not None:
            parts.append(f"第 {list_numbers[-1]} 项：")
            list_numbers[-1] += 1
        elif token.type in {"ordered_list_close", "bullet_list_close"}:
            list_numbers.pop()
    text = re.sub(r"https?://[^\s<>]+", "链接请查看文字回复", "\n".join(parts))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise VoiceError(422, "SPEECH_EMPTY", "这条回复没有可朗读的文字")
    if len(text) > max_chars:
        raise VoiceError(422, "SPEECH_TOO_LONG", "这条回复较长，请阅读文字或让助手分部分回答")
    chunks = []
    while text:
        end = min(TTS_CHUNK_CHARS, len(text))
        if end < len(text):
            boundaries = [match.end() for match in re.finditer(r"[。！？；.!?;，,\s]", text[:end])]
            if boundaries and boundaries[-1] >= end // 2:
                end = boundaries[-1]
        chunk, text = text[:end].strip(), text[end:].lstrip()
        if chunk:
            chunks.append(chunk)
    return chunks


def audio_download_url(raw: str) -> str:
    try:
        url = urlsplit(raw)
        valid = (
            url.scheme in {"http", "https"}
            and not url.username
            and not url.password
            and url.port in {None, 80, 443}
            and not url.fragment
            and re.fullmatch(
                r"dashscope-result-[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com", url.hostname or ""
            )
        )
        if valid:
            return urlunsplit(("https", url.hostname or "", url.path, url.query, ""))
    except (ValueError, TypeError):
        pass
    raise VoiceError(502, "AUDIO_URL_INVALID", "语音服务返回了无效的音频地址")


class VoiceMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.requests = Counter(
            "fitness_agent_voice_requests_total",
            "语音请求结果",
            ("operation", "outcome"),
            registry=registry,
        )
        self.duration = Histogram(
            "fitness_agent_voice_duration_seconds",
            "语音处理耗时",
            ("operation",),
            registry=registry,
            buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120),
        )
        self.units = Counter(
            "fitness_agent_voice_units_total",
            "语音输入秒数或合成字符数",
            ("operation", "unit"),
            registry=registry,
        )
        self.cache = Counter(
            "fitness_agent_voice_cache_total", "语音缓存结果", ("outcome",), registry=registry
        )


class VoiceService:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        metrics: VoiceMetrics | None = None,
    ) -> None:
        self.settings = settings
        # 下载 OSS 时不能携带百炼凭证，因此 Authorization 仅设置在 POST 上。
        self.client = client or httpx.AsyncClient(
            timeout=settings.voice_timeout_seconds,
            follow_redirects=False,
        )
        self.metrics = metrics
        self.capacity = asyncio.Semaphore(settings.voice_max_concurrency)
        self.cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self.cache_bytes = 0
        self.inflight: dict[str, asyncio.Task[bytes]] = {}
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cache.clear()
        self.cache_bytes = 0
        await self.client.aclose()

    def manifest(self, run_id: str, answer: str) -> dict[str, Any]:
        chunks = speech_chunks(answer, self.settings.voice_max_spoken_chars)
        revision = hashlib.sha256(
            json.dumps(
                {
                    "chunks": chunks,
                    "version": SPEECH_VERSION,
                    "model": self.settings.voice_tts_model,
                    "voice": self.settings.voice_tts_voice,
                    "language": self.settings.voice_tts_language,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return {
            "run_id": run_id,
            "revision": revision,
            "segment_count": len(chunks),
            "segments": [{"index": i, "text": text} for i, text in enumerate(chunks)],
        }

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.client.stream(
            "POST",
            self.settings.voice_base_url.rstrip("/") + path,
            headers={"Authorization": f"Bearer {self.settings.voice_api_key}"},
            json=payload,
        ) as response:
            if response.status_code == 429:
                raise VoiceError(429, "PROVIDER_BUSY", "语音服务繁忙，请稍后重试")
            if response.is_error:
                raise VoiceError(502, "PROVIDER_UNAVAILABLE", "语音服务暂时不可用，请稍后重试")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 1024 * 1024:
                    raise ValueError("oversized provider response")
        result = json.loads(data)
        if not isinstance(result, dict) or result.get("code"):
            raise VoiceError(502, "PROVIDER_RESPONSE", "语音服务返回异常，请稍后重试")
        return result

    async def transcribe(self, data: bytes) -> str:
        seconds = validate_recording(data, self.settings.voice_max_recording_seconds)

        async def generate() -> str:
            result = await self._post(
                "/compatible-mode/v1/chat/completions",
                {
                    "model": self.settings.voice_asr_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": "data:audio/wav;base64,"
                                        + base64.b64encode(data).decode("ascii")
                                    },
                                }
                            ],
                        }
                    ],
                    "stream": False,
                    "asr_options": {"enable_itn": True},
                },
            )
            text = result["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError()
            if not text.strip():
                raise VoiceError(422, "TRANSCRIPT_EMPTY", "没有识别到清晰的语音，请重录或输入文字")
            if len(text.strip()) > 4000:
                raise VoiceError(422, "TRANSCRIPT_TOO_LONG", "识别文字过长，请分成几条录音发送")
            if self.metrics:
                self.metrics.units.labels("asr", "seconds").inc(seconds)
            return text.strip()

        return await self._bounded("asr", generate)

    async def _bounded(self, operation: str, generate: Callable[[], Awaitable[T]]) -> T:
        started = monotonic()
        outcome = "success"
        try:
            if self.closed:
                raise VoiceError(503, "VOICE_DRAINING", "语音服务正在更新，请稍后重试")
            try:
                await asyncio.wait_for(self.capacity.acquire(), timeout=1)
            except TimeoutError as exc:
                raise VoiceError(429, "VOICE_CAPACITY", "语音任务较多，请稍后重试") from exc
            try:
                async with asyncio.timeout(self.settings.voice_timeout_seconds):
                    # 自动 HTTP span 会采集 signed URL；语音用无正文的专用指标观测。
                    with suppress_http_instrumentation():
                        result = await generate()
            finally:
                self.capacity.release()
            logger.info(
                "voice_request_completed",
                operation=operation,
                elapsed_ms=round((monotonic() - started) * 1000),
            )
            return result
        except VoiceError as exc:
            outcome = exc.code
            raise
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            outcome = "timeout"
            raise VoiceError(
                504, "VOICE_TIMEOUT", "语音处理超时，请重试；文字对话仍可使用"
            ) from exc
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            wave.Error,
            EOFError,
        ) as exc:
            outcome = "invalid_response"
            logger.warning(
                "voice_request_failed", operation=operation, error_type=type(exc).__name__
            )
            raise VoiceError(502, "PROVIDER_RESPONSE", "语音服务返回异常，请稍后重试") from exc
        finally:
            if self.metrics:
                self.metrics.requests.labels(operation, outcome).inc()
                self.metrics.duration.labels(operation).observe(monotonic() - started)

    async def synthesize(self, owner: str, run_id: str, revision: str, text: str) -> bytes:
        key = hashlib.sha256(
            (owner + "\0" + run_id + "\0" + revision + "\0" + text).encode()
        ).hexdigest()
        now = monotonic()
        for expired in [k for k, (deadline, _) in self.cache.items() if deadline <= now]:
            self.cache_bytes -= len(self.cache.pop(expired)[1])
        if key in self.cache:
            self.cache.move_to_end(key)
            if self.metrics:
                self.metrics.cache.labels("hit").inc()
            return self.cache[key][1]
        if key not in self.inflight:
            if len(self.inflight) >= self.settings.voice_max_concurrency * 2:
                raise VoiceError(429, "VOICE_CAPACITY", "语音任务较多，请稍后重试")
            if self.metrics:
                self.metrics.cache.labels("miss").inc()

            async def generate_cached() -> bytes:
                try:
                    data = await self._bounded("tts", lambda: self._synthesize(text))
                    if len(data) <= self.settings.voice_cache_max_bytes:
                        while (
                            self.cache
                            and self.cache_bytes + len(data) > self.settings.voice_cache_max_bytes
                        ):
                            self.cache_bytes -= len(self.cache.popitem(last=False)[1][1])
                        self.cache[key] = (monotonic() + 600, data)
                        self.cache_bytes += len(data)
                    return data
                finally:
                    self.inflight.pop(key, None)

            task = asyncio.create_task(generate_cached())
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            self.inflight[key] = task
        # 请求断开不影响其他同片段等待者；任务受总截止时间和 close() 管理。
        return await asyncio.shield(self.inflight[key])

    async def _synthesize(self, text: str) -> bytes:
        result = await self._post(
            "/api/v1/services/aigc/multimodal-generation/generation",
            {
                "model": self.settings.voice_tts_model,
                "input": {
                    "text": text,
                    "voice": self.settings.voice_tts_voice,
                    "language_type": self.settings.voice_tts_language,
                },
            },
        )
        url = audio_download_url(result["output"]["audio"]["url"])
        data = bytearray()
        async with self.client.stream("GET", url) as response:
            if response.status_code != 200:
                raise VoiceError(502, "AUDIO_DOWNLOAD", "语音音频暂时无法获取，请重试")
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_AUDIO_BYTES:
                    raise ValueError("oversized audio")
        with wave.open(io.BytesIO(data), "rb") as audio:
            if audio.getnframes() < 1 or audio.getcomptype() != "NONE":
                raise ValueError()
            channels, width, rate = audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
            block = channels * width
            pcm = audio.readframes(min(audio.getnframes(), MAX_AUDIO_BYTES // block + 1))
            expected_bytes = audio.getnframes() * block
            # 百炼下载结果可能使用 2GB/4GB 占位长度的流式 WAV 头。
            # 普通截断文件仍拒绝；占位头也必须具有完整 PCM 帧和有界正文。
            streaming_header = expected_bytes >= 0x7F000000
            if not pcm or len(pcm) % block or (len(pcm) != expected_bytes and not streaming_header):
                raise ValueError()
        normalized = io.BytesIO()
        with wave.open(normalized, "wb") as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(width)
            audio.setframerate(rate)
            audio.writeframes(pcm)
        if self.metrics:
            self.metrics.units.labels("tts", "characters").inc(len(text))
        return normalized.getvalue()
