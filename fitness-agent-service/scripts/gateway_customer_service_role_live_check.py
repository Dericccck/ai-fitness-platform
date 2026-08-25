"""执行客服工单管理员、教练和学员只读权限真实联调。

该脚本不调用 Agent/DeepSeek，也不创建、修改或删除客服工单。它直接调用 Java
Tool Gateway 的只读客服工单接口，使用本地开发签发器生成三个角色的短时
AgentContext，验证权限最终由 Gateway 和客服服务共同执行：

* 学员只能查询自己的工单；
* 教练只能查询自己的工单；
* 组织管理员可以查询机构内其他用户的工单；
* 任意角色访问授权机构之外的机构都必须被拒绝。

本脚本只适用于本机开发环境。它不替代认证服务，也不把本地 HMAC 签发器当成生产
认证方案；真实环境应由认证服务签发短时 AgentContext。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse
from uuid import uuid4

import httpx

try:
    from issue_dev_agent_context import DevContextIssuerError, issue_token
except ModuleNotFoundError:  # 从项目根目录运行 pytest 时使用包路径。
    from scripts.issue_dev_agent_context import DevContextIssuerError, issue_token


class CustomerServiceRoleLiveCheckError(RuntimeError):
    """客服工单角色只读联调失败。"""


@dataclass(frozen=True)
class RoleFixture:
    """一组只读联调所需的角色主体。"""

    name: str
    subject: str
    role: str


@dataclass(frozen=True)
class RoleProbe:
    """一个权限探针的预期结果，不保存响应正文。"""

    name: str
    actor: RoleFixture
    subject_user_id: str | None
    organization_id: str
    expected_status: int


def build_parser() -> argparse.ArgumentParser:
    """构造只读角色联调参数。"""

    parser = argparse.ArgumentParser(description="客服工单三角色只读权限真实联调")
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("AGENT_GATEWAY_BASE_URL", "http://127.0.0.1:8081"),
        help="Java Tool Gateway 地址，只读联调要求使用本机地址",
    )
    parser.add_argument(
        "--internal-token",
        default=os.getenv("GATEWAY_INTERNAL_SERVICE_TOKEN", ""),
        help="Agent 到 Gateway 的内部 Token",
    )
    parser.add_argument(
        "--context-signing-secret",
        default=os.getenv("GATEWAY_CONTEXT_SIGNING_SECRET", ""),
        help="本地开发 AgentContext 签名密钥",
    )
    parser.add_argument(
        "--organization-id",
        default=os.getenv(
            "CUSTOMER_SERVICE_LIVE_ORGANIZATION_ID",
            os.getenv("TRAINING_LIVE_ORGANIZATION_ID", ""),
        ),
    )
    parser.add_argument(
        "--student-id",
        default=os.getenv(
            "CUSTOMER_SERVICE_LIVE_STUDENT_ID", os.getenv("TRAINING_LIVE_STUDENT_ID", "")
        ),
    )
    parser.add_argument(
        "--coach-id",
        default=os.getenv(
            "CUSTOMER_SERVICE_LIVE_COACH_ID", os.getenv("TRAINING_LIVE_COACH_ID", "")
        ),
    )
    parser.add_argument(
        "--admin-id",
        default=os.getenv(
            "CUSTOMER_SERVICE_LIVE_ADMIN_ID", os.getenv("TRAINING_LIVE_ADMIN_ID", "")
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("GATEWAY_LIVE_TIMEOUT_SECONDS", "10")),
    )
    return parser


def _is_loopback_url(url: str) -> bool:
    """角色权限脚本只允许连接本机 Gateway，避免误测共享环境。"""

    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def validate_args(args: argparse.Namespace) -> None:
    """检查只读联调必需配置。"""

    if not _is_loopback_url(str(args.gateway_url).rstrip("/")):
        raise CustomerServiceRoleLiveCheckError("客服角色联调只允许本机 Gateway 地址")
    for name in ("internal_token", "context_signing_secret", "organization_id"):
        if not str(getattr(args, name)).strip():
            raise CustomerServiceRoleLiveCheckError(f"缺少必要配置：{name}")
    for name in ("student_id", "coach_id", "admin_id"):
        if not str(getattr(args, name)).strip():
            raise CustomerServiceRoleLiveCheckError(f"缺少角色测试主体：{name}")
    if os.getenv("FITNESS_DEV_CONTEXT_ISSUER") != "1":
        raise CustomerServiceRoleLiveCheckError(
            "只读角色联调必须显式设置 FITNESS_DEV_CONTEXT_ISSUER=1"
        )
    if args.timeout_seconds <= 0:
        raise CustomerServiceRoleLiveCheckError("timeout 参数必须大于 0")


def build_fixtures(args: argparse.Namespace) -> tuple[RoleFixture, ...]:
    """构造三种角色，不打印 Token。"""

    return (
        RoleFixture(str(args.admin_id), str(args.admin_id), "ORGANIZATION_ADMIN"),
        RoleFixture(str(args.coach_id), str(args.coach_id), "COACH"),
        RoleFixture(str(args.student_id), str(args.student_id), "STUDENT"),
    )


def build_probes(args: argparse.Namespace) -> tuple[RoleProbe, ...]:
    """定义权限矩阵；状态码是外部可观测契约，不依赖自然语言回答。"""

    fixtures = {fixture.role: fixture for fixture in build_fixtures(args)}
    organization_id = str(args.organization_id)
    student = fixtures["STUDENT"]
    coach = fixtures["COACH"]
    admin = fixtures["ORGANIZATION_ADMIN"]
    return (
        RoleProbe("student-own", student, student.subject, organization_id, 200),
        RoleProbe("student-other", student, coach.subject, organization_id, 403),
        RoleProbe("coach-own", coach, coach.subject, organization_id, 200),
        RoleProbe("coach-other", coach, student.subject, organization_id, 403),
        RoleProbe("admin-other", admin, student.subject, organization_id, 200),
        RoleProbe("student-outside-organization", student, None, "outside-org", 403),
    )


def _issue_context(args: argparse.Namespace, fixture: RoleFixture) -> str:
    """签发仅限本地的短时角色上下文；失败时不泄露密钥。"""

    try:
        return cast(
            str,
            issue_token(
                secret=str(args.context_signing_secret),
                subject=fixture.subject,
                organization_id=str(args.organization_id),
                role=fixture.role,
                ttl_seconds=300,
            ),
        )
    except DevContextIssuerError as exc:
        raise CustomerServiceRoleLiveCheckError("本地角色 AgentContext 签发失败") from exc


async def _run_probe(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    probe: RoleProbe,
) -> None:
    """调用 Gateway 只读接口并严格验证预期 HTTP 状态。"""

    context = _issue_context(args, probe.actor)
    request_id = f"customer-service-role-live-check-{uuid4().hex}"
    params: dict[str, str | int] = {
        "organizationId": probe.organization_id,
        "limit": 20,
    }
    if probe.subject_user_id is not None:
        params["subjectUserId"] = probe.subject_user_id
    try:
        response = await client.get(
            args.gateway_url.rstrip("/") + "/internal/agent-tools/v1/customer-service/tickets",
            headers={
                "X-Internal-Service-Token": str(args.internal_token),
                "X-Agent-Context": context,
                "X-Request-ID": request_id,
            },
            params=params,
        )
    except httpx.HTTPError as exc:
        raise CustomerServiceRoleLiveCheckError(f"{probe.name} 无法连接 Gateway") from exc
    if response.status_code != probe.expected_status:
        raise CustomerServiceRoleLiveCheckError(
            f"{probe.name} 预期 HTTP {probe.expected_status}，实际 HTTP {response.status_code}"
        )
    if probe.expected_status == 200:
        try:
            payload = response.json()
        except ValueError as exc:
            raise CustomerServiceRoleLiveCheckError(f"{probe.name} 成功响应不是 JSON") from exc
        if not isinstance(payload, list):
            raise CustomerServiceRoleLiveCheckError(f"{probe.name} 成功响应不是工单数组")


async def run_check(args: argparse.Namespace) -> None:
    """执行权限矩阵；所有请求都是 GET，不会写入客服数据库。"""

    validate_args(args)
    probes = build_probes(args)
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        for probe in probes:
            await _run_probe(client, args, probe)
            print(f"[通过] {probe.name}: HTTP {probe.expected_status}")
    print("Customer Service 三角色只读权限联调通过（本轮没有写入业务数据）")


def main() -> int:
    """命令行入口；不输出 Token、请求正文或客服工单描述。"""

    try:
        asyncio.run(run_check(build_parser().parse_args()))
    except (CustomerServiceRoleLiveCheckError, ValueError) as exc:
        print(f"Customer Service 角色联调失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
