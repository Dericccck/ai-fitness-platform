"""Versioned Java Tool View DTOs. No Agent, LLM or database imports."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class GatewayStudentTrainingContextAccess(_GatewayModel):
    organization_id: str = Field(alias="organizationId")
    actor_id: str = Field(alias="actorId")
    student_id: str = Field(alias="studentId")
    access_type: Literal["SELF", "ASSIGNED_COACH", "ADMIN"] = Field(alias="accessType")


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


class GatewayBookingAvailability(_GatewayModel):
    """Java Gateway 的预约预检结果；available 不等于预约已创建。"""

    organization_id: str = Field(alias="organizationId")
    student_id: str = Field(alias="studentId")
    coach_id: str = Field(alias="coachId")
    course_id: str | None = Field(default=None, alias="courseId")
    start_time: datetime = Field(alias="startTime")
    end_time: datetime = Field(alias="endTime")
    available: bool
    reason_codes: list[str] = Field(alias="reasonCodes")
    conflicts: list[GatewayAppointment]


class GatewayOperationsMetricRow(_GatewayModel):
    """Java Gateway 返回的一行已聚合经营指标，不包含明细业务记录。"""

    dimension: str
    label: str
    value: int


class GatewayOperationsMetric(_GatewayModel):
    """组织管理员可读取的固定指标查询结果。"""

    metric: str
    bucket: Literal["NONE", "DAY", "WEEK"] = "NONE"
    organization_id: str = Field(alias="organizationId")
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    rows: list[GatewayOperationsMetricRow]
    generated_at: datetime = Field(alias="generatedAt")


class GatewayCustomerServiceTicket(_GatewayModel):
    """客服服务返回的只读工单事实；空列表表示当前范围内没有工单。"""

    id: str
    organization_id: str = Field(alias="organizationId")
    subject_user_id: str = Field(alias="subjectUserId")
    created_by_user_id: str = Field(alias="createdByUserId")
    category: str
    source: str
    subject: str
    description: str
    status: Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
    related_resource_type: str | None = Field(default=None, alias="relatedResourceType")
    related_resource_id: str | None = Field(default=None, alias="relatedResourceId")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")


class GatewayBookingCreated(_GatewayModel):
    """创建预约后的稳定事实，包含扣减后的剩余课时。"""

    id: str
    organization_id: str = Field(alias="organizationId")
    user_id: str = Field(alias="userId")
    coach_id: str = Field(alias="coachId")
    course_id: str = Field(alias="courseId")
    course_name: str | None = Field(default=None, alias="courseName")
    start_time: datetime = Field(alias="startTime")
    end_time: datetime = Field(alias="endTime")
    status: int
    contract_id: str = Field(alias="contractId")
    remaining_class_hours: int = Field(alias="remainingClassHours")


class GatewayBookingCancelled(GatewayBookingCreated):
    """取消预约后的稳定事实，明确表示旧预约已被删除标记并退回课时。"""

    cancelled: bool


class GatewayBookingOperation(_GatewayModel):
    operation_id: str = Field(alias="operationId")
    status: Literal["SUCCEEDED", "PROCESSING", "FAILED", "UNKNOWN"]
    organization_id: str | None = Field(default=None, alias="organizationId")
    actor_id: str | None = Field(default=None, alias="actorId")
    appointment: GatewayBookingCreated | None = None


class GatewayTrainingItem(_GatewayModel):
    id: str
    exercise_name: str = Field(alias="exerciseName")
    sort_order: int = Field(alias="sortOrder")
    sets: int
    reps: str
    rest_seconds: int | None = Field(default=None, alias="restSeconds")
    target_weight_kg: float | None = Field(default=None, alias="targetWeightKg")
    target_rpe: float | None = Field(default=None, alias="targetRpe")
    notes: str | None = None


class GatewayTrainingDay(_GatewayModel):
    id: str
    day_number: int = Field(alias="dayNumber")
    title: str
    scheduled_date: str | None = Field(default=None, alias="scheduledDate")
    items: list[GatewayTrainingItem]


class GatewayTrainingPlan(_GatewayModel):
    id: str
    organization_id: str = Field(alias="organizationId")
    student_id: str = Field(alias="studentId")
    coach_id: str = Field(alias="coachId")
    title: str
    goal_type: str = Field(alias="goalType")
    # 来源只说明计划由 Agent 草拟还是教练直接创建，不代表审核结果。
    source: Literal["AGENT", "COACH"]
    # Java 训练计划状态：DRAFT 草案；PENDING_REVIEW 待教练审核；APPROVED 已审核；
    # REJECTED 已驳回；PUBLISHED 已发布。学员只能读取和执行 PUBLISHED 计划。
    status: Literal["DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "PUBLISHED"]
    version: int
    created_by: str = Field(alias="createdBy")
    reviewed_by: str | None = Field(default=None, alias="reviewedBy")
    published_by: str | None = Field(default=None, alias="publishedBy")
    review_comment: str | None = Field(default=None, alias="reviewComment")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    reviewed_at: datetime | None = Field(default=None, alias="reviewedAt")
    published_at: datetime | None = Field(default=None, alias="publishedAt")
    days: list[GatewayTrainingDay]


class GatewayTrainingDayExecution(_GatewayModel):
    """训练日完成/跳过记录；本阶段不包含逐组实绩或健康量表。"""

    id: str
    plan_id: str = Field(alias="planId")
    day_id: str = Field(alias="dayId")
    organization_id: str = Field(alias="organizationId")
    student_id: str = Field(alias="studentId")
    status: Literal["COMPLETED", "SKIPPED"]
    execution_date: str = Field(alias="executionDate")
    note: str | None = None
    version: int
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
