"""执行 Gateway 到训练服务的完整角色工作流真实验收。

该脚本只允许本地受控夹具使用，默认拒绝所有写入。显式开启后，流程为：教练创建草案、
提交审核、审核通过、发布，再用管理员、负责教练和学员上下文验证发布后的可见性。

脚本在结束时只按本轮生成的精确计划 ID、请求 ID 和确认消费记录清理，不使用机构、学员或
状态条件批量删除。真实生产环境仍必须使用认证服务签发的 AgentContext 和确认凭证；这里的
本地 HMAC 签发器只用于隔离验证 Gateway/训练服务边界。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import aio_pika
import asyncpg  # type: ignore[import-untyped]
import httpx

from scripts.issue_dev_agent_context import DevContextIssuerError, issue_token


class GatewayTrainingWorkflowError(RuntimeError):
    """训练计划完整角色工作流验收失败。"""


@dataclass(frozen=True)
class ProbeResult:
    """不包含 Token、签名上下文或完整计划正文的验收结果。"""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class WorkflowRequest:
    """本轮工作流的请求标识，用于确认凭证绑定和精确清理。"""

    request_id: str
    jti: str
    confirmation_id: str


@dataclass(frozen=True)
class ProactiveEventObservation:
    """一条训练主动提醒事件及其下游通知状态摘要。"""

    event_id: str
    event_type: str
    status: str
    notification_outbox_count: int
    published_notification_count: int
    in_app_notification_count: int
    notification_recipient_ids: tuple[str, ...]
    in_app_recipient_ids: tuple[str, ...]


def validate_proactive_event(
    observation: ProactiveEventObservation, expected_recipient_id: str
) -> ProbeResult:
    """只有事件已处理且恰好生成一条已投递站内通知才算通过。"""

    if (
        observation.status == "PROCESSED"
        and observation.notification_outbox_count == 1
        and observation.published_notification_count == 1
        and observation.in_app_notification_count == 1
        and observation.notification_recipient_ids == (expected_recipient_id,)
        and observation.in_app_recipient_ids == (expected_recipient_id,)
    ):
        return ProbeResult(
            f"training-proactive-{observation.event_type.lower()}",
            True,
            "事件已进入 Inbox、通知 Outbox 和站内收件箱",
        )
    return ProbeResult(
        f"training-proactive-{observation.event_type.lower()}",
        False,
        "事件或下游通知尚未完成",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gateway 训练计划完整角色工作流真实验收")
    parser.add_argument(
        "--gateway-url", default=os.getenv("GATEWAY_LIVE_URL", "http://127.0.0.1:8081")
    )
    parser.add_argument("--internal-token", default=os.getenv("GATEWAY_INTERNAL_SERVICE_TOKEN", ""))
    parser.add_argument(
        "--context-signing-secret", default=os.getenv("GATEWAY_CONTEXT_SIGNING_SECRET", "")
    )
    parser.add_argument(
        "--confirmation-signing-secret",
        default=os.getenv("GATEWAY_CONFIRMATION_SIGNING_SECRET", ""),
    )
    parser.add_argument("--organization-id", default=os.getenv("TRAINING_LIVE_ORGANIZATION_ID", ""))
    parser.add_argument("--student-id", default=os.getenv("TRAINING_LIVE_STUDENT_ID", ""))
    parser.add_argument("--coach-id", default=os.getenv("TRAINING_LIVE_COACH_ID", ""))
    parser.add_argument(
        "--admin-id", default=os.getenv("TRAINING_LIVE_ADMIN_ID", "local-workflow-admin")
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("GATEWAY_LIVE_TIMEOUT_SECONDS", "10")),
    )
    parser.add_argument("--mysql-host", default=os.getenv("GATEWAY_DB_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", default=os.getenv("GATEWAY_DB_PORT", "3307"))
    parser.add_argument("--mysql-database", default=os.getenv("GATEWAY_DB_NAME", "fitness"))
    parser.add_argument("--mysql-username", default=os.getenv("GATEWAY_DB_USERNAME", ""))
    parser.add_argument("--mysql-password", default=os.getenv("GATEWAY_DB_PASSWORD", ""))
    parser.add_argument(
        "--mysql-container",
        default=os.getenv("GATEWAY_DB_CLEANUP_CONTAINER", ""),
        help="可选：通过 Docker 容器中的 mysql 客户端执行精确清理",
    )
    parser.add_argument(
        "--verify-proactive-chain",
        action="store_true",
        default=os.getenv("GATEWAY_TRAINING_VERIFY_PROACTIVE_CHAIN") == "1",
        help="发布后等待 Training -> RabbitMQ -> Agent 通知链路完成",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="只检查主动提醒依赖，不创建、审核或发布训练计划",
    )
    parser.add_argument(
        "--agent-database-url",
        default=os.getenv(
            "AGENT_DATABASE_URL",
            "postgresql+asyncpg://fitness_agent:fitness_agent@127.0.0.1:5433/fitness_agent",
        ),
        help="Agent PostgreSQL 地址，仅用于主动提醒链路验收",
    )
    parser.add_argument(
        "--agent-url",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址，仅用于主动提醒链路前置检查",
    )
    parser.add_argument(
        "--rabbitmq-url",
        default=os.getenv(
            "AGENT_PROACTIVE_RABBITMQ_URL",
            "amqp://fitness_agent:fitness_agent_secret@127.0.0.1:5672/",
        ),
        help="RabbitMQ 地址，仅用于主动提醒链路前置检查",
    )
    parser.add_argument(
        "--training-url",
        default=os.getenv("TRAINING_LIVE_URL", "http://127.0.0.1:8082"),
        help="Training Service 地址，仅用于主动提醒链路前置检查",
    )
    parser.add_argument(
        "--proactive-poll-timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_POLL_TIMEOUT_SECONDS", "90")),
        help="等待训练事件下游链路完成的最长时间",
    )
    return parser


def validate_transition(
    name: str, status_code: int, payload: Any, expected_plan_id: str, expected_status: str
) -> ProbeResult:
    """状态转换必须返回指定计划和目标状态，不能只根据 HTTP 200 判定成功。"""

    if (
        status_code == 200
        and isinstance(payload, dict)
        and payload.get("id") == expected_plan_id
        and payload.get("status") == expected_status
    ):
        return ProbeResult(name, True, f"计划已进入 {expected_status}")
    return ProbeResult(name, False, f"预期 HTTP 200/{expected_status}，实际 HTTP {status_code}")


def validate_student_hidden(name: str, status_code: int, payload: Any) -> ProbeResult:
    """学员在计划发布前读取必须由 Gateway 返回 403。"""

    if status_code == 403 and isinstance(payload, dict) and payload.get("code") == "FORBIDDEN":
        return ProbeResult(name, True, "学员不能读取未发布计划")
    return ProbeResult(name, False, f"预期 HTTP 403，实际 HTTP {status_code}")


def validate_visible(name: str, status_code: int, payload: Any, plan_id: str) -> ProbeResult:
    """发布后各角色读取到的必须是本轮创建的同一计划。"""

    if (
        status_code == 200
        and isinstance(payload, dict)
        and payload.get("id") == plan_id
        and payload.get("status") == "PUBLISHED"
    ):
        return ProbeResult(name, True, "角色可读取已发布计划")
    return ProbeResult(name, False, f"预期 HTTP 200/PUBLISHED，实际 HTTP {status_code}")


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _issue_confirmation(
    *,
    secret: str,
    subject: str,
    organization_id: str,
    tool_id: str,
    action: str,
    resource: str,
    request: WorkflowRequest,
    payload_hash: str,
) -> str:
    """签发与 Gateway v1 HMAC 兼容的本地确认凭证。"""

    payload = {
        "sub": subject,
        "action": action,
        "resource": resource,
        "request_id": request.request_id,
        "tool_id": tool_id,
        "organization_id": organization_id,
        "confirmation_id": request.confirmation_id,
        "payload_hash": payload_hash,
        "jti": request.jti,
        "exp": str(int(time.time()) + 120),
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return f"{_base64url(payload_bytes)}.{_base64url(signature)}"


def _issue_context(secret: str, subject: str, organization_id: str, role: str) -> str:
    if os.getenv("FITNESS_DEV_CONTEXT_ISSUER") != "1":
        raise GatewayTrainingWorkflowError(
            "必须设置 FITNESS_DEV_CONTEXT_ISSUER=1；完整工作流只允许使用本地开发签发器"
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
        raise GatewayTrainingWorkflowError("本地 AgentContext 签发失败") from exc


def _request(prefix: str, suffix: str) -> WorkflowRequest:
    value = uuid4().hex
    return WorkflowRequest(
        f"{prefix}-{value}", f"{suffix}-{value}", f"confirmation-{suffix}-{value}"
    )


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _draft_payload(organization_id: str, student_id: str, coach_id: str) -> dict[str, Any]:
    return {
        "organizationId": organization_id,
        "studentId": student_id,
        "coachId": coach_id,
        "title": "[GATEWAY_ROLE_WORKFLOW_FIXTURE] 训练角色闭环验收草案",
        "goalType": "力量基础",
        "days": [
            {
                "dayNumber": 1,
                "title": "基础力量训练",
                "items": [
                    {
                        "exerciseName": "徒手深蹲",
                        "sortOrder": 1,
                        "sets": 3,
                        "reps": "8-10",
                        "restSeconds": 60,
                        "notes": "仅用于本地 Gateway 角色闭环验收",
                    }
                ],
            }
        ],
    }


async def _request_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> tuple[int, Any]:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise GatewayTrainingWorkflowError("无法连接 Java Gateway") from exc
    try:
        return response.status_code, response.json()
    except ValueError as exc:
        raise GatewayTrainingWorkflowError("Gateway 返回了非 JSON 响应") from exc


def _headers(
    internal_token: str, context: str, request_id: str, confirmation: str | None = None
) -> dict[str, str]:
    headers = {
        "X-Internal-Service-Token": internal_token,
        "X-Agent-Context": context,
        "X-Request-ID": request_id,
    }
    if confirmation:
        headers["X-Confirmation-Token"] = confirmation
    return headers


def _cleanup(args: argparse.Namespace, plan_id: str, requests: tuple[WorkflowRequest, ...]) -> None:
    """只按本轮明确生成的计划 ID 和请求 ID 清理，不允许通配删除。"""

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", plan_id):
        raise GatewayTrainingWorkflowError("计划 ID 不符合安全清理格式，已停止清理")
    if not args.mysql_username.strip() or not args.mysql_password:
        raise GatewayTrainingWorkflowError("缺少 MySQL 清理凭证，无法安全完成验收收尾")
    request_ids = ", ".join(f"'{request.request_id}'" for request in requests)
    proactive_outbox_cleanup = (
        f"DELETE FROM agent_training_outbox WHERE aggregate_id = '{plan_id}';"
        if args.verify_proactive_chain
        else ""
    )
    sql = f"""
{proactive_outbox_cleanup}
DELETE FROM training_day_execution_audit WHERE plan_id = '{plan_id}';
DELETE FROM training_day_execution WHERE plan_id = '{plan_id}';
DELETE FROM training_plan_audit WHERE plan_id = '{plan_id}';
DELETE FROM training_confirmation_consumption WHERE request_id IN ({request_ids});
DELETE FROM training_plan_item WHERE day_id IN (SELECT id FROM training_plan_day WHERE plan_id = '{plan_id}');
DELETE FROM training_plan_day WHERE plan_id = '{plan_id}';
DELETE FROM training_plan WHERE id = '{plan_id}';
"""
    env = {**os.environ, "MYSQL_PWD": args.mysql_password}
    if args.mysql_container.strip():
        # 当前开发机没有宿主机 mysql CLI，因此允许显式指定已有的 fitness-mysql 容器。
        # 容器名来自本地配置，不接受计划 ID 或 SQL 片段，SQL 仍通过标准输入传入。
        command = [
            "docker",
            "exec",
            "-i",
            "-e",
            f"MYSQL_PWD={args.mysql_password}",
            args.mysql_container,
            "mysql",
        ]
    else:
        command = ["mysql"]
    # 宿主机客户端连接 Docker 映射端口（默认 3307），容器内客户端必须连接
    # MySQL 容器自己的 3306。两种连接路径不能复用同一组 host/port 参数，
    # 否则清理 SQL 会在业务流程成功后失败，留下本轮测试计划和 Outbox 记录。
    connection_args = [
        "--protocol=tcp",
        "--default-character-set=utf8mb4",
        "-u",
        args.mysql_username,
        args.mysql_database,
        "--batch",
        "--skip-column-names",
    ]
    if not args.mysql_container.strip():
        connection_args[2:2] = ["-h", args.mysql_host, "-P", args.mysql_port]
    command.extend(connection_args)
    result = subprocess.run(
        command,
        input=sql,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise GatewayTrainingWorkflowError("测试计划清理失败，请根据输出的明确计划 ID 手工核对")


async def _cleanup_agent_records(args: argparse.Namespace, plan_id: str) -> None:
    """删除本轮计划在 Agent 侧产生的事件和通知测试数据。

    Training MySQL 清理成功并不代表主动提醒链路没有副作用：事件 Inbox、通知 Outbox、
    投递尝试和站内收件箱位于独立的 Agent PostgreSQL。它们同样必须按本轮明确的聚合
    ID 精确清理，避免本地验收通知混入后续真实测试或前端收件箱。
    """

    database_url = args.agent_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM agent_in_app_notifications WHERE aggregate_id = $1", plan_id
            )
            await connection.execute(
                """
                DELETE FROM agent_notification_delivery_attempts
                WHERE outbox_id IN (
                    SELECT id FROM agent_notification_outbox WHERE aggregate_id = $1
                )
                """,
                plan_id,
            )
            await connection.execute(
                "DELETE FROM agent_notification_outbox WHERE aggregate_id = $1", plan_id
            )
            await connection.execute(
                """
                DELETE FROM agent_proactive_event_inbox
                WHERE aggregate_id = $1 AND source = 'training'
                """,
                plan_id,
            )
    finally:
        await connection.close()


async def _check_proactive_preflight(args: argparse.Namespace) -> None:
    """主动提醒验收前检查 Agent、Training、PostgreSQL 和 RabbitMQ。"""

    database_url = args.agent_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if not database_url.strip() or not args.rabbitmq_url.strip():
        raise GatewayTrainingWorkflowError("主动提醒验收缺少 Agent PostgreSQL 或 RabbitMQ 配置")
    if args.proactive_poll_timeout_seconds <= 0:
        raise GatewayTrainingWorkflowError("--proactive-poll-timeout-seconds 必须大于 0")
    async with httpx.AsyncClient(timeout=min(args.timeout_seconds, 10.0)) as client:
        for endpoint, path, label in (
            (args.agent_url.rstrip("/"), "/health/ready", "Agent"),
            (args.training_url.rstrip("/"), "/health", "Training Service"),
        ):
            try:
                response = await client.get(endpoint + path)
            except httpx.HTTPError as exc:
                raise GatewayTrainingWorkflowError(f"{label} 健康检查无法连接") from exc
            if response.status_code >= 400:
                raise GatewayTrainingWorkflowError(
                    f"{label} 健康检查返回 HTTP {response.status_code}"
                )
    connection = await asyncpg.connect(database_url)
    try:
        await connection.fetchval("SELECT 1")
        await connection.fetchval("SELECT 1 FROM agent_proactive_event_inbox LIMIT 1")
    finally:
        await connection.close()
    rabbit_connection = await aio_pika.connect_robust(args.rabbitmq_url)
    await rabbit_connection.close()


async def _probe_proactive_events(
    connection: asyncpg.Connection,
    *,
    organization_id: str,
    plan_id: str,
    started_at: datetime,
) -> tuple[ProactiveEventObservation, ...]:
    """读取本轮计划的两类事件和下游通知状态，不读取通知正文。"""

    rows = await connection.fetch(
        """
        SELECT event_id, event_type, status
        FROM agent_proactive_event_inbox
        WHERE organization_id = $1
          AND aggregate_id = $2
          AND source = 'training'
          AND event_type IN ('TRAINING_PLAN_REVIEW_REQUIRED', 'TRAINING_PLAN_PUBLISHED')
          AND created_at >= $3
        ORDER BY event_type, created_at, event_id
        """,
        organization_id,
        plan_id,
        started_at,
    )
    observations: list[ProactiveEventObservation] = []
    for row in rows:
        event_id = str(row["event_id"])
        notification_rows = await connection.fetch(
            "SELECT status, subject_user_id FROM agent_notification_outbox WHERE dedupe_key LIKE $1",
            f"proactive:{event_id}:%",
        )
        in_app_rows = await connection.fetch(
            "SELECT subject_user_id FROM agent_in_app_notifications WHERE dedupe_key LIKE $1",
            f"proactive:{event_id}:%",
        )
        observations.append(
            ProactiveEventObservation(
                event_id=event_id,
                event_type=str(row["event_type"]),
                status=str(row["status"]),
                notification_outbox_count=len(notification_rows),
                published_notification_count=sum(
                    item["status"] == "PUBLISHED" for item in notification_rows
                ),
                in_app_notification_count=len(in_app_rows),
                notification_recipient_ids=tuple(
                    sorted({str(item["subject_user_id"]) for item in notification_rows})
                ),
                in_app_recipient_ids=tuple(
                    sorted({str(item["subject_user_id"]) for item in in_app_rows})
                ),
            )
        )
    return tuple(observations)


async def _wait_for_proactive_chain(
    args: argparse.Namespace, *, organization_id: str, plan_id: str, started_at: datetime
) -> tuple[ProbeResult, ...]:
    """等待待审核和已发布两类事件都完成 Agent 通知链路。"""

    database_url = args.agent_database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    deadline = time.monotonic() + args.proactive_poll_timeout_seconds
    connection = await asyncpg.connect(database_url)
    last: tuple[ProactiveEventObservation, ...] = ()
    try:
        while time.monotonic() < deadline:
            last = await _probe_proactive_events(
                connection,
                organization_id=organization_id,
                plan_id=plan_id,
                started_at=started_at,
            )
            by_type = {observation.event_type: observation for observation in last}
            expected_types = {
                "TRAINING_PLAN_REVIEW_REQUIRED",
                "TRAINING_PLAN_PUBLISHED",
            }
            if expected_types.issubset(by_type) and all(
                validate_proactive_event(
                    by_type[event_type],
                    args.coach_id
                    if event_type == "TRAINING_PLAN_REVIEW_REQUIRED"
                    else args.student_id,
                ).passed
                for event_type in expected_types
            ):
                return tuple(
                    validate_proactive_event(
                        by_type[event_type],
                        args.coach_id
                        if event_type == "TRAINING_PLAN_REVIEW_REQUIRED"
                        else args.student_id,
                    )
                    for event_type in sorted(expected_types)
                )
            await asyncio.sleep(1)
    finally:
        await connection.close()
    if not last:
        raise GatewayTrainingWorkflowError("训练主动提醒验收超时，未收到 Training 事件")
    summary = "; ".join(
        f"{item.event_type}:status={item.status},outbox={item.notification_outbox_count},"
        f"published={item.published_notification_count},in_app={item.in_app_notification_count}"
        for item in last
    )
    raise GatewayTrainingWorkflowError(f"训练主动提醒链路未完成：{summary}")


async def run_check(args: argparse.Namespace) -> tuple[ProbeResult, ...]:
    if args.preflight_only:
        await _check_proactive_preflight(args)
        return (
            ProbeResult(
                "training-proactive-preflight", True, "Agent、Training、PostgreSQL、RabbitMQ 均可用"
            ),
        )
    if os.getenv("GATEWAY_LIVE_EXECUTE_WORKFLOW_WRITES") != "1":
        raise GatewayTrainingWorkflowError(
            "默认禁止工作流写入；确认本地夹具后设置 GATEWAY_LIVE_EXECUTE_WORKFLOW_WRITES=1"
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
        raise GatewayTrainingWorkflowError(f"缺少配置：{', '.join(missing)}")
    if args.timeout_seconds <= 0:
        raise GatewayTrainingWorkflowError("--timeout-seconds 必须大于 0")
    if args.verify_proactive_chain:
        try:
            await _check_proactive_preflight(args)
        except (asyncpg.PostgresError, aio_pika.exceptions.AMQPError) as exc:
            raise GatewayTrainingWorkflowError(
                "主动提醒 PostgreSQL 或 RabbitMQ 前置检查失败"
            ) from exc

    organization_id, student_id, coach_id = args.organization_id, args.student_id, args.coach_id
    contexts = {
        "admin": _issue_context(
            args.context_signing_secret, args.admin_id, organization_id, "ORGANIZATION_ADMIN"
        ),
        "coach": _issue_context(args.context_signing_secret, coach_id, organization_id, "COACH"),
        "student": _issue_context(
            args.context_signing_secret, student_id, organization_id, "STUDENT"
        ),
    }
    payload = _draft_payload(organization_id, student_id, coach_id)
    base_url = args.gateway_url.rstrip("/")
    plan_id: str | None = None
    workflow_requests: list[WorkflowRequest] = []
    results: list[ProbeResult] = []
    started_at = datetime.now(UTC)

    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        try:
            create_request = _request("gateway-role-workflow-create", "create")
            workflow_requests.append(create_request)
            create_token = _issue_confirmation(
                secret=args.confirmation_signing_secret,
                subject=coach_id,
                organization_id=organization_id,
                tool_id="fitness.training.plan.create_draft.v1",
                action="CREATE_TRAINING_DRAFT",
                resource=f"{organization_id}:{student_id}",
                request=create_request,
                payload_hash=_payload_hash(payload),
            )
            status_code, response_payload = await _request_json(
                client,
                "POST",
                base_url + "/internal/agent-tools/v1/training/plans/drafts",
                headers=_headers(
                    args.internal_token, contexts["coach"], create_request.request_id, create_token
                ),
                json=payload,
            )
            if not isinstance(response_payload, dict) or not isinstance(
                response_payload.get("id"), str
            ):
                raise GatewayTrainingWorkflowError(f"创建草案失败，实际 HTTP {status_code}")
            plan_id = response_payload["id"]
            results.append(
                validate_transition(
                    "gateway-workflow-create-draft", status_code, response_payload, plan_id, "DRAFT"
                )
            )

            submit_request = _request("gateway-role-workflow-submit", "submit")
            workflow_requests.append(submit_request)
            submit_token = _issue_confirmation(
                secret=args.confirmation_signing_secret,
                subject=coach_id,
                organization_id=organization_id,
                tool_id="fitness.training.plan.submit_review.v1",
                action="SUBMIT_TRAINING_REVIEW",
                resource=plan_id,
                request=submit_request,
                payload_hash=_payload_hash({}),
            )
            status_code, response_payload = await _request_json(
                client,
                "POST",
                base_url + f"/internal/agent-tools/v1/training/plans/{plan_id}/submit-review",
                headers=_headers(
                    args.internal_token, contexts["coach"], submit_request.request_id, submit_token
                ),
            )
            results.append(
                validate_transition(
                    "gateway-workflow-submit-review",
                    status_code,
                    response_payload,
                    plan_id,
                    "PENDING_REVIEW",
                )
            )

            status_code, response_payload = await _request_json(
                client,
                "GET",
                base_url + f"/internal/agent-tools/v1/training/plans/{plan_id}",
                headers=_headers(
                    args.internal_token,
                    contexts["student"],
                    "gateway-role-workflow-student-before-publish",
                ),
            )
            results.append(
                validate_student_hidden(
                    "gateway-workflow-student-before-publish", status_code, response_payload
                )
            )

            review_request = _request("gateway-role-workflow-review", "review")
            workflow_requests.append(review_request)
            review_payload = {"decision": "APPROVE", "comment": "本地角色闭环验收通过"}
            review_token = _issue_confirmation(
                secret=args.confirmation_signing_secret,
                subject=coach_id,
                organization_id=organization_id,
                tool_id="fitness.training.plan.review.v1",
                action="REVIEW_TRAINING_PLAN",
                resource=plan_id,
                request=review_request,
                payload_hash=_payload_hash(review_payload),
            )
            status_code, response_payload = await _request_json(
                client,
                "POST",
                base_url + f"/internal/agent-tools/v1/training/plans/{plan_id}/review",
                headers=_headers(
                    args.internal_token, contexts["coach"], review_request.request_id, review_token
                ),
                json=review_payload,
            )
            results.append(
                validate_transition(
                    "gateway-workflow-review", status_code, response_payload, plan_id, "APPROVED"
                )
            )

            publish_request = _request("gateway-role-workflow-publish", "publish")
            workflow_requests.append(publish_request)
            publish_token = _issue_confirmation(
                secret=args.confirmation_signing_secret,
                subject=coach_id,
                organization_id=organization_id,
                tool_id="fitness.training.plan.publish.v1",
                action="PUBLISH_TRAINING_PLAN",
                resource=plan_id,
                request=publish_request,
                payload_hash=_payload_hash({}),
            )
            status_code, response_payload = await _request_json(
                client,
                "POST",
                base_url + f"/internal/agent-tools/v1/training/plans/{plan_id}/publish",
                headers=_headers(
                    args.internal_token,
                    contexts["coach"],
                    publish_request.request_id,
                    publish_token,
                ),
            )
            results.append(
                validate_transition(
                    "gateway-workflow-publish", status_code, response_payload, plan_id, "PUBLISHED"
                )
            )

            for role_name in ("admin", "coach", "student"):
                status_code, response_payload = await _request_json(
                    client,
                    "GET",
                    base_url + f"/internal/agent-tools/v1/training/plans/{plan_id}",
                    headers=_headers(
                        args.internal_token,
                        contexts[role_name],
                        f"gateway-role-workflow-{role_name}-after-publish",
                    ),
                )
                results.append(
                    validate_visible(
                        f"gateway-workflow-{role_name}-after-publish",
                        status_code,
                        response_payload,
                        plan_id,
                    )
                )
            if args.verify_proactive_chain:
                results.extend(
                    await _wait_for_proactive_chain(
                        args,
                        organization_id=organization_id,
                        plan_id=plan_id,
                        started_at=started_at,
                    )
                )
        finally:
            if plan_id:
                print(f"workflow_plan_id={plan_id}")
                _cleanup(args, plan_id, tuple(workflow_requests))
                if args.verify_proactive_chain:
                    await _cleanup_agent_records(args, plan_id)
                print("workflow_cleanup=completed")

    return tuple(results)


def main() -> int:
    try:
        results = asyncio.run(run_check(build_parser().parse_args()))
    except (
        GatewayTrainingWorkflowError,
        asyncpg.PostgresError,
        aio_pika.exceptions.AMQPError,
        OSError,
        ValueError,
    ) as exc:
        print(f"Gateway 训练完整工作流验收失败：{exc}", file=sys.stderr)
        return 1
    for result in results:
        state = "通过" if result.passed else "失败"
        print(f"[{state}] {result.name}: {result.detail}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
