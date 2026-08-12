import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings

T = TypeVar("T", bound=BaseModel)


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


class GatewayNotFoundError(GatewayClientError):
    """Gateway 找不到请求的健身业务资源。"""


class GatewayUnavailableError(GatewayClientError):
    """Gateway 网络超时、连接失败或暂时返回服务端错误。"""


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


class _GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GatewayUser(_GatewayModel):
    id: str
    name: str | None = None
    phone: str | None = None
    avatar: str | None = None
    introduction: str | None = None
    enabled: bool


class GatewayOrganization(_GatewayModel):
    id: str
    name: str | None = None
    address: str | None = None
    summary: str | None = None
    organization_type: str | None = Field(default=None, alias="organizationType")


class GatewayCourse(_GatewayModel):
    id: str
    name: str | None = None
    code: str | None = None
    price: int | None = None
    status: int | None = None


class GatewayContract(_GatewayModel):
    id: str
    organization_id: str = Field(alias="organizationId")
    user_id: str = Field(alias="userId")
    course_id: str | None = Field(default=None, alias="courseId")
    number_id: str | None = Field(default=None, alias="numberId")
    start_date: str | None = Field(default=None, alias="startDate")
    end_date: str | None = Field(default=None, alias="endDate")
    total_class_hours: int | None = Field(default=None, alias="totalClassHours")
    remaining_class_hours: int | None = Field(default=None, alias="remainingClassHours")
    status: int | None = None


class GatewayAppointment(_GatewayModel):
    id: str
    organization_id: str = Field(alias="organizationId")
    user_id: str = Field(alias="userId")
    coach_id: str | None = Field(default=None, alias="coachId")
    course_id: str | None = Field(default=None, alias="courseId")
    course_name: str | None = Field(default=None, alias="courseName")
    start_time: datetime | None = Field(default=None, alias="startTime")
    end_time: datetime | None = Field(default=None, alias="endTime")
    status: int | None = None
    contract_id: str | None = Field(default=None, alias="contractId")


class GatewayClient:
    """调用 Java 健身核心 Gateway 的异步客户端。

    只对 GET 查询的连接异常、超时、429 和 5xx 做有限重试；401/403/400 等确定性
    错误不会重试，避免把权限问题放大成请求风暴。客户端复用一个 HTTP 连接池，关闭
    由 FastAPI lifespan 负责。响应先通过 Pydantic 校验，再交给 Agent Runtime。
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.gateway_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.gateway_timeout_seconds),
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
                "from": from_time.isoformat() if from_time else None,
                "to": to_time.isoformat() if to_time else None,
                "limit": limit,
            },
            GatewayAppointment,
        )

    async def _get(
        self, path: str, context: GatewayRequestContext, params: dict[str, Any], model: type[T]
    ) -> T:
        response_json = await self._request(path, context, params)
        try:
            return model.model_validate(response_json)
        except ValidationError as exc:
            raise GatewayProtocolError("gateway response does not match tool contract") from exc

    async def _get_list(
        self,
        path: str,
        context: GatewayRequestContext,
        params: dict[str, Any],
        model: type[T],
    ) -> list[T]:
        response_json = await self._request(path, context, params)
        if not isinstance(response_json, list):
            raise GatewayProtocolError("gateway list response does not match tool contract")
        try:
            return [model.model_validate(item) for item in response_json]
        except ValidationError as exc:
            raise GatewayProtocolError(
                "gateway list response does not match tool contract"
            ) from exc

    async def _request(
        self,
        path: str,
        context: GatewayRequestContext,
        params: dict[str, Any],
    ) -> Any:
        if not self.settings.gateway_configured:
            raise GatewayConfigurationError("fitness core gateway is not configured")
        if not context.signed_context:
            raise GatewayAuthenticationError("signed agent context is required")

        headers = {
            "X-Internal-Service-Token": self.settings.gateway_internal_service_token,
            "X-Agent-Context": context.signed_context,
            "X-Request-ID": context.request_id or str(uuid.uuid4()),
        }
        if context.trace_id:
            headers["X-Trace-ID"] = context.trace_id
        clean_params = {key: value for key, value in params.items() if value is not None}
        last_error: Exception | None = None

        for attempt in range(self.settings.gateway_max_retries + 1):
            try:
                response = await self._client.get(path, params=clean_params, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.settings.gateway_max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise GatewayUnavailableError("fitness core gateway request failed") from exc

            if response.status_code in {408, 429} or response.status_code >= 500:
                if attempt < self.settings.gateway_max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise GatewayUnavailableError(
                    "fitness core gateway is temporarily unavailable",
                    status_code=response.status_code,
                )
            if response.status_code == 401:
                raise GatewayAuthenticationError("gateway authentication failed", status_code=401)
            if response.status_code == 403:
                raise GatewayForbiddenError(
                    "gateway denied the requested fitness resource", status_code=403
                )
            if response.status_code == 404:
                raise GatewayNotFoundError("fitness resource was not found", status_code=404)
            if response.status_code >= 400:
                raise GatewayBadRequestError(
                    "gateway rejected the tool request", status_code=response.status_code
                )

            try:
                return response.json()
            except ValueError as exc:
                raise GatewayProtocolError(
                    "gateway returned invalid JSON", status_code=response.status_code
                ) from exc

        raise GatewayUnavailableError("fitness core gateway request failed") from last_error

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = self.settings.gateway_retry_backoff_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    async def close(self) -> None:
        """关闭由本客户端创建的连接池；测试注入的客户端由测试方负责关闭。"""

        if self._owns_client:
            await self._client.aclose()
