"""Operations Agent 第一阶段的受控经营指标工具。

这里不实现任意 Text-to-SQL。模型只能选择固定指标 ID，Python 只调用 Java Gateway 的
只读接口；角色、组织范围、SQL 投影和时间范围会在 Java 侧再次校验。后续增加自然语言
指标解析时，也必须把解析结果映射到这个白名单，而不是把模型文本直接当 SQL 执行。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.metrics import HttpMetrics
from app.infrastructure.cache import Cache
from app.infrastructure.gateway_client import GatewayClient, GatewayOperationsMetric

from .operations_audit import (
    OperationsAuditPersistenceError,
    OperationsAuditRepository,
)
from .tool_registry import ToolContext, ToolDefinition

_logger = structlog.get_logger("agent.operations")

_ADMIN_ROLES = frozenset({"SYSTEM_ADMIN", "ORGANIZATION_ADMIN"})
_METRICS = Literal[
    "APPOINTMENT_COUNT",
    "APPOINTMENT_STATUS_BREAKDOWN",
    "COMPLETED_CLASS_COUNT",
    "NEW_CUSTOMER_COUNT",
    "REVENUE_AMOUNT",
    "COURSE_APPOINTMENT_COUNT",
    "COACH_APPOINTMENT_COUNT",
    "REMAINING_CLASS_HOURS",
]
_TIME_BUCKETS = Literal["NONE", "DAY", "WEEK"]
_COMPARISONS = Literal["NONE", "PREVIOUS_PERIOD", "SAME_PERIOD_LAST_YEAR"]
_BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")
OPERATIONS_MAX_GATEWAY_CALLS = 2


@dataclass(frozen=True)
class OperationsQueryHint:
    """从自然语言提取的非授权查询提示；最终权限和 SQL 仍由 Gateway 决定。"""

    metric: _METRICS
    from_date: date
    to_date: date
    matched_terms: tuple[str, ...]
    bucket: _TIME_BUCKETS
    comparison: _COMPARISONS


@dataclass(frozen=True)
class OperationsQueryPolicyDecision:
    """自然语言问题与模型工具参数的一致性校验结果。"""

    allowed: bool
    reason_code: str
    message: str


@dataclass(frozen=True)
class OperationsMetricDefinition:
    """固定经营指标的业务口径和可用能力。"""

    metric: _METRICS
    label: str
    description: str
    dimension_description: str
    supported_buckets: frozenset[_TIME_BUCKETS]
    supports_previous_period: bool
    supports_year_over_year: bool


# 这是 Agent、Gateway 和前端可共享的受控口径目录。新增指标必须同时补充数据范围、
# 维度含义和能力边界，不能只在自然语言解析器里增加一个关键词。
OPERATIONS_METRIC_CATALOG: tuple[OperationsMetricDefinition, ...] = (
    OperationsMetricDefinition(
        "APPOINTMENT_COUNT",
        "预约总量",
        "指定机构和时间范围内的预约记录总数。",
        "总量维度，不返回预约明细。",
        frozenset({"NONE", "DAY", "WEEK"}),
        True,
        True,
    ),
    OperationsMetricDefinition(
        "APPOINTMENT_STATUS_BREAKDOWN",
        "预约状态分布",
        "按预约状态统计数量，用于观察待确认、成功、完成和取消等状态构成。",
        "预约状态编码和脱敏后的状态名称。",
        frozenset({"NONE"}),
        False,
        False,
    ),
    OperationsMetricDefinition(
        "COMPLETED_CLASS_COUNT",
        "完课量",
        "指定机构和时间范围内，预约状态为已完成的课程次数；不返回学员明细。",
        "完课总量或按日期分组的完课次数。",
        frozenset({"NONE", "DAY", "WEEK"}),
        True,
        True,
    ),
    OperationsMetricDefinition(
        "NEW_CUSTOMER_COUNT",
        "新客量",
        "指定机构和合同创建时间范围内，被标记为新客的有效合同所涉及的去重学员数；不返回学员明细。",
        "新客总量或按合同创建日期分组的新客数量。",
        frozenset({"NONE", "DAY", "WEEK"}),
        True,
        True,
    ),
    OperationsMetricDefinition(
        "REVENUE_AMOUNT",
        "营收金额",
        "指定机构和合同创建时间范围内，有效合同总金额扣除合同退款金额后的净营收；不返回合同明细，金额单位沿用业务合同字段。",
        "营收总额或按合同创建日期分组的净营收金额。",
        frozenset({"NONE", "DAY", "WEEK"}),
        True,
        True,
    ),
    OperationsMetricDefinition(
        "COURSE_APPOINTMENT_COUNT",
        "课程预约量",
        "按课程统计有课程 ID 的预约数量，用于观察课程使用规模。",
        "课程 ID 和课程名称，不返回学员明细。",
        frozenset({"NONE", "DAY", "WEEK"}),
        True,
        True,
    ),
    OperationsMetricDefinition(
        "COACH_APPOINTMENT_COUNT",
        "教练预约量",
        "按教练统计有教练 ID 的预约数量，用于观察教练预约工作量。",
        "教练 ID 及其展示标签，不返回学员明细。",
        frozenset({"NONE", "DAY", "WEEK"}),
        True,
        True,
    ),
    OperationsMetricDefinition(
        "REMAINING_CLASS_HOURS",
        "课程剩余课时",
        "按课程汇总当前合同中的剩余课时。",
        "课程 ID 和课程名称，不返回合同明细。",
        frozenset({"NONE"}),
        False,
        False,
    ),
)
_METRIC_CATALOG_BY_ID: dict[str, OperationsMetricDefinition] = {
    item.metric: item for item in OPERATIONS_METRIC_CATALOG
}


def _build_operations_metric_catalog_version() -> str:
    """根据指标定义内容生成稳定版本，避免修改口径后忘记手工更新版本号。"""

    payload = [
        {
            "metric": item.metric,
            "label": item.label,
            "description": item.description,
            "dimension_description": item.dimension_description,
            "supported_buckets": sorted(item.supported_buckets),
            "supports_previous_period": item.supports_previous_period,
            "supports_year_over_year": item.supports_year_over_year,
        }
        for item in OPERATIONS_METRIC_CATALOG
    ]
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


# 版本由完整目录内容计算得到。只要指标名称、口径或能力边界变化，版本和 ETag
# 就会自动变化，前端不会继续使用旧筛选规则；它不包含任何机构或业务数据。
OPERATIONS_METRIC_CATALOG_VERSION = _build_operations_metric_catalog_version()


def get_operations_metric_definition(metric: str) -> OperationsMetricDefinition | None:
    """按稳定指标 ID 获取业务口径定义。"""

    return _METRIC_CATALOG_BY_ID.get(metric)


def validate_operations_metric_capability(
    metric: str,
    *,
    bucket: str | None = None,
    comparison_role: str | None = None,
) -> None:
    """校验指标与时间桶、对比角色是否是目录声明的合法组合。

    这个校验同时服务于 Agent 工具输入和管理员审计筛选接口，避免两个入口各自
    维护一套容易漂移的能力规则。例如“预约状态分布 + 按日”并不是一个支持的
    组合；如果只依赖类型检查，接口虽然能接收参数，却会让前端得到一个语义上
    不成立的筛选条件。这里只校验指标能力，不授予任何组织或角色权限。
    """

    definition = get_operations_metric_definition(metric)
    if definition is None:
        raise ValueError("unsupported operations metric definition")
    if bucket is not None and bucket not in definition.supported_buckets:
        raise ValueError(f"metric {metric} does not support bucket {bucket}")
    if comparison_role == "PREVIOUS_PERIOD" and not definition.supports_previous_period:
        raise ValueError(f"metric {metric} does not support PREVIOUS_PERIOD comparison")
    if comparison_role == "SAME_PERIOD_LAST_YEAR" and not definition.supports_year_over_year:
        raise ValueError(f"metric {metric} does not support SAME_PERIOD_LAST_YEAR comparison")


def _metric_definition_view(metric: str) -> dict[str, object]:
    """生成可交给模型和管理端的非敏感指标口径说明。"""

    definition = get_operations_metric_definition(metric)
    if definition is None:
        return {
            "id": metric,
            "label": metric,
            "description": "未识别的指标定义，禁止据此生成业务解释。",
            "dimension_description": "未知",
            "supported_buckets": [],
            "supports_previous_period": False,
            "supports_year_over_year": False,
        }
    return {
        "id": definition.metric,
        "label": definition.label,
        "description": definition.description,
        "dimension_description": definition.dimension_description,
        "supported_buckets": sorted(definition.supported_buckets),
        "supports_previous_period": definition.supports_previous_period,
        "supports_year_over_year": definition.supports_year_over_year,
    }


def operations_metric_catalog_prompt() -> str:
    """生成不含数据的指标目录提示，用于歧义问题澄清。"""

    entries = "；".join(
        f"{item.label}（{item.metric}）：{item.description.rstrip('。')}"
        for item in OPERATIONS_METRIC_CATALOG
    )
    return (
        "当前可查询的固定经营指标包括："
        + entries
        + "。预约总量、完课量、新客量、营收金额、课程预约量和教练预约量支持按日/周趋势、上一等长周期环比及上一自然年同期同比；"
        + "预约状态分布和课程剩余课时当前只支持汇总查询，不自动执行环比或同比。"
    )


def build_operations_report(result: GatewayOperationsMetric) -> dict[str, object]:
    """把固定指标结果转换成可供模型解释的确定性报表摘要。

    <p>摘要由程序根据 Gateway 返回的聚合结果计算，不让模型自行计算总量或百分比。
    DAY/WEEK 结果会在这里补齐查询范围内没有记录的时间桶并填充为 0；没有足够时间桶
    时仍明确标记“暂不判断趋势”，避免模型把不完整的区间汇总误说成增长或下降。</p>
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

    metric_definition = get_operations_metric_definition(result.metric)
    report: dict[str, object] = {
        "metric": result.metric,
        "metric_label": metric_definition.label if metric_definition is not None else result.metric,
        "metric_definition": _metric_definition_view(result.metric),
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
        series, missing_bucket_count = _build_complete_time_series(result)
        report["series"] = series
        if missing_bucket_count:
            warnings.append(f"已将 {missing_bucket_count} 个无记录时间桶按 0 计入趋势计算。")
        if len(rows) >= 2 and len(series) >= 2:
            first_bucket, last_bucket = series[0], series[-1]
            first_value = cast(int, first_bucket["value"])
            last_value = cast(int, last_bucket["value"])
            delta = last_value - first_value
            change_percent = round(delta / first_value * 100, 2) if first_value else None
            direction = "UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT"
            report["trend_available"] = True
            report["trend"] = {
                "direction": direction,
                "first_bucket": first_bucket["bucket"],
                "first_value": first_value,
                "last_bucket": last_bucket["bucket"],
                "last_value": last_value,
                "delta": delta,
                "change_percent": change_percent,
                "note": "趋势基于完整时间桶序列；没有预约的时间桶已按 0 计入。",
            }
        else:
            report["trend_note"] = "有效数据桶少于 2 个，暂不判断趋势。"
    return report


def _build_complete_time_series(
    result: GatewayOperationsMetric,
) -> tuple[list[dict[str, object]], int]:
    """按照查询边界补齐 DAY/WEEK 时间桶，避免缺失日期被模型误解为数据缺失。"""

    if result.bucket == "DAY":
        first_bucket = result.from_date
        last_bucket = result.to_date
        step = timedelta(days=1)
    else:
        # WEEK 的桶起点统一为周一，并覆盖 from/to 所在的完整周桶。
        first_bucket = result.from_date - timedelta(days=result.from_date.weekday())
        last_bucket = result.to_date - timedelta(days=result.to_date.weekday())
        step = timedelta(days=7)
    values = {row.dimension: row.value for row in result.rows}
    series: list[dict[str, object]] = []
    missing_count = 0
    current = first_bucket
    while current <= last_bucket:
        bucket = current.isoformat()
        if bucket not in values:
            missing_count += 1
        series.append({"bucket": bucket, "value": values.get(bucket, 0)})
        current += step
    return series, missing_count


def build_operations_tool_result(result: GatewayOperationsMetric) -> dict[str, object]:
    """保留 Gateway 原始聚合结果，同时附加程序计算的解释摘要。

    <p>原始结果用于追溯和回答具体维度，report 用于让模型稳定地生成中文解释；两者
    都不包含预约、合同或用户明细记录。</p>
    """

    return {
        "data": result.model_dump(mode="json", by_alias=True),
        "report": build_operations_report(result),
    }


def build_operations_comparison_report(
    current: GatewayOperationsMetric,
    previous: GatewayOperationsMetric,
    *,
    comparison_type: Literal["PREVIOUS_PERIOD", "SAME_PERIOD_LAST_YEAR"] = "PREVIOUS_PERIOD",
) -> dict[str, object]:
    """比较两个固定周期，只根据程序计算的汇总值生成环比或同比摘要。"""

    current_total = sum(row.value for row in current.rows)
    previous_total = sum(row.value for row in previous.rows)
    delta = current_total - previous_total
    change_percent = round(delta / previous_total * 100, 2) if previous_total else None
    direction = "UP" if delta > 0 else "DOWN" if delta < 0 else "FLAT"
    return {
        "type": comparison_type,
        "current_period": {
            "from": current.from_date.isoformat(),
            "to": current.to_date.isoformat(),
        },
        "previous_period": {
            "from": previous.from_date.isoformat(),
            "to": previous.to_date.isoformat(),
        },
        "current_total": current_total,
        "previous_total": previous_total,
        "delta": delta,
        "change_percent": change_percent,
        "direction": direction,
        "note": ("变化百分比仅在对比周期非 0 时计算；对比周期为 0 时只报告差值，不伪造百分比。"),
    }


def _previous_period_bounds(result: GatewayOperationsMetric) -> tuple[date, date]:
    """按当前查询的自然日长度计算上一等长周期。"""

    period_days = (result.to_date - result.from_date).days + 1
    previous_to = result.from_date - timedelta(days=1)
    previous_from = previous_to - timedelta(days=period_days - 1)
    return previous_from, previous_to


def _same_period_last_year_bounds(result: GatewayOperationsMetric) -> tuple[date, date]:
    """按相同月日映射到上一自然年，2 月 29 日在非闰年收敛到 2 月 28 日。

    这是平台固定的同比口径，不使用 365 天减法：跨闰年时仍保持自然月日语义，
    并把边界日期保存到审计中，让管理员可以看见实际比较的两个日期范围。
    """

    def shift_year(value: date) -> date:
        try:
            return value.replace(year=value.year - 1)
        except ValueError:
            # 只有 2 月 29 日映射到非闰年会触发；按业务口径归并到 2 月 28 日。
            return value.replace(year=value.year - 1, day=28)

    return shift_year(result.from_date), shift_year(result.to_date)


def build_operations_comparison_tool_result(
    current: GatewayOperationsMetric,
    previous: GatewayOperationsMetric,
    *,
    comparison_type: Literal["PREVIOUS_PERIOD", "SAME_PERIOD_LAST_YEAR"] = "PREVIOUS_PERIOD",
) -> dict[str, object]:
    """保留两个周期的真实聚合结果，并附加确定性的环比/同比摘要。"""

    return {
        "current": build_operations_tool_result(current),
        "previous": build_operations_tool_result(previous),
        "comparison": build_operations_comparison_report(
            current, previous, comparison_type=comparison_type
        ),
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
        ("COMPLETED_CLASS_COUNT", ("完课量", "完课数", "完成课程量", "已完成课程")),
        ("NEW_CUSTOMER_COUNT", ("新客量", "新客数", "新增客户", "新增用户")),
        ("REVENUE_AMOUNT", ("营收", "营收金额", "收入", "营业收入")),
        ("REMAINING_CLASS_HOURS", ("剩余课时", "课时余额", "剩余课")),
        ("COURSE_APPOINTMENT_COUNT", ("课程预约量", "课程预约", "课程利用", "课程使用")),
        ("COACH_APPOINTMENT_COUNT", ("教练预约量", "教练预约", "教练表现", "教练工作量")),
        ("APPOINTMENT_COUNT", ("预约量", "预约数", "预约总量", "预约多少")),
    ]
    matches = [(metric, term) for metric, terms in metric_terms for term in terms if term in text]
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
    comparison = _parse_comparison(text)
    if comparison is None:
        return None
    return OperationsQueryHint(metric, start, end, matched_terms, bucket, comparison)


def operations_prompt_hint(user_message: str) -> str:
    """生成只读的 Operations 提示，明确告诉模型不能退化为任意 SQL。"""

    hint = parse_operations_intent(user_message)
    if hint is None:
        return (
            "经营问题暂未安全映射到唯一指标。"
            + operations_metric_catalog_prompt()
            + "请先向用户澄清一个明确的指标和时间范围；"
            "不要调用经营指标工具，也不要生成或执行任意 SQL。"
        )
    bucket_note = ""
    if hint.bucket != "NONE":
        definition = get_operations_metric_definition(hint.metric)
        if definition is None or hint.bucket not in definition.supported_buckets:
            return (
                operations_metric_catalog_prompt() + "请先向用户澄清，"
                "不要调用不支持时间桶的经营指标工具，也不要生成任意 SQL。"
            )
        bucket_note = "，时间分组=" + hint.bucket
    definition = get_operations_metric_definition(hint.metric)
    if definition is None:
        return operations_metric_catalog_prompt() + "请先澄清经营指标，不要调用工具。"
    if hint.comparison == "PREVIOUS_PERIOD" and not definition.supports_previous_period:
        return (
            operations_metric_catalog_prompt() + "当前指标不支持上一等长周期环比，请先向用户澄清。"
        )
    if hint.comparison == "SAME_PERIOD_LAST_YEAR" and not definition.supports_year_over_year:
        return operations_metric_catalog_prompt() + "当前指标不支持同比，请先向用户澄清。"
    comparison_note = {
        "PREVIOUS_PERIOD": "，对比上一等长周期（环比）",
        "SAME_PERIOD_LAST_YEAR": "，对比上一自然年同一月日区间（同比）",
    }.get(hint.comparison, "")
    return (
        "经营查询安全提示：可优先使用固定指标工具，指标="
        + hint.metric
        + "，开始日期="
        + hint.from_date.isoformat()
        + "，结束日期="
        + hint.to_date.isoformat()
        + bucket_note
        + comparison_note
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
    if "本月" in text:
        return today.replace(day=1), today
    if "上月" in text:
        first_this_month = today.replace(day=1)
        last_previous_month = first_this_month - timedelta(days=1)
        return last_previous_month.replace(day=1), last_previous_month
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


def _parse_comparison(text: str) -> _COMPARISONS | None:
    """只识别固定环比和固定同比；任意自定义对比周期仍要求澄清。"""

    if "同比" in text:
        return "SAME_PERIOD_LAST_YEAR"
    if any(term in text for term in ("环比", "较上期", "与上期", "和上期", "对比上期")):
        return "PREVIOUS_PERIOD"
    if any(term in text for term in ("和上月比", "与上月比", "相比上月", "较上月")):
        return "PREVIOUS_PERIOD"
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
    comparison: _COMPARISONS = "NONE"

    @model_validator(mode="after")
    def validate_range(self) -> OperationsMetricToolInput:
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from must be earlier than or equal to to")
        if self.from_date and self.to_date and (self.to_date - self.from_date).days > 92:
            raise ValueError("operations time range must not exceed 92 days")
        validate_operations_metric_capability(
            self.metric,
            bucket=self.bucket,
            comparison_role=(self.comparison if self.comparison != "NONE" else None),
        )
        return self


def validate_operations_query_policy(
    user_message: str,
    query: OperationsMetricToolInput,
    *,
    today: date | None = None,
    allowed_organization_ids: frozenset[str] | None = None,
) -> OperationsQueryPolicyDecision:
    """校验模型提出的经营查询是否忠实于用户问题和签名组织范围。

    <p>模型可能生成一个格式合法但语义不同的工具参数，例如用户问“本月预约量”，
    模型却改查课程预约量，或者把查询时间扩大到全年。Pydantic 只能验证参数格式，
    不能验证这种语义偏移，因此这里再次把工具参数和确定性意图提示逐项比对。该函数
    不授予权限；组织范围仍由签名 AgentContext 和 Java Gateway 最终确认。</p>
    """

    if (
        allowed_organization_ids is not None
        and query.organization_id not in allowed_organization_ids
    ):
        return OperationsQueryPolicyDecision(
            False,
            "ORGANIZATION_SCOPE_MISMATCH",
            "经营查询组织不在当前签名身份的组织范围内，不能执行查询。",
        )

    hint = parse_operations_intent(user_message, today=today)
    if hint is None:
        return OperationsQueryPolicyDecision(
            False,
            "INTENT_REQUIRES_CLARIFICATION",
            "经营问题未能安全映射到唯一指标、时间范围或对比口径，请先澄清后再查询。",
        )
    if query.metric != hint.metric:
        return OperationsQueryPolicyDecision(
            False,
            "METRIC_MISMATCH",
            f"工具指标 {query.metric} 与用户问题识别出的指标 {hint.metric} 不一致。",
        )
    if query.bucket != hint.bucket:
        return OperationsQueryPolicyDecision(
            False,
            "BUCKET_MISMATCH",
            f"工具时间桶 {query.bucket} 与用户问题识别出的时间桶 {hint.bucket} 不一致。",
        )
    if query.comparison != hint.comparison:
        return OperationsQueryPolicyDecision(
            False,
            "COMPARISON_MISMATCH",
            f"工具对比口径 {query.comparison} 与用户问题识别出的口径 {hint.comparison} 不一致。",
        )
    if query.from_date != hint.from_date or query.to_date != hint.to_date:
        return OperationsQueryPolicyDecision(
            False,
            "DATE_RANGE_MISMATCH",
            "工具时间范围与用户问题识别出的时间范围不一致，不能擅自扩大或缩小查询范围。",
        )
    return OperationsQueryPolicyDecision(True, "ALLOWED", "经营查询参数与用户问题和组织范围一致。")


def build_operations_tool_definitions(
    gateway: GatewayClient,
    *,
    audit_repository: OperationsAuditRepository | None = None,
    rate_limit_cache: Cache | None = None,
    rate_limit_requests: int = 60,
    rate_limit_window_seconds: int = 60,
    query_timeout_seconds: float | None = None,
    metrics: HttpMetrics | None = None,
) -> tuple[ToolDefinition, ...]:
    """注册固定经营查询工具，并可选接入 PostgreSQL 持久化审计。

    ``audit_repository`` 保持可选是为了让纯单元测试和离线工具组合不必启动数据库；
    正式 FastAPI 进程始终注入仓储。仓储注入后，查询成功但审计写入失败会 fail-closed，
    不把已经失去审计保护的经营结果返回给模型。
    """

    effective_timeout_seconds = query_timeout_seconds
    if effective_timeout_seconds is None:
        effective_timeout_seconds = getattr(
            getattr(gateway, "settings", None), "gateway_timeout_seconds", 5.0
        )
    if effective_timeout_seconds <= 0:
        raise ValueError("operations query timeout must be positive")
    if rate_limit_requests < 1 or rate_limit_window_seconds < 1:
        raise ValueError("operations rate limit configuration is invalid")

    async def query_metric(raw: BaseModel, context: ToolContext) -> object:
        data = cast(OperationsMetricToolInput, raw)
        if context.user_message is not None:
            decision = validate_operations_query_policy(
                context.user_message,
                data,
                allowed_organization_ids=(
                    context.identity.organization_ids if context.identity is not None else None
                ),
            )
            if not decision.allowed:
                # 在 Gateway 调用前 fail-closed；ToolRegistry 会把内部原因收敛成稳定
                # 的工具失败，不把用户原文或组织权限细节写入 Agent 审计。
                raise ValueError(f"operations query policy rejected: {decision.reason_code}")

        if rate_limit_cache is not None:
            # 频率限制按机构聚合，避免平台管理员通过切换主体绕过单机构保护；Redis Key
            # 只保存不可逆摘要，不把机构 ID 原文写进缓存键。
            rate_limit_key = _operations_rate_limit_key(data.organization_id)
            try:
                allowed = await rate_limit_cache.consume_fixed_window(
                    rate_limit_key,
                    limit=rate_limit_requests,
                    window_seconds=rate_limit_window_seconds,
                )
            except Exception as exc:
                if metrics is not None:
                    metrics.record_operations_query_event("RATE_LIMITER_UNAVAILABLE")
                _logger.error(
                    "operations_rate_limiter_unavailable",
                    organization_id=data.organization_id,
                    request_id=context.gateway_context.request_id,
                    trace_id=context.gateway_context.trace_id,
                )
                raise ValueError("operations resource limiter unavailable") from exc
            if not allowed:
                if metrics is not None:
                    metrics.record_operations_query_event("RATE_LIMITED")
                _logger.warning(
                    "operations_rate_limit_exceeded",
                    organization_id=data.organization_id,
                    request_id=context.gateway_context.request_id,
                    trace_id=context.gateway_context.trace_id,
                )
                raise ValueError("operations query rate limit exceeded")

        gateway_call_count = 0

        async def query_period(
            *, from_date: date | None, to_date: date | None, role: str
        ) -> GatewayOperationsMetric:
            nonlocal gateway_call_count
            gateway_call_count += 1
            if gateway_call_count > OPERATIONS_MAX_GATEWAY_CALLS:
                if metrics is not None:
                    metrics.record_operations_query_event("GATEWAY_CALL_BUDGET_EXCEEDED")
                raise ValueError("operations gateway call budget exceeded")
            try:
                async with asyncio.timeout(effective_timeout_seconds):
                    result = await gateway.query_operations_metric(
                        context.gateway_context,
                        data.organization_id,
                        data.metric,
                        from_date=from_date,
                        to_date=to_date,
                        limit=data.limit,
                        bucket=data.bucket,
                    )
            except TimeoutError as exc:
                if metrics is not None:
                    metrics.record_operations_query_event("GATEWAY_TIMEOUT")
                if audit_repository is not None:
                    try:
                        await audit_repository.record(
                            identity=context.identity,
                            organization_id=data.organization_id,
                            metric=data.metric,
                            bucket=data.bucket,
                            comparison_role=role,
                            from_date=from_date,
                            to_date=to_date,
                            row_count=None,
                            status="FAILED",
                            error_code=type(exc).__name__[:100],
                            request_id=context.gateway_context.request_id,
                            trace_id=context.gateway_context.trace_id,
                        )
                    except Exception:  # noqa: BLE001 - 保留 Gateway 原始失败，审计失败只记日志
                        if metrics is not None:
                            metrics.record_operations_query_event("AUDIT_FAILED")
                        _logger.error(
                            "operations_query_failure_audit_persistence_failed",
                            metric=data.metric,
                            comparison_role=role,
                            organization_id=data.organization_id,
                            request_id=context.gateway_context.request_id,
                            trace_id=context.gateway_context.trace_id,
                        )
                _logger.warning(
                    "operations_query_timeout",
                    metric=data.metric,
                    comparison_role=role,
                    organization_id=data.organization_id,
                    request_id=context.gateway_context.request_id,
                    trace_id=context.gateway_context.trace_id,
                )
                raise
            except Exception as exc:
                if metrics is not None:
                    metrics.record_operations_query_event("GATEWAY_ERROR")
                if audit_repository is not None:
                    try:
                        await audit_repository.record(
                            identity=context.identity,
                            organization_id=data.organization_id,
                            metric=data.metric,
                            bucket=data.bucket,
                            comparison_role=role,
                            from_date=from_date,
                            to_date=to_date,
                            row_count=None,
                            status="FAILED",
                            error_code=type(exc).__name__[:100],
                            request_id=context.gateway_context.request_id,
                            trace_id=context.gateway_context.trace_id,
                        )
                    except Exception:  # noqa: BLE001 - 保留 Gateway 原始失败，审计失败只记日志
                        if metrics is not None:
                            metrics.record_operations_query_event("AUDIT_FAILED")
                        # 原始 Gateway 错误仍然是业务调用的真实失败原因；审计失败只记录
                        # 受控事件，不把数据库异常正文暴露给模型或用户。
                        _logger.error(
                            "operations_query_failure_audit_persistence_failed",
                            metric=data.metric,
                            comparison_role=role,
                            organization_id=data.organization_id,
                            request_id=context.gateway_context.request_id,
                            trace_id=context.gateway_context.trace_id,
                        )
                _logger.warning(
                    "operations_query_failed",
                    metric=data.metric,
                    comparison_role=role,
                    organization_id=data.organization_id,
                    request_id=context.gateway_context.request_id,
                    trace_id=context.gateway_context.trace_id,
                )
                raise

            if audit_repository is not None:
                try:
                    await audit_repository.record(
                        identity=context.identity,
                        organization_id=data.organization_id,
                        metric=data.metric,
                        bucket=data.bucket,
                        comparison_role=role,
                        from_date=result.from_date,
                        to_date=result.to_date,
                        row_count=len(result.rows),
                        status="SUCCEEDED",
                        error_code=None,
                        request_id=context.gateway_context.request_id,
                        trace_id=context.gateway_context.trace_id,
                    )
                except Exception as exc:
                    if metrics is not None:
                        metrics.record_operations_query_event("AUDIT_FAILED")
                    # 经营结果已经从 Gateway 返回，但没有形成完整审计事实。生产环境不
                    # 允许继续返回，以免出现“用户看到了数据、平台却无法追溯”的窗口。
                    _logger.error(
                        "operations_query_audit_persistence_failed",
                        metric=data.metric,
                        comparison_role=role,
                        organization_id=data.organization_id,
                        request_id=context.gateway_context.request_id,
                        trace_id=context.gateway_context.trace_id,
                    )
                    raise OperationsAuditPersistenceError(
                        "operations query audit persistence failed"
                    ) from exc
            # 经营查询审计只保留“谁在什么范围查询了哪个固定指标以及结果规模”，
            # 不记录 SQL、Prompt、明细数据或模型原始输出，避免日志变成第二个数据出口。
            _logger.info(
                "operations_query_completed",
                metric=data.metric,
                comparison_role=role,
                organization_id=data.organization_id,
                from_date=result.from_date.isoformat(),
                to_date=result.to_date.isoformat(),
                bucket=data.bucket,
                row_count=len(result.rows),
                request_id=context.gateway_context.request_id,
                trace_id=context.gateway_context.trace_id,
            )
            return result

        result = await query_period(from_date=data.from_date, to_date=data.to_date, role="CURRENT")
        if data.comparison == "NONE":
            if metrics is not None:
                metrics.record_operations_query_event("SUCCEEDED")
            return build_operations_tool_result(result)
        if data.comparison == "PREVIOUS_PERIOD":
            previous_from, previous_to = _previous_period_bounds(result)
            comparison_role = "PREVIOUS_PERIOD"
        else:
            previous_from, previous_to = _same_period_last_year_bounds(result)
            comparison_role = "SAME_PERIOD_LAST_YEAR"
        previous = await query_period(
            from_date=previous_from, to_date=previous_to, role=comparison_role
        )
        if metrics is not None:
            metrics.record_operations_query_event("SUCCEEDED")
        return build_operations_comparison_tool_result(
            result,
            previous,
            comparison_type=data.comparison,
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


def _operations_rate_limit_key(organization_id: str) -> str:
    """生成不暴露机构原文的 Redis 固定窗口 Key。"""

    digest = hashlib.sha256(organization_id.encode("utf-8")).hexdigest()
    return f"fitness:agent:operations-rate:{digest}"
