"""Operations Agent 第一阶段的受控经营指标工具。

这里不实现任意 Text-to-SQL。模型只能选择固定指标 ID，Python 只调用 Java Gateway 的
只读接口；角色、组织范围、SQL 投影和时间范围会在 Java 侧再次校验。后续增加自然语言
指标解析时，也必须把解析结果映射到这个白名单，而不是把模型文本直接当 SQL 执行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infrastructure.gateway_client import GatewayClient, GatewayOperationsMetric

from .tool_registry import ToolContext, ToolDefinition

_logger = structlog.get_logger("agent.operations")

_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN"})
_METRICS = Literal[
    "APPOINTMENT_COUNT",
    "APPOINTMENT_STATUS_BREAKDOWN",
    "COURSE_APPOINTMENT_COUNT",
    "COACH_APPOINTMENT_COUNT",
    "REMAINING_CLASS_HOURS",
]
_TIME_BUCKETS = Literal["NONE", "DAY", "WEEK"]
_BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class OperationsQueryHint:
    """从自然语言提取的非授权查询提示；最终权限和 SQL 仍由 Gateway 决定。"""

    metric: _METRICS
    from_date: date
    to_date: date
    matched_terms: tuple[str, ...]
    bucket: _TIME_BUCKETS


_METRIC_LABELS = {
    "APPOINTMENT_COUNT": "预约总量",
    "APPOINTMENT_STATUS_BREAKDOWN": "预约状态分布",
    "COURSE_APPOINTMENT_COUNT": "课程预约量",
    "COACH_APPOINTMENT_COUNT": "教练预约量",
    "REMAINING_CLASS_HOURS": "课程剩余课时",
}


def build_operations_report(result: GatewayOperationsMetric) -> dict[str, object]:
    """把固定指标结果转换成可供模型解释的确定性报表摘要。

    <p>摘要由程序根据 Gateway 返回的聚合结果计算，不让模型自行计算总量或百分比。
    当前 Gateway 返回的是整个时间段的聚合值，没有按日/周拆分，因此这里明确标记
    “暂不判断趋势”，避免模型把区间汇总误说成增长或下降。</p>
    """

    rows = list(result.rows)
    total_value = sum(row.value for row in rows)
    top_row = max(rows, key=lambda row: row.value, default=None)
    top_share = (
        round(top_row.value / total_value * 100, 2)
        if top_row is not None and total_value > 0
        else None
    )
    warnings: list[str] = []
    if not rows or total_value == 0:
        warnings.append("该时间范围没有可统计的有效记录，不能据此判断业务异常。")
    elif top_share is not None and len(rows) > 1 and top_share >= 80:
        warnings.append(f"结果集中度较高，第一维度占汇总值 {top_share:.2f}%。")

    report: dict[str, object] = {
        "metric": result.metric,
        "metric_label": _METRIC_LABELS.get(result.metric, result.metric),
        "bucket": result.bucket,
        "period": {"from": result.from_date.isoformat(), "to": result.to_date.isoformat()},
        "row_count": len(rows),
        "total_value": total_value,
        "top_dimension": top_row.label if top_row is not None else None,
        "top_value": top_row.value if top_row is not None else None,
        "top_share_percent": top_share,
        "warnings": warnings,
        "trend_available": False,
        "trend_note": "当前结果未返回足够的日/周时间桶，暂不判断趋势。",
    }
    if result.metric == "APPOINTMENT_STATUS_BREAKDOWN" and total_value > 0:
        report["breakdown"] = [
            {
                "label": row.label,
                "value": row.value,
                "share_percent": round(row.value / total_value * 100, 2),
            }
            for row in rows
        ]
    if result.bucket in {"DAY", "WEEK"}:
        if len(rows) >= 2:
            first_row, last_row = rows[0], rows[-1]
            delta = last_row.value - first_row.value
            change_percent = (
                round(delta / first_row.value * 100, 2) if first_row.value else None
            )
            direction = "UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT"
            report["trend_available"] = True
            report["trend"] = {
                "direction": direction,
                "first_bucket": first_row.label,
                "first_value": first_row.value,
                "last_bucket": last_row.label,
                "last_value": last_row.value,
                "delta": delta,
                "change_percent": change_percent,
                "note": "趋势基于首个和末个有记录的时间桶；没有预约的时间桶不会出现在当前结果中。",
            }
        else:
            report["trend_note"] = "有效时间桶少于 2 个，暂不判断趋势。"
    return report


def build_operations_tool_result(result: GatewayOperationsMetric) -> dict[str, object]:
    """保留 Gateway 原始聚合结果，同时附加程序计算的解释摘要。

    <p>原始结果用于追溯和回答具体维度，report 用于让模型稳定地生成中文解释；两者
    都不包含预约、合同或用户明细记录。</p>
    """

    return {
        "data": result.model_dump(mode="json", by_alias=True),
        "report": build_operations_report(result),
    }


def parse_operations_intent(
    user_message: str,
    *,
    today: date | None = None,
) -> OperationsQueryHint | None:
    """把常见中文经营问题映射到固定指标目录。

    <p>解析器只产生查询提示，不产生 SQL，也不接受用户提供的表名和字段名。无法
    明确判断指标时返回 None，由 Supervisor 要求模型先向用户澄清，避免猜测统计口径。</p>
    """

    text = user_message.lower()
    metric_terms: list[tuple[_METRICS, tuple[str, ...]]] = [
        ("APPOINTMENT_STATUS_BREAKDOWN", ("预约状态", "预约成功率", "取消率", "完成率")),
        ("REMAINING_CLASS_HOURS", ("剩余课时", "课时余额", "剩余课")),
        ("COURSE_APPOINTMENT_COUNT", ("课程预约量", "课程预约", "课程利用", "课程使用")),
        ("COACH_APPOINTMENT_COUNT", ("教练预约量", "教练预约", "教练表现", "教练工作量")),
        ("APPOINTMENT_COUNT", ("预约量", "预约数", "预约总量", "预约多少")),
    ]
    matches = [
        (metric, term)
        for metric, terms in metric_terms
        for term in terms
        if term in text
    ]
    if not matches:
        return None
    # 如果一个短词完整包含在同一问题命中的长词中，则短词只是泛化别名；例如
    # “预约量”包含在“课程预约量”里，应优先使用课程维度。不同指标之间没有这种
    # 包含关系时必须保留全部命中，不能靠最长词擅自丢掉用户想查的第二个指标。
    matches = [
        (metric, term)
        for metric, term in matches
        if not any(term != other_term and term in other_term for _, other_term in matches)
    ]
    metrics = {metric for metric, _ in matches}
    if len(metrics) != 1:
        return None
    metric = next(iter(metrics))
    matched_terms = tuple(term for _, term in matches)
    start, end = _parse_date_range(text, today or datetime.now(_BUSINESS_ZONE).date())
    bucket = _parse_time_bucket(text)
    if bucket is None:
        return None
    return OperationsQueryHint(metric, start, end, matched_terms, bucket)


def operations_prompt_hint(user_message: str) -> str:
    """生成只读的 Operations 提示，明确告诉模型不能退化为任意 SQL。"""

    hint = parse_operations_intent(user_message)
    if hint is None:
        return (
            "经营问题暂未安全映射到唯一指标。请先向用户澄清指标和时间范围；"
            "不要调用经营指标工具，也不要生成或执行任意 SQL。"
        )
    bucket_note = ""
    if hint.bucket != "NONE":
        if hint.metric != "APPOINTMENT_COUNT":
            return (
                "当前仅支持对预约总量按日或按周查询趋势。请先向用户澄清，"
                "不要调用不支持时间桶的经营指标工具，也不要生成任意 SQL。"
            )
        bucket_note = "，时间分组=" + hint.bucket
    return (
        "经营查询安全提示：可优先使用固定指标工具，指标=" + hint.metric
        + "，开始日期=" + hint.from_date.isoformat()
        + "，结束日期=" + hint.to_date.isoformat()
        + bucket_note
        + "。该提示不代表已授权，不能改写为 SQL；工具参数仍须严格使用指标白名单。"
        + "工具返回的 report 是程序根据真实聚合结果计算的摘要；回答时优先引用 report，"
        + "只能陈述返回数据，不得把集中度提示说成因果结论。当前没有日/周时间桶时，"
        + "不得声称趋势增长或下降。"
    )


def _parse_date_range(text: str, today: date) -> tuple[date, date]:
    explicit = re.search(
        r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})\s*(?:到|至|-)\s*"
        r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})",
        text,
    )
    if explicit:
        start = date(int(explicit.group(1)), int(explicit.group(2)), int(explicit.group(3)))
        end = date(int(explicit.group(4)), int(explicit.group(5)), int(explicit.group(6)))
        return start, end
    recent = re.search(r"近\s*(\d{1,3})\s*天", text)
    if recent:
        days = int(recent.group(1))
        if 1 <= days <= 92:
            return today - timedelta(days=days - 1), today
    if "本周" in text:
        return today - timedelta(days=today.weekday()), today
    if "上月" in text:
        first_this_month = today.replace(day=1)
        last_previous_month = first_this_month - timedelta(days=1)
        return last_previous_month.replace(day=1), last_previous_month
    if "本月" in text:
        return today.replace(day=1), today
    return today - timedelta(days=29), today


def _parse_time_bucket(text: str) -> _TIME_BUCKETS | None:
    """识别趋势问题的固定时间桶；同时出现日和周时拒绝猜测。"""

    day_requested = any(term in text for term in ("每天", "每日", "按日", "日趋势"))
    week_requested = any(term in text for term in ("每周", "按周", "周趋势"))
    if day_requested and week_requested:
        return None
    if day_requested:
        return "DAY"
    if week_requested:
        return "WEEK"
    if "趋势" in text:
        return "DAY"
    return "NONE"


class OperationsMetricToolInput(BaseModel):
    """固定指标查询参数；不允许出现 SQL、表名或任意字段名。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    organization_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    metric: _METRICS
    from_date: date | None = Field(default=None, alias="from")
    to_date: date | None = Field(default=None, alias="to")
    limit: int = Field(default=20, ge=1, le=100)
    bucket: _TIME_BUCKETS = "NONE"

    @model_validator(mode="after")
    def validate_range(self) -> OperationsMetricToolInput:
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from must be earlier than or equal to to")
        if self.from_date and self.to_date and (self.to_date - self.from_date).days > 92:
            raise ValueError("operations time range must not exceed 92 days")
        if self.bucket != "NONE" and self.metric != "APPOINTMENT_COUNT":
            raise ValueError("DAY/WEEK buckets currently support APPOINTMENT_COUNT only")
        return self


def build_operations_tool_definitions(gateway: GatewayClient) -> tuple[ToolDefinition, ...]:
    async def query_metric(raw: BaseModel, context: ToolContext) -> object:
        data = cast(OperationsMetricToolInput, raw)
        try:
            result = await gateway.query_operations_metric(
                context.gateway_context,
                data.organization_id,
                data.metric,
                from_date=data.from_date,
                to_date=data.to_date,
                limit=data.limit,
                bucket=data.bucket,
            )
            # 经营查询审计只保留“谁在什么范围查询了哪个固定指标以及结果规模”，
            # 不记录 SQL、Prompt、明细数据或模型原始输出，避免日志变成第二个数据出口。
            _logger.info(
                "operations_query_completed",
                metric=data.metric,
                organization_id=data.organization_id,
                from_date=data.from_date.isoformat() if data.from_date else None,
                to_date=data.to_date.isoformat() if data.to_date else None,
                row_count=len(result.rows),
                request_id=context.gateway_context.request_id,
                trace_id=context.gateway_context.trace_id,
            )
            return build_operations_tool_result(result)
        except Exception:
            _logger.warning(
                "operations_query_failed",
                metric=data.metric,
                organization_id=data.organization_id,
                request_id=context.gateway_context.request_id,
                trace_id=context.gateway_context.trace_id,
            )
            raise

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
