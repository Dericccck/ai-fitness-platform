"""经营分析双模式编排引擎：受控 Query DSL 填槽、诊断拆解树与确定性贡献度分析。

本模块严格延续平台“确定性代码兜底 + 大模型负责语义理解与综合表达”的安全原则：

1. 绝不生成或执行任意 Text-to-SQL，所有查询参数均收敛到 8 类固定指标枚举与 92 天边界；
2. 租户 ``organization_id`` 永远不在模型可见 Schema 中出现，只由服务端从已验签身份强制绑定；
3. 常规看数优先走确定性规则快路径（0 次前置 LLM）；仅在口语或复合多指标场景才调用一次
   受控 JSON 填槽（最多并发 3 项指标）；
4. 经营诊断与归因走受控只读 ReAct 下钻循环（最多 3 轮、全局查询上限与去重保护），并用
   Python 确定性计算同环比差值与维度贡献占比，明确标注 ``causality_status="NOT_ESTABLISHED"``
   防止把统计拆解编造成未经证实的因果结论。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.infrastructure.model_gateway import (
    ModelConfigurationError,
    ModelGateway,
    ModelResponseError,
)

from .operations_tools import (
    _COMPARISONS,
    _METRICS,
    _TIME_BUCKETS,
    OPERATIONS_METRIC_CATALOG,
    get_operations_metric_definition,
    operations_metric_catalog_prompt,
    parse_operations_intent,
    validate_operations_metric_capability,
)

_logger = structlog.get_logger("agent.operations.planner")
_BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")

OperationsMode = Literal["DIRECT_QUERY", "DIAGNOSIS"]
OperationsPlanSource = Literal["DETERMINISTIC_RULE", "LLM_DSL", "CLARIFICATION"]

MAX_DIRECT_QUERIES = 3
MAX_DIAGNOSIS_STEPS = 3
MAX_DIAGNOSIS_QUERIES_PER_STEP = 2
MAX_DIAGNOSIS_TOTAL_QUERIES = 6
PLANNER_TIMEOUT_SECONDS = 4.0
MIN_SLOT_CONFIDENCE = 0.75

# 固定经营诊断拆解树（Metric Tree）：当某核心指标波动时，优先排查的关联与维度下钻指标。
OPERATIONS_DIAGNOSIS_TREE: dict[str, tuple[str, ...]] = {
    "REVENUE_AMOUNT": (
        "NEW_CUSTOMER_COUNT",
        "COMPLETED_CLASS_COUNT",
        "COURSE_APPOINTMENT_COUNT",
        "REMAINING_CLASS_HOURS",
    ),
    "COMPLETED_CLASS_COUNT": (
        "APPOINTMENT_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
        "COACH_APPOINTMENT_COUNT",
        "COURSE_APPOINTMENT_COUNT",
    ),
    "APPOINTMENT_COUNT": (
        "APPOINTMENT_STATUS_BREAKDOWN",
        "COACH_APPOINTMENT_COUNT",
        "COURSE_APPOINTMENT_COUNT",
        "NEW_CUSTOMER_COUNT",
    ),
    "NEW_CUSTOMER_COUNT": (
        "REVENUE_AMOUNT",
        "COURSE_APPOINTMENT_COUNT",
        "APPOINTMENT_COUNT",
    ),
    "COACH_APPOINTMENT_COUNT": (
        "COMPLETED_CLASS_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
        "COURSE_APPOINTMENT_COUNT",
    ),
    "COURSE_APPOINTMENT_COUNT": (
        "COMPLETED_CLASS_COUNT",
        "APPOINTMENT_STATUS_BREAKDOWN",
        "REVENUE_AMOUNT",
    ),
    "APPOINTMENT_STATUS_BREAKDOWN": (
        "APPOINTMENT_COUNT",
        "COMPLETED_CLASS_COUNT",
        "COACH_APPOINTMENT_COUNT",
    ),
    "REMAINING_CLASS_HOURS": (
        "COMPLETED_CLASS_COUNT",
        "REVENUE_AMOUNT",
        "NEW_CUSTOMER_COUNT",
    ),
}

_DIAGNOSIS_MARKERS: tuple[str, ...] = (
    "为什么",
    "为何",
    "啥原因",
    "什么原因",
    "原因是什么",
    "排查",
    "诊断",
    "归因",
    "定位原因",
    "怎么回事",
    "哪里出了问题",
    "哪块掉了",
    "哪边跌了",
    "波动原因",
    "下滑原因",
    "下降原因",
    "下跌原因",
    "增长原因",
    "异常原因",
    "分析原因",
    "找一下原因",
    "找出原因",
)

_PRIMARY_METRIC_HINTS: tuple[tuple[_METRICS, tuple[str, ...]], ...] = (
    ("REVENUE_AMOUNT", ("营收", "收入", "进账", "流水", "回款", "销售额", "卖课")),
    ("COMPLETED_CLASS_COUNT", ("完课", "消课", "耗课")),
    ("NEW_CUSTOMER_COUNT", ("新客", "新增客户", "新增会员", "拉新")),
    ("COACH_APPOINTMENT_COUNT", ("教练", "排期", "饱和度")),
    ("COURSE_APPOINTMENT_COUNT", ("课程利用", "课程预约", "课程表现")),
    ("APPOINTMENT_STATUS_BREAKDOWN", ("取消率", "爽约", "完成率", "预约状态")),
    ("REMAINING_CLASS_HOURS", ("剩余课时", "课时余额", "续费")),
    ("APPOINTMENT_COUNT", ("预约", "约课")),
)


class OperationsQueryItem(BaseModel):
    """面向受控语义层（Query DSL）的单项指标查询契约；禁止模型传入租户字段。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    metric: _METRICS
    from_date: date = Field(alias="from")
    to_date: date = Field(alias="to")
    bucket: _TIME_BUCKETS = "NONE"
    comparison: _COMPARISONS = "NONE"
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_semantics(self) -> OperationsQueryItem:
        if self.from_date > self.to_date:
            raise ValueError("from 必须早于或等于 to")
        if (self.to_date - self.from_date).days > 92:
            raise ValueError("经营查询时间范围不能超过 92 天")
        validate_operations_metric_capability(
            self.metric,
            bucket=self.bucket,
            comparison_role=(self.comparison if self.comparison != "NONE" else None),
        )
        return self

    def dedupe_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.metric,
            self.from_date.isoformat(),
            self.to_date.isoformat(),
            self.bucket,
            self.comparison,
        )

    def as_state_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    def to_tool_input(self, organization_id: str) -> dict[str, object]:
        payload = self.model_dump(mode="json", by_alias=True)
        payload["organization_id"] = organization_id
        return cast(dict[str, object], payload)


class OperationsQueryPlan(BaseModel):
    """结构化 LLM 填槽输出的多指标直接查询计划。"""

    model_config = ConfigDict(extra="forbid")

    queries: list[OperationsQueryItem] = Field(default_factory=list, max_length=MAX_DIRECT_QUERIES)
    needs_clarification: bool = False
    clarification_reason: str = Field(default="", max_length=200)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class OperationsDiagnosisDecision(BaseModel):
    """受控经营诊断 ReAct 单步决策输出。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["QUERY", "SYNTHESIZE", "CLARIFY"]
    rationale: str = Field(default="", max_length=240)
    queries: list[OperationsQueryItem] = Field(
        default_factory=list, max_length=MAX_DIAGNOSIS_QUERIES_PER_STEP
    )


@dataclass(frozen=True)
class DirectQueryPlanResult:
    """常规直接查数的规划结果。"""

    source: OperationsPlanSource
    queries: tuple[OperationsQueryItem, ...]
    enforce_single_query_policy: bool
    clarification_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class DiagnosisStepPlanResult:
    """诊断单步决策的规划结果。"""

    action: Literal["QUERY", "SYNTHESIZE", "CLARIFY"]
    rationale: str
    queries: tuple[OperationsQueryItem, ...]
    source: Literal["LLM_REACT", "DETERMINISTIC_TREE"]
    input_tokens: int = 0
    output_tokens: int = 0


def classify_operations_mode(user_message: str) -> OperationsMode:
    """确定性区分常规看数（单向流水线）与经营诊断归因（受控 ReAct 下钻）。"""

    text = "".join(user_message.lower().split())
    if any(marker in text for marker in _DIAGNOSIS_MARKERS):
        return "DIAGNOSIS"
    return "DIRECT_QUERY"


def looks_like_multi_metric_operations_query(user_message: str) -> bool:
    """识别包含多个经营主题词（含口语同义词）的复合问法，避免被单指标规则半截短路。"""

    text = user_message.lower()
    matched_metrics: set[str] = set()
    for metric, markers in _PRIMARY_METRIC_HINTS:
        if any(marker in text for marker in markers):
            matched_metrics.add(metric)
    # “预约”既出现在“预约量”也出现在“预约状态/课程预约/教练预约”，避免把单个具体预约指标误判成多指标。
    if "APPOINTMENT_COUNT" in matched_metrics and matched_metrics.intersection(
        {"APPOINTMENT_STATUS_BREAKDOWN", "COURSE_APPOINTMENT_COUNT", "COACH_APPOINTMENT_COUNT"}
    ):
        matched_metrics.discard("APPOINTMENT_COUNT")
    return len(matched_metrics) > 1


async def plan_direct_operations_queries(
    user_message: str,
    models: ModelGateway | Any,
    *,
    today: date | None = None,
    timeout_seconds: float = PLANNER_TIMEOUT_SECONDS,
) -> DirectQueryPlanResult:
    """常规看数混合规划：规则快路径优先（0 LLM），口语或复合多指标才走受控 JSON 填槽。"""

    business_today = today or datetime.now(_BUSINESS_ZONE).date()
    hint = (
        None
        if looks_like_multi_metric_operations_query(user_message)
        else parse_operations_intent(user_message, today=business_today)
    )
    if hint is not None:
        try:
            item = OperationsQueryItem.model_validate(
                {
                    "metric": hint.metric,
                    "from": hint.from_date,
                    "to": hint.to_date,
                    "bucket": hint.bucket,
                    "comparison": hint.comparison,
                    "limit": 20,
                }
            )
            return DirectQueryPlanResult(
                source="DETERMINISTIC_RULE",
                queries=(item,),
                enforce_single_query_policy=True,
            )
        except ValidationError:
            # 规则命中了不支持的时间桶或对比口径（例如预约状态分布+按日），直接澄清。
            return DirectQueryPlanResult(
                source="CLARIFICATION",
                queries=(),
                enforce_single_query_policy=False,
                clarification_reason="UNSUPPORTED_METRIC_CAPABILITY",
            )

    chat_json = getattr(models, "chat_json_with_usage", None)
    if chat_json is None:
        return DirectQueryPlanResult(
            source="CLARIFICATION",
            queries=(),
            enforce_single_query_policy=False,
            clarification_reason="RULE_UNMATCHED_NO_SLOT_PLANNER",
        )

    prompt = _direct_query_dsl_system_prompt(business_today)
    try:
        turn = await asyncio.wait_for(
            chat_json(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_message[:2000]},
                ],
                max_output_tokens=420,
                temperature=0,
            ),
            timeout=timeout_seconds,
        )
        parsed = OperationsQueryPlan.model_validate_json(turn.content)
    except (
        TimeoutError,
        ModelConfigurationError,
        ModelResponseError,
        ValidationError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        _logger.warning("operations_direct_dsl_fallback", error_type=type(exc).__name__)
        return DirectQueryPlanResult(
            source="CLARIFICATION",
            queries=(),
            enforce_single_query_policy=False,
            clarification_reason="DSL_PLANNER_UNAVAILABLE",
        )

    deduped = _dedupe_query_items(parsed.queries)[:MAX_DIRECT_QUERIES]
    if parsed.needs_clarification or parsed.confidence < MIN_SLOT_CONFIDENCE or not deduped:
        return DirectQueryPlanResult(
            source="CLARIFICATION",
            queries=(),
            enforce_single_query_policy=False,
            clarification_reason=parsed.clarification_reason or "DSL_LOW_CONFIDENCE",
            input_tokens=turn.input_tokens or 0,
            output_tokens=turn.output_tokens or 0,
        )
    return DirectQueryPlanResult(
        source="LLM_DSL",
        queries=deduped,
        enforce_single_query_policy=False,
        input_tokens=turn.input_tokens or 0,
        output_tokens=turn.output_tokens or 0,
    )


async def plan_diagnosis_step(
    user_message: str,
    observations: Sequence[dict[str, Any]],
    models: ModelGateway | Any,
    *,
    step_index: int,
    max_steps: int = MAX_DIAGNOSIS_STEPS,
    max_total_queries: int = MAX_DIAGNOSIS_TOTAL_QUERIES,
    today: date | None = None,
    timeout_seconds: float = PLANNER_TIMEOUT_SECONDS,
) -> DiagnosisStepPlanResult:
    """经营诊断受控 ReAct 单步决策：结合已有观察结果与指标拆解树决定下一步下钻或收口。"""

    remaining_budget = max_total_queries - len(observations)
    if step_index >= max_steps or remaining_budget <= 0:
        return DiagnosisStepPlanResult(
            action="SYNTHESIZE",
            rationale="已达到经营诊断受控下钻上限，基于已有真实聚合数据综合生成诊断报告。",
            queries=(),
            source="DETERMINISTIC_TREE",
        )

    business_today = today or datetime.now(_BUSINESS_ZONE).date()
    executed_keys = _executed_query_keys(observations)
    step_limit = min(MAX_DIAGNOSIS_QUERIES_PER_STEP, remaining_budget)
    chat_json = getattr(models, "chat_json_with_usage", None)

    if chat_json is not None:
        system_prompt = _diagnosis_planner_system_prompt(business_today, step_limit)
        payload = {
            "question": user_message[:2000],
            "step_index": step_index,
            "remaining_steps": max_steps - step_index,
            "remaining_query_budget": remaining_budget,
            "executed_queries": [list(key) for key in sorted(executed_keys)],
            "observations": _compact_observations_for_planner(observations),
        }
        try:
            turn = await asyncio.wait_for(
                chat_json(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    max_output_tokens=480,
                    temperature=0,
                ),
                timeout=timeout_seconds,
            )
            decision = OperationsDiagnosisDecision.model_validate_json(turn.content)
            input_tokens = turn.input_tokens or 0
            output_tokens = turn.output_tokens or 0
            if decision.action == "CLARIFY" and not observations:
                return DiagnosisStepPlanResult(
                    action="CLARIFY",
                    rationale=decision.rationale,
                    queries=(),
                    source="LLM_REACT",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            if decision.action == "QUERY":
                fresh = _filter_unexecuted_queries(decision.queries, executed_keys)[:step_limit]
                if fresh:
                    return DiagnosisStepPlanResult(
                        action="QUERY",
                        rationale=decision.rationale or "按指标拆解树继续下钻核查关联维度。",
                        queries=fresh,
                        source="LLM_REACT",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
            return DiagnosisStepPlanResult(
                action="SYNTHESIZE",
                rationale=decision.rationale or "已完成关键维度排查，进入综合诊断总结。",
                queries=(),
                source="LLM_REACT",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        except (
            TimeoutError,
            ModelConfigurationError,
            ModelResponseError,
            ValidationError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            _logger.warning("operations_diagnosis_llm_fallback", error_type=type(exc).__name__)

    return _deterministic_diagnosis_step(
        user_message,
        observations,
        executed_keys=executed_keys,
        step_limit=step_limit,
        today=business_today,
    )


def build_operations_observation(
    query: OperationsQueryItem,
    result: Any,
    *,
    rationale: str = "",
) -> dict[str, Any]:
    """把单次受控经营查询结果封装为带确定性贡献度分析的观察条目。"""

    entry: dict[str, Any] = {
        "query": query.as_state_dict(),
        "result": result,
        "analysis": compute_operations_contribution_analysis(query.metric, result),
    }
    if rationale:
        entry["rationale"] = rationale
    return entry


def compute_operations_contribution_analysis(metric: str, result: Any) -> dict[str, Any]:
    """由 Python 确定性计算波动差值、维度贡献度占比与状态结构摘要。

    绝不让大模型心算差值或贡献度百分比，同时始终标记 ``causality_status="NOT_ESTABLISHED"``，
    防止把统计拆解误导为已证实的业务因果结论。
    """

    analysis: dict[str, Any] = {
        "metric": metric,
        "causality_status": "NOT_ESTABLISHED",
    }
    if not isinstance(result, dict):
        return analysis

    comparison = result.get("comparison")
    if isinstance(comparison, dict):
        analysis["period_comparison"] = {
            "type": comparison.get("type"),
            "current_total": comparison.get("current_total"),
            "previous_total": comparison.get("previous_total"),
            "delta": comparison.get("delta"),
            "change_percent": comparison.get("change_percent"),
            "direction": comparison.get("direction"),
        }
        current_rows = _extract_rows(result.get("current"))
        previous_rows = _extract_rows(result.get("previous"))
        if current_rows or previous_rows:
            contributions = _compute_dimension_contributions(current_rows, previous_rows)
            if contributions:
                analysis["dimension_contributions"] = contributions
        return analysis

    report = result.get("report")
    if isinstance(report, dict):
        analysis["summary"] = {
            "total_value": report.get("total_value"),
            "row_count": report.get("row_count"),
            "top_dimension": report.get("top_dimension"),
            "top_value": report.get("top_value"),
            "top_share_percent": report.get("top_share_percent"),
            "trend_available": report.get("trend_available"),
            "trend": report.get("trend"),
            "warnings": report.get("warnings", []),
        }
        breakdown = report.get("breakdown")
        if isinstance(breakdown, list) and breakdown:
            analysis["status_breakdown"] = breakdown
    return analysis


def build_direct_summary_prompt_payload(observations: Sequence[dict[str, Any]]) -> str:
    """构造常规直接查数的总结输入块，单指标保持原始 JSON 视图，多指标打包为清晰清单。"""

    if len(observations) == 1:
        raw_result = observations[0].get("result")
        return (
            "以下是服务端已完成权限校验并从固定经营指标接口查询到的真实聚合结果（JSON），"
            "请严格基于其中的 data 与 report 生成简洁准确的中文解读，不得编造数据或因果结论：\n"
            + json.dumps(raw_result, ensure_ascii=False)
        )
    bundle = {
        "causality_status": "NOT_ESTABLISHED",
        "metrics_count": len(observations),
        "items": [
            {
                "query": item.get("query"),
                "result": item.get("result"),
                "analysis": item.get("analysis"),
            }
            for item in observations
        ],
    }
    return (
        "以下是服务端已完成权限校验并并发查询到的多指标真实聚合结果（JSON），"
        "请逐项准确陈述数据并汇总关键变化；严格基于返回数据解读，不得编造未查询的数据或因果结论：\n"
        + json.dumps(bundle, ensure_ascii=False)
    )


def build_diagnosis_synthesis_prompt_payload(
    observations: Sequence[dict[str, Any]],
    *,
    rationales: Sequence[str] = (),
) -> str:
    """构造经营诊断与归因的三段式防幻觉综合输入包。"""

    packet = {
        "causality_status": "NOT_ESTABLISHED",
        "diagnosis_steps": list(rationales),
        "observations": [
            {
                "query": item.get("query"),
                "analysis": item.get("analysis"),
                "result": item.get("result"),
            }
            for item in observations
        ],
    }
    return (
        "以下是服务端通过受控只读多步下钻查到的真实经营聚合数据及程序计算的贡献度拆解（JSON）。"
        "请严格按以下三段结构输出清晰、克制的中文经营诊断报告：\n"
        "1. **已证实的数据事实**：只陈述本次真实查到的指标、时间周期、数值与同环比变化；\n"
        "2. **维度拆解与波动贡献**：引用程序计算出的 dimension_contributions 或 status_breakdown，"
        "明确说明是哪些关联指标/维度贡献了主要波动，并注明这属于统计拆解而非最终业务因果；\n"
        "3. **待核实假设与建议动作**：基于数据异常提出 2~3 条具体的门店核查方向或跟进建议，"
        "严禁把未经核实的外部猜测（如天气、竞品、服务质量）当作已证实原因。\n\n"
        + json.dumps(packet, ensure_ascii=False)
    )


def _extract_rows(period_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(period_payload, dict):
        return []
    data = period_payload.get("data")
    if not isinstance(data, dict):
        return []
    rows = data.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _compute_dimension_contributions(
    current_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按维度键对齐两期聚合行，程序计算每个维度的增减量与对总波动的贡献占比。"""

    current_map: dict[str, tuple[str, int]] = {}
    previous_map: dict[str, tuple[str, int]] = {}
    for row in current_rows:
        key = str(row.get("dimension", ""))
        if not key:
            continue
        label = str(row.get("label") or key)
        value = int(row.get("value", 0))
        current_map[key] = (label, value)
    for row in previous_rows:
        key = str(row.get("dimension", ""))
        if not key:
            continue
        label = str(row.get("label") or key)
        value = int(row.get("value", 0))
        previous_map[key] = (label, value)

    all_keys = set(current_map) | set(previous_map)
    # 过滤掉纯总量单行（如 dimension=="total"），只对多维切片输出贡献拆解。
    if len(all_keys) <= 1 and any(k.lower() in {"total", "all"} for k in all_keys):
        return []

    total_current = sum(val for _, val in current_map.values())
    total_previous = sum(val for _, val in previous_map.values())
    total_delta = total_current - total_previous

    items: list[dict[str, Any]] = []
    for key in all_keys:
        cur_label, cur_val = current_map.get(key, ("", 0))
        prev_label, prev_val = previous_map.get(key, ("", 0))
        label = cur_label or prev_label or key
        delta = cur_val - prev_val
        if delta == 0 and cur_val == 0 and prev_val == 0:
            continue
        change_percent = round(delta / prev_val * 100, 2) if prev_val else None
        contribution_share = round(delta / total_delta * 100, 2) if total_delta != 0 else None
        items.append(
            {
                "dimension": key,
                "label": label,
                "current_value": cur_val,
                "previous_value": prev_val,
                "delta": delta,
                "change_percent": change_percent,
                "contribution_share_percent": contribution_share,
            }
        )
    items.sort(key=lambda item: (-abs(int(item["delta"])), str(item["dimension"])))
    return items[:5]


def _deterministic_diagnosis_step(
    user_message: str,
    observations: Sequence[dict[str, Any]],
    *,
    executed_keys: set[tuple[str, str, str, str, str]],
    step_limit: int,
    today: date,
) -> DiagnosisStepPlanResult:
    """规划 LLM 不可用时的确定性指标拆解树兜底，保证诊断闭环仍可安全下钻。"""

    primary_metric = _infer_primary_metric(user_message)
    if primary_metric is None:
        if observations:
            return DiagnosisStepPlanResult(
                action="SYNTHESIZE",
                rationale="已完成可识别指标的查询，整理当前诊断结论。",
                queries=(),
                source="DETERMINISTIC_TREE",
            )
        return DiagnosisStepPlanResult(
            action="CLARIFY",
            rationale="未能识别待诊断的核心经营指标，请用户补充具体指标和时间范围。",
            queries=(),
            source="DETERMINISTIC_TREE",
        )

    from_date, to_date = _infer_diagnosis_period(user_message, today)
    if not observations:
        primary_query = _build_safe_diagnosis_query(
            primary_metric, from_date=from_date, to_date=to_date, prefer_comparison=True
        )
        if primary_query.dedupe_key() not in executed_keys:
            return DiagnosisStepPlanResult(
                action="QUERY",
                rationale=f"先核查核心指标 {primary_metric} 的本期与上期环比波动。",
                queries=(primary_query,),
                source="DETERMINISTIC_TREE",
            )

    # 第一步已有核心指标后，按拆解树拉取最多 step_limit 个尚未查询的关联/维度指标。
    tree_metrics = OPERATIONS_DIAGNOSIS_TREE.get(primary_metric, ())
    candidates: list[OperationsQueryItem] = []
    for metric_id in tree_metrics:
        query = _build_safe_diagnosis_query(
            cast(_METRICS, metric_id),
            from_date=from_date,
            to_date=to_date,
            prefer_comparison=True,
        )
        if query.dedupe_key() not in executed_keys:
            candidates.append(query)
        if len(candidates) >= step_limit:
            break

    if candidates:
        names = "、".join(item.metric for item in candidates)
        return DiagnosisStepPlanResult(
            action="QUERY",
            rationale=f"按 {primary_metric} 拆解树下钻核查关联维度：{names}。",
            queries=tuple(candidates),
            source="DETERMINISTIC_TREE",
        )

    return DiagnosisStepPlanResult(
        action="SYNTHESIZE",
        rationale="已完成核心指标与拆解树维度的核查，综合输出诊断报告。",
        queries=(),
        source="DETERMINISTIC_TREE",
    )


def _infer_primary_metric(user_message: str) -> _METRICS | None:
    text = user_message.lower()
    for metric, markers in _PRIMARY_METRIC_HINTS:
        if any(marker in text for marker in markers):
            return metric
    return None


def _infer_diagnosis_period(user_message: str, today: date) -> tuple[date, date]:
    text = user_message.lower()
    if "本月" in text or "这个月" in text:
        return today.replace(day=1), today
    if "上月" in text or "上个月" in text:
        last_prev = today.replace(day=1) - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if "本周" in text or "这周" in text:
        return today - timedelta(days=today.weekday()), today
    if "上周" in text:
        last_sunday = today - timedelta(days=today.weekday() + 1)
        return last_sunday - timedelta(days=6), last_sunday
    return today - timedelta(days=29), today


def _build_safe_diagnosis_query(
    metric: _METRICS,
    *,
    from_date: date,
    to_date: date,
    prefer_comparison: bool,
) -> OperationsQueryItem:
    definition = get_operations_metric_definition(metric)
    comparison: _COMPARISONS = (
        "PREVIOUS_PERIOD"
        if prefer_comparison and definition is not None and definition.supports_previous_period
        else "NONE"
    )
    return OperationsQueryItem.model_validate(
        {
            "metric": metric,
            "from": from_date,
            "to": to_date,
            "bucket": "NONE",
            "comparison": comparison,
            "limit": 20,
        }
    )


def _dedupe_query_items(items: Sequence[OperationsQueryItem]) -> tuple[OperationsQueryItem, ...]:
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[OperationsQueryItem] = []
    for item in items:
        key = item.dedupe_key()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def _executed_query_keys(
    observations: Sequence[dict[str, Any]],
) -> set[tuple[str, str, str, str, str]]:
    keys: set[tuple[str, str, str, str, str]] = set()
    for obs in observations:
        query = obs.get("query")
        if not isinstance(query, dict):
            continue
        keys.add(
            (
                str(query.get("metric", "")),
                str(query.get("from", "")),
                str(query.get("to", "")),
                str(query.get("bucket", "NONE")),
                str(query.get("comparison", "NONE")),
            )
        )
    return keys


def _filter_unexecuted_queries(
    items: Sequence[OperationsQueryItem],
    executed_keys: set[tuple[str, str, str, str, str]],
) -> tuple[OperationsQueryItem, ...]:
    fresh: list[OperationsQueryItem] = []
    seen = set(executed_keys)
    for item in items:
        key = item.dedupe_key()
        if key not in seen:
            seen.add(key)
            fresh.append(item)
    return tuple(fresh)


def _compact_observations_for_planner(
    observations: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "query": item.get("query"),
            "analysis": item.get("analysis"),
        }
        for item in observations
    ]


def _calendar_context(today: date) -> str:
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    week_start = today - timedelta(days=today.weekday())
    prev_week_end = week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)
    return (
        f"今天={today.isoformat()}，本月={month_start.isoformat()}~{today.isoformat()}，"
        f"上月={prev_month_start.isoformat()}~{prev_month_end.isoformat()}，"
        f"本周={week_start.isoformat()}~{today.isoformat()}，"
        f"上周={prev_week_start.isoformat()}~{prev_week_end.isoformat()}，"
        f"近14天={(today - timedelta(days=13)).isoformat()}~{today.isoformat()}，"
        f"近30天={(today - timedelta(days=29)).isoformat()}~{today.isoformat()}。"
    )


def _catalog_json_spec() -> str:
    return json.dumps(
        [
            {
                "metric": item.metric,
                "label": item.label,
                "supported_buckets": sorted(item.supported_buckets),
                "supports_previous_period": item.supports_previous_period,
                "supports_year_over_year": item.supports_year_over_year,
            }
            for item in OPERATIONS_METRIC_CATALOG
        ],
        ensure_ascii=False,
    )


def _direct_query_dsl_system_prompt(today: date) -> str:
    return (
        "你是健身平台的经营指标 Query DSL 填槽器。只输出严格 JSON，不写 SQL，不猜门店 ID。\n"
        f"日期参考：{_calendar_context(today)}\n"
        f"可用固定指标目录：{_catalog_json_spec()}\n"
        "约束：\n"
        f"1. queries 最多 {MAX_DIRECT_QUERIES} 项，时间跨度不超过 92 天；不得包含 organization_id。\n"
        "2. 只能使用目录内支持的 bucket（NONE/DAY/WEEK）和 comparison（NONE/PREVIOUS_PERIOD/SAME_PERIOD_LAST_YEAR）。\n"
        "3. 如果用户问题没有明确指标（如“看看经营情况”）、没有明确日期（如“指定月份”）、"
        "或询问目录外指标（如到期合同数、续费合同数、客单价），必须设置 needs_clarification=true、queries=[]。\n"
        '输出格式：{"queries":[{"metric":"...","from":"YYYY-MM-DD","to":"YYYY-MM-DD",'
        '"bucket":"NONE","comparison":"NONE","limit":20}],'
        '"needs_clarification":false,"clarification_reason":"","confidence":0.95}'
    )


def _diagnosis_planner_system_prompt(today: date, step_limit: int) -> str:
    tree_json = json.dumps(
        {key: list(value) for key, value in OPERATIONS_DIAGNOSIS_TREE.items()},
        ensure_ascii=False,
    )
    return (
        "你是健身平台的受控经营诊断规划器（只读 ReAct 下钻）。只输出严格 JSON，不写 SQL，不生成最终报告。\n"
        f"日期参考：{_calendar_context(today)}\n"
        f"可用固定指标目录：{_catalog_json_spec()}\n"
        f"指标拆解树：{tree_json}\n"
        f"目录口径：{operations_metric_catalog_prompt()}\n"
        "决策规则：\n"
        f"1. 如果尚未查核心指标，action='QUERY' 先查核心指标（优先 comparison='PREVIOUS_PERIOD'）。\n"
        f"2. 如果已拿到核心指标变化，根据数据异常结合拆解树，每次最多提出 {step_limit} 个尚未查过的关联/维度指标下钻。\n"
        "3. 如果已有数据足以定位主要波动维度，或拆解树相关维度已查完，返回 action='SYNTHESIZE'、queries=[]。\n"
        "4. 如果问题完全没有指明任何可排查的经营主题，返回 action='CLARIFY'、queries=[]。\n"
        "5. 严禁重复 executed_queries 中已查过的完全相同条件；单次范围 <= 92 天；不得含 organization_id。\n"
        '输出格式：{"action":"QUERY|SYNTHESIZE|CLARIFY","rationale":"简短中文排查思路",'
        '"queries":[{"metric":"...","from":"YYYY-MM-DD","to":"YYYY-MM-DD",'
        '"bucket":"NONE","comparison":"NONE","limit":20}]}'
    )
