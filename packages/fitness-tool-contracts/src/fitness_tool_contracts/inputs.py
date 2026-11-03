"""Versioned business inputs shared by the Agent and MCP adapter."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID_FIELD = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
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


class CourseListToolInput(OrganizationToolInput):
    limit: int | None = Field(default=20, ge=1, le=100)


class ContractListToolInput(OrganizationToolInput):
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    limit: int | None = Field(default=20, ge=1, le=100)


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
    limit: int | None = Field(default=20, ge=1, le=100)


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
    session_minutes: int = Field(alias="sessionMinutes", ge=20, le=180)
    available_equipment: list[str] = Field(alias="availableEquipment", max_length=20)
    constraints: str | None = Field(default=None, max_length=1000)
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


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OperationsMetricInput(OrganizationToolInput):
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
    from_date: date | None = None
    to_date: date | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    bucket: Literal["NONE", "DAY", "WEEK"] = "NONE"

    @model_validator(mode="after")
    def dates(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not follow to_date")
        return self


class BookingCreateArguments(EmptyInput):
    payload: BookingCreateToolInput


class BookingRescheduleArguments(EmptyInput):
    payload: BookingRescheduleToolInput


class BookingCancelArguments(EmptyInput):
    payload: BookingCancelToolInput


class TicketCreateArguments(EmptyInput):
    payload: CustomerServiceTicketCreateToolInput


class DraftCreateArguments(EmptyInput):
    payload: CreateTrainingDraftToolInput


class ReviewPayload(EmptyInput):
    decision: Literal["APPROVE", "REJECT"]
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def rejection_comment(self):
        if self.decision == "REJECT" and not self.comment:
            raise ValueError("rejection requires a comment")
        return self


class ReviewArguments(TrainingPlanToolInput):
    payload: ReviewPayload


class ExecutionPayload(EmptyInput):
    # Existing Gateway method payloads also carry the persisted snake_case day ID.
    day_id: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    status: Literal["COMPLETED", "SKIPPED"]
    note: str | None = Field(default=None, max_length=1000)


class ExecutionArguments(TrainingPlanToolInput):
    day_id: str = _ID_FIELD
    payload: ExecutionPayload

    @model_validator(mode="after")
    def matching_day(self) -> ExecutionArguments:
        if self.payload.day_id is not None and self.payload.day_id != self.day_id:
            raise ValueError("payload day_id must match the route")
        return self
