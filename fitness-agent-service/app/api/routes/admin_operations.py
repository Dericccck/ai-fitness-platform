"""管理员经营查询审计只读 API。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from app.agent.operations_audit import OperationsAuditRecord, OperationsAuditRepository
from app.agent.operations_tools import (
    OPERATIONS_METRIC_CATALOG,
    OPERATIONS_METRIC_CATALOG_VERSION,
    OperationsMetricDefinition,
    get_operations_metric_definition,
    validate_operations_metric_capability,
)
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
    supports_year_over_year: bool


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


class OperationsMetricCatalogResponse(BaseModel):
    """管理员前端使用的固定经营指标目录，不包含任何业务数据。"""

    model_config = ConfigDict(extra="forbid")

    catalog_version: str
    items: tuple[OperationsMetricDefinitionResponse, ...]


@router.get(
    "/metric-catalog",
    response_model=OperationsMetricCatalogResponse,
    responses={304: {"description": "指标目录未发生变化"}},
)
async def get_operations_metric_catalog(
    request: Request,
    response: Response,
    x_agent_context: str | None = Header(default=None),
    if_none_match: str | None = Header(default=None),
) -> OperationsMetricCatalogResponse | Response:
    """返回管理员可配置的固定经营指标及其能力边界。

    指标目录是代码维护的公开能力元数据，不查询组织业务数据；仍要求管理员签名身份，
    让普通用户不能通过管理员接口枚举后台能力。组织范围不参与过滤，因为目录本身不
    包含任何组织专属数据，真正的组织权限仍在查询时由 AgentContext 和 Gateway 校验。
    """

    _verify_operations_admin(request, x_agent_context)
    etag = f'"{OPERATIONS_METRIC_CATALOG_VERSION}"'
    cache_headers = {
        "ETag": etag,
        # 目录不含业务数据，但接口需要管理员身份，所以只允许浏览器/客户端私有缓存。
        "Cache-Control": "private, max-age=300, must-revalidate",
    }
    if _if_none_match_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
    response.headers.update(cache_headers)
    return OperationsMetricCatalogResponse(
        catalog_version=OPERATIONS_METRIC_CATALOG_VERSION,
        items=tuple(_to_metric_definition_response(item) for item in OPERATIONS_METRIC_CATALOG),
    )


def _if_none_match_matches(if_none_match: str | None, etag: str) -> bool:
    """判断客户端缓存是否仍对应当前目录版本，支持逗号分隔和弱 ETag。"""

    if not if_none_match:
        return False
    return any(
        candidate.strip().removeprefix("W/") == etag for candidate in if_none_match.split(",")
    )


@router.get("/query-audits", response_model=OperationsAuditPageResponse)
async def list_operations_query_audits(
    request: Request,
    organization_id: str | None = Query(default=None, min_length=1, max_length=128),
    metric: Literal[
        "APPOINTMENT_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
        "COMPLETED_CLASS_COUNT",
        "NEW_CUSTOMER_COUNT",
        "REVENUE_AMOUNT",
        "COURSE_APPOINTMENT_COUNT",
        "COACH_APPOINTMENT_COUNT",
        "REMAINING_CLASS_HOURS",
    ]
    | None = Query(default=None),
    bucket: Literal["NONE", "DAY", "WEEK"] | None = Query(default=None),
    comparison_role: Literal["CURRENT", "PREVIOUS_PERIOD", "SAME_PERIOD_LAST_YEAR"] | None = Query(
        default=None
    ),
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
    if metric is not None:
        try:
            validate_operations_metric_capability(
                metric,
                bucket=bucket,
                comparison_role=comparison_role,
            )
        except ValueError as exc:
            # 这是筛选条件与固定指标目录能力不一致，不是数据库查询失败；提前返回
            # 422 能避免前端把“没有匹配审计记录”误解成真实的零结果。
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
    if (
        organization_id is not None
        and organization_id not in identity.organization_ids
        and not _PLATFORM_ADMIN_ROLES.intersection(identity.roles)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="机构不在已签名管理员的权限范围内",
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
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
            status_code=status.HTTP_401_UNAUTHORIZED, detail="必须提供已签名的 AgentContext"
        )
    try:
        identity = cast(AgentIdentity, request.app.state.context_verifier.verify(token))
    except AgentContextVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="已签名的 AgentContext 无效"
        ) from exc
    if not _OPERATIONS_ADMIN_ROLES.intersection(identity.roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要经营管理员角色")
    return identity


def _to_response(record: OperationsAuditRecord) -> OperationsAuditResponse:
    return OperationsAuditResponse(
        id=record.id,
        subject_user_id=record.subject_user_id,
        actor_roles=record.actor_roles,
        organization_id=record.organization_id,
        metric=record.metric,
        metric_definition=_to_metric_definition_response(
            get_operations_metric_definition(record.metric), metric=record.metric
        ),
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


def _to_metric_definition_response(
    definition: OperationsMetricDefinition | None,
    *,
    metric: str | None = None,
) -> OperationsMetricDefinitionResponse:
    """把内部目录模型转换成稳定 API 响应，并兼容异常历史指标。"""

    if definition is None:
        unknown_metric = metric or "UNKNOWN"
        return OperationsMetricDefinitionResponse(
            id=unknown_metric,
            label=unknown_metric,
            description="未识别的历史指标定义，禁止据此生成业务解释。",
            dimension_description="未知",
            supported_buckets=(),
            supports_previous_period=False,
            supports_year_over_year=False,
        )
    return OperationsMetricDefinitionResponse(
        id=definition.metric,
        label=definition.label,
        description=definition.description,
        dimension_description=definition.dimension_description,
        supported_buckets=tuple(sorted(definition.supported_buckets)),
        supports_previous_period=definition.supports_previous_period,
        supports_year_over_year=definition.supports_year_over_year,
    )
