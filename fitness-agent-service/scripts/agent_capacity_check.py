"""执行 Agent HTTP 层容量基线检查。

本脚本只访问健康检查接口，不调用 DeepSeek、RAG、Java Gateway 或任何业务写接口；
它用于观察本地 Agent 进程在有限并发下的响应成功率和延迟。默认只发送一次请求，
必须显式传入 ``--execute`` 才会执行并发基线，避免把检查命令误当成压力工具。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections import Counter
from dataclasses import dataclass
from math import ceil

import httpx


class AgentCapacityCheckError(RuntimeError):
    """Agent HTTP 容量基线未达到预期。"""


@dataclass(frozen=True)
class CapacityCheckConfig:
    """容量基线参数；只允许健康检查路径，避免误调用业务接口。"""

    base_url: str
    path: str
    request_count: int
    concurrency: int
    timeout_seconds: float
    execute: bool


@dataclass(frozen=True)
class RequestResult:
    """单次健康检查的脱敏结果。"""

    status_code: int | None
    elapsed_ms: float
    error_type: str | None = None


def build_parser() -> argparse.ArgumentParser:
    """构造容量基线参数。"""

    parser = argparse.ArgumentParser(description="验收 Agent HTTP 层容量基线")
    parser.add_argument(
        "--agent-url",
        default=os.getenv("AGENT_CAPACITY_URL", "http://127.0.0.1:8090"),
        help="Agent 地址，默认读取 AGENT_CAPACITY_URL",
    )
    parser.add_argument(
        "--path",
        choices=("/health/live", "/health/ready"),
        default="/health/live",
        help="只允许健康检查路径，默认 /health/live",
    )
    parser.add_argument("--requests", type=int, default=100, help="请求数量，默认 100")
    parser.add_argument("--concurrency", type=int, default=20, help="并发数，默认 20")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="单请求超时时间，默认 5 秒",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="执行有限并发基线；默认只发送一次只读健康请求",
    )
    return parser


def build_config(args: argparse.Namespace) -> CapacityCheckConfig:
    """校验参数并限制本地基线规模，避免命令造成无界压力。"""

    base_url = str(args.agent_url).strip().rstrip("/")
    request_count = int(args.requests)
    concurrency = int(args.concurrency)
    timeout_seconds = float(args.timeout_seconds)
    if not base_url:
        raise AgentCapacityCheckError("Agent 地址不能为空")
    if request_count < 1 or request_count > 10_000:
        raise AgentCapacityCheckError("requests 必须在 1 到 10000 之间")
    if concurrency < 1 or concurrency > min(200, request_count):
        raise AgentCapacityCheckError("concurrency 必须在 1 到 requests 且不超过 200")
    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise AgentCapacityCheckError("timeout-seconds 必须在 0 到 120 秒之间")
    return CapacityCheckConfig(
        base_url=base_url,
        path=str(args.path),
        request_count=request_count,
        concurrency=concurrency,
        timeout_seconds=timeout_seconds,
        execute=bool(args.execute),
    )


def percentile(values: list[float], ratio: float) -> float:
    """计算最近秩百分位，避免小样本线性插值制造虚假的精度。"""

    if not values or not 0 < ratio <= 1:
        raise ValueError("百分位输入无效")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, ceil(len(ordered) * ratio) - 1))
    return ordered[index]


async def _request(
    client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore
) -> RequestResult:
    """发送单次健康检查，只返回状态码和耗时，不读取或输出响应正文。"""

    async with semaphore:
        started_at = time.perf_counter()
        try:
            response = await client.get(url)
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            return RequestResult(status_code=response.status_code, elapsed_ms=elapsed_ms)
        except httpx.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            return RequestResult(
                status_code=None,
                elapsed_ms=elapsed_ms,
                error_type=type(exc).__name__,
            )


async def run_check(config: CapacityCheckConfig) -> None:
    """执行单次前置检查或有限并发容量基线。"""

    url = config.base_url + config.path
    async with httpx.AsyncClient(
        timeout=config.timeout_seconds,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        if not config.execute:
            result = await _request(client, url, asyncio.Semaphore(1))
            if result.status_code is None:
                raise AgentCapacityCheckError(f"Agent 健康检查无法连接：{result.error_type}")
            print(f"Agent 容量基线前置检查通过：HTTP {result.status_code}")
            return

        semaphore = asyncio.Semaphore(config.concurrency)
        results = await asyncio.gather(
            *(_request(client, url, semaphore) for _ in range(config.request_count))
        )

    status_counts = Counter(
        str(result.status_code) if result.status_code is not None else "NETWORK_ERROR"
        for result in results
    )
    successful = [result.elapsed_ms for result in results if result.status_code == 200]
    if len(successful) != config.request_count:
        raise AgentCapacityCheckError(
            f"健康检查存在失败请求：success={len(successful)}, total={config.request_count}, "
            f"statuses={dict(sorted(status_counts.items()))}"
        )
    print("Agent HTTP 容量基线通过")
    print(
        f"path={config.path}; requests={config.request_count}; concurrency={config.concurrency}; "
        f"statuses={dict(sorted(status_counts.items()))}; "
        f"p50_ms={percentile(successful, 0.50):.1f}; "
        f"p95_ms={percentile(successful, 0.95):.1f}; max_ms={max(successful):.1f}"
    )


def main() -> int:
    """命令行入口；不打印健康接口响应正文。"""

    try:
        args = build_parser().parse_args()
        asyncio.run(run_check(build_config(args)))
    except (AgentCapacityCheckError, OSError, httpx.HTTPError) as exc:
        print(f"Agent HTTP 容量基线失败：{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
