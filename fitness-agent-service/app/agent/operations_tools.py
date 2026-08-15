"""Operations Agent 第一阶段的受控经营指标工具。

这里不实现任意 Text-to-SQL。模型只能选择固定指标 ID，Python 只调用 Java Gateway 的
只读接口；角色、组织范围、SQL 投影和时间范围会在 Java 侧再次校验。后续增加自然语言
指标解析时，也必须把解析结果映射到这个白名单，而不是把模型文本直接当 SQL 执行。
"""

from __future__ import annotations

from datetime import date
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.gateway_client import GatewayClient

from .tool_registry import ToolContext, ToolDefinition

_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN"})
_METRICS = Literal[
    "APPOINTMENT_COUNT",
    "APPOINTMENT_STATUS_BREAKDOWN",
    "COURSE_APPOINTMENT_COUNT",
    "COACH_APPOINTMENT_COUNT",
    "REMAINING_CLASS_HOURS",
]


class OperationsMetricToolInput(BaseModel):
    """固定指标查询参数；不允许出现 SQL、表名或任意字段名。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    organization_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    metric: _METRICS
    from_date: date | None = Field(default=None, alias="from")
    to_date: date | None = Field(default=None, alias="to")
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_range(self) -> OperationsMetricToolInput:
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from must be earlier than or equal to to")
        if self.from_date and self.to_date and (self.to_date - self.from_date).days > 92:
            raise ValueError("operations time range must not exceed 92 days")
        return self


def build_operations_tool_definitions(gateway: GatewayClient) -> tuple[ToolDefinition, ...]:
    async def query_metric(raw: BaseModel, context: ToolContext) -> object:
        data = cast(OperationsMetricToolInput, raw)
        return await gateway.query_operations_metric(
            context.gateway_context,
            data.organization_id,
            data.metric,
            from_date=data.from_date,
            to_date=data.to_date,
            limit=data.limit,
        )

    return (
        ToolDefinition(
            tool_id="fitness.operations.metric.query.v1",
            description=(
                "查询机构经营指标，如预约量、预约状态、课程预约量、教练预约量和剩余课时；"
                "只允许管理员，查询结果来自 Java Gateway 固定只读指标目录。"
            ),
            input_model=OperationsMetricToolInput,
            handler=query_metric,
            allowed_roles=_ADMIN_ROLES,
            read_only=True,
            requires_confirmation=False,
        ),
    )
