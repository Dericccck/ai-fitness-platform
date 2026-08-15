"""Operations Agent 查询审计的 PostgreSQL 持久化。

Operations 查询返回的是机构经营聚合数据，不能只依赖结构化日志：日志轮转、服务重启
或多实例采集异常时，管理员仍需要知道谁在什么机构、什么时间范围查询了哪个固定指标。
本仓储只保存审计元数据，不保存 SQL、Prompt、模型原始输出、预约明细或 Gateway 返回行。
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import uuid4

from sqlalchemy import text

from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database

OperationsAuditStatus = Literal["SUCCEEDED", "FAILED"]
OperationsAuditRole = Literal["CURRENT", "PREVIOUS_PERIOD"]

_METRICS = frozenset(
    {
        "APPOINTMENT_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
        "COURSE_APPOINTMENT_COUNT",
        "COACH_APPOINTMENT_COUNT",
        "REMAINING_CLASS_HOURS",
    }
)
_BUCKETS = frozenset({"NONE", "DAY", "WEEK"})
_ROLES = frozenset({"CURRENT", "PREVIOUS_PERIOD"})
_STATUSES = frozenset({"SUCCEEDED", "FAILED"})


class OperationsAuditValidationError(ValueError):
    """审计事件包含不在固定目录中的指标、桶、角色或状态。"""


class OperationsAuditPersistenceError(RuntimeError):
    """查询成功但审计无法持久化，调用方必须拒绝返回未审计结果。"""


class OperationsAuditRepository:
    """把 Operations 查询元数据追加写入 Agent PostgreSQL。

    审计是只追加事实，不允许根据模型文本自由拼接 SQL 或写入任意字段。生产环境
    由已验证的 ToolContext 提供主体和角色；没有身份的系统任务可以留下空主体，但
    仍必须保留机构、指标、查询范围和请求链路，便于后续排查。
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def record(
        self,
        *,
        identity: AgentIdentity | None,
        organization_id: str,
        metric: str,
        bucket: str,
        comparison_role: str,
        from_date: date | None,
        to_date: date | None,
        row_count: int | None,
        status: OperationsAuditStatus,
        error_code: str | None,
        request_id: str | None,
        trace_id: str | None,
    ) -> None:
        """追加一次查询审计。

        失败查询允许没有最终日期和行数，因为 Gateway 可能在参数校验、鉴权或网络
        阶段就失败；但失败编码只接收异常类型名，不能把底层异常正文或 SQL 写入库。
        """

        _validate_event(
            organization_id=organization_id,
            metric=metric,
            bucket=bucket,
            comparison_role=comparison_role,
            row_count=row_count,
            status=status,
            error_code=error_code,
        )
        statement = text(
            """
            INSERT INTO agent_operations_query_audits (
                id, subject_user_id, actor_roles, organization_id, metric, bucket,
                comparison_role, from_date, to_date, row_count, status, error_code,
                request_id, trace_id
            ) VALUES (
                :id, :subject_user_id, :actor_roles, :organization_id, :metric, :bucket,
                :comparison_role, :from_date, :to_date, :row_count, :status, :error_code,
                :request_id, :trace_id
            )
            """
        )
        params = {
            "id": str(uuid4()),
            "subject_user_id": identity.subject if identity is not None else None,
            "actor_roles": ",".join(sorted(identity.roles)) if identity is not None else None,
            "organization_id": organization_id,
            "metric": metric,
            "bucket": bucket,
            "comparison_role": comparison_role,
            "from_date": from_date,
            "to_date": to_date,
            "row_count": row_count,
            "status": status,
            "error_code": error_code,
            "request_id": request_id,
            "trace_id": trace_id,
        }
        async with self._database.engine.begin() as connection:
            await connection.execute(statement, params)


def _validate_event(
    *,
    organization_id: str,
    metric: str,
    bucket: str,
    comparison_role: str,
    row_count: int | None,
    status: str,
    error_code: str | None,
) -> None:
    """在应用层先拒绝非法审计事件，数据库约束负责最后一道防线。"""

    if not organization_id:
        raise OperationsAuditValidationError("organization_id is required")
    if metric not in _METRICS:
        raise OperationsAuditValidationError("unsupported operations metric")
    if bucket not in _BUCKETS:
        raise OperationsAuditValidationError("unsupported operations bucket")
    if comparison_role not in _ROLES:
        raise OperationsAuditValidationError("unsupported comparison role")
    if status not in _STATUSES:
        raise OperationsAuditValidationError("unsupported operations audit status")
    if row_count is not None and row_count < 0:
        raise OperationsAuditValidationError("row_count must not be negative")
    if status == "SUCCEEDED" and error_code is not None:
        raise OperationsAuditValidationError("successful audit must not contain an error")
    if status == "FAILED" and error_code is None:
        raise OperationsAuditValidationError("failed audit requires an error code")
