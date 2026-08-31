"""执行客服工单真实写入验收，并在结束时精确清理测试数据。

该脚本与只读的 ``customer_service_live_check.py`` 分开，避免把“验证确认单”误当成
“允许写入”。真实写入必须同时满足以下条件：

* 命令行明确传入 ``--execute``；
* 环境变量 ``CUSTOMER_SERVICE_LIVE_ALLOW_WRITE=1``；
* 环境变量 ``CUSTOMER_SERVICE_LIVE_CLEANUP=1``；
* Agent 地址是本机回环地址；
* MySQL 使用显式凭证，并且脚本结束时能够通过宿主机客户端或指定容器执行清理。

验收只创建一条带随机 ``request_id`` 的客服工单，验证 Agent 路由、LangGraph
``interrupt()`` 确认、Gateway、客服服务和 MySQL 事实。清理 SQL 只使用本轮生成的
``request_id``，并在清理前检查工单、确认消费和审计记录数量，防止误删已有数据。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from urllib.parse import urlparse
from uuid import uuid4

import httpx

try:
    from business_live_check_support import (
        BusinessLiveCheckError,
        _decide_confirmation,
        _get_confirmation,
        _post_chat,
        _validate_confirmation_response,
        require_context,
    )
except ModuleNotFoundError:  # 从项目根目录运行 pytest 时使用包路径。
    from scripts.business_live_check_support import (
        BusinessLiveCheckError,
        _decide_confirmation,
        _get_confirmation,
        _post_chat,
        _validate_confirmation_response,
        require_context,
    )


class CustomerServiceWriteLiveCheckError(RuntimeError):
    """客服工单真实写入验收未达到安全或业务预期。"""


@dataclass(frozen=True)
class WriteCheckConfig:
    """一轮客服工单受控写入验收所需的配置。"""

    endpoint: str
    context: str
    timeout_seconds: float
    poll_timeout_seconds: float
    mysql_host: str
    mysql_port: str
    mysql_database: str
    mysql_username: str
    mysql_password: str
    mysql_container: str
    message: str = ""
    expected_route: str = "CUSTOMER_SERVICE"
    expected_action_prefix: tuple[str, ...] = ("CREATE_CUSTOMER_SERVICE_TICKET",)


@dataclass(frozen=True)
class TicketFact:
    """从 MySQL 读取的最小业务事实，不保留工单描述全文。"""

    ticket_id: str
    organization_id: str
    subject_user_id: str
    source: str
    status: str
    subject: str
    description: str
    ticket_count: int
    consumption_count: int
    audit_count: int


def build_parser() -> argparse.ArgumentParser:
    """构造真实写入验收参数；默认不允许执行。"""

    parser = argparse.ArgumentParser(description="客服工单受控写入与精确清理验收")
    parser.add_argument(
        "--endpoint",
        default=os.getenv("AGENT_LIVE_API_URL", "http://127.0.0.1:8090"),
        help="Agent 服务地址；真实写入只允许本机回环地址",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_TIMEOUT_SECONDS", "90")),
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=float,
        default=float(os.getenv("AGENT_LIVE_POLL_TIMEOUT_SECONDS", "60")),
    )
    parser.add_argument("--mysql-host", default=os.getenv("GATEWAY_DB_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", default=os.getenv("GATEWAY_DB_PORT", "3307"))
    parser.add_argument("--mysql-database", default=os.getenv("GATEWAY_DB_NAME", "fitness"))
    parser.add_argument("--mysql-username", default=os.getenv("GATEWAY_DB_USERNAME", ""))
    parser.add_argument("--mysql-password", default=os.getenv("GATEWAY_DB_PASSWORD", ""))
    parser.add_argument(
        "--mysql-container",
        default=os.getenv("GATEWAY_DB_CLEANUP_CONTAINER", ""),
        help="可选：通过 Docker 容器中的 mysql 客户端执行清理",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="显式执行真实客服工单写入；仍需两个环境变量安全开关",
    )
    return parser


def _is_loopback_endpoint(endpoint: str) -> bool:
    """只允许本地 Agent，防止把测试请求发送到生产或共享环境。"""

    parsed = urlparse(endpoint)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def validate_write_guard(args: argparse.Namespace) -> None:
    """执行写入前进行不可绕过的环境和清理能力检查。"""

    if not args.execute:
        raise CustomerServiceWriteLiveCheckError(
            "客服工单真实验收默认禁止写入；需要显式传入 --execute"
        )
    if os.getenv("CUSTOMER_SERVICE_LIVE_ALLOW_WRITE") != "1":
        raise CustomerServiceWriteLiveCheckError(
            "需要设置 CUSTOMER_SERVICE_LIVE_ALLOW_WRITE=1 才能执行客服工单写入验收"
        )
    if os.getenv("CUSTOMER_SERVICE_LIVE_CLEANUP") != "1":
        raise CustomerServiceWriteLiveCheckError(
            "需要设置 CUSTOMER_SERVICE_LIVE_CLEANUP=1；没有清理开关不能创建测试工单"
        )
    if not _is_loopback_endpoint(str(args.endpoint).rstrip("/")):
        raise CustomerServiceWriteLiveCheckError(
            "客服工单写入验收只允许访问本机 Agent 地址，拒绝非回环地址"
        )
    if not str(args.mysql_username).strip() or not str(args.mysql_password):
        raise CustomerServiceWriteLiveCheckError("缺少 MySQL 凭证，无法保证验收后精确清理")
    if not str(args.mysql_container).strip() and not str(args.mysql_host).strip():
        raise CustomerServiceWriteLiveCheckError("未提供 MySQL 主机或 Docker 容器")
    if args.timeout_seconds <= 0 or args.poll_timeout_seconds <= 0:
        raise CustomerServiceWriteLiveCheckError("timeout 参数必须大于 0")


def build_config(args: argparse.Namespace) -> WriteCheckConfig:
    """读取受控验收配置，并在构造配置前执行写入安全门禁。"""

    validate_write_guard(args)
    return WriteCheckConfig(
        endpoint=str(args.endpoint).rstrip("/"),
        context=require_context(),
        timeout_seconds=float(args.timeout_seconds),
        poll_timeout_seconds=float(args.poll_timeout_seconds),
        mysql_host=str(args.mysql_host),
        mysql_port=str(args.mysql_port),
        mysql_database=str(args.mysql_database),
        mysql_username=str(args.mysql_username),
        mysql_password=str(args.mysql_password),
        mysql_container=str(args.mysql_container).strip(),
    )


def _safe_sql_literal(value: str) -> str:
    """把脚本生成的 ID 转成 SQL 字符串；不接受换行和单引号。"""

    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise CustomerServiceWriteLiveCheckError("测试 request_id 不符合安全清理格式")
    return "'" + value + "'"


def build_cleanup_sql(request_id: str) -> str:
    """生成只按本轮 request_id 清理的 SQL，顺序与业务审计依赖相反。"""

    request_literal = _safe_sql_literal(request_id)
    return f"""
START TRANSACTION;
DELETE FROM agent_customer_service_ticket_audit WHERE request_id = {request_literal};
DELETE FROM agent_customer_service_confirmation_consumption WHERE request_id = {request_literal};
DELETE FROM agent_customer_service_ticket WHERE create_request_id = {request_literal};
COMMIT;
"""


def _mysql_command(config: WriteCheckConfig) -> list[str]:
    """构造 MySQL 客户端命令；密码通过环境变量或容器环境传入，不进入参数列表。"""

    command = []
    if config.mysql_container:
        command.extend(
            [
                "docker",
                "exec",
                "-i",
                "-e",
                f"MYSQL_PWD={config.mysql_password}",
                config.mysql_container,
                "mysql",
            ]
        )
    else:
        command.append("mysql")
    connection_args = [
        "--protocol=tcp",
        "--default-character-set=utf8mb4",
        "-u",
        config.mysql_username,
        config.mysql_database,
        "--batch",
        "--skip-column-names",
    ]
    if not config.mysql_container:
        connection_args[2:2] = ["-h", config.mysql_host, "-P", config.mysql_port]
    command.extend(connection_args)
    return command


def _run_mysql(config: WriteCheckConfig, sql: str) -> str:
    """通过宿主机 mysql 或指定 fitness-mysql 容器执行 SQL，不打印 SQL 结果中的正文。"""

    environment = {**os.environ, "MYSQL_PWD": config.mysql_password}
    result = subprocess.run(
        _mysql_command(config),
        input=sql,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise CustomerServiceWriteLiveCheckError(
            "MySQL 验收查询或清理失败；请检查 fitness-mysql、账号和端口"
        )
    return result.stdout.strip()


def _ensure_request_id_is_new(config: WriteCheckConfig, request_id: str) -> None:
    """写入前确认随机幂等键没有碰撞，避免清理时误触已有业务记录。"""

    result = _run_mysql(
        config,
        "SELECT COUNT(1) FROM agent_customer_service_ticket "
        f"WHERE create_request_id = {_safe_sql_literal(request_id)};",
    )
    if result != "0":
        raise CustomerServiceWriteLiveCheckError("随机 request_id 已存在，拒绝继续写入")


def _read_ticket_fact(config: WriteCheckConfig, request_id: str) -> TicketFact:
    """按精确 request_id 读取工单、确认消费和审计事实。"""

    request_literal = _safe_sql_literal(request_id)
    sql = f"""
SELECT
  HEX(t.id), HEX(t.organization_id), HEX(t.subject_user_id), HEX(t.source), HEX(t.status),
  HEX(t.subject), HEX(t.description),
  (SELECT COUNT(1) FROM agent_customer_service_ticket WHERE create_request_id = {request_literal}),
  (SELECT COUNT(1) FROM agent_customer_service_confirmation_consumption WHERE request_id = {request_literal}),
  (SELECT COUNT(1) FROM agent_customer_service_ticket_audit WHERE request_id = {request_literal})
FROM agent_customer_service_ticket t
WHERE t.create_request_id = {request_literal};
"""
    output = _run_mysql(config, sql)
    rows = [line.split("\t") for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 10:
        raise CustomerServiceWriteLiveCheckError(
            f"按 request_id 未读取到唯一客服工单，实际记录数={len(rows)}"
        )
    row = rows[0]

    def decode(index: int) -> str:
        try:
            return bytes.fromhex(row[index]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise CustomerServiceWriteLiveCheckError("客服工单字段不是有效 UTF-8") from exc

    return TicketFact(
        ticket_id=decode(0),
        organization_id=decode(1),
        subject_user_id=decode(2),
        source=decode(3),
        status=decode(4),
        subject=decode(5),
        description=decode(6),
        ticket_count=int(row[7]),
        consumption_count=int(row[8]),
        audit_count=int(row[9]),
    )


def _validate_ticket_fact(fact: TicketFact, marker: str) -> None:
    """验证业务事实而不是只看 Agent HTTP 200。"""

    if fact.ticket_count != 1 or fact.consumption_count != 1 or fact.audit_count != 1:
        raise CustomerServiceWriteLiveCheckError(
            "工单、确认消费和 CREATED 审计没有各生成一条，幂等/事务闭环不完整"
        )
    if fact.source != "AGENT" or fact.status != "OPEN":
        raise CustomerServiceWriteLiveCheckError(
            f"工单来源或初始状态不正确：source={fact.source!r}, status={fact.status!r}"
        )
    if marker not in fact.subject and marker not in fact.description:
        raise CustomerServiceWriteLiveCheckError(
            "中文测试标识未写入工单，无法证明内容编码和透传正确"
        )


def _cleanup(config: WriteCheckConfig, request_id: str) -> None:
    """只清理当前脚本生成的 request_id，绝不按机构、状态或前缀删除。"""

    _run_mysql(config, build_cleanup_sql(request_id))
    remaining = _run_mysql(
        config,
        "SELECT COUNT(1) FROM agent_customer_service_ticket "
        f"WHERE create_request_id = {_safe_sql_literal(request_id)};",
    )
    if remaining != "0":
        raise CustomerServiceWriteLiveCheckError("精确清理后仍有测试工单残留")


async def run_check(config: WriteCheckConfig) -> None:
    """执行确认、真实写入、事实校验和 finally 精确清理。"""

    request_id = f"customer-service-live-check-{uuid4().hex}"
    conversation_id = f"customer-service-live-conversation-{uuid4().hex}"
    marker = f"[CUSTOMER_SERVICE_LIVE_FIXTURE_{uuid4().hex[:12]}]"
    message = (
        f"请提交客服工单，标题和描述必须保留测试标识 {marker}。"
        "反馈预约状态异常：我昨天预约了私教课，页面显示状态不一致，请客服核查。"
    )
    request_config = replace(config, message=message)
    _ensure_request_id_is_new(config, request_id)
    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await _post_chat(
                client,
                request_config,
                conversation_id=conversation_id,
                request_id=request_id,
            )
            confirmation_id, operation = _validate_confirmation_response(
                response,
                request_config,
            )
            current = await _get_confirmation(client, request_config, confirmation_id)
            if current.get("authorization_status") != "PENDING":
                raise CustomerServiceWriteLiveCheckError("客服确认单初始状态不是 PENDING")
            approved = await _decide_confirmation(
                client,
                request_config,
                confirmation_id,
                request_id,
                decision="APPROVE",
            )
            if approved.get("authorization_status") != "APPROVED":
                raise CustomerServiceWriteLiveCheckError("客服确认单没有进入 APPROVED")
            deadline = time.monotonic() + config.poll_timeout_seconds
            final = approved
            while time.monotonic() < deadline:
                final = await _get_confirmation(client, request_config, confirmation_id)
                if final.get("execution_status") in {
                    "SUCCEEDED",
                    "FAILED_FINAL",
                    "FAILED_RETRYABLE",
                }:
                    break
                await asyncio.sleep(min(1.0, max(0.1, config.poll_timeout_seconds / 20)))
            if final.get("execution_status") != "SUCCEEDED":
                raise CustomerServiceWriteLiveCheckError(
                    f"客服工单执行未成功，execution_status={final.get('execution_status')!r}"
                )
        fact = _read_ticket_fact(config, request_id)
        _validate_ticket_fact(fact, marker)
        print("客服真实写入验收通过")
        print(f"operation={operation} execution_status=SUCCEEDED")
        print(f"request_id={request_id} ticket_id={fact.ticket_id}")
        print("已验证：确认批准、AGENT/OPEN 工单、确认 JTI 消费、CREATED 审计、中文内容编码")
    finally:
        _cleanup(config, request_id)
        print(f"已按 request_id={request_id} 精确清理客服工单及审计数据")


def main() -> int:
    """命令行入口；错误日志不打印 Token、密码和客服描述全文。"""

    try:
        args = build_parser().parse_args()
        config = build_config(args)
        asyncio.run(run_check(config))
    except (CustomerServiceWriteLiveCheckError, BusinessLiveCheckError, ValueError) as exc:
        print(f"客服真实写入验收失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
