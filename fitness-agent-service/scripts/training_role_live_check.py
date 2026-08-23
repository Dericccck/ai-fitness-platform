"""执行训练服务角色边界的本地真实验收。

该脚本只检查训练服务健康状态，以及学员创建训练计划草案是否被下游拒绝。
它不会批准确认、不会创建草案，也不会修改训练数据库；管理员/教练的完整读取和
发布可见性验收需要先准备明确标记的本地训练计划夹具后再单独执行。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx


class TrainingRoleLiveCheckError(RuntimeError):
    """训练服务角色验收未达到预期。"""


@dataclass(frozen=True)
class ProbeResult:
    """不包含 Token 或业务正文的验收结果。"""

    name: str
    passed: bool
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="训练服务三角色边界真实验收")
    parser.add_argument(
        "--training-url",
        default=os.getenv("AGENT_TRAINING_SERVICE_URL", "http://127.0.0.1:8082"),
        help="训练服务地址，默认读取 AGENT_TRAINING_SERVICE_URL",
    )
    parser.add_argument(
        "--internal-token",
        default=os.getenv("TRAINING_INTERNAL_SERVICE_TOKEN", ""),
        help="训练服务内部 Token，建议通过环境变量 TRAINING_INTERNAL_SERVICE_TOKEN 提供",
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
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("TRAINING_LIVE_TIMEOUT_SECONDS", "5")),
        help="单个 HTTP 请求超时时间",
    )
    return parser


def validate_health(status_code: int, payload: Any) -> ProbeResult:
    """只接受训练服务自身约定的 ``{"status":"UP"}`` 健康响应。"""

    if status_code == 200 and isinstance(payload, dict) and payload.get("status") == "UP":
        return ProbeResult("training-health", True, "训练服务已启动")
    return ProbeResult("training-health", False, f"HTTP {status_code} 或健康状态异常")


def validate_student_create_denied(status_code: int, payload: Any) -> ProbeResult:
    """学员创建草案必须在训练服务业务写入前返回 403。"""

    if status_code == 403 and isinstance(payload, dict) and payload.get("code") == "FORBIDDEN":
        return ProbeResult("student-create-denied", True, "学员创建草案已被训练服务拒绝")
    return ProbeResult("student-create-denied", False, f"预期 HTTP 403，实际 HTTP {status_code}")


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> tuple[int, Any]:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise TrainingRoleLiveCheckError("无法连接训练服务") from exc
    try:
        return response.status_code, response.json()
    except ValueError as exc:
        raise TrainingRoleLiveCheckError("训练服务返回了非 JSON 响应") from exc


async def run_check(args: argparse.Namespace) -> tuple[ProbeResult, ...]:
    if not args.internal_token.strip():
        raise TrainingRoleLiveCheckError("缺少 TRAINING_INTERNAL_SERVICE_TOKEN")
    if not args.organization_id or not args.student_id or not args.coach_id:
        raise TrainingRoleLiveCheckError(
            "需要 TRAINING_LIVE_ORGANIZATION_ID、TRAINING_LIVE_STUDENT_ID 和 TRAINING_LIVE_COACH_ID"
        )
    if args.timeout_seconds <= 0:
        raise TrainingRoleLiveCheckError("--timeout-seconds 必须大于 0")

    base_url = args.training_url.rstrip("/")
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        health_status, health_payload = await _request_json(client, "GET", base_url + "/health")
        health_result = validate_health(health_status, health_payload)
        if not health_result.passed:
            return (health_result,)

        headers = {
            "X-Internal-Service-Token": args.internal_token,
            "X-Actor-User-Id": args.student_id,
            "X-Actor-Roles": "STUDENT",
            "X-Actor-Organization-Ids": args.organization_id,
            "X-Request-ID": "training-role-live-student-create-denied",
            "X-Confirmation-Id": "role-check-confirmation",
            "X-Confirmation-JTI": "role-check-jti",
            "X-Confirmation-Tool-ID": "fitness.training.plan.create_draft.v1",
            "X-Confirmation-Action": "CREATE_TRAINING_DRAFT",
            "X-Confirmation-Organization-ID": args.organization_id,
            "X-Confirmation-Resource": f"{args.organization_id}:{args.student_id}",
            "X-Confirmation-Payload-Hash": "a" * 64,
        }
        payload = {
            "organizationId": args.organization_id,
            "studentId": args.student_id,
            "coachId": args.coach_id,
            "title": "角色权限验收草案",
            "goalType": "力量",
            "days": [
                {
                    "dayNumber": 1,
                    "title": "基础训练",
                    "items": [
                        {
                            "exerciseName": "深蹲",
                            "sortOrder": 1,
                            "sets": 3,
                            "reps": "8-10",
                        }
                    ],
                }
            ],
        }
        denied_status, denied_payload = await _request_json(
            client,
            "POST",
            base_url + "/internal/training/v1/plans/drafts",
            headers=headers,
            json=payload,
        )
    return health_result, validate_student_create_denied(denied_status, denied_payload)


def main() -> int:
    try:
        results = asyncio.run(run_check(build_parser().parse_args()))
    except TrainingRoleLiveCheckError as exc:
        print(f"训练服务角色验收失败：{exc}", file=sys.stderr)
        return 1
    for result in results:
        state = "通过" if result.passed else "失败"
        print(f"[{state}] {result.name}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
