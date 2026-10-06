"""有界、合成账号的完整 Agent SSE 基准与断线恢复检查，不批准任何业务写操作。

会真实调用模型并生成测试会话/台账，可能计费。默认仅输出计划，不发请求。
不记录身份、问题或答案；模型链路基准不能代替本脚本的端到端观测。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

QUESTION = (
    "健身知识问答：解释热身与正式训练的区别。只回答知识，不修改训练计划、不执行任何业务操作。"
)


@dataclass(frozen=True)
class BenchmarkConfig:
    endpoint: str
    context: str = field(repr=False)
    samples: int = 3
    concurrency: int = 1
    timeout: float = 120
    disconnect_sample: bool = False


def validate(config: BenchmarkConfig) -> None:
    url = urlparse(config.endpoint)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ValueError("目标地址必须为 HTTPS 或本机 HTTP，不接受凭证、路径和查询参数")
    if not 1 <= config.samples <= 20 or not 1 <= config.concurrency <= min(4, config.samples):
        raise ValueError("samples 范围为 1..20，concurrency 范围为 1..4 且不超过 samples")
    if not 5 <= config.timeout <= 180:
        raise ValueError("timeout 范围为 5..180 秒")


async def measure(
    client: httpx.AsyncClient, config: BenchmarkConfig, *, disconnect: bool
) -> dict[str, Any]:
    run_id, conversation = str(uuid4()), "e2e-" + uuid4().hex
    request_id = str(uuid4())
    headers = {
        "X-Agent-Context": config.context,
        "Accept": "text/event-stream",
        "X-Request-ID": request_id,
        "X-Trace-ID": request_id,
    }
    started = perf_counter()
    first: float | None = None
    result: dict[str, Any] | None = None
    failure: str | None = None
    disconnected = False
    try:
        async with asyncio.timeout(config.timeout):
            async with client.stream(
                "POST",
                config.endpoint.rstrip("/") + "/api/v1/agent/chat/stream",
                headers=headers,
                json={
                    "conversation_id": conversation,
                    "run_id": run_id,
                    "message": QUESTION,
                    "locale": "zh-CN",
                },
            ) as response:
                if response.status_code != 200:
                    raise ValueError("http_" + str(response.status_code))
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ValueError("unexpected_content_type")
                data_lines: list[str] = []
                event_name, frame_bytes, total_bytes, sequence = "", 0, 0, -1
                async for line in response.aiter_lines():
                    frame_bytes += len(line.encode())
                    total_bytes += len(line.encode())
                    if frame_bytes > 131072 or total_bytes > 1048576:
                        raise ValueError("stream_size_limit")
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                    elif not line:
                        if not data_lines:
                            frame_bytes = 0
                            continue
                        envelope = json.loads("\n".join(data_lines))
                        if not isinstance(envelope, dict):
                            raise TypeError("invalid_stream_envelope")
                        if envelope["run_id"] != run_id or envelope["event"] != event_name:
                            raise ValueError("stream_identity_mismatch")
                        # 服务端的初始帧与持久化终态快照使用sequence=0；
                        # 它们不是要重放的增量事件。正常delta仍必须严格递增。
                        current_sequence = envelope["sequence"]
                        snapshot = current_sequence == 0 and event_name in {
                            "run.completed",
                            "run.failed",
                        }
                        if (
                            isinstance(current_sequence, bool)
                            or not isinstance(current_sequence, int)
                            or current_sequence < 0
                            or (current_sequence <= sequence and not snapshot)
                        ):
                            raise ValueError("stream_sequence_mismatch")
                        sequence = max(sequence, current_sequence)
                        data = envelope["data"]
                        if not isinstance(data, dict):
                            raise TypeError("invalid_stream_data")
                        if (
                            event_name == "answer.delta"
                            and isinstance(data.get("text"), str)
                            and data["text"].strip()
                            and first is None
                        ):
                            first = perf_counter() - started
                        if event_name == "run.completed":
                            result = data["response"]
                            break
                        if event_name == "run.failed":
                            raise ValueError("run_failed")
                        if disconnect or event_name == "stream.reset":
                            disconnected = True
                            break
                        data_lines, event_name, frame_bytes = [], "", 0
            # 断线只查同一 run_id，绝不重发 POST，也不批准确认单。
            while result is None:
                recovered = await client.get(
                    config.endpoint.rstrip("/") + "/api/v1/agent/runs/" + run_id, headers=headers
                )
                if recovered.status_code != 200:
                    raise ValueError("recovery_http_" + str(recovered.status_code))
                stored = recovered.json()
                if not isinstance(stored, dict):
                    raise TypeError("invalid_recovery_response")
                if stored["status"] == "COMPLETED":
                    result = stored["response"]
                    break
                if stored["status"] in {"FAILED", "UNKNOWN"}:
                    raise ValueError("recovery_" + stored["status"].lower())
                await asyncio.sleep(1)
            if (
                not isinstance(result, dict)
                or result.get("status", "COMPLETED") != "COMPLETED"
                or result.get("confirmation_id")
            ):
                raise ValueError("unexpected_confirmation")
            if not isinstance(result.get("answer"), str) or not result["answer"].strip():
                raise ValueError("empty_answer")
    except (TimeoutError, httpx.HTTPError):
        failure = "timeout_or_network_error"
    except (ValueError, TypeError, KeyError):
        # 不输出服务响应、答案、凭证或底层异常信息。
        failure = "http_or_protocol_or_run_failure"
    usage = result.get("request_usage") if result else None
    usage_fields = (
        "model_calls",
        "usage_complete",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
        "currency",
    )
    return {
        "passed": failure is None,
        "run_id": run_id,
        "request_id": request_id,
        "ttft_seconds": first,
        "total_seconds": perf_counter() - started,
        "disconnected": disconnected,
        "recovered": disconnected and failure is None,
        "request_usage": {key: usage.get(key) for key in usage_fields}
        if isinstance(usage, dict)
        else None,
        "error": failure,
    }


async def benchmark(
    config: BenchmarkConfig, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    validate(config)
    semaphore = asyncio.Semaphore(config.concurrency)

    async def sample(index: int, http: httpx.AsyncClient) -> dict[str, Any]:
        async with semaphore:
            return await measure(
                http, config, disconnect=config.disconnect_sample and index == config.samples - 1
            )

    if client is None:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as http:
            rows = await asyncio.gather(*(sample(index, http) for index in range(config.samples)))
    else:
        rows = await asyncio.gather(*(sample(index, client) for index in range(config.samples)))
    return {
        "mode": "live_agent_sse_end_to_end_synthetic_account",
        "passed": all(row["passed"] for row in rows),
        "samples": config.samples,
        "concurrency": config.concurrency,
        "results": rows,
        "production_load_evidence": False,
        "interpretation": "完整 Agent 路由与工具链观测；小样本不可当作 P95 SLA，未测试写入吞吐或真实用户规模",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8090")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--disconnect-sample", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--synthetic-test-account", action="store_true", help="确认已使用专用测试身份与测试环境"
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    config = BenchmarkConfig(
        args.endpoint, "", args.samples, args.concurrency, args.timeout, args.disconnect_sample
    )
    try:
        validate(config)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "executed": False,
                        "samples_planned": args.samples,
                        "concurrency": args.concurrency,
                        "requires_synthetic_identity": True,
                    }
                )
            )
            return 0
        context = os.getenv("AGENT_LIVE_AGENT_CONTEXT", "").strip()
        if not args.synthetic_test_account or not context:
            raise ValueError("必须确认测试账号并注入 AGENT_LIVE_AGENT_CONTEXT")
        config = BenchmarkConfig(
            args.endpoint,
            context,
            args.samples,
            args.concurrency,
            args.timeout,
            args.disconnect_sample,
        )
        report = asyncio.run(benchmark(config))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"passed": report["passed"], "executed": True, "samples": args.samples}))
        return 0 if report["passed"] else 1
    except (ValueError, TypeError, OSError):
        print(
            json.dumps(
                {"passed": False, "error": "参数、测试身份或输出路径未满足验收契约"},
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
