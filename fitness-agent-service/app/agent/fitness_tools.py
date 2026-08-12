"""首批健身只读工具适配器。

这些函数只负责把已校验的 Tool Input 转换为 GatewayClient 调用，不拼接任意 URL、
不查询 MySQL，也不在 Python 侧自行判断组织权限。真正的主体、租户和资源关系校验
仍由 Java Tool Gateway 根据签名 AgentContext 执行。
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.gateway_client import GatewayClient

from .tool_registry import (
    EmptyToolInput,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)

_ID_FIELD = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
_READ_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH", "STUDENT"})


class OrganizationToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str = _ID_FIELD


class CourseListToolInput(OrganizationToolInput):
    limit: int = Field(default=20, ge=1, le=100)


class ContractListToolInput(OrganizationToolInput):
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    limit: int = Field(default=20, ge=1, le=100)


class AppointmentListToolInput(ContractListToolInput):
    from_time: datetime | None = None
    to_time: datetime | None = None

    @model_validator(mode="after")
    def validate_time_window(self) -> AppointmentListToolInput:
        if self.from_time and self.to_time and self.from_time >= self.to_time:
            raise ValueError("from_time must be earlier than to_time")
        return self


def build_fitness_tool_registry(gateway: GatewayClient) -> ToolRegistry:
    """创建进程级健身工具注册表。

    Registry 在应用生命周期内只创建一次，所有工具共享 Gateway 连接池。每个 handler
    都显式绑定到一个固定的 GatewayClient 方法，后续增加写工具时可以在这里集中审查
    角色、确认和幂等元数据，而不是让模型或业务 Agent 自由调用 HTTP 客户端。
    """

    registry = ToolRegistry()

    async def get_current_user(_: BaseModel, context: ToolContext) -> object:
        return await gateway.get_current_user(context.gateway_context)

    async def get_organization(raw: BaseModel, context: ToolContext) -> object:
        data = cast(OrganizationToolInput, raw)
        return await gateway.get_organization(context.gateway_context, data.organization_id)

    async def list_courses(raw: BaseModel, context: ToolContext) -> object:
        data = cast(CourseListToolInput, raw)
        return await gateway.list_courses(
            context.gateway_context,
            data.organization_id,
            limit=data.limit,
        )

    async def list_contracts(raw: BaseModel, context: ToolContext) -> object:
        data = cast(ContractListToolInput, raw)
        return await gateway.list_contracts(
            context.gateway_context,
            data.organization_id,
            user_id=data.user_id,
            limit=data.limit,
        )

    async def list_appointments(raw: BaseModel, context: ToolContext) -> object:
        data = cast(AppointmentListToolInput, raw)
        return await gateway.list_appointments(
            context.gateway_context,
            data.organization_id,
            user_id=data.user_id,
            from_time=data.from_time,
            to_time=data.to_time,
            limit=data.limit,
        )

    definitions = (
        ToolDefinition(
            tool_id="fitness.user.get_current.v1",
            description="查询当前签名 AgentContext 对应的健身用户资料。",
            input_model=EmptyToolInput,
            handler=get_current_user,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.organization.get.v1",
            description="查询当前用户有权限访问的健身机构资料。",
            input_model=OrganizationToolInput,
            handler=get_organization,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.course.list.v1",
            description="查询指定健身机构中当前权限范围内的课程。",
            input_model=CourseListToolInput,
            handler=list_courses,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.contract.list.v1",
            description="查询指定机构中当前权限范围内的合同和剩余课时。",
            input_model=ContractListToolInput,
            handler=list_contracts,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.appointment.list.v1",
            description="查询指定机构中当前权限范围内的预约记录。",
            input_model=AppointmentListToolInput,
            handler=list_appointments,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
    )
    for definition in definitions:
        registry.register(definition)
    return registry
