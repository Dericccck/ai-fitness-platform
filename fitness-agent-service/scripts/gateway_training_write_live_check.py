"""执行 Gateway 到训练服务的确认写入、幂等和 JTI 重放真实验收。

该脚本是本地联调工具，不是生产确认服务。它使用本地共享密钥模拟“确认服务已经签发
的短时确认凭证”，验证 Gateway 验签后是否只透传已验证声明、训练服务是否在业务事务
中消费 JTI，以及重复请求是否不会创建第二份训练计划。

脚本默认拒绝写入，必须显式设置 ``GATEWAY_LIVE_EXECUTE_WRITES=1``。写入的计划使用
固定前缀，验收结束后必须按脚本输出的明确计划 ID 清理。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

from scripts.issue_dev_agent_context import DevContextIssuerError, issue_token


class GatewayTrainingWriteLiveCheckError(RuntimeError):
    """Gateway 训练写入验收未达到预期。"""


@dataclass(frozen=True)
class ProbeResult:
    """不包含 Token、签名上下文或完整业务正文的验收结果。"""

    name: str
    passed: bool
    detail: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gateway 训练确认写入真实验收")
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
        help="本地开发 AgentContext 签名密钥",
    )
    parser.add_argument(
        "--confirmation-signing-secret",
        default=os.getenv("GATEWAY_CONFIRMATION_SIGNING_SECRET", ""),
        help="Gateway 确认凭证签名密钥",
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
        default=float(os.getenv("GATEWAY_LIVE_TIMEOUT_SECONDS", "10")),
        help="单个 HTTP 请求超时时间",
    )
    return parser


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_confirmation_token(
    *,
    secret: str,
    subject: str,
    organization_id: str,
    resource: str,
    request_id: str,
    jti: str,
    confirmation_id: str,
    payload_hash: str,
    expires_at: int,
) -> str:
    """生成与 Java Gateway v1 兼容的本地确认凭证。

    真实生产环境由 Agent 确认服务在用户批准并通过 ``interrupt()`` 恢复后调用同一契约；
    此处只为了隔离验证 Gateway 和训练服务的下游边界，不代表客户端可以自行签发 Token。
    """

    payload = {
        "sub": subject,
        "action": "CREATE_TRAINING_DRAFT",
        "resource": resource,
        "request_id": request_id,
        "tool_id": "fitness.training.plan.create_draft.v1",
        "organization_id": organization_id,
        "confirmation_id": confirmation_id,
        "payload_hash": payload_hash,
        "jti": jti,
        "exp": str(expires_at),
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{_encode_base64url(payload_bytes)}.{_encode_base64url(signature)}"


def validate_created(status_code: int, payload: Any) -> ProbeResult:
    """确认 Gateway 返回的是 Agent 来源的草案，而不是其他状态或来源。"""

    if (
        status_code == 200
        and isinstance(payload, dict)
        and isinstance(payload.get("id"), str)
        and payload.get("status") == "DRAFT"
        and payload.get("source") == "AGENT"
        and str(payload.get("title", "")).startswith("[GATEWAY_WRITE_FIXTURE]")
    ):
        return ProbeResult("gateway-create-draft", True, "Gateway 已创建 DRAFT 训练计划")
    return ProbeResult(
        "gateway-create-draft", False, f"预期 HTTP 200/DRAFT/AGENT，实际 HTTP {status_code}"
    )


def validate_idempotent(status_code: int, payload: Any, plan_id: str) -> ProbeResult:
    """同一请求 ID 重试必须返回同一计划，不能产生第二条业务事实。"""

    if status_code == 200 and isinstance(payload, dict) and payload.get("id") == plan_id:
        return ProbeResult("gateway-create-idempotent", True, "相同请求 ID 重试返回同一训练计划")
    return ProbeResult(
        "gateway-create-idempotent", False, f"幂等重试未返回原计划，实际 HTTP {status_code}"
    )


def validate_jti_replay(status_code: int, payload: Any) -> ProbeResult:
    """相同 JTI 换请求 ID 重放必须由训练服务拒绝。"""

    if status_code == 409 and isinstance(payload, dict) and payload.get("code") == "CONFLICT":
        return ProbeResult("gateway-jti-replay-denied", True, "重复 JTI 已被训练服务拒绝")
    return ProbeResult(
        "gateway-jti-replay-denied", False, f"预期 HTTP 409，实际 HTTP {status_code}"
    )


async def _request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> tuple[int, Any]:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise GatewayTrainingWriteLiveCheckError("无法连接 Java Gateway") from exc
    try:
        return response.status_code, response.json()
    except ValueError as exc:
        raise GatewayTrainingWriteLiveCheckError("Gateway 返回了非 JSON 响应") from exc


def _issue_agent_context(secret: str, subject: str, organization_id: str) -> str:
    if os.getenv("FITNESS_DEV_CONTEXT_ISSUER") != "1":
        raise GatewayTrainingWriteLiveCheckError(
            "必须设置 FITNESS_DEV_CONTEXT_ISSUER=1；该验收只允许使用本地开发签发器"
        )
    try:
        return issue_token(
            secret=secret,
            subject=subject,
            organization_id=organization_id,
            role="COACH",
            ttl_seconds=300,
        )
    except DevContextIssuerError as exc:
        raise GatewayTrainingWriteLiveCheckError("本地 AgentContext 签发失败") from exc


def _build_payload(organization_id: str, student_id: str, coach_id: str) -> dict[str, Any]:
    """固定夹具内容，确保验收不会把用户输入或模型输出当作测试数据。"""

    return {
        "organizationId": organization_id,
        "studentId": student_id,
        "coachId": coach_id,
        "title": "[GATEWAY_WRITE_FIXTURE] 确认写入验收草案",
        "goalType": "力量基础",
        "days": [
            {
                "dayNumber": 1,
                "title": "基础训练",
                "items": [
                    {
                        "exerciseName": "徒手深蹲",
                        "sortOrder": 1,
                        "sets": 3,
                        "reps": "8-10",
                        "restSeconds": 60,
                        "notes": "Gateway 确认写入验收夹具",
                    }
                ],
            }
        ],
    }


async def run_check(args: argparse.Namespace) -> tuple[ProbeResult, ...]:
    if os.getenv("GATEWAY_LIVE_EXECUTE_WRITES") != "1":
        raise GatewayTrainingWriteLiveCheckError(
            "默认禁止写入；确认只写入本地夹具时再设置 GATEWAY_LIVE_EXECUTE_WRITES=1"
        )
    required = {
        "GATEWAY_INTERNAL_SERVICE_TOKEN": args.internal_token,
        "GATEWAY_CONTEXT_SIGNING_SECRET": args.context_signing_secret,
        "GATEWAY_CONFIRMATION_SIGNING_SECRET": args.confirmation_signing_secret,
        "TRAINING_LIVE_ORGANIZATION_ID": args.organization_id,
        "TRAINING_LIVE_STUDENT_ID": args.student_id,
        "TRAINING_LIVE_COACH_ID": args.coach_id,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise GatewayTrainingWriteLiveCheckError(f"缺少配置：{', '.join(missing)}")
    if args.timeout_seconds <= 0:
        raise GatewayTrainingWriteLiveCheckError("--timeout-seconds 必须大于 0")

    organization_id = args.organization_id
    student_id = args.student_id
    coach_id = args.coach_id
    request_id = "gateway-training-write-fixture-create-20260823"
    jti = "gateway-training-write-fixture-jti"
    confirmation_id = "gateway-training-write-fixture-confirmation"
    resource = f"{organization_id}:{student_id}"
    payload = _build_payload(organization_id, student_id, coach_id)
    payload_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    now = int(time.time())
    context = _issue_agent_context(args.context_signing_secret, coach_id, organization_id)
    confirmation_token = issue_confirmation_token(
        secret=args.confirmation_signing_secret,
        subject=coach_id,
        organization_id=organization_id,
        resource=resource,
        request_id=request_id,
        jti=jti,
        confirmation_id=confirmation_id,
        payload_hash=payload_hash,
        expires_at=now + 120,
    )
    base_url = args.gateway_url.rstrip("/")
    endpoint = base_url + "/internal/agent-tools/v1/training/plans/drafts"
    headers = {
        "X-Internal-Service-Token": args.internal_token,
        "X-Agent-Context": context,
        "X-Request-ID": request_id,
        "X-Confirmation-Token": confirmation_token,
    }
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        create_status, create_payload = await _request_json(
            client, "POST", endpoint, headers=headers, json=payload
        )
        created_result = validate_created(create_status, create_payload)
        if not created_result.passed:
            return (created_result,)
        plan_id = str(create_payload["id"])
        print(f"created_plan_id={plan_id}")

        retry_status, retry_payload = await _request_json(
            client, "POST", endpoint, headers=headers, json=payload
        )
        idempotent_result = validate_idempotent(retry_status, retry_payload, plan_id)

        replay_request_id = "gateway-training-write-fixture-replay-20260823"
        replay_token = issue_confirmation_token(
            secret=args.confirmation_signing_secret,
            subject=coach_id,
            organization_id=organization_id,
            resource=resource,
            request_id=replay_request_id,
            jti=jti,
            confirmation_id="gateway-training-write-fixture-replay-confirmation",
            payload_hash=payload_hash,
            expires_at=now + 120,
        )
        replay_status, replay_payload = await _request_json(
            client,
            "POST",
            endpoint,
            headers={
                **headers,
                "X-Request-ID": replay_request_id,
                "X-Confirmation-Token": replay_token,
            },
            json=payload,
        )
        replay_result = validate_jti_replay(replay_status, replay_payload)
    return created_result, idempotent_result, replay_result


def main() -> int:
    try:
        results = asyncio.run(run_check(build_parser().parse_args()))
    except GatewayTrainingWriteLiveCheckError as exc:
        print(f"Gateway 训练确认写入验收失败：{exc}", file=sys.stderr)
        return 1
    for result in results:
        state = "通过" if result.passed else "失败"
        print(f"[{state}] {result.name}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
