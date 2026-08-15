"""管理员 Operations 查询审计只读 API。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from app.agent.operations_audit import OperationsAuditRecord, OperationsAuditRepository
from app.agent.operations_tools import get_operations_metric_definition
from app.infrastructure.agent_context import AgentContextVerificationError, AgentIdentity

router = APIRouter(prefix="/api/v1/admin/operations", tags=["admin-operations"])

_OPERATIONS_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "ADMIN", "SUPER_ADMIN"})
_PLATFORM_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ADMIN", "SUPER_ADMIN"})


class OperationsMetricDefinitionResponse(BaseModel):
    """管理员查看历史查询时使用的固定指标口径说明。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    dimension_description: str
    supported_buckets: tuple[str, ...]
    supports_previous_period: bool


class OperationsAuditResponse(BaseModel):
    """单条经营查询审计摘要，不返回 SQL、Prompt 或查询明细。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    subject_user_id: str | None
    actor_roles: str | None
    organization_id: str
    metric: str
    metric_definition: OperationsMetricDefinitionResponse
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


class OperationsAuditPageResponse(BaseModel):
    """经营查询审计分页结果；has_more 避免返回不必要的全量 count。"""

    model_config = ConfigDict(extra="forbid")

    items: tuple[OperationsAuditResponse, ...]
    limit: int
    offset: int
    has_more: bool


@router.get("/query-audits", response_model=OperationsAuditPageResponse)
async def list_operations_query_audits(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    metric: Literal[
        "APPOINTMENT_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
        "COURSE_APPOINTMENT_COUNT",
        "COACH_APPOINTMENT_COUNT",
        "REMAINING_CLASS_HOURS",
    ]
    | None = Query(default=None),
    bucket: Literal["NONE", "DAY", "WEEK"] | None = Query(default=None),
    comparison_role: Literal["CURRENT", "PREVIOUS_PERIOD"] | None = Query(default=None),
    audit_status: Literal["SUCCEEDED", "FAILED"] | None = Query(default=None),
    created_from: datetime | None = Query(default=None),  # noqa: B008
    created_to: datetime | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    x_agent_context: str | None = Header(default=None),
) -> OperationsAuditPageResponse:
    """按管理员可见机构范围读取经营查询审计。

    平台管理员可以查询全平台或指定机构；机构管理员即使不传机构，也只能看到签名
    AgentContext 中的机构。接口只提供追溯元数据，不把它扩展成经营明细或 Text-to-SQL
    查询入口。
    """

    identity = _verify_operations_admin(request, x_agent_context)
    if (
        organization_id is not None
        and organization_id not in identity.organization_ids
        and not _PLATFORM_ADMIN_ROLES.intersection(identity.roles)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organization is outside signed admin scope",
        )
    organization_ids = (
        None
        if _PLATFORM_ADMIN_ROLES.intersection(identity.roles)
        else tuple(sorted(identity.organization_ids))
    )
    repository = _repository(request)
    try:
        async with request.app.state.database.engine.connect() as connection:
            records, has_more = await repository.list(
                connection,
                organization_id=organization_id,
                organization_ids=organization_ids,
                metric=metric,
                bucket=bucket,
                comparison_role=comparison_role,
                status=audit_status,
                created_from=created_from,
                created_to=created_to,
                limit=limit,
                offset=offset,
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return OperationsAuditPageResponse(
        items=tuple(_to_response(record) for record in records),
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


def _repository(request: Request) -> OperationsAuditRepository:
    return cast(OperationsAuditRepository, request.app.state.operations_audit)


def _verify_operations_admin(request: Request, token: str | None) -> AgentIdentity:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="signed agent context is required"
        )
    try:
        identity = cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid signed agent context"
        ) from exc
    if not _OPERATIONS_ADMIN_ROLES.intersection(identity.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="operations admin role is required"
        )
    return identity


def _to_response(record: OperationsAuditRecord) -> OperationsAuditResponse:
    definition = get_operations_metric_definition(record.metric)
    if definition is None:
        # 审计仓储会拦截未知指标；这里保留兼容兜底，避免历史异常数据导致管理员
        # 无法打开整页记录，同时明确告诉前端该口径不能用于业务解释。
        metric_definition = OperationsMetricDefinitionResponse(
            id=record.metric,
            label=record.metric,
            description="未识别的历史指标定义，禁止据此生成业务解释。",
            dimension_description="未知",
            supported_buckets=(),
            supports_previous_period=False,
        )
    else:
        metric_definition = OperationsMetricDefinitionResponse(
            id=definition.metric,
            label=definition.label,
            description=definition.description,
            dimension_description=definition.dimension_description,
            supported_buckets=tuple(sorted(definition.supported_buckets)),
            supports_previous_period=definition.supports_previous_period,
        )
    return OperationsAuditResponse(
        id=record.id,
        subject_user_id=record.subject_user_id,
        actor_roles=record.actor_roles,
        organization_id=record.organization_id,
        metric=record.metric,
        metric_definition=metric_definition,
        bucket=record.bucket,
        comparison_role=record.comparison_role,
        from_date=record.from_date,
        to_date=record.to_date,
        row_count=record.row_count,
        status=record.status,
        error_code=record.error_code,
        request_id=record.request_id,
        trace_id=record.trace_id,
        created_at=record.created_at,
    )
