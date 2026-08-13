"""首批健身只读工具适配器。

这些函数只负责把已校验的 Tool Input 转换为 GatewayClient 调用，不拼接任意 URL、
不查询 MySQL，也不在 Python 侧自行判断组织权限。真正的主体、租户和资源关系校验
仍由 Java Tool Gateway 根据签名 AgentContext 执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.confirmation.normalization import ConfirmationPolicy
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


class TrainingItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exercise_name: str = Field(min_length=1, max_length=128)
    sort_order: int = Field(ge=1, le=100)
    sets: int = Field(ge=1, le=100)
    reps: str = Field(min_length=1, max_length=64)
    rest_seconds: int | None = Field(default=None, ge=0, le=3600)
    target_weight_kg: float | None = Field(default=None, ge=0, le=1000)
    target_rpe: float | None = Field(default=None, ge=0, le=10)
    notes: str | None = Field(default=None, max_length=1000)


class TrainingDayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1, le=31)
    title: str = Field(min_length=1, max_length=128)
    scheduled_date: str | None = None
    items: list[TrainingItemInput] = Field(min_length=1, max_length=100)


class CreateTrainingDraftToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str = _ID_FIELD
    student_id: str = _ID_FIELD
    coach_id: str = _ID_FIELD
    title: str = Field(min_length=1, max_length=128)
    goal_type: str = Field(min_length=1, max_length=32)
    days: list[TrainingDayInput] = Field(min_length=1, max_length=31)


class TrainingPlanToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = _ID_FIELD


class ListTrainingDayExecutionsToolInput(TrainingPlanToolInput):
    """查询一个训练计划已经明确提交的训练日结果。"""


class RecordTrainingDayExecutionToolInput(TrainingPlanToolInput):
    """学员记录训练日已完成或已跳过及可选简短备注。"""

    day_id: str = _ID_FIELD
    status: str = Field(pattern=r"^(COMPLETED|SKIPPED)$")
    note: str | None = Field(default=None, max_length=1000)


class ReviewTrainingPlanToolInput(TrainingPlanToolInput):
    decision: str = Field(pattern=r"^(APPROVE|REJECT)$")
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_rejection_comment(self) -> ReviewTrainingPlanToolInput:
        """驳回必须给出可追溯原因，避免确认卡和审核记录出现无解释拒绝。"""

        if self.decision == "REJECT" and not self.comment:
            raise ValueError("comment is required when decision is REJECT")
        return self


def _create_training_draft_payload(data: CreateTrainingDraftToolInput) -> dict[str, object]:
    """生成创建草案的唯一 Gateway Payload，确认摘要与真实执行共用它。"""

    return data.model_dump(mode="json", by_alias=True)


def _review_training_plan_payload(data: ReviewTrainingPlanToolInput) -> dict[str, object]:
    """生成审核 Payload，避免摘要构造和 Gateway 调用各维护一份字段映射。"""

    return {"decision": data.decision, "comment": data.comment}


def _summary(
    operation: str,
    action: str,
    target_status: str,
    payload: dict[str, object],
    resource: Mapping[str, object] | None,
    *,
    organization_id: str,
    resource_type: str = "training_plan",
    resource_id: str | None = None,
    expected_resource_version: int | None = None,
) -> dict[str, object]:
    """统一生成给确认卡片使用的机器可渲染摘要。

    operation 是固定的中文业务名称，不能被模型覆盖；其余字段是前端可逐字段展示和
    对比的稳定 JSON。规范化器随后会再次校验这些字段与实际 Payload 完全绑定。
    """

    return {
        "operation": operation,
        "action": action,
        "target_status": target_status,
        "organization_id": organization_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "expected_resource_version": expected_resource_version,
        "details": payload,
        "resource": resource,
    }


def _create_summary(data: BaseModel, _: Mapping[str, object] | None) -> dict[str, object]:
    typed = cast(CreateTrainingDraftToolInput, data)
    payload = _create_training_draft_payload(typed)
    return _summary(
        "创建训练计划草案",
        "CREATE_TRAINING_DRAFT",
        "DRAFT",
        payload,
        None,
        organization_id=typed.organization_id,
    )


def _plan_summary(
    operation: str,
    action: str,
    target_status: str,
    data: BaseModel,
    resource: Mapping[str, object] | None,
) -> dict[str, object]:
    typed = cast(TrainingPlanToolInput, data)
    return _summary(
        operation,
        action,
        target_status,
        {},
        resource,
        organization_id="__resolved_from_resource__",
        resource_id=typed.plan_id,
    )


def _submit_summary(data: BaseModel, resource: Mapping[str, object] | None) -> dict[str, object]:
    return _plan_summary(
        "提交训练计划审核", "SUBMIT_TRAINING_REVIEW", "PENDING_REVIEW", data, resource
    )


def _review_summary(data: BaseModel, resource: Mapping[str, object] | None) -> dict[str, object]:
    typed = cast(ReviewTrainingPlanToolInput, data)
    return _summary(
        "审核训练计划",
        "REVIEW_TRAINING_PLAN",
        "APPROVED" if typed.decision == "APPROVE" else "REJECTED",
        _review_training_plan_payload(typed),
        resource,
        organization_id="__resolved_from_resource__",
        resource_id=typed.plan_id,
    )


def _publish_summary(data: BaseModel, resource: Mapping[str, object] | None) -> dict[str, object]:
    return _plan_summary("发布训练计划", "PUBLISH_TRAINING_PLAN", "PUBLISHED", data, resource)


def _record_execution_payload(data: RecordTrainingDayExecutionToolInput) -> dict[str, object]:
    """生成执行记录的唯一 Payload，确认摘要和真实 Gateway 调用共用该结构。"""

    return {
        "day_id": data.day_id,
        "status": data.status,
        "note": data.note,
    }


def _record_execution_summary(
    data: BaseModel, resource: Mapping[str, object] | None
) -> dict[str, object]:
    typed = cast(RecordTrainingDayExecutionToolInput, data)
    return _summary(
        "记录训练日执行结果",
        "RECORD_TRAINING_DAY_EXECUTION",
        typed.status,
        _record_execution_payload(typed),
        resource,
        organization_id="__resolved_from_resource__",
        resource_id=typed.plan_id,
    )


def _create_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="CREATE_TRAINING_DRAFT",
        resource_type="training_plan",
        risk_level="WRITE",
        operation="创建训练计划草案",
        target_status="DRAFT",
        payload_builder=lambda raw: _create_training_draft_payload(
            cast(CreateTrainingDraftToolInput, raw)
        ),
        summary_builder=_create_summary,
        organization_id_builder=lambda raw: cast(CreateTrainingDraftToolInput, raw).organization_id,
    )


def _submit_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="SUBMIT_TRAINING_REVIEW",
        resource_type="training_plan",
        risk_level="WRITE",
        operation="提交训练计划审核",
        target_status="PENDING_REVIEW",
        payload_builder=lambda _: {},
        summary_builder=_submit_summary,
        resource_required=True,
        resource_id_builder=lambda raw: cast(TrainingPlanToolInput, raw).plan_id,
    )


def _review_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="REVIEW_TRAINING_PLAN",
        resource_type="training_plan",
        risk_level="WRITE",
        operation="审核训练计划",
        target_status="DYNAMIC",
        payload_builder=lambda raw: _review_training_plan_payload(
            cast(ReviewTrainingPlanToolInput, raw)
        ),
        summary_builder=_review_summary,
        resource_required=True,
        resource_id_builder=lambda raw: cast(ReviewTrainingPlanToolInput, raw).plan_id,
    )


def _publish_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="PUBLISH_TRAINING_PLAN",
        resource_type="training_plan",
        risk_level="WRITE",
        operation="发布训练计划",
        target_status="PUBLISHED",
        payload_builder=lambda _: {},
        summary_builder=_publish_summary,
        resource_required=True,
        resource_id_builder=lambda raw: cast(TrainingPlanToolInput, raw).plan_id,
    )


def _record_execution_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="RECORD_TRAINING_DAY_EXECUTION",
        resource_type="training_plan",
        risk_level="WRITE",
        operation="记录训练日执行结果",
        target_status="DYNAMIC",
        payload_builder=lambda raw: _record_execution_payload(
            cast(RecordTrainingDayExecutionToolInput, raw)
        ),
        summary_builder=_record_execution_summary,
        resource_required=True,
        resource_id_builder=lambda raw: (
            f"{cast(RecordTrainingDayExecutionToolInput, raw).plan_id}:"
            f"{cast(RecordTrainingDayExecutionToolInput, raw).day_id}"
        ),
    )


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

    async def get_training_plan(raw: BaseModel, context: ToolContext) -> object:
        data = cast(TrainingPlanToolInput, raw)
        return await gateway.get_training_plan(context.gateway_context, data.plan_id)

    async def create_training_draft(raw: BaseModel, context: ToolContext) -> object:
        data = cast(CreateTrainingDraftToolInput, raw)
        # confirmation_token 位于请求上下文，不会被模型写入业务 payload；Payload 转换
        # 与确认摘要共用 _create_training_draft_payload，避免二者字段漂移。
        return await gateway.create_training_draft(
            context.gateway_context,
            _create_training_draft_payload(data),
        )

    async def submit_training_review(raw: BaseModel, context: ToolContext) -> object:
        data = cast(TrainingPlanToolInput, raw)
        return await gateway.submit_training_review(context.gateway_context, data.plan_id)

    async def review_training_plan(raw: BaseModel, context: ToolContext) -> object:
        data = cast(ReviewTrainingPlanToolInput, raw)
        return await gateway.review_training_plan(
            context.gateway_context,
            data.plan_id,
            _review_training_plan_payload(data),
        )

    async def publish_training_plan(raw: BaseModel, context: ToolContext) -> object:
        data = cast(TrainingPlanToolInput, raw)
        return await gateway.publish_training_plan(context.gateway_context, data.plan_id)

    async def list_training_day_executions(raw: BaseModel, context: ToolContext) -> object:
        data = cast(ListTrainingDayExecutionsToolInput, raw)
        return await gateway.list_training_day_executions(context.gateway_context, data.plan_id)

    async def record_training_day_execution(raw: BaseModel, context: ToolContext) -> object:
        data = cast(RecordTrainingDayExecutionToolInput, raw)
        return await gateway.record_training_day_execution(
            context.gateway_context,
            data.plan_id,
            data.day_id,
            _record_execution_payload(data),
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
            tool_id="fitness.training.plan.get.v1",
            description="按权限查询结构化训练计划及其训练日和动作明细。",
            input_model=TrainingPlanToolInput,
            handler=get_training_plan,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.create_draft.v1",
            description="创建结构化训练计划草案；草案不能直接发布，必须经过教练审核。",
            input_model=CreateTrainingDraftToolInput,
            handler=create_training_draft,
            allowed_roles=_READ_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_create_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.submit_review.v1",
            description="提交训练计划审核；只有负责教练或机构管理员可以完成该状态转换。",
            input_model=TrainingPlanToolInput,
            handler=submit_training_review,
            allowed_roles=frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH"}),
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_submit_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.review.v1",
            description="审核或驳回训练计划；驳回必须填写原因。",
            input_model=ReviewTrainingPlanToolInput,
            handler=review_training_plan,
            allowed_roles=frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH"}),
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_review_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.publish.v1",
            description="发布已审核通过的训练计划，发布后学员才可以执行。",
            input_model=TrainingPlanToolInput,
            handler=publish_training_plan,
            allowed_roles=frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH"}),
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_publish_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.training.day.executions.list.v1",
            description="查询训练计划中已经提交的训练日完成或跳过记录；未执行训练日不会伪造记录。",
            input_model=ListTrainingDayExecutionsToolInput,
            handler=list_training_day_executions,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.training.day.record_execution.v1",
            description="学员记录本人已发布训练计划中某个训练日已完成或已跳过；可附加简短备注。",
            input_model=RecordTrainingDayExecutionToolInput,
            handler=record_training_day_execution,
            allowed_roles=frozenset({"STUDENT"}),
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_record_execution_policy(),
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
