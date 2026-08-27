"""执行 Agent 发布/回滚后的只读验收。

本脚本不切换镜像、不修改环境变量、不执行数据库迁移，也不调用 DeepSeek 或业务接口；
它只检查服务版本标识、存活探针和就绪探针。部署平台完成灰度发布或回滚后，应使用
``--expected-version`` 指定目标版本，避免旧实例误接收流量。
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx


class ReleaseRollbackCheckError(RuntimeError):
    """发布/回滚后的只读验收失败。"""


@dataclass(frozen=True)
class ReleaseCheckConfig:
    """发布验收参数；不包含用户、机构或业务数据。"""

    agent_url: str
    expected_version: str | None
    expected_environment: str | None
    timeout_seconds: float


def build_parser() -> argparse.ArgumentParser:
    """构造发布验收参数。"""

    parser = argparse.ArgumentParser(description="验收 Agent 发布或回滚结果")
    parser.add_argument(
        "--agent-url",
        default=os.getenv("AGENT_RELEASE_URL", "http://127.0.0.1:8090"),
        help="Agent 地址，默认读取 AGENT_RELEASE_URL",
    )
    parser.add_argument(
        "--expected-version",
        default=os.getenv("AGENT_EXPECTED_VERSION", ""),
        help="期望版本；为空时只打印当前版本，不执行版本匹配",
    )
    parser.add_argument(
        "--expected-environment",
        default=os.getenv("AGENT_EXPECTED_ENVIRONMENT", ""),
        help="期望环境；为空时不执行环境匹配",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="每个探针的超时时间，默认 5 秒",
    )
    return parser


def build_config(args: argparse.Namespace) -> ReleaseCheckConfig:
    """校验发布验收参数，拒绝空地址和异常超时时间。"""

    agent_url = str(args.agent_url).strip().rstrip("/")
    expected_version = str(args.expected_version).strip() or None
    expected_environment = str(args.expected_environment).strip() or None
    timeout_seconds = float(args.timeout_seconds)
    if not agent_url:
        raise ReleaseRollbackCheckError("Agent 地址不能为空")
    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise ReleaseRollbackCheckError("timeout-seconds 必须在 0 到 120 秒之间")
    return ReleaseCheckConfig(agent_url, expected_version, expected_environment, timeout_seconds)


def _read_version(payload: Any) -> tuple[str, str, str]:
    """读取版本响应的固定字段，不接受缺失字段的实例进入发布验收通过状态。"""

    if not isinstance(payload, dict):
        raise ReleaseRollbackCheckError("版本接口返回格式异常")
    service = payload.get("service")
    version = payload.get("version")
    environment = payload.get("environment")
    if (
        not isinstance(service, str)
        or not service.strip()
        or not isinstance(version, str)
        or not version.strip()
        or not isinstance(environment, str)
        or not environment.strip()
    ):
        raise ReleaseRollbackCheckError("版本接口缺少 service、version 或 environment")
    return service, version, environment


async def run_check(config: ReleaseCheckConfig) -> None:
    """检查版本、存活和就绪状态；只返回稳定错误，不输出响应正文。"""

    async with httpx.AsyncClient(
        timeout=config.timeout_seconds,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        try:
            version_response = await client.get(config.agent_url + "/health/version")
            live_response = await client.get(config.agent_url + "/health/live")
            ready_response = await client.get(config.agent_url + "/health/ready")
        except httpx.HTTPError as exc:
            raise ReleaseRollbackCheckError("无法连接 Agent 发布实例") from exc

    if version_response.status_code != 200:
        raise ReleaseRollbackCheckError(f"版本接口返回 HTTP {version_response.status_code}")
    if live_response.status_code != 200:
        raise ReleaseRollbackCheckError(f"存活接口返回 HTTP {live_response.status_code}")
    if ready_response.status_code != 200:
        raise ReleaseRollbackCheckError(f"就绪接口返回 HTTP {ready_response.status_code}")

    try:
        service, version, environment = _read_version(version_response.json())
        live_payload = live_response.json()
        ready_payload = ready_response.json()
    except (ValueError, ReleaseRollbackCheckError) as exc:
        raise ReleaseRollbackCheckError("发布探针响应格式异常") from exc
    if live_payload.get("status") != "ok":
        raise ReleaseRollbackCheckError("存活接口状态不是 ok")
    if not isinstance(ready_payload, dict) or ready_payload.get("status") != "ready":
        raise ReleaseRollbackCheckError("就绪接口状态不是 ready")
    if config.expected_version is not None and version != config.expected_version:
        raise ReleaseRollbackCheckError(
            f"版本不符合预期：expected={config.expected_version}, actual={version}"
        )
    if config.expected_environment is not None and environment != config.expected_environment:
        raise ReleaseRollbackCheckError(
            f"环境不符合预期：expected={config.expected_environment}, actual={environment}"
        )
    print("Agent 发布/回滚只读验收通过")
    print(f"service={service}; version={version}; environment={environment}; ready=ok")


def main() -> int:
    """命令行入口；不会执行任何发布、回滚或业务写操作。"""

    try:
        args = build_parser().parse_args()
        asyncio.run(run_check(build_config(args)))
    except (ReleaseRollbackCheckError, OSError, httpx.HTTPError) as exc:
        print(f"Agent 发布/回滚验收失败：{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
