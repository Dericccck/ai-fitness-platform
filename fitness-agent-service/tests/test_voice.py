from __future__ import annotations

import asyncio
import io
import json
import wave
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from prometheus_client import CollectorRegistry
from pydantic import ValidationError

from app.api.routes.voice import router, voice_error
from app.core.config import Settings
from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity
from app.streaming.control import run_owner
from app.streaming.repository import ChatRun
from app.voice.service import (
    VoiceError,
    VoiceMetrics,
    VoiceService,
    audio_download_url,
    speech_chunks,
    validate_recording,
)


def wav(seconds=1, rate=16000, silent=False):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((b"\0\0" if silent else b"\x10\x01") * int(rate * seconds))
    return target.getvalue()


def settings(**fields):
    return Settings(
        _env_file=None,
        voice_enabled=True,
        voice_api_key="test-only-key",
        voice_rate_limit_enabled=False,
        **fields,
    )


@pytest.mark.parametrize(
    "data,code",
    [
        (b"not-wave", "RECORDING_FORMAT"),
        (wav(0.1), "RECORDING_DURATION"),
        (wav(61), "RECORDING_DURATION"),
        (wav(rate=24000), "RECORDING_FORMAT"),
        (wav(silent=True), "RECORDING_SILENT"),
        (wav()[:-20], "RECORDING_FORMAT"),
    ],
)
def test_recording_validation(data, code):
    with pytest.raises(VoiceError) as exc:
        validate_recording(data, 60)
    assert exc.value.code == code


def test_settings_hide_secret_and_require_secure_endpoint():
    assert "test-only-key" not in repr(settings())
    with pytest.raises(ValidationError):
        settings(voice_base_url="http://insecure")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, voice_enabled=True, voice_api_key="")


def test_markdown_keeps_table_labels_units_and_order():
    answer = """## 建议
| 动作 | 重量 | 次数 |
| --- | --- | --- |
| 深蹲 | 12.5 kg | 3 × 10 |

1. [热身](https://example.com/private)
2. 休息 60 秒，负重 -2.5 kg。

![图](https://example.com/photo)
```python
secret_code()
```
"""
    text = "".join(speech_chunks(answer, 15360))
    assert "动作：深蹲；重量：12.5 kg；次数：3 × 10" in text
    assert "第 1 项" in text and "第 2 项" in text and "-2.5 kg" in text
    assert "https://" not in text and "secret_code" not in text
    assert "代码内容请查看" in text


def test_speech_split_preserves_unicode():
    text = "动作🙂建议。" * 250
    chunks = speech_chunks(text, 15360)
    assert len(chunks) > 1 and all(len(chunk) <= 480 for chunk in chunks)
    assert "".join(chunks) == text
    with pytest.raises(VoiceError, match="较长"):
        speech_chunks("文" * 15361, 15360)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/a",
        "https://evil.aliyuncs.com/a",
        "https://user@dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a",
        "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com:999/a",
        "http://x:bad/a",
    ],
)
def test_download_host_validation(url):
    with pytest.raises(VoiceError):
        audio_download_url(url)


async def test_provider_contract_cache_and_owner_isolation():
    calls = []

    async def handle(request):
        calls.append(request)
        if request.method == "GET":
            assert "authorization" not in request.headers
            assert request.url.scheme == "https"
            return httpx.Response(200, content=wav(rate=24000))
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-only-key"
        if body["model"] == "qwen3-asr-flash":
            assert body["messages"][0]["content"][0]["input_audio"]["data"].startswith(
                "data:audio/wav;base64,"
            )
            return httpx.Response(200, json={"choices": [{"message": {"content": "  我要热身  "}}]})
        assert len(body["input"]["text"]) <= 600
        await asyncio.sleep(0.01)
        return httpx.Response(
            200,
            json={
                "output": {
                    "audio": {
                        "url": "http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav?Signature=hidden"
                    }
                }
            },
        )

    service = VoiceService(
        settings(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        metrics=VoiceMetrics(CollectorRegistry()),
    )
    assert await service.transcribe(wav()) == "我要热身"
    revision = service.manifest("run", "热身建议")["revision"]
    first, duplicate = await asyncio.gather(
        *[service.synthesize("owner", "run", revision, "热身建议") for _ in range(2)]
    )
    assert first == duplicate
    assert await service.synthesize("owner", "run", revision, "热身建议") == first
    assert len(calls) == 3
    await service.synthesize("other", "run", revision, "热身建议")
    assert len(calls) == 5
    await service.close()
    assert not service.inflight and service.cache_bytes == 0


async def test_upstream_error_and_timeout_redaction():
    async def fail(request):
        return httpx.Response(401, json={"message": "private-provider-secret"})

    service = VoiceService(
        settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(VoiceError) as exc:
        await service.transcribe(wav())
    assert "private-provider-secret" not in str(exc.value)
    await service.close()

    async def slow(request):
        await asyncio.sleep(2)
        return httpx.Response(200)

    service = VoiceService(
        settings(voice_timeout_seconds=1),
        client=httpx.AsyncClient(transport=httpx.MockTransport(slow)),
    )
    with pytest.raises(VoiceError) as exc:
        await service.transcribe(wav())
    assert exc.value.status == 504
    await service.close()


async def test_redirected_download_not_followed():
    calls = []

    async def handle(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {
                        "audio": {
                            "url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a"
                        }
                    }
                },
            )
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    service = VoiceService(
        settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(VoiceError):
        await service.synthesize("owner", "r", "v", "文本")
    assert len(calls) == 2
    await service.close()


async def test_bounded_cache_and_cancelled_waiter():
    service = VoiceService(settings(voice_cache_max_bytes=len(wav()) + 5))
    service._synthesize = AsyncMock(side_effect=lambda _: wav())
    await service.synthesize("o", "r1", "v", "a")
    await service.synthesize("o", "r2", "v", "b")
    assert len(service.cache) == 1 and service.cache_bytes <= service.settings.voice_cache_max_bytes
    event = asyncio.Event()

    async def wait(_):
        await event.wait()
        return wav()

    service._synthesize = wait
    first = asyncio.create_task(service.synthesize("o", "r3", "v", "c"))
    second = asyncio.create_task(service.synthesize("o", "r3", "v", "c"))
    await asyncio.sleep(0.01)
    first.cancel()
    event.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await second == wav()
    await service.close()


class Verifier:
    identity = AgentIdentity(
        subject="u1",
        organization_ids=frozenset({"o1"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )

    def verify(self, token):
        if token == "valid":
            return self.identity
        if token == "other":
            return replace(self.identity, subject="u2")
        raise AgentContextVerificationError("bad identity")


def api_app():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(VoiceError, voice_error)
    app.state.settings = settings()
    app.state.context_verifier = Verifier()
    app.state.voice = VoiceService(app.state.settings)
    app.state.voice.transcribe = AsyncMock(return_value="语音文字")
    app.state.voice.synthesize = AsyncMock(return_value=wav())
    run_id = str(uuid4())
    owner = run_owner(Verifier.identity)
    runs = {(run_id, owner): ChatRun(run_id, "COMPLETED", result={"answer": "先热身，再训练。"})}
    app.state.chat_runs = SimpleNamespace(
        repository=SimpleNamespace(
            get=AsyncMock(side_effect=lambda key, scope: runs.get((key, scope)))
        )
    )
    app.state.cache = SimpleNamespace(consume_fixed_window=AsyncMock(return_value=True))
    return app, run_id, runs


async def test_api_auth_canonical_reply_and_injected_text():
    app, run_id, _ = api_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/agent/voice/config")).status_code == 401
        assert (
            await client.get("/api/v1/agent/voice/config", headers={"X-Agent-Context": "invalid"})
        ).status_code == 401
        config = await client.get(
            "/api/v1/agent/voice/config", headers={"X-Agent-Context": "valid"}
        )
        assert config.json()["enabled"] and "api_key" not in config.text
        transcript = await client.post(
            "/api/v1/agent/voice/transcriptions",
            headers={"X-Agent-Context": "valid", "Content-Type": "audio/wav"},
            content=wav(),
        )
        assert transcript.json() == {"text": "语音文字", "duration_ms": 1000}
        url = f"/api/v1/agent/voice/replies/{run_id}"
        manifest = await client.get(url, headers={"X-Agent-Context": "valid"})
        revision = manifest.json()["revision"]
        response = await client.post(
            url + "/segments/0", headers={"X-Agent-Context": "valid"}, json={"revision": revision}
        )
        assert response.status_code == 200 and response.content == wav()
        assert response.headers["cache-control"] == "no-store"
        assert app.state.voice.synthesize.call_args.args[-1] == "先热身，再训练。"
        assert (await client.get(url, headers={"X-Agent-Context": "other"})).status_code == 404
        assert (
            await client.post(
                url + "/segments/0",
                headers={"X-Agent-Context": "valid"},
                json={"revision": revision, "text": "injected"},
            )
        ).status_code == 422
        assert (
            await client.post(
                url + "/segments/0",
                headers={"X-Agent-Context": "valid"},
                json={"revision": "0" * 64},
            )
        ).status_code == 409
        assert (
            await client.post(
                url + "/segments/-1",
                headers={"X-Agent-Context": "valid"},
                json={"revision": revision},
            )
        ).status_code == 404
    await app.state.voice.close()


@pytest.mark.parametrize(
    "status", ["RUNNING", "CANCEL_REQUESTED", "CANCELLED", "FAILED", "UNKNOWN"]
)
async def test_incomplete_run_not_spoken(status):
    app, run_id, runs = api_app()
    runs[(run_id, run_owner(Verifier.identity))] = ChatRun(
        run_id, status, result={"answer": "partial"}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/agent/voice/replies/{run_id}", headers={"X-Agent-Context": "valid"}
        )
        assert response.status_code == 409
    app.state.voice.synthesize.assert_not_called()
    await app.state.voice.close()


async def test_upload_size_format_and_fail_closed_quota():
    app, _, _ = api_app()
    app.state.settings.voice_max_upload_bytes = 65536

    async def oversized():
        yield b"x" * 40000
        yield b"x" * 40000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        url = "/api/v1/agent/voice/transcriptions"
        headers = {"X-Agent-Context": "valid", "Content-Type": "audio/wav"}
        assert (await client.post(url, headers=headers, content=oversized())).status_code == 413
        assert (await client.post(url, headers=headers, content=b"bad")).status_code == 415
        app.state.settings.voice_rate_limit_enabled = True
        app.state.cache.consume_fixed_window.return_value = False
        response = await client.post(url, headers=headers, content=wav())
        assert response.status_code == 429 and response.headers["retry-after"] == "60"
        app.state.cache.consume_fixed_window.side_effect = ConnectionError("private-redis-password")
        response = await client.post(url, headers=headers, content=wav())
        assert response.status_code == 503 and "private-redis-password" not in response.text
    app.state.voice.transcribe.assert_not_called()
    await app.state.voice.close()


@pytest.mark.parametrize("streaming", [False, True])
async def test_provider_wave_streaming_placeholder_vs_truncated_file(streaming):
    data = bytearray(wav(rate=24000))
    if streaming:
        data[4:8] = (0x7FFFFFFF).to_bytes(4, "little")
        data[40:44] = (0x7FFFFF9A).to_bytes(4, "little")
    else:
        data = data[:-10]

    async def handle(request):
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {
                        "audio": {
                            "url": "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a"
                        }
                    }
                },
            )
        return httpx.Response(200, content=bytes(data))

    service = VoiceService(
        settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    if streaming:
        result = await service.synthesize("owner", "run", "revision", "测试")
        with wave.open(io.BytesIO(result), "rb") as audio:
            assert audio.getnframes() == 24000
            assert len(audio.readframes(24000)) == 48000
    else:
        with pytest.raises(VoiceError):
            await service.synthesize("owner", "run", "revision", "测试")
    await service.close()


async def test_voice_cookie_auth_requires_csrf_and_origin():
    from tests.test_browser_auth import build, logged_in

    app, auth, _ = build()
    app.include_router(router)
    app.add_exception_handler(VoiceError, voice_error)
    app.state.settings.voice_enabled = True
    app.state.settings.voice_rate_limit_enabled = False
    app.state.context_verifier = auth.verifier
    app.state.voice = VoiceService(app.state.settings)
    app.state.voice.transcribe = AsyncMock(return_value="识别成功")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://fitness.test"
    ) as client:
        login = await logged_in(client)
        url = "/api/v1/agent/voice/transcriptions"
        headers = {"Content-Type": "audio/wav", "Origin": "https://fitness.test"}
        assert (await client.post(url, headers=headers, content=wav())).status_code == 403
        headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = await client.post(url, headers=headers, content=wav())
        assert response.status_code == 200 and response.json()["text"] == "识别成功"
        headers["Origin"] = "https://foreign.test"
        assert (await client.post(url, headers=headers, content=wav())).status_code == 403
    assert app.state.voice.transcribe.call_count == 1
    await app.state.voice.close()
    await auth.close()
