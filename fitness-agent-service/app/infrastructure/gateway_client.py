import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, TypeVar
from zoneinfo import ZoneInfo

import httpx
from fitness_tool_contracts.canonical import canonical_json_bytes, training_execution_payload
from fitness_tool_contracts.models import (
    GatewayAppointment,
    GatewayBookingAvailability,
    GatewayBookingCancelled,
    GatewayBookingCreated,
    GatewayBookingOperation,
    GatewayContract,
    GatewayCourse,
    GatewayCustomerServiceTicket,
    GatewayOperationsMetric,
    GatewayOperationsMetricRow,
    GatewayOrganization,
    GatewayStudentTrainingContextAccess,
    GatewayTrainingDay,
    GatewayTrainingDayExecution,
    GatewayTrainingItem,
    GatewayTrainingPlan,
    GatewayUser,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings

__all__ = [
    "GatewayAppointment",
    "GatewayAuthenticationError",
    "GatewayBadRequestError",
    "GatewayBookingAvailability",
    "GatewayBookingCancelled",
    "GatewayBookingCreated",
    "GatewayBookingOperation",
    "GatewayClient",
    "GatewayClientError",
    "GatewayConfigurationError",
    "GatewayConflictError",
    "GatewayContract",
    "GatewayCourse",
    "GatewayCustomerServiceTicket",
    "GatewayExecutionUnknownError",
    "GatewayForbiddenError",
    "GatewayNotFoundError",
    "GatewayOperationsMetric",
    "GatewayOperationsMetricRow",
    "GatewayOrganization",
    "GatewayProtocolError",
    "GatewayRequestContext",
    "GatewayStudentTrainingContextAccess",
    "GatewayToolOperation",
    "GatewayTrainingDay",
    "GatewayTrainingDayExecution",
    "GatewayTrainingItem",
    "GatewayTrainingPlan",
    "GatewayUnavailableError",
    "GatewayUser",
]

T = TypeVar("T", bound=BaseModel)
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


class GatewayPersonalPlanSummary(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    title: str = Field(max_length=128)


def _instant_parameter(value: datetime) -> str:
    """把 Python datetime 编码为 Java Instant 可解析的 RFC3339 字符串。"""

    aware = value.replace(tzinfo=_BUSINESS_TIMEZONE) if value.tzinfo is None else value
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class GatewayClientError(RuntimeError):
    """Gateway 调用失败的稳定基类，不携带密钥、Prompt 或完整响应体。"""

    def __init__(
        self, message: str, *, status_code: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class GatewayConfigurationError(GatewayClientError):
    """Gateway 地址或服务间凭证尚未配置。"""


class GatewayAuthenticationError(GatewayClientError):
    """Gateway 拒绝了服务凭证或 AgentContext。"""


class GatewayForbiddenError(GatewayClientError):
    """Gateway 判定请求超出用户或组织资源范围。"""


class GatewayBadRequestError(GatewayClientError):
    """Gateway 拒绝了不符合契约的查询参数。"""


class GatewayConflictError(GatewayClientError):
    """Gateway 检测到版本冲突或重复请求无法安全复用。"""


class GatewayNotFoundError(GatewayClientError):
    """Gateway 找不到请求的健身业务资源。"""


class GatewayUnavailableError(GatewayClientError):
    """Gateway 网络超时、连接失败或暂时返回服务端错误。"""


class GatewayExecutionUnknownError(GatewayUnavailableError):
    """写请求已发送或无法证明未发送；必须查询原业务 ID，禁止自动重发。"""


class GatewayProtocolError(GatewayClientError):
    """Gateway 返回的数据不符合已版本化的 Tool View 契约。"""


@dataclass(frozen=True)
class GatewayRequestContext:
    """一次 Agent 请求调用 Gateway 所需的已签名上下文。

    signed_context 必须由认证服务签发，Agent 只能透传，不能根据模型输出或请求参数
    自己拼装。request_id/trace_id 用于跨服务检索，均不承载用户业务数据。
    """

    signed_context: str
    request_id: str | None = None
    trace_id: str | None = None
    # 仅由上游确认流程注入，模型输出和工具参数都不能生成确认凭证。
    confirmation_token: str | None = None
    # 仅保存非秘密的路由选择；确认创建时固定，恢复时不能改用当前灰度配置。
    transport: str | None = None
    contract_version: str = "v1"


class _GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GatewayToolOperation(_GatewayModel):
    """全领域操作结果；所有状态都必须与原确认动作完整绑定。"""

    operation_id: str = Field(alias="operationId")
    tool_id: str = Field(alias="toolId")
    confirmation_id: str = Field(alias="confirmationId")
    organization_id: str = Field(alias="organizationId")
    actor_id: str = Field(alias="actorId")
    payload_hash: str = Field(alias="payloadHash")
    status: Literal["SUCCEEDED", "PROCESSING", "FAILED", "UNKNOWN"]
    result_reference: str | dict[str, Any] | None = Field(default=None, alias="resultReference")


class GatewayClient:
    """调用 Java 健身核心 Gateway 的异步客户端。

    只对 GET 查询做有限重试。业务 POST 的超时、5xx 或不可验证响应均为 UNKNOWN，
    不因幂等键而自动重发或重签确认凭证。客户端复用一个 HTTP 连接池，
    关闭由 FastAPI lifespan 负责。响应先通过 Pydantic 校验，再交给 Agent Runtime。
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.gateway_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.gateway_timeout_seconds),
            # Gateway 是受控的内部服务，不能继承宿主机的 HTTP(S)_PROXY。
            # 否则 localhost/内网请求可能被系统代理接管并返回空 502，同时还会把
            # 内部服务 Token 与 AgentContext 发送给非预期代理端点。
            trust_env=False,
        )

    async def get_current_user(self, context: GatewayRequestContext) -> GatewayUser:
        return await self._get("/internal/agent-tools/v1/me", context, {}, GatewayUser)

    async def get_organization(
        self,
        context: GatewayRequestContext,
        organization_id: str,
    ) -> GatewayOrganization:
        return await self._get(
            f"/internal/agent-tools/v1/organizations/{organization_id}",
            context,
            {},
            GatewayOrganization,
        )

    async def list_courses(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        limit: int | None = None,
    ) -> list[GatewayCourse]:
        return await self._get_list(
            "/internal/agent-tools/v1/courses",
            context,
            {"organizationId": organization_id, "limit": limit},
            GatewayCourse,
        )

    async def list_contracts(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        user_id: str | None = None,
        limit: int | None = None,
    ) -> list[GatewayContract]:
        return await self._get_list(
            "/internal/agent-tools/v1/contracts",
            context,
            {"organizationId": organization_id, "userId": user_id, "limit": limit},
            GatewayContract,
        )

    async def list_appointments(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        user_id: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int | None = None,
    ) -> list[GatewayAppointment]:
        return await self._get_list(
            "/internal/agent-tools/v1/appointments",
            context,
            {
                "organizationId": organization_id,
                "userId": user_id,
                "from": _instant_parameter(from_time) if from_time else None,
                "to": _instant_parameter(to_time) if to_time else None,
                "limit": limit,
            },
            GatewayAppointment,
        )

    async def check_booking_availability(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        student_id: str | None,
        coach_id: str,
        course_id: str | None,
        start_time: datetime,
        end_time: datetime,
        exclude_appointment_id: str | None = None,
    ) -> GatewayBookingAvailability:
        return await self._get(
            "/internal/agent-tools/v1/booking/availability",
            context,
            {
                "organizationId": organization_id,
                "studentId": student_id,
                "coachId": coach_id,
                "courseId": course_id,
                "start": _instant_parameter(start_time),
                "end": _instant_parameter(end_time),
                "excludeAppointmentId": exclude_appointment_id,
            },
            GatewayBookingAvailability,
        )

    async def authorize_student_training_context(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        student_id: str,
    ) -> GatewayStudentTrainingContextAccess:
        """由 Java Gateway 按当前签名身份校验目标学员关系。"""

        return await self._get(
            "/internal/agent-tools/v1/training-context-access",
            context,
            {"organizationId": organization_id, "studentId": student_id},
            GatewayStudentTrainingContextAccess,
            service_scope="query",
        )

    async def query_operations_metric(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        metric: str,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int | None = None,
        bucket: Literal["NONE", "DAY", "WEEK"] = "NONE",
    ) -> GatewayOperationsMetric:
        """查询 Java Gateway 的固定经营指标目录。

        Python Agent 只传递指标 ID 和日期范围；不接收 SQL，也不把模型生成的字段名
        拼接进请求。Java Gateway 会再次根据签名 AgentContext 校验管理员角色和组织范围。
        """

        return await self._get(
            "/internal/agent-tools/v1/operations/metrics",
            context,
            {
                "organizationId": organization_id,
                "metric": metric,
                "from": from_date.isoformat() if from_date else None,
                "to": to_date.isoformat() if to_date else None,
                "limit": limit,
                "bucket": bucket,
            },
            GatewayOperationsMetric,
        )

    async def list_customer_service_tickets(
        self,
        context: GatewayRequestContext,
        organization_id: str,
        *,
        subject_user_id: str | None = None,
        status: Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"] | None = None,
        limit: int | None = None,
    ) -> list[GatewayCustomerServiceTicket]:
        """查询客服工单；Agent 不能传 SQL 或任意过滤表达式。"""

        return await self._get_list(
            "/internal/agent-tools/v1/customer-service/tickets",
            context,
            {
                "organizationId": organization_id,
                "subjectUserId": subject_user_id,
                "status": status,
                "limit": limit,
            },
            GatewayCustomerServiceTicket,
        )

    async def get_customer_service_ticket(
        self, context: GatewayRequestContext, organization_id: str, ticket_id: str
    ) -> GatewayCustomerServiceTicket:
        return await self._get(
            f"/internal/agent-tools/v1/customer-service/tickets/{ticket_id}",
            context,
            {"organizationId": organization_id},
            GatewayCustomerServiceTicket,
        )

    async def create_customer_service_ticket(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> GatewayCustomerServiceTicket:
        return await self._post(
            "/internal/agent-tools/v1/customer-service/tickets",
            context,
            payload,
            GatewayCustomerServiceTicket,
        )

    async def create_booking(
        self,
        context: GatewayRequestContext,
        payload: dict[str, Any],
    ) -> GatewayBookingCreated:
        return await self._post(
            "/internal/agent-tools/v1/appointments", context, payload, GatewayBookingCreated
        )

    async def query_booking_operation(
        self, context: GatewayRequestContext, operation_id: str
    ) -> GatewayBookingOperation:
        if not operation_id.strip():
            raise GatewayBadRequestError("操作 ID 不能为空")
        return await self._get(
            f"/internal/agent-tools/v1/booking/operations/{operation_id}",
            context,
            {},
            GatewayBookingOperation,
            service_scope="query",
        )

    async def reconcile_booking_operation(
        self, *, operation_id: str, organization_id: str, actor_id: str
    ) -> GatewayBookingOperation:
        """后台服务身份对账；Gateway 会将返回范围与确认单机构、操作者绑定。"""

        if not operation_id.strip() or not organization_id.strip() or not actor_id.strip():
            raise GatewayBadRequestError("操作对账范围不能为空")
        response_json = await self._service_request(
            f"/internal/agent-reconciliation/v1/booking/operations/{operation_id}",
            {"organizationId": organization_id, "actorId": actor_id},
        )
        try:
            return GatewayBookingOperation.model_validate(response_json)
        except ValidationError as exc:
            raise GatewayProtocolError("Gateway 对账响应不符合工具契约") from exc

    async def query_tool_operation(
        self,
        context: GatewayRequestContext,
        *,
        operation_id: str,
        tool_id: str,
        confirmation_id: str,
        organization_id: str,
        payload_hash: str,
    ) -> GatewayToolOperation:
        self._validate_operation_scope(
            operation_id, tool_id, confirmation_id, organization_id, payload_hash
        )
        return await self._get(
            f"/internal/agent-tools/v1/operations/{operation_id}",
            context,
            {
                "toolId": tool_id,
                "confirmationId": confirmation_id,
                "organizationId": organization_id,
                "payloadHash": payload_hash,
            },
            GatewayToolOperation,
            service_scope="query",
        )

    async def reconcile_tool_operation(
        self,
        *,
        operation_id: str,
        tool_id: str,
        confirmation_id: str,
        organization_id: str,
        actor_id: str,
        payload_hash: str,
    ) -> GatewayToolOperation:
        self._validate_operation_scope(
            operation_id, tool_id, confirmation_id, organization_id, actor_id, payload_hash
        )
        data = await self._service_request(
            f"/internal/agent-reconciliation/v1/operations/{operation_id}",
            {
                "toolId": tool_id,
                "confirmationId": confirmation_id,
                "organizationId": organization_id,
                "actorId": actor_id,
                "payloadHash": payload_hash,
            },
        )
        try:
            return GatewayToolOperation.model_validate(data)
        except ValidationError as exc:
            raise GatewayProtocolError("Gateway 全域对账响应不符合契约") from exc

    @staticmethod
    def _validate_operation_scope(*values: str) -> None:
        if any(not value.strip() or "/" in value for value in values):
            raise GatewayBadRequestError("操作查询绑定范围不完整")

    async def reschedule_booking(
        self,
        context: GatewayRequestContext,
        payload: dict[str, Any],
    ) -> GatewayBookingCreated:
        """调用 Java Gateway 的改约写接口；预约 ID 由 payload 中的稳定字段绑定。"""

        appointment_id = payload.get("appointmentId")
        if not isinstance(appointment_id, str) or not appointment_id:
            raise GatewayBadRequestError("改约请求缺少 appointmentId")
        return await self._post(
            f"/internal/agent-tools/v1/appointments/{appointment_id}/reschedule",
            context,
            payload,
            GatewayBookingCreated,
        )

    async def cancel_booking(
        self,
        context: GatewayRequestContext,
        payload: dict[str, Any],
    ) -> GatewayBookingCancelled:
        """调用取消接口；预约 ID 必须来自经过 Schema 校验的稳定 Payload。"""

        appointment_id = payload.get("appointmentId")
        if not isinstance(appointment_id, str) or not appointment_id:
            raise GatewayBadRequestError("取消请求缺少 appointmentId")
        return await self._post(
            f"/internal/agent-tools/v1/appointments/{appointment_id}/cancel",
            context,
            payload,
            GatewayBookingCancelled,
        )

    async def get_training_plan(
        self, context: GatewayRequestContext, plan_id: str
    ) -> GatewayTrainingPlan:
        return await self._get(
            f"/internal/agent-tools/v1/training/plans/{plan_id}",
            context,
            {},
            GatewayTrainingPlan,
            service_scope="query",
        )

    async def list_personal_training_plans(
        self, context: GatewayRequestContext, before: str | None
    ) -> list[GatewayPersonalPlanSummary]:
        params = {"before": before} if before is not None else {}
        return await self._get_list(
            "/internal/agent-tools/v1/training/plans/mine",
            context,
            params,
            GatewayPersonalPlanSummary,
            service_scope="query",
        )

    async def create_training_draft(
        self, context: GatewayRequestContext, payload: dict[str, Any]
    ) -> GatewayTrainingPlan:
        return await self._post(
            "/internal/agent-tools/v1/training/plans/drafts", context, payload, GatewayTrainingPlan
        )

    async def submit_training_review(
        self, context: GatewayRequestContext, plan_id: str
    ) -> GatewayTrainingPlan:
        return await self._post(
            f"/internal/agent-tools/v1/training/plans/{plan_id}/submit-review",
            context,
            {},
            GatewayTrainingPlan,
        )

    async def review_training_plan(
        self, context: GatewayRequestContext, plan_id: str, payload: dict[str, Any]
    ) -> GatewayTrainingPlan:
        return await self._post(
            f"/internal/agent-tools/v1/training/plans/{plan_id}/review",
            context,
            payload,
            GatewayTrainingPlan,
        )

    async def publish_training_plan(
        self, context: GatewayRequestContext, plan_id: str
    ) -> GatewayTrainingPlan:
        return await self._post(
            f"/internal/agent-tools/v1/training/plans/{plan_id}/publish",
            context,
            {},
            GatewayTrainingPlan,
        )

    async def list_training_day_executions(
        self, context: GatewayRequestContext, plan_id: str
    ) -> list[GatewayTrainingDayExecution]:
        return await self._get_list(
            f"/internal/agent-tools/v1/training/plans/{plan_id}/executions",
            context,
            {},
            GatewayTrainingDayExecution,
        )

    async def record_training_day_execution(
        self, context: GatewayRequestContext, plan_id: str, day_id: str, payload: dict[str, Any]
    ) -> GatewayTrainingDayExecution:
        return await self._post(
            f"/internal/agent-tools/v1/training/plans/{plan_id}/days/{day_id}/execution",
            context,
            training_execution_payload(day_id, payload),
            GatewayTrainingDayExecution,
        )

    async def _get(
        self,
        path: str,
        context: GatewayRequestContext,
        params: dict[str, Any],
        model: type[T],
        *,
        service_scope: Literal["execute", "query"] = "execute",
    ) -> T:
        response_json = await self._request(
            path, context, params, method="GET", service_scope=service_scope
        )
        try:
            return model.model_validate(response_json)
        except ValidationError as exc:
            raise GatewayProtocolError("Gateway 响应不符合工具契约") from exc

    async def _post(
        self,
        path: str,
        context: GatewayRequestContext,
        payload: dict[str, Any],
        model: type[T],
    ) -> T:
        response_json = await self._request(path, context, {}, method="POST", json_body=payload)
        try:
            return model.model_validate(response_json)
        except ValidationError as exc:
            raise GatewayExecutionUnknownError(
                "Gateway 写入响应无法验证，必须查询操作结果"
            ) from exc

    async def _get_list(
        self,
        path: str,
        context: GatewayRequestContext,
        params: dict[str, Any],
        model: type[T],
        *,
        service_scope: Literal["execute", "query"] = "execute",
    ) -> list[T]:
        response_json = await self._request(
            path, context, params, method="GET", service_scope=service_scope
        )
        if not isinstance(response_json, list):
            raise GatewayProtocolError("Gateway 列表响应不符合工具契约")
        try:
            return [model.model_validate(item) for item in response_json]
        except ValidationError as exc:
            raise GatewayProtocolError("Gateway 列表响应不符合工具契约") from exc

    async def _request(
        self,
        path: str,
        context: GatewayRequestContext,
        params: dict[str, Any],
        *,
        method: str,
        json_body: dict[str, Any] | None = None,
        service_scope: Literal["execute", "query"] = "execute",
    ) -> Any:
        if not context.signed_context:
            raise GatewayAuthenticationError("必须提供已签名的 AgentContext")

        is_write = method != "GET"
        if is_write and service_scope == "query":
            raise GatewayConfigurationError("内部查询凭证不能用于业务执行")
        if is_write and not context.request_id:
            raise GatewayBadRequestError("业务写请求必须提供稳定的请求 ID")
        service_token = self._service_token(service_scope)

        headers = {
            "X-Internal-Service-Token": service_token,
            "X-Agent-Context": context.signed_context,
            "X-Request-ID": context.request_id or str(uuid.uuid4()),
        }
        if context.trace_id:
            headers["X-Trace-ID"] = context.trace_id
        if context.confirmation_token:
            headers["X-Confirmation-Token"] = context.confirmation_token
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        wire_body = canonical_json_bytes(json_body) if json_body is not None else None
        clean_params = {key: value for key, value in params.items() if value is not None}
        last_error: Exception | None = None

        retries = 0 if is_write else self.settings.gateway_max_retries
        for attempt in range(retries + 1):
            try:
                response = await self._client.request(
                    method, path, params=clean_params, headers=headers, content=wire_body
                )
            except httpx.TransportError as exc:
                last_error = exc
                if is_write:
                    raise GatewayExecutionUnknownError(
                        "Gateway 写入结果未知，必须查询原操作"
                    ) from exc
                if attempt < retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise GatewayUnavailableError("健身核心 Gateway 请求失败") from exc

            if response.status_code in {408, 429} or response.status_code >= 500:
                if is_write:
                    raise GatewayExecutionUnknownError(
                        "Gateway 写入结果未知，必须查询原操作", status_code=response.status_code
                    )
                if attempt < retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise GatewayUnavailableError(
                    "健身核心 Gateway 暂时不可用",
                    status_code=response.status_code,
                )
            if response.status_code == 401:
                raise GatewayAuthenticationError("Gateway 身份验证失败", status_code=401)
            if response.status_code == 403:
                raise GatewayForbiddenError("Gateway 拒绝了请求的健身资源", status_code=403)
            if response.status_code == 404:
                raise GatewayNotFoundError("未找到健身资源", status_code=404)
            if response.status_code == 409:
                raise GatewayConflictError("Gateway 检测到并发的健身计划变更", status_code=409)
            if response.status_code >= 400:
                raise GatewayBadRequestError(
                    "Gateway 拒绝了工具请求", status_code=response.status_code
                )
            if is_write and not 200 <= response.status_code < 300:
                raise GatewayExecutionUnknownError("Gateway 写入未返回可验证的成功状态")

            try:
                return response.json()
            except ValueError as exc:
                if is_write:
                    raise GatewayExecutionUnknownError(
                        "Gateway 写入响应无效，必须查询原操作"
                    ) from exc
                raise GatewayProtocolError(
                    "Gateway 返回了无效 JSON", status_code=response.status_code
                ) from exc

        raise GatewayUnavailableError("健身核心 Gateway 请求失败") from last_error

    async def _service_request(self, path: str, json_body: dict[str, Any]) -> Any:
        """仅供无用户请求上下文的后台任务调用受限服务端点。"""

        headers = {
            "X-Internal-Service-Token": self._service_token("reconcile"),
            "X-Request-ID": str(uuid.uuid4()),
        }
        last_error: Exception | None = None
        for attempt in range(self.settings.gateway_max_retries + 1):
            try:
                response = await self._client.request("POST", path, headers=headers, json=json_body)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.settings.gateway_max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise GatewayUnavailableError("健身核心 Gateway 对账请求失败") from exc
            if response.status_code in {408, 429} or response.status_code >= 500:
                last_error = GatewayUnavailableError("健身核心 Gateway 对账暂时不可用")
                if attempt < self.settings.gateway_max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise last_error
            if response.status_code in {401, 403}:
                raise GatewayForbiddenError("Gateway 拒绝操作对账范围")
            if response.status_code >= 400:
                raise GatewayBadRequestError("Gateway 拒绝操作对账请求")
            try:
                return response.json()
            except ValueError as exc:
                raise GatewayProtocolError("Gateway 对账响应不是 JSON") from exc
        raise GatewayUnavailableError("健身核心 Gateway 对账请求失败") from last_error

    def _service_token(self, scope: Literal["execute", "query", "reconcile"]) -> str:
        """每个通道独立校验地址和凭证，不借用其他作用域的已配置状态或 Token。"""

        if not self.settings.gateway_base_url.strip():
            raise GatewayConfigurationError("健身核心 Gateway 地址未配置")
        settings_by_scope = {
            "execute": "gateway_http_execution_service_token",
            "query": "gateway_query_service_token",
            "reconcile": "gateway_reconciliation_service_token",
        }
        # 旧 Token 只保留原有显式迁移兼容，Java 仍验证其过期时间和允许路由。
        token = (
            getattr(self.settings, settings_by_scope[scope], "")
            or self.settings.gateway_internal_service_token
        )
        if not token.strip():
            raise GatewayConfigurationError("当前 Gateway 通道的服务凭证未配置")
        return token

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = self.settings.gateway_retry_backoff_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    async def close(self) -> None:
        """关闭由本客户端创建的连接池；测试注入的客户端由测试方负责关闭。"""

        if self._owns_client:
            await self._client.aclose()
