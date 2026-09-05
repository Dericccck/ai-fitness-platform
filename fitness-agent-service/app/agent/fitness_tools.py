"""首批健身只读工具适配器。

这些函数只负责把已校验的 Tool Input 转换为 GatewayClient 调用，不拼接任意 URL、
不查询 MySQL，也不在 Python 侧自行判断组织权限。真正的主体、租户和资源关系校验
仍由 Java Tool Gateway 根据签名 AgentContext 执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.confirmation.normalization import ConfirmationPolicy
from app.core.metrics import HttpMetrics
from app.infrastructure.cache import Cache
from app.infrastructure.gateway_client import GatewayClient
from app.memory.service import MemoryService

from .operations_audit import OperationsAuditRepository
from .operations_tools import build_operations_tool_definitions
from .tool_registry import (
    EmptyToolInput,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)

_ID_FIELD = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
_READ_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH", "STUDENT"})
_PLAN_AUTHOR_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN", "COACH"})
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _normalize_gateway_datetime(value: datetime) -> datetime:
    """把模型生成的时间统一为带时区的 UTC Instant。

    中文预约请求经常让模型生成无时区的本地时间，例如 ``2026-08-24T09:00:00``。
    Java Gateway 的契约使用 ``Instant``，无法解析这种值；这里把无时区时间按健身
    业务默认时区 Asia/Shanghai 解释，再统一输出 UTC。带时区输入也会归一化，确保
    确认摘要、加密 Payload 和 Gateway 请求使用同一时刻。
    """

    aware = value.replace(tzinfo=_BUSINESS_TIMEZONE) if value.tzinfo is None else value
    return aware.astimezone(UTC)


class OrganizationToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str = _ID_FIELD


class SaveMemoryToolInput(OrganizationToolInput):
    """保存或覆盖一条用户明确提供的低敏健身 Memory。"""

    memory_type: str = Field(
        pattern=r"^(TRAINING_GOAL|TRAINING_PREFERENCE|EQUIPMENT_AVAILABILITY|"
        r"SCHEDULE_PREFERENCE|COMMUNICATION_PREFERENCE)$"
    )
    memory_key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=500)
    unit: str | None = Field(default=None, max_length=16)
    expires_at: datetime | None = None


class ListMemoryToolInput(OrganizationToolInput):
    """查询当前用户在指定机构内仍生效的长期 Memory。"""


class RevokeMemoryToolInput(OrganizationToolInput):
    """撤销一条 Memory，并用读取时版本防止确认期间误撤销新内容。"""

    memory_id: str = _ID_FIELD
    expected_version: int = Field(ge=1)


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
        if self.from_time:
            self.from_time = _normalize_gateway_datetime(self.from_time)
        if self.to_time:
            self.to_time = _normalize_gateway_datetime(self.to_time)
        if self.from_time and self.to_time and self.from_time >= self.to_time:
            raise ValueError("from_time 必须早于 to_time")
        return self


class BookingAvailabilityToolInput(OrganizationToolInput):
    """预约写入前的只读预检参数；预检成功不代表预约已经创建。"""

    student_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    coach_id: str = _ID_FIELD
    course_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    start_time: datetime
    end_time: datetime
    exclude_appointment_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    @model_validator(mode="after")
    def validate_time_window(self) -> BookingAvailabilityToolInput:
        self.start_time = _normalize_gateway_datetime(self.start_time)
        self.end_time = _normalize_gateway_datetime(self.end_time)
        if self.start_time >= self.end_time:
            raise ValueError("start_time 必须早于 end_time")
        return self


class CustomerServiceTicketListToolInput(OrganizationToolInput):
    """客服工单查询参数；普通用户最终只能看到自己的工单。"""

    subject_user_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    status: Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class CustomerServiceTicketGetToolInput(OrganizationToolInput):
    """查询单个客服工单的稳定事实。"""

    ticket_id: str = _ID_FIELD


class CustomerServiceTicketCreateToolInput(OrganizationToolInput):
    """创建客服工单参数；执行前必须由用户在 interrupt 确认卡中批准。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # 客服服务通过 Java Gateway 接收 camelCase JSON；这里不能直接沿用只读工具的
    # snake_case 机构字段，否则 Gateway 会读不到 organizationId，确认凭证的资源
    # 范围就会与实际请求不一致。模型仍可用 organization_id 生成，跨服务序列化时
    # 统一输出 organizationId。
    organization_id: str = Field(
        alias="organizationId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )

    subject_user_id: str | None = Field(
        default=None,
        alias="subjectUserId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    category: Literal["GENERAL", "APPOINTMENT", "TRAINING_PLAN", "COURSE", "CONTRACT"]
    subject: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=5000)
    related_resource_type: str | None = Field(
        default=None, alias="relatedResourceType", max_length=64
    )
    related_resource_id: str | None = Field(default=None, alias="relatedResourceId", max_length=128)


class BookingCreateToolInput(OrganizationToolInput):
    """创建预约参数；必须在确认恢复后才会真正调用 Gateway 写接口。"""

    # LLM 使用 snake_case 生成参数，但 Java Gateway 的稳定契约是 camelCase。
    # populate_by_name 允许模型输入继续使用 Python 字段名，最终 HTTP 请求通过
    # model_dump(by_alias=True) 输出 Java 约定的字段名；确认摘要和真实请求因此保持完全一致。
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    organization_id: str = Field(
        alias="organizationId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    student_id: str = Field(
        alias="studentId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    contract_id: str = Field(
        alias="contractId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    coach_id: str = Field(
        alias="coachId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    course_id: str = Field(
        alias="courseId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    start_time: datetime = Field(alias="startTime")
    end_time: datetime = Field(alias="endTime")
    mark: int | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_time_window(self) -> BookingCreateToolInput:
        self.start_time = _normalize_gateway_datetime(self.start_time)
        self.end_time = _normalize_gateway_datetime(self.end_time)
        if self.start_time >= self.end_time:
            raise ValueError("start_time 必须早于 end_time")
        if self.end_time - self.start_time > timedelta(hours=8):
            raise ValueError("预约时长不能超过 8 小时")
        return self


class BookingRescheduleToolInput(OrganizationToolInput):
    """改约参数；v1 只允许调整原预约的教练和时间，不更换合同或课程。"""

    # 改约同样以 Java Gateway 的 camelCase 作为跨服务稳定契约。模型可以使用
    # snake_case 填参，但确认哈希和最终 HTTP 请求都必须基于 by_alias 后的 JSON。
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    organization_id: str = Field(
        alias="organizationId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    appointment_id: str = Field(
        alias="appointmentId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    coach_id: str = Field(
        alias="coachId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    # expected_start_time 是乐观并发前置条件：确认卡展示后，如果原预约已被修改，
    # Java 预约服务会拒绝本次改约，避免把用户确认的旧预约误改到新状态。
    expected_start_time: datetime = Field(alias="expectedStartTime")
    start_time: datetime = Field(alias="startTime")
    end_time: datetime = Field(alias="endTime")

    @model_validator(mode="after")
    def validate_time_window(self) -> BookingRescheduleToolInput:
        self.expected_start_time = _normalize_gateway_datetime(self.expected_start_time)
        self.start_time = _normalize_gateway_datetime(self.start_time)
        self.end_time = _normalize_gateway_datetime(self.end_time)
        if self.start_time >= self.end_time:
            raise ValueError("start_time 必须早于 end_time")
        if self.end_time - self.start_time > timedelta(hours=8):
            raise ValueError("预约时长不能超过 8 小时")
        return self


class BookingCancelToolInput(OrganizationToolInput):
    """取消参数；v1 只允许取消尚未开始的预约，成功后退回一个课时。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    organization_id: str = Field(
        alias="organizationId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    appointment_id: str = Field(
        alias="appointmentId",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    # 取消确认卡绑定用户查询到的原开始时间；如果确认后预约发生变化，Java 侧拒绝执行。
    expected_start_time: datetime = Field(alias="expectedStartTime")

    @model_validator(mode="after")
    def normalize_expected_start_time(self) -> BookingCancelToolInput:
        self.expected_start_time = _normalize_gateway_datetime(self.expected_start_time)
        return self


class TrainingItemInput(BaseModel):
    # Agent 内部统一使用 snake_case，但 Java Gateway 的跨服务 JSON 契约使用
    # camelCase。这里必须在 Schema 层声明别名，不能只在确认摘要里“看起来”转换；
    # 否则确认凭证按 organizationId/studentId 计算的资源范围，与真正发送给
    # Gateway 的 JSON 字段就会发生漂移，Gateway 会拒绝执行高风险写操作。
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    exercise_name: str = Field(alias="exerciseName", min_length=1, max_length=128)
    sort_order: int = Field(alias="sortOrder", ge=1, le=100)
    sets: int = Field(ge=1, le=100)
    reps: str = Field(min_length=1, max_length=64)
    rest_seconds: int | None = Field(alias="restSeconds", default=None, ge=0, le=3600)
    target_weight_kg: float | None = Field(alias="targetWeightKg", default=None, ge=0, le=1000)
    target_rpe: float | None = Field(alias="targetRpe", default=None, ge=0, le=10)
    notes: str | None = Field(default=None, max_length=1000)


class TrainingDayInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    day_number: int = Field(alias="dayNumber", ge=1, le=31)
    title: str = Field(min_length=1, max_length=128)
    scheduled_date: str | None = Field(alias="scheduledDate", default=None)
    items: list[TrainingItemInput] = Field(min_length=1, max_length=100)


class CreateTrainingDraftToolInput(BaseModel):
    # 这是 Agent 工具输入的最后一道跨服务契约边界：模型仍可以生成 snake_case，
    # 但 model_dump(by_alias=True) 必须输出 Java TrainingToolInputs.DraftInput
    # 能够反序列化的字段名。确认摘要、payload 哈希和真实 HTTP 请求都复用这个
    # Schema，保证“用户确认的内容”与“Gateway 实际收到的内容”完全一致。
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    organization_id: str = Field(
        alias="organizationId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    student_id: str = Field(
        alias="studentId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    coach_id: str = Field(
        alias="coachId", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    title: str = Field(min_length=1, max_length=128)
    goal_type: str = Field(alias="goalType", min_length=1, max_length=32)
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
            raise ValueError("decision 为 REJECT 时必须填写 comment")
        return self


def _create_training_draft_payload(data: CreateTrainingDraftToolInput) -> dict[str, object]:
    """生成创建草案的唯一 Gateway Payload，确认摘要与真实执行共用它。"""

    return data.model_dump(mode="json", by_alias=True)


def _create_booking_payload(data: BookingCreateToolInput) -> dict[str, object]:
    """创建预约的唯一 Payload，确认摘要和最终 HTTP 请求共用。"""

    return data.model_dump(mode="json", by_alias=True, exclude_none=True)


def _reschedule_booking_payload(data: BookingRescheduleToolInput) -> dict[str, object]:
    """生成改约的唯一 Payload，确认展示、确认哈希和真实调用共用。"""

    return data.model_dump(mode="json", by_alias=True)


def _cancel_booking_payload(data: BookingCancelToolInput) -> dict[str, object]:
    """生成取消预约的唯一 Payload，确认摘要和真实 Gateway 请求共用。"""

    return data.model_dump(mode="json", by_alias=True)


def _create_customer_service_ticket_payload(
    data: CustomerServiceTicketCreateToolInput,
) -> dict[str, object]:
    """创建工单的唯一 Payload，确认摘要和最终 Gateway 请求共用。"""

    return data.model_dump(mode="json", by_alias=True, exclude_none=True)


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


def _create_booking_summary(data: BaseModel, _: Mapping[str, object] | None) -> dict[str, object]:
    typed = cast(BookingCreateToolInput, data)
    return _summary(
        "创建健身预约",
        "CREATE_APPOINTMENT",
        "APPOINTMENT_SUCCESS",
        _create_booking_payload(typed),
        None,
        organization_id=typed.organization_id,
        resource_type="appointment",
        resource_id=typed.contract_id,
    )


def _create_customer_service_ticket_summary(
    data: BaseModel, _: Mapping[str, object] | None
) -> dict[str, object]:
    typed = cast(CustomerServiceTicketCreateToolInput, data)
    return _summary(
        "创建健身客服工单",
        "CREATE_CUSTOMER_SERVICE_TICKET",
        "OPEN",
        _create_customer_service_ticket_payload(typed),
        None,
        organization_id=typed.organization_id,
        resource_type="customer_service_ticket",
        resource_id=f"{typed.organization_id}:{typed.subject_user_id or 'current-user'}",
    )


def _reschedule_booking_summary(
    data: BaseModel, _: Mapping[str, object] | None
) -> dict[str, object]:
    typed = cast(BookingRescheduleToolInput, data)
    return _summary(
        "改约健身预约",
        "RESCHEDULE_APPOINTMENT",
        "APPOINTMENT_SUCCESS",
        _reschedule_booking_payload(typed),
        None,
        organization_id=typed.organization_id,
        resource_type="appointment",
        resource_id=typed.appointment_id,
    )


def _cancel_booking_summary(data: BaseModel, _: Mapping[str, object] | None) -> dict[str, object]:
    typed = cast(BookingCancelToolInput, data)
    return _summary(
        "取消健身预约",
        "CANCEL_APPOINTMENT",
        "CANCELLED",
        _cancel_booking_payload(typed),
        None,
        organization_id=typed.organization_id,
        resource_type="appointment",
        resource_id=typed.appointment_id,
    )


def _save_memory_payload(data: SaveMemoryToolInput) -> dict[str, object]:
    """生成 Memory 保存的唯一 Payload，确认展示和落库共用同一份字段。"""

    return data.model_dump(mode="json", exclude_none=True)


def _save_memory_summary(data: BaseModel, _: Mapping[str, object] | None) -> dict[str, object]:
    typed = cast(SaveMemoryToolInput, data)
    payload = _save_memory_payload(typed)
    return _summary(
        "保存或更新健身 Memory",
        "SAVE_FITNESS_MEMORY",
        "ACTIVE",
        payload,
        None,
        organization_id=typed.organization_id,
        resource_type="agent_memory",
    )


def _revoke_memory_summary(data: BaseModel, _: Mapping[str, object] | None) -> dict[str, object]:
    typed = cast(RevokeMemoryToolInput, data)
    payload = typed.model_dump(mode="json")
    return _summary(
        "撤销健身 Memory",
        "REVOKE_FITNESS_MEMORY",
        "REVOKED",
        payload,
        None,
        organization_id=typed.organization_id,
        resource_type="agent_memory",
        resource_id=typed.memory_id,
        expected_resource_version=typed.expected_version,
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


def _create_booking_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="CREATE_APPOINTMENT",
        resource_type="appointment",
        risk_level="WRITE",
        operation="创建健身预约",
        target_status="APPOINTMENT_SUCCESS",
        payload_builder=lambda raw: _create_booking_payload(cast(BookingCreateToolInput, raw)),
        summary_builder=_create_booking_summary,
        resource_id_builder=lambda raw: cast(BookingCreateToolInput, raw).contract_id,
        organization_id_builder=lambda raw: cast(BookingCreateToolInput, raw).organization_id,
    )


def _create_customer_service_ticket_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="CREATE_CUSTOMER_SERVICE_TICKET",
        resource_type="customer_service_ticket",
        risk_level="WRITE",
        operation="创建健身客服工单",
        target_status="OPEN",
        payload_builder=lambda raw: _create_customer_service_ticket_payload(
            cast(CustomerServiceTicketCreateToolInput, raw)
        ),
        summary_builder=_create_customer_service_ticket_summary,
        organization_id_builder=lambda raw: (
            cast(CustomerServiceTicketCreateToolInput, raw).organization_id
        ),
        resource_id_builder=lambda raw: (
            f"{cast(CustomerServiceTicketCreateToolInput, raw).organization_id}:"
            f"{cast(CustomerServiceTicketCreateToolInput, raw).subject_user_id or 'current-user'}"
        ),
    )


def _reschedule_booking_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="RESCHEDULE_APPOINTMENT",
        resource_type="appointment",
        risk_level="WRITE",
        operation="改约健身预约",
        target_status="APPOINTMENT_SUCCESS",
        payload_builder=lambda raw: _reschedule_booking_payload(
            cast(BookingRescheduleToolInput, raw)
        ),
        summary_builder=_reschedule_booking_summary,
        resource_id_builder=lambda raw: cast(BookingRescheduleToolInput, raw).appointment_id,
        organization_id_builder=lambda raw: cast(BookingRescheduleToolInput, raw).organization_id,
    )


def _cancel_booking_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="CANCEL_APPOINTMENT",
        resource_type="appointment",
        risk_level="WRITE",
        operation="取消健身预约",
        target_status="CANCELLED",
        payload_builder=lambda raw: _cancel_booking_payload(cast(BookingCancelToolInput, raw)),
        summary_builder=_cancel_booking_summary,
        resource_id_builder=lambda raw: cast(BookingCancelToolInput, raw).appointment_id,
        organization_id_builder=lambda raw: cast(BookingCancelToolInput, raw).organization_id,
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


def _save_memory_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="SAVE_FITNESS_MEMORY",
        resource_type="agent_memory",
        risk_level="WRITE",
        operation="保存或更新健身 Memory",
        target_status="ACTIVE",
        payload_builder=lambda raw: _save_memory_payload(cast(SaveMemoryToolInput, raw)),
        summary_builder=_save_memory_summary,
        organization_id_builder=lambda raw: cast(SaveMemoryToolInput, raw).organization_id,
    )


def _revoke_memory_policy() -> ConfirmationPolicy:
    return ConfirmationPolicy(
        action="REVOKE_FITNESS_MEMORY",
        resource_type="agent_memory",
        risk_level="WRITE",
        operation="撤销健身 Memory",
        target_status="REVOKED",
        payload_builder=lambda raw: cast(RevokeMemoryToolInput, raw).model_dump(mode="json"),
        summary_builder=_revoke_memory_summary,
        resource_id_builder=lambda raw: cast(RevokeMemoryToolInput, raw).memory_id,
        organization_id_builder=lambda raw: cast(RevokeMemoryToolInput, raw).organization_id,
        resource_version_builder=lambda raw: cast(RevokeMemoryToolInput, raw).expected_version,
    )


def build_fitness_tool_registry(
    gateway: GatewayClient,
    *,
    plan_generator: Any | None = None,
    memory_service: MemoryService | None = None,
    operations_audit_repository: OperationsAuditRepository | None = None,
    operations_rate_limit_cache: Cache | None = None,
    operations_rate_limit_requests: int = 60,
    operations_rate_limit_window_seconds: int = 60,
    operations_query_timeout_seconds: float | None = None,
    operations_metrics: HttpMetrics | None = None,
    telemetry: Any | None = None,
) -> ToolRegistry:
    """创建进程级健身工具注册表。

    Registry 在应用生命周期内只创建一次，所有工具共享 Gateway 连接池。每个 handler
    都显式绑定到一个固定的 GatewayClient 方法，后续增加写工具时可以在这里集中审查
    角色、确认和幂等元数据，而不是让模型或业务 Agent 自由调用 HTTP 客户端。
    """

    registry = ToolRegistry(telemetry=telemetry)

    # 延迟导入是有意的：生成服务需要复用本文件中的训练计划 Schema，若在模块顶部
    # 导入会形成循环依赖。函数执行时本模块已经完成加载，仍然保持单一 Schema 来源。
    from .training_plan_generation import (
        TrainingPlanGenerationError,
        TrainingPlanGenerationInput,
    )

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

    async def check_booking_availability(raw: BaseModel, context: ToolContext) -> object:
        data = cast(BookingAvailabilityToolInput, raw)
        return await gateway.check_booking_availability(
            context.gateway_context,
            data.organization_id,
            student_id=data.student_id,
            coach_id=data.coach_id,
            course_id=data.course_id,
            start_time=data.start_time,
            end_time=data.end_time,
            exclude_appointment_id=data.exclude_appointment_id,
        )

    async def list_customer_service_tickets(raw: BaseModel, context: ToolContext) -> object:
        data = cast(CustomerServiceTicketListToolInput, raw)
        return await gateway.list_customer_service_tickets(
            context.gateway_context,
            data.organization_id,
            subject_user_id=data.subject_user_id,
            status=data.status,
            limit=data.limit,
        )

    async def get_customer_service_ticket(raw: BaseModel, context: ToolContext) -> object:
        data = cast(CustomerServiceTicketGetToolInput, raw)
        return await gateway.get_customer_service_ticket(
            context.gateway_context, data.organization_id, data.ticket_id
        )

    async def create_customer_service_ticket(raw: BaseModel, context: ToolContext) -> object:
        data = cast(CustomerServiceTicketCreateToolInput, raw)
        return await gateway.create_customer_service_ticket(
            context.gateway_context,
            _create_customer_service_ticket_payload(data),
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

    async def create_booking(raw: BaseModel, context: ToolContext) -> object:
        data = cast(BookingCreateToolInput, raw)
        return await gateway.create_booking(
            context.gateway_context,
            _create_booking_payload(data),
        )

    async def reschedule_booking(raw: BaseModel, context: ToolContext) -> object:
        data = cast(BookingRescheduleToolInput, raw)
        return await gateway.reschedule_booking(
            context.gateway_context,
            _reschedule_booking_payload(data),
        )

    async def cancel_booking(raw: BaseModel, context: ToolContext) -> object:
        data = cast(BookingCancelToolInput, raw)
        return await gateway.cancel_booking(
            context.gateway_context,
            _cancel_booking_payload(data),
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

    async def generate_training_plan_draft(raw: BaseModel, context: ToolContext) -> object:
        """生成只读草案预览；不调用 Gateway 写接口，也不创建确认单。"""

        if plan_generator is None:
            raise TrainingPlanGenerationError("训练计划生成依赖未配置")
        if context.identity is None:
            raise TrainingPlanGenerationError("生成训练计划需要已验证的 AgentContext")
        return await plan_generator.generate(
            cast(TrainingPlanGenerationInput, raw),
            context.identity,
            context.gateway_context,
        )

    async def list_memories(raw: BaseModel, context: ToolContext) -> object:
        """只返回签名身份本人在指定机构内的 active Memory。"""

        if memory_service is None or context.identity is None:
            raise RuntimeError("Memory 服务和已验证的 AgentContext 均为必需项")
        data = cast(ListMemoryToolInput, raw)
        memories = await memory_service.list_active(
            identity=context.identity, organization_id=data.organization_id
        )
        return [_memory_view(memory) for memory in memories]

    async def save_memory(raw: BaseModel, context: ToolContext) -> object:
        """确认恢复后保存 Memory；主体始终来自签名上下文，不接受模型指定用户。"""

        if memory_service is None or context.identity is None:
            raise RuntimeError("Memory 服务和已验证的 AgentContext 均为必需项")
        data = cast(SaveMemoryToolInput, raw)
        request_id = context.gateway_context.request_id
        if not request_id:
            raise RuntimeError("已确认的 Memory 写入需要 request_id")
        memory = await memory_service.save(
            identity=context.identity,
            organization_id=data.organization_id,
            memory_type=data.memory_type,
            memory_key=data.memory_key,
            value=data.value,
            unit=data.unit,
            expires_at=data.expires_at,
            source_request_id=request_id,
            request_id=request_id,
        )
        return _memory_view(memory)

    async def revoke_memory(raw: BaseModel, context: ToolContext) -> object:
        """确认恢复后撤销 Memory，保留数据库记录但移出后续上下文。"""

        if memory_service is None or context.identity is None:
            raise RuntimeError("Memory 服务和已验证的 AgentContext 均为必需项")
        data = cast(RevokeMemoryToolInput, raw)
        request_id = context.gateway_context.request_id
        if not request_id:
            raise RuntimeError("已确认的 Memory 撤销需要 request_id")
        memory = await memory_service.revoke(
            identity=context.identity,
            organization_id=data.organization_id,
            memory_id=data.memory_id,
            expected_version=data.expected_version,
            source_request_id=request_id,
            request_id=request_id,
        )
        return _memory_view(memory)

    def _memory_view(memory: Any) -> dict[str, object]:
        """把领域对象转换为可写入消息和 HTTP 响应的稳定 JSON。"""

        return {
            "id": memory.id,
            "organization_id": memory.organization_id,
            "memory_type": memory.memory_type,
            "memory_key": memory.memory_key,
            "content": memory.content,
            "status": memory.status,
            "version": memory.version,
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
        }

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
            tool_id="fitness.training.plan.generate_draft.v1",
            description=(
                "检索已发布健身知识并生成结构化训练计划草案预览；不会直接写入，"
                "后续创建草案仍需用户确认并经过教练审核。"
            ),
            input_model=TrainingPlanGenerationInput,
            handler=generate_training_plan_draft,
            allowed_roles=_PLAN_AUTHOR_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.create_draft.v1",
            description="创建结构化训练计划草案；草案不能直接发布，必须经过教练审核。",
            input_model=CreateTrainingDraftToolInput,
            handler=create_training_draft,
            # 学员可以查看已发布计划和提交执行记录，但不能通过自然语言触发计划
            # 创建。即使这个写操作还需要 interrupt/确认凭证，也不能把学员暴露到
            # “先创建再等待审核”的教练工作流中；Java Gateway 仍会做最终资源校验。
            allowed_roles=_PLAN_AUTHOR_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_create_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.booking.create.v1",
            description=(
                "用户明确要求创建预约时必须调用本工具；执行前必须展示预约时间、教练、课程、"
                "合同和扣减课时确认卡，工具本身不会绕过确认直接写入。"
            ),
            input_model=BookingCreateToolInput,
            handler=create_booking,
            allowed_roles=_READ_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_create_booking_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.booking.reschedule.v1",
            description="改约健身预约；v1 只调整教练和时间，执行前必须展示改约确认卡。",
            input_model=BookingRescheduleToolInput,
            handler=reschedule_booking,
            allowed_roles=_READ_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_reschedule_booking_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.booking.cancel.v1",
            description="取消尚未开始的健身预约并退回一个课时；执行前必须展示取消确认卡。",
            input_model=BookingCancelToolInput,
            handler=cancel_booking,
            allowed_roles=_READ_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_cancel_booking_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.memory.list.v1",
            description="查询当前用户在指定机构内已确认且未过期的健身 Memory。",
            input_model=ListMemoryToolInput,
            handler=list_memories,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.memory.save.v1",
            description=(
                "保存或覆盖用户明确提供的训练目标、偏好、器械、时间或沟通偏好；"
                "执行前必须展示确认卡。"
            ),
            input_model=SaveMemoryToolInput,
            handler=save_memory,
            allowed_roles=_READ_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_save_memory_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.memory.revoke.v1",
            description="撤销当前用户的一条健身 Memory；执行前必须展示确认卡。",
            input_model=RevokeMemoryToolInput,
            handler=revoke_memory,
            allowed_roles=_READ_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_revoke_memory_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.submit_review.v1",
            description="提交训练计划审核；只有负责教练或机构管理员可以完成该状态转换。",
            input_model=TrainingPlanToolInput,
            handler=submit_training_review,
            allowed_roles=_PLAN_AUTHOR_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_submit_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.review.v1",
            description="审核或驳回训练计划；驳回必须填写原因。",
            input_model=ReviewTrainingPlanToolInput,
            handler=review_training_plan,
            allowed_roles=_PLAN_AUTHOR_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_review_policy(),
        ),
        ToolDefinition(
            tool_id="fitness.training.plan.publish.v1",
            description="发布已审核通过的训练计划，发布后学员才可以执行。",
            input_model=TrainingPlanToolInput,
            handler=publish_training_plan,
            allowed_roles=_PLAN_AUTHOR_ROLES,
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
        ToolDefinition(
            tool_id="fitness.booking.availability.check.v1",
            description="检查指定教练和时间段是否满足当前已接入的预约规则；只读预检，不会创建预约或扣减课时。",
            input_model=BookingAvailabilityToolInput,
            handler=check_booking_availability,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.support.ticket.list.v1",
            description="查询当前权限范围内的客服工单；只读，不会创建或修改工单。",
            input_model=CustomerServiceTicketListToolInput,
            handler=list_customer_service_tickets,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.support.ticket.get.v1",
            description="查询一条当前权限范围内的客服工单详情；只读，不会改变工单状态。",
            input_model=CustomerServiceTicketGetToolInput,
            handler=get_customer_service_ticket,
            allowed_roles=_READ_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
        ToolDefinition(
            tool_id="fitness.support.ticket.create.v1",
            description=(
                "用户明确要求提交健身业务问题时创建客服工单；必须先通过 interrupt 展示标题、"
                "分类和描述并获得用户确认，不能因普通咨询自动创建。"
            ),
            input_model=CustomerServiceTicketCreateToolInput,
            handler=create_customer_service_ticket,
            allowed_roles=_READ_ROLES,
            read_only=False,
            requires_confirmation=True,
            confirmation_policy=_create_customer_service_ticket_policy(),
        ),
    ) + build_operations_tool_definitions(
        gateway,
        audit_repository=operations_audit_repository,
        rate_limit_cache=operations_rate_limit_cache,
        rate_limit_requests=operations_rate_limit_requests,
        rate_limit_window_seconds=operations_rate_limit_window_seconds,
        query_timeout_seconds=operations_query_timeout_seconds,
        metrics=operations_metrics,
    )
    for definition in definitions:
        registry.register(definition)
    return registry
