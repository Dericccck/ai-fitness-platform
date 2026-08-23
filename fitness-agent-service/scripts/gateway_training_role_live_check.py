"""执行 Java Gateway 到训练服务的角色与双层认证真实验收。

该脚本只访问 Gateway 的训练计划读取接口，不创建计划、不批准确认、也不修改 MySQL。
角色上下文使用仅限本地的开发签发器生成，正式环境不得把这个脚本当成认证服务。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from scripts.issue_dev_agent_context import DevContextIssuerError, issue_token


class GatewayTrainingLiveCheckError(RuntimeError):
    """Gateway 训练角色验收未达到预期。"""


@dataclass(frozen=True)
class ProbeResult:
    """不包含 AgentContext、Token 或计划正文的验收结果。"""

    name: str
    passed: bool
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Java Gateway 训练角色和认证真实验收")
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("GATEWAY_LIVE_URL", "http://127.0.0.1:8081"),
        help="Gateway 地址，默认读取 GATEWAY_LIVE_URL",
    )
    parser.add_argument(
        "--internal-token",
        default=os.getenv("GATEWAY_INTERNAL_SERVICE_TOKEN", ""),
        help="Agent 调用 Gateway 的内部 Token",
    )
    parser.add_argument(
        "--context-signing-secret",
        default=os.getenv("GATEWAY_CONTEXT_SIGNING_SECRET", ""),
        help="本地开发签发器与 Gateway 共享的上下文签名密钥",
    )
    parser.add_argument(
        "--organization-id",
        default=os.getenv("TRAINING_LIVE_ORGANIZATION_ID", ""),
        help="本地验收机构 ID",
    )
    parser.add_argument(
        "--student-id",
        default=os.getenv("TRAINING_LIVE_STUDENT_ID", ""),
        help="本地验收学员 ID",
    )
    parser.add_argument(
        "--coach-id",
        default=os.getenv("TRAINING_LIVE_COACH_ID", ""),
        help="本地验收教练 ID",
    )
    parser.add_argument(
        "--draft-plan-id",
        default=os.getenv("TRAINING_LIVE_DRAFT_PLAN_ID", ""),
        help="本地 DRAFT 夹具计划 ID",
    )
    parser.add_argument(
        "--published-plan-id",
        default=os.getenv("TRAINING_LIVE_PUBLISHED_PLAN_ID", ""),
        help="本地 PUBLISHED 夹具计划 ID",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("GATEWAY_LIVE_TIMEOUT_SECONDS", "5")),
        help="单个 HTTP 请求超时时间",
    )
    return parser


def validate_visible(
    name: str, status_code: int, payload: Any, plan_id: str, expected_status: str
) -> ProbeResult:
    """同时校验计划 ID 和状态，避免只根据 Gateway HTTP 200 判定成功。"""

    if (
        status_code == 200
        and isinstance(payload, dict)
        and payload.get("id") == plan_id
        and payload.get("status") == expected_status
    ):
        return ProbeResult(name, True, f"Gateway 返回 {expected_status} 计划")
    return ProbeResult(name, False, f"预期 HTTP 200/{expected_status}，实际 HTTP {status_code}")


def validate_hidden(name: str, status_code: int, payload: Any) -> ProbeResult:
    """学员读取草案必须由 Gateway 统一转换为 403。"""

    if status_code == 403 and isinstance(payload, dict) and payload.get("code") == "FORBIDDEN":
        return ProbeResult(name, True, "Gateway 拒绝学员读取未发布计划")
    return ProbeResult(name, False, f"预期 HTTP 403，实际 HTTP {status_code}")


def validate_unauthorized(name: str, status_code: int, payload: Any) -> ProbeResult:
    """缺少 Agent 到 Gateway 的内部 Token 时，必须在 Gateway 入口失败。"""

    if status_code == 401 and isinstance(payload, dict) and payload.get("code") == "UNAUTHORIZED":
        return ProbeResult(name, True, "Gateway 拒绝缺少内部 Token 的请求")
    return ProbeResult(name, False, f"预期 HTTP 401，实际 HTTP {status_code}")


def validate_confirmation_required(name: str, status_code: int, payload: Any) -> ProbeResult:
    """写入口缺少确认凭证时必须在进入业务服务前拒绝。

    Spring MVC 可能在 Controller 参数绑定阶段返回 400，Gateway 安全过滤器也可能返回
    401；两者都表示请求没有进入训练服务写事务。这里故意不把该探针写成“请求成功后
    再查询数据库”，避免验收脚本为了验证安全边界而制造业务数据。
    """

    if status_code in {400, 401}:
        return ProbeResult(name, True, "Gateway 在缺少确认凭证时拒绝写入口")
    return ProbeResult(name, False, f"预期 HTTP 400/401，实际 HTTP {status_code}")


def validate_execution_list(name: str, status_code: int, payload: Any) -> ProbeResult:
    """执行记录接口必须返回数组，不能把未授权错误误判成空列表。"""

    if status_code == 200 and isinstance(payload, list):
        return ProbeResult(name, True, "Gateway 返回训练执行记录列表")
    return ProbeResult(name, False, f"预期 HTTP 200/数组，实际 HTTP {status_code}")


async def _request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> tuple[int, Any]:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise GatewayTrainingLiveCheckError("无法连接 Java Gateway") from exc
    try:
        return response.status_code, response.json()
    except ValueError as exc:
        raise GatewayTrainingLiveCheckError("Gateway 返回了非 JSON 响应") from exc


def _issue_context(secret: str, subject: str, organization_id: str, role: str) -> str:
    """只在显式本地开关下签发短时上下文，避免误把脚本带入生产流程。"""

    if os.getenv("FITNESS_DEV_CONTEXT_ISSUER") != "1":
        raise GatewayTrainingLiveCheckError(
            "必须设置 FITNESS_DEV_CONTEXT_ISSUER=1；该验收只允许使用本地开发签发器"
        )
    try:
        return issue_token(
            secret=secret,
            subject=subject,
            organization_id=organization_id,
            role=role,
            ttl_seconds=300,
        )
    except DevContextIssuerError as exc:
        raise GatewayTrainingLiveCheckError("本地 AgentContext 签发失败") from exc


async def run_check(args: argparse.Namespace) -> tuple[ProbeResult, ...]:
    required = {
        "GATEWAY_INTERNAL_SERVICE_TOKEN": args.internal_token,
        "GATEWAY_CONTEXT_SIGNING_SECRET": args.context_signing_secret,
        "TRAINING_LIVE_ORGANIZATION_ID": args.organization_id,
        "TRAINING_LIVE_STUDENT_ID": args.student_id,
        "TRAINING_LIVE_COACH_ID": args.coach_id,
        "TRAINING_LIVE_DRAFT_PLAN_ID": args.draft_plan_id,
        "TRAINING_LIVE_PUBLISHED_PLAN_ID": args.published_plan_id,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise GatewayTrainingLiveCheckError(f"缺少配置：{', '.join(missing)}")
    if args.timeout_seconds <= 0:
        raise GatewayTrainingLiveCheckError("--timeout-seconds 必须大于 0")

    contexts = {
        "admin": _issue_context(
            args.context_signing_secret,
            "local-role-fixture-admin",
            args.organization_id,
            "ORGANIZATION_ADMIN",
        ),
        "coach": _issue_context(
            args.context_signing_secret, args.coach_id, args.organization_id, "COACH"
        ),
        "student": _issue_context(
            args.context_signing_secret, args.student_id, args.organization_id, "STUDENT"
        ),
    }
    base_url = args.gateway_url.rstrip("/")
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        results: list[ProbeResult] = []

        # 内部 Token 是 Agent 到 Gateway 的第一层认证，不能只依赖用户上下文签名。
        unauthorized_status, unauthorized_payload = await _request_json(
            client,
            "GET",
            base_url + f"/internal/agent-tools/v1/training/plans/{args.published_plan_id}",
            headers={
                "X-Agent-Context": contexts["student"],
                "X-Request-ID": "gateway-training-live-missing-internal-token",
            },
        )
        results.append(
            validate_unauthorized(
                "gateway-internal-auth-denied", unauthorized_status, unauthorized_payload
            )
        )

        # 这是一个故意失败的 POST：缺少确认凭证时必须在 Gateway 入口终止，不能触发
        # submit-review，也不会消费 JTI 或修改训练计划状态，因此属于无写入安全探针。
        missing_confirmation_status, missing_confirmation_payload = await _request_json(
            client,
            "POST",
            base_url
            + f"/internal/agent-tools/v1/training/plans/{args.draft_plan_id}/submit-review",
            headers={
                "X-Internal-Service-Token": args.internal_token,
                "X-Agent-Context": contexts["coach"],
                "X-Request-ID": "gateway-training-live-missing-confirmation",
            },
        )
        results.append(
            validate_confirmation_required(
                "gateway-write-missing-confirmation-denied",
                missing_confirmation_status,
                missing_confirmation_payload,
            )
        )

        cases = (
            ("gateway-admin-read-draft", "admin", args.draft_plan_id, "DRAFT", True),
            ("gateway-coach-read-draft", "coach", args.draft_plan_id, "DRAFT", True),
            ("gateway-student-hide-draft", "student", args.draft_plan_id, "DRAFT", False),
            ("gateway-admin-read-published", "admin", args.published_plan_id, "PUBLISHED", True),
            ("gateway-coach-read-published", "coach", args.published_plan_id, "PUBLISHED", True),
            (
                "gateway-student-read-published",
                "student",
                args.published_plan_id,
                "PUBLISHED",
                True,
            ),
        )
        for name, context_name, plan_id, expected_status, should_be_visible in cases:
            status_code, payload = await _request_json(
                client,
                "GET",
                base_url + f"/internal/agent-tools/v1/training/plans/{plan_id}",
                headers={
                    "X-Internal-Service-Token": args.internal_token,
                    "X-Agent-Context": contexts[context_name],
                    "X-Request-ID": f"gateway-training-live-{name}",
                },
            )
            if should_be_visible:
                results.append(
                    validate_visible(name, status_code, payload, plan_id, expected_status)
                )
            else:
                results.append(validate_hidden(name, status_code, payload))

        execution_cases = (
            ("gateway-admin-list-published-executions", "admin", args.published_plan_id, True),
            ("gateway-coach-list-published-executions", "coach", args.published_plan_id, True),
            ("gateway-student-list-published-executions", "student", args.published_plan_id, True),
            ("gateway-student-hide-draft-executions", "student", args.draft_plan_id, False),
        )
        for name, context_name, plan_id, should_be_visible in execution_cases:
            status_code, payload = await _request_json(
                client,
                "GET",
                base_url + f"/internal/agent-tools/v1/training/plans/{plan_id}/executions",
                headers={
                    "X-Internal-Service-Token": args.internal_token,
                    "X-Agent-Context": contexts[context_name],
                    "X-Request-ID": f"gateway-training-live-{name}",
                },
            )
            if should_be_visible:
                results.append(validate_execution_list(name, status_code, payload))
            else:
                results.append(validate_hidden(name, status_code, payload))
    return tuple(results)


def main() -> int:
    try:
        results = asyncio.run(run_check(build_parser().parse_args()))
    except GatewayTrainingLiveCheckError as exc:
        print(f"Gateway 训练角色验收失败：{exc}", file=sys.stderr)
        return 1
    for result in results:
        state = "通过" if result.passed else "失败"
        print(f"[{state}] {result.name}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
