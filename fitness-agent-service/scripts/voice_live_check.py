"""显式执行的小额百炼语音联调；仅使用固定测试句，不输出凭证或 signed URL。"""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
import wave
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.routes.voice import router, voice_error
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.streaming.control import run_owner
from app.streaming.repository import ChatRun
from app.voice.service import VoiceError, VoiceService

TEXT = "你好，这是健身助手的语音测试。请先热身五分钟，再开始训练。"


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = Settings(_env_file=root / ".env")
    report = {}

    async def inspect_download(response: httpx.Response) -> None:
        if response.request.method == "GET" and response.status_code == 200:
            data = await response.aread()
            report["download_bytes"] = len(data)
            report["download_type"] = response.headers.get("content-type")
            try:
                with wave.open(io.BytesIO(data), "rb") as parsed:
                    report["wav_header"] = {
                        "frames": parsed.getnframes(),
                        "rate": parsed.getframerate(),
                        "width": parsed.getsampwidth(),
                        "channels": parsed.getnchannels(),
                        "actual_pcm_bytes": len(parsed.readframes(parsed.getnframes())),
                    }
            except (wave.Error, EOFError):
                report["wav_header"] = "invalid"

    service = VoiceService(
        settings,
        client=httpx.AsyncClient(
            timeout=settings.voice_timeout_seconds,
            follow_redirects=False,
            event_hooks={"response": [inspect_download]},
        ),
    )
    run_id = str(uuid4())
    identity = AgentIdentity(
        subject="voice-live-check",
        organization_ids=frozenset({"test"}),
        roles=frozenset({"STUDENT"}),
        issued_at=1,
        expires_at=2,
    )
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(VoiceError, voice_error)
    app.state.settings = settings
    app.state.voice = service
    # 仅进程内 ASGI 测试，不启动监听；隔离正常 Agent 和业务数据库。
    app.state.context_verifier = SimpleNamespace(verify=lambda _: identity)
    owner = run_owner(identity)
    app.state.chat_runs = SimpleNamespace(
        repository=SimpleNamespace(
            get=AsyncMock(
                side_effect=lambda key, scope: (
                    ChatRun(run_id, "COMPLETED", result={"answer": TEXT})
                    if (key, scope) == (run_id, owner)
                    else None
                )
            )
        )
    )
    app.state.cache = SimpleNamespace(consume_fixed_window=AsyncMock(return_value=True))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://voice-test"
        ) as client:
            headers = {"X-Agent-Context": "isolated-test"}
            manifest = await client.get(f"/api/v1/agent/voice/replies/{run_id}", headers=headers)
            manifest.raise_for_status()
            started = perf_counter()
            audio = await client.post(
                f"/api/v1/agent/voice/replies/{run_id}/segments/0",
                headers=headers,
                json={"revision": manifest.json()["revision"]},
            )
            report["tts_seconds"] = round(perf_counter() - started, 3)
            if audio.status_code != 200:
                raise VoiceError(
                    audio.status_code, "LIVE_TTS_FAILED", audio.json().get("message", "TTS failed")
                )
            with wave.open(io.BytesIO(audio.content), "rb") as parsed:
                report["audio_seconds"] = round(parsed.getnframes() / parsed.getframerate(), 3)
                report["sample_rate"] = parsed.getframerate()
            # 测试脚本的转换工具；正式浏览器在 Web Audio 中归一化，服务端无 ffmpeg 依赖。
            converted_process = await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    "pipe:0",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-acodec",
                    "pcm_s16le",
                    "-f",
                    "wav",
                    "pipe:1",
                ],
                input=audio.content,
                capture_output=True,
                check=True,
            )
            converted = converted_process.stdout
            # ffmpeg 管道 WAV 采用未知帧数，重写为精确长度的普通 WAV。
            with wave.open(io.BytesIO(converted), "rb") as parsed:
                pcm = parsed.readframes(parsed.getnframes())
            target = io.BytesIO()
            with wave.open(target, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(pcm)
            started = perf_counter()
            transcript = await client.post(
                "/api/v1/agent/voice/transcriptions",
                headers={**headers, "Content-Type": "audio/wav"},
                content=target.getvalue(),
            )
            report["asr_seconds"] = round(perf_counter() - started, 3)
            if transcript.status_code != 200:
                raise VoiceError(
                    transcript.status_code,
                    "LIVE_ASR_FAILED",
                    transcript.json().get("message", "ASR failed"),
                )
            recognized = transcript.json()["text"]
            report.update(
                tts_model=settings.voice_tts_model,
                asr_model=settings.voice_asr_model,
                audio_bytes=len(audio.content),
                recognized=recognized,
                passed=all(word in recognized for word in ["热身", "训练"]),
            )
            if not report["passed"]:
                raise ValueError("ASR did not recognize the fixed test sentence")
            output_dir = root.parent / "output" / "voice"
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "live-reply.wav").write_bytes(audio.content)
            (output_dir / "live-recording.wav").write_bytes(target.getvalue())
            (output_dir / "live-check.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(report, ensure_ascii=False))
    except VoiceError as exc:
        print(
            json.dumps(
                {
                    **report,
                    "passed": False,
                    "code": exc.code,
                    "status": exc.status,
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
    except (
        httpx.HTTPError,
        ValueError,
        KeyError,
        wave.Error,
        OSError,
        subprocess.CalledProcessError,
    ) as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
