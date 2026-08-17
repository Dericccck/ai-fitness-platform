"""Operations Agent 查询审计的 PostgreSQL 持久化。

Operations 查询返回的是机构经营聚合数据，不能只依赖结构化日志：日志轮转、服务重启
或多实例采集异常时，管理员仍需要知道谁在什么机构、什么时间范围查询了哪个固定指标。
本仓储只保存审计元数据，不保存 SQL、Prompt、模型原始输出、预约明细或 Gateway 返回行。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import text

from app.infrastructure.agent_context import AgentIdentity
from app.infrastructure.database import Database

OperationsAuditStatus = Literal["SUCCEEDED", "FAILED"]
OperationsAuditRole = Literal["CURRENT", "PREVIOUS_PERIOD", "SAME_PERIOD_LAST_YEAR"]

_METRICS = frozenset(
    {
        "APPOINTMENT_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
        "COMPLETED_CLASS_COUNT",
        "NEW_CUSTOMER_COUNT",
        "REVENUE_AMOUNT",
        "COURSE_APPOINTMENT_COUNT",
        "COACH_APPOINTMENT_COUNT",
        "REMAINING_CLASS_HOURS",
    }
)
_BUCKETS = frozenset({"NONE", "DAY", "WEEK"})
_ROLES = frozenset({"CURRENT", "PREVIOUS_PERIOD", "SAME_PERIOD_LAST_YEAR"})
_STATUSES = frozenset({"SUCCEEDED", "FAILED"})


class OperationsAuditValidationError(ValueError):
    """审计事件包含不在固定目录中的指标、桶、角色或状态。"""


class OperationsAuditPersistenceError(RuntimeError):
    """查询成功但审计无法持久化，调用方必须拒绝返回未审计结果。"""


@dataclass(frozen=True)
class OperationsAuditRecord:
    """管理员可查看的经营查询审计摘要，不包含查询正文或业务明细。"""

    id: str
    subject_user_id: str | None
    actor_roles: str | None
    organization_id: str
    metric: str
    bucket: str
    comparison_role: str
    from_date: date | None
    to_date: date | None
    row_count: int | None
    status: str
    error_code: str | None
    request_id: str | None
    trace_id: str | None
    created_at: datetime


class OperationsAuditRepository:
    """把 Operations 查询元数据追加写入 Agent PostgreSQL。

    审计是只追加事实，不允许根据模型文本自由拼接 SQL 或写入任意字段。生产环境
    由已验证的 ToolContext 提供主体和角色；没有身份的系统任务可以留下空主体，但
    仍必须保留机构、指标、查询范围和请求链路，便于后续排查。
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list(
        self,
        connection: object,
        *,
        organization_id: str | None,
        organization_ids: tuple[str, ...] | None,
        metric: str | None,
        bucket: str | None,
        comparison_role: str | None,
        status: str | None,
        created_from: datetime | None,
        created_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[OperationsAuditRecord], bool]:
        """按管理员授权组织范围分页读取审计摘要。

        ``organization_ids`` 是从签名上下文得到的服务端范围，不来自模型或客户端；
        当组织管理员未指定机构时，只能看到自己 Token 中的机构。返回多取一条以判断
        是否还有下一页，避免额外的 count 查询和不必要的全表扫描。
        """

        if limit < 1 or limit > 100 or offset < 0 or offset > 100_000:
            raise ValueError("operations audit pagination is out of range")
        if created_from is not None and created_to is not None and created_from > created_to:
            raise ValueError("created_from must be earlier than or equal to created_to")
        if organization_id is not None and not organization_id:
            raise ValueError("organization_id must not be empty")
        if organization_ids is not None and not organization_ids:
            return [], False

        where_clauses = ["1 = 1"]
        params: dict[str, object] = {
            "limit_plus_one": limit + 1,
            "offset": offset,
        }
        if organization_id is not None:
            where_clauses.append("organization_id = :organization_id")
            params["organization_id"] = organization_id
        if organization_ids is not None:
            where_clauses.append("organization_id = ANY(CAST(:organization_ids AS TEXT[]))")
            params["organization_ids"] = list(organization_ids)
        if metric is not None:
            where_clauses.append("metric = :metric")
            params["metric"] = metric
        if bucket is not None:
            where_clauses.append("bucket = :bucket")
            params["bucket"] = bucket
        if comparison_role is not None:
            where_clauses.append("comparison_role = :comparison_role")
            params["comparison_role"] = comparison_role
        if status is not None:
            where_clauses.append("status = :status")
            params["status"] = status
        if created_from is not None:
            where_clauses.append("created_at >= :created_from")
            params["created_from"] = created_from
        if created_to is not None:
            where_clauses.append("created_at <= :created_to")
            params["created_to"] = created_to

        statement = text(
            f"""
            SELECT id, subject_user_id, actor_roles, organization_id, metric, bucket,
                   comparison_role, from_date, to_date, row_count, status, error_code,
                   request_id, trace_id, created_at
            FROM agent_operations_query_audits
            WHERE {" AND ".join(where_clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit_plus_one OFFSET :offset
            """
        )
        result = await connection.execute(  # type: ignore[attr-defined]
            statement,
            params,
        )
        rows = result.mappings().all()
        has_more = len(rows) > limit
        return [_audit_record_from_row(row) for row in rows[:limit]], has_more

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


def _audit_record_from_row(row: object) -> OperationsAuditRecord:
    """把数据库行转换为 API/服务层稳定的只读审计模型。"""

    data = row  # SQLAlchemy Mapping 在这里保持结构化字段，不转成任意 JSON。
    return OperationsAuditRecord(
        id=str(data["id"]),  # type: ignore[index]
        subject_user_id=data["subject_user_id"],  # type: ignore[index]
        actor_roles=data["actor_roles"],  # type: ignore[index]
        organization_id=str(data["organization_id"]),  # type: ignore[index]
        metric=str(data["metric"]),  # type: ignore[index]
        bucket=str(data["bucket"]),  # type: ignore[index]
        comparison_role=str(data["comparison_role"]),  # type: ignore[index]
        from_date=data["from_date"],  # type: ignore[index]
        to_date=data["to_date"],  # type: ignore[index]
        row_count=data["row_count"],  # type: ignore[index]
        status=str(data["status"]),  # type: ignore[index]
        error_code=data["error_code"],  # type: ignore[index]
        request_id=data["request_id"],  # type: ignore[index]
        trace_id=data["trace_id"],  # type: ignore[index]
        created_at=data["created_at"],  # type: ignore[index]
    )


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
