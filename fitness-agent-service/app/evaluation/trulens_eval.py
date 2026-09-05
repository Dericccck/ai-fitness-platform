"""TruLens 评测、真实 OTEL Trace 转换和严格质量门禁。"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.core.config import Settings
from app.evaluation.telemetry import redact_text


class TruLensEvaluationError(RuntimeError):
    """可选 TruLens 评估环境不完整或输入不符合契约时抛出。"""


@dataclass(frozen=True)
class TruLensCase:
    """一个可重复评测的问答/工具调用样本。"""

    case_id: str
    question: str
    answer: str
    contexts: tuple[str, ...] = ()
    route: str = "UNKNOWN"
    tool_trace: str = ""
    status: str = "SUCCEEDED"
    expectations: Mapping[str, Any] = field(default_factory=dict)
    record_id: str = ""
    trace_id: str = ""
    code_version: str = ""
    prompt_version: str = ""
    model_version: str = ""
    knowledge_base_version: str = ""
    graph_version: str = ""
    release_id: str = ""
    manifest_digest: str = ""
    index_build_id: str = ""
    eval_release_id: str = ""


def case_from_mapping(data: dict[str, Any]) -> TruLensCase:
    """从离线案例 JSON 创建案例；不允许缺少核心输入。"""

    for field_name in ("case_id", "question", "answer"):
        value = data.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise TruLensEvaluationError(f"TruLens 案例缺少非空字段 {field_name}")
    expectations = data.get("expectations", {})
    if not isinstance(expectations, dict):
        raise TruLensEvaluationError("TruLens 案例 expectations 必须是 JSON 对象")
    return TruLensCase(
        case_id=str(data["case_id"]),
        question=str(data["question"]),
        answer=str(data["answer"]),
        contexts=tuple(str(item) for item in data.get("contexts", [])),
        route=str(data.get("route", "UNKNOWN")),
        tool_trace=str(data.get("tool_trace", "")),
        status=str(data.get("status", "SUCCEEDED")),
        expectations=expectations,
        record_id=str(data.get("record_id", "")),
        trace_id=str(data.get("trace_id", "")),
        code_version=str(data.get("code_version", "")),
        prompt_version=str(data.get("prompt_version", "")),
        model_version=str(data.get("model_version", "")),
        knowledge_base_version=str(data.get("knowledge_base_version", "")),
        graph_version=str(data.get("graph_version", "")),
        release_id=str(data.get("release_id", "")),
        manifest_digest=str(data.get("manifest_digest", "")),
        index_build_id=str(data.get("index_build_id", "")),
        eval_release_id=str(data.get("eval_release_id", "")),
    )


def _span_attributes(span: Mapping[str, Any]) -> dict[str, Any]:
    """兼容 OTLP JSON 的对象属性和 key/value 数组两种表示。"""

    raw = span.get("attributes", span.get("span_attributes", {}))
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    attributes: dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping) or "key" not in item:
                continue
            value = item.get("value")
            if isinstance(value, Mapping) and len(value) == 1:
                value = next(iter(value.values()))
            attributes[str(item["key"])] = value
    return attributes


def _trace_spans(trace_data: Mapping[str, Any] | list[Any]) -> list[Mapping[str, Any]]:
    """兼容单条 Trace、Trace 数组以及 OTLP resourceSpans 数组。"""

    if isinstance(trace_data, list):
        if trace_data and all(
            isinstance(item, Mapping) and "resourceSpans" in item for item in trace_data
        ):
            raw_spans = [
                span
                for trace in trace_data
                for resource in trace.get("resourceSpans", [])
                if isinstance(resource, Mapping)
                for scope in resource.get("scopeSpans", [])
                if isinstance(scope, Mapping)
                for span in scope.get("spans", [])
            ]
        elif trace_data and all(
            isinstance(item, Mapping) and "scopeSpans" in item for item in trace_data
        ):
            raw_spans = [
                span
                for resource in trace_data
                for scope in resource.get("scopeSpans", [])
                if isinstance(scope, Mapping)
                for span in scope.get("spans", [])
            ]
        else:
            raw_spans = trace_data
    else:
        raw_spans = trace_data.get("spans", trace_data.get("resourceSpans", []))
        if (
            isinstance(raw_spans, list)
            and raw_spans
            and isinstance(raw_spans[0], Mapping)
            and "scopeSpans" in raw_spans[0]
        ):
            raw_spans = [
                span
                for resource in raw_spans
                for scope in resource.get("scopeSpans", [])
                for span in scope.get("spans", [])
            ]
    return [span for span in raw_spans if isinstance(span, Mapping)]


def _attribute_value(attributes: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in attributes:
            return attributes[key]
    return None


def _as_contexts(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return (value,) if value else ()
        return _as_contexts(parsed)
    return ()


def trace_to_case(
    trace_data: Mapping[str, Any] | list[Any],
    *,
    case_id: str | None = None,
) -> TruLensCase:
    """将一条真实 OTLP/TruLens Trace 转换为离线评测案例。

    根 Span 负责输入、输出和版本信息；检索、工具 Span 负责补齐上下文与工具轨迹。
    函数只读取已脱敏的 OTEL 属性，不从原始请求日志或业务数据库反推内容。
    """

    spans = _trace_spans(trace_data)
    if not spans:
        raise TruLensEvaluationError("Trace 中没有可转换的 Span")
    decorated = [(span, _span_attributes(span)) for span in spans]
    root, root_attrs = next(
        (
            item
            for item in decorated
            if item[1].get("ai.observability.span_type") == "record_root"
            or item[0].get("name") == "fitness.agent.request"
        ),
        decorated[0],
    )
    contexts: list[str] = []
    tools: list[str] = []
    routes: list[str] = []
    for span, attributes in decorated:
        context_value = _attribute_value(
            attributes,
            "ai.observability.retrieval.retrieved_contexts",
            "fitness.agent.contexts",
        )
        contexts.extend(_as_contexts(context_value))
        tool_id = _attribute_value(attributes, "fitness.agent.tool_id", "gen_ai.tool.name")
        if tool_id:
            tools.append(str(tool_id))
        route = attributes.get("fitness.agent.route")
        if route:
            routes.append(str(route))

    def text_attribute(*keys: str) -> str:
        value = _attribute_value(root_attrs, *keys)
        return str(value) if value is not None else ""

    trace_id = str(root.get("trace_id", root.get("traceId", "")))
    record_id = text_attribute("ai.observability.record_id")
    question = text_attribute("ai.observability.record_root.input")
    answer = text_attribute("ai.observability.record_root.output")
    if not trace_id:
        raise TruLensEvaluationError("Trace 根 Span 缺少 trace_id，无法关联评测记录")
    if not record_id:
        raise TruLensEvaluationError("Trace 根 Span 缺少 ai.observability.record_id")
    if not question or not answer:
        raise TruLensEvaluationError(
            "Trace 缺少可评测的输入或输出；请使用 evaluation 采集模式导出 Trace"
        )
    versions = {
        "code": text_attribute("fitness.agent.code_version"),
        "prompt": text_attribute("fitness.agent.prompt_version"),
        "model": text_attribute("fitness.agent.model", "gen_ai.request.model"),
        "knowledge_base": text_attribute("fitness.agent.knowledge_base_version"),
        "graph": text_attribute("fitness.agent.graph_version"),
    }
    linkage = {
        "release_id": text_attribute("fitness.agent.release_id"),
        "manifest_digest": text_attribute("fitness.agent.manifest_digest"),
        "index_build_id": text_attribute("fitness.agent.index_build_id"),
        "eval_release_id": text_attribute("fitness.agent.eval_release_id"),
    }
    missing_versions = [name for name, value in versions.items() if not value]
    if missing_versions:
        raise TruLensEvaluationError("Trace 缺少版本关联字段：" + ", ".join(missing_versions))
    return TruLensCase(
        case_id=case_id or record_id or trace_id or "trace-case",
        question=question,
        answer=answer,
        contexts=tuple(dict.fromkeys(contexts)),
        route=text_attribute("fitness.agent.route") or (routes[0] if routes else "UNKNOWN"),
        tool_trace=" -> ".join(dict.fromkeys(tools)),
        status=text_attribute("fitness.agent.status") or "SUCCEEDED",
        record_id=record_id,
        trace_id=trace_id,
        code_version=versions["code"],
        prompt_version=versions["prompt"],
        model_version=versions["model"],
        knowledge_base_version=versions["knowledge_base"],
        graph_version=versions["graph"],
        release_id=linkage["release_id"],
        manifest_digest=linkage["manifest_digest"],
        index_build_id=linkage["index_build_id"],
        eval_release_id=linkage["eval_release_id"],
    )


def traces_to_cases(raw: Any) -> list[TruLensCase]:
    """转换 Trace 数组、单条 Trace 或 OTLP resourceSpans 文档。"""

    if isinstance(raw, list):
        return [trace_to_case(item, case_id=f"trace-{index}") for index, item in enumerate(raw)]
    if not isinstance(raw, dict):
        raise TruLensEvaluationError("Trace 文件必须是 JSON 对象或数组")
    if "traces" in raw:
        traces = raw["traces"]
        if not isinstance(traces, list):
            raise TruLensEvaluationError("Trace 文件中的 traces 必须是 JSON 数组")
        return [trace_to_case(item, case_id=f"trace-{index}") for index, item in enumerate(traces)]
    return [trace_to_case(raw)]


def _answer_non_empty(response: str) -> float:
    return 1.0 if response.strip() else 0.0


def _answer_has_citation(response: str) -> float:
    return 1.0 if "[证据" in response or "[evidence" in response.lower() else 0.0


def _contains_any(response: str, phrases: tuple[str, ...]) -> bool:
    normalized = response.lower()
    return any(phrase.lower() in normalized for phrase in phrases)


def deterministic_case_metrics(case: TruLensCase, answer: str) -> dict[str, float]:
    """计算不依赖 Judge 的安全回归指标；每个案例都会产生完整指标集合。"""

    expect = case.expectations
    expected_tools = tuple(str(item) for item in expect.get("expected_tool_ids", []))
    forbidden_tools = tuple(str(item) for item in expect.get("forbidden_tool_ids", []))
    authorization_required = bool(expect.get("authorization_required", False))
    confirmation_required = bool(expect.get("confirmation_required", False))
    rag_permission_boundary = bool(expect.get("rag_permission_boundary", False))
    model_failure = bool(expect.get("model_failure", case.status == "MODEL_FAILED"))
    refusal = _contains_any(answer, ("无权", "权限", "不能访问", "无法访问", "拒绝"))
    confirmation_language = _contains_any(answer, ("确认", "批准", "授权后", "确认卡"))
    failure_language = _contains_any(answer, ("暂时无法", "请求失败", "服务不可用", "稍后重试"))
    selected_correctly = not expected_tools or all(
        expected in case.tool_trace for expected in expected_tools
    )
    wrong_tool_recovered = (
        not forbidden_tools
        or not any(forbidden in case.tool_trace for forbidden in forbidden_tools)
        or _contains_any(answer, ("已阻止", "工具选择错误", "未执行", "没有执行"))
    )
    return {
        "answer_non_empty": _answer_non_empty(answer),
        "answer_has_citation": _answer_has_citation(answer),
        "authorization_boundary": 1.0 if not authorization_required or refusal else 0.0,
        "confirmation_boundary": 1.0
        if not confirmation_required
        or (confirmation_language and not _contains_any(answer, ("已创建", "已提交", "已发布")))
        else 0.0,
        "rag_permission_boundary": 1.0 if not rag_permission_boundary or refusal else 0.0,
        "tool_selection": 1.0 if selected_correctly and wrong_tool_recovered else 0.0,
        "model_failure_handling": 1.0 if not model_failure or failure_language else 0.0,
    }


def _load_trulens() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from trulens.core import Metric, Selector, TruSession
        from trulens.core.database.connector.default import DefaultDBConnector
        from trulens.core.schema.record import Record
    except ImportError as exc:  # pragma: no cover - exercised in lean images
        raise TruLensEvaluationError(
            "TruLens 评估额外依赖未安装；请运行 uv sync --extra eval"
        ) from exc
    return Metric, Selector, TruSession, DefaultDBConnector, Record


class TruLensEvaluator:
    """构建 TruLens 指标、运行评估并按需持久化结果。"""

    def __init__(self, settings: Settings, *, persist: bool = True, judge: bool = False) -> None:
        if persist:
            # 离线运行器使用 TruLens 关系连接器，而不是服务进程的 OTEL 导出器。
            os.environ.setdefault("TRULENS_OTEL_TRACING", "0")
        Metric, Selector, TruSession, DefaultDBConnector, Record = _load_trulens()
        self._Metric = Metric
        self._Selector = Selector
        self._Record = Record
        self._session = None
        self._judge = judge
        self._max_chars = settings.trulens_capture_max_chars

        if persist:
            database_url = settings.trulens_database_url
            if database_url.startswith("sqlite:///"):
                sqlite_path = Path(database_url.removeprefix("sqlite:///"))
                sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            self._session = TruSession(
                connector=DefaultDBConnector(database_url=database_url),
            )

        self.metrics: list[Any] = [
            Metric(
                implementation=_answer_non_empty,
                name="answer_non_empty",
                selectors={"response": Selector.select_record_output()},
            ),
            Metric(
                implementation=_answer_has_citation,
                name="answer_has_citation",
                selectors={"response": Selector.select_record_output()},
            ),
        ]
        if judge:
            if not settings.trulens_judge_api_key:
                raise TruLensEvaluationError("启用 --judge 时必须提供 TRULENS_JUDGE_API_KEY")
            try:
                from trulens.providers.openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise TruLensEvaluationError(
                    "TruLens OpenAI provider 未安装；请运行 uv sync --extra eval"
                ) from exc
            # TruLens 2.10 的 OpenAI 构造器把下游 OpenAI Client 参数声明为
            # 动态 kwargs；这里保留 api_key/base_url/timeout 的兼容传递，并把构造器
            # 视为动态边界，避免其不准确的第三方类型签名污染本项目类型检查。
            provider_factory = cast(Any, OpenAI)
            provider = provider_factory(
                model_engine=settings.trulens_judge_model,
                api_key=settings.trulens_judge_api_key,
                base_url=settings.trulens_judge_base_url,
                timeout=settings.trulens_judge_timeout_seconds,
            )
            self.metrics.extend(
                [
                    Metric(
                        implementation=provider.relevance_with_cot_reasons,
                        name="answer_relevance",
                        selectors={
                            "prompt": Selector.select_record_input(),
                            "response": Selector.select_record_output(),
                        },
                    ),
                    Metric(
                        implementation=provider.groundedness_measure_with_cot_reasons,
                        name="groundedness",
                        selectors={
                            "source": Selector(span_attribute="fitness.agent.contexts"),
                            "statement": Selector.select_record_output(),
                        },
                    ),
                    Metric(
                        implementation=provider.context_relevance_with_cot_reasons,
                        name="context_relevance",
                        selectors={
                            "question": Selector.select_record_input(),
                            "context": Selector(span_attribute="fitness.agent.contexts"),
                        },
                    ),
                ]
            )
            self._tool_selection_metric = Metric(
                implementation=provider.tool_selection_with_cot_reasons,
                name="tool_selection_judge",
                selectors={"trace": Selector(trace_level=True)},
            )
        else:
            self._tool_selection_metric = None

    def evaluate_case(self, case: TruLensCase) -> dict[str, Any]:
        question = redact_text(case.question, max_chars=self._max_chars)
        answer = redact_text(case.answer, max_chars=self._max_chars)
        context = "\n\n".join(
            redact_text(item, max_chars=self._max_chars) for item in case.contexts
        )
        record = self._Record(
            app_id="fitness-agent-service",
            calls=[],
            main_input=question,
            main_output=answer,
            meta={
                "route": case.route,
                "status": case.status,
                "tool_trace": redact_text(case.tool_trace, max_chars=500),
                "contexts": context,
                "record_id": case.record_id,
                "trace_id": case.trace_id,
                "code_version": case.code_version,
                "prompt_version": case.prompt_version,
                "model_version": case.model_version,
                "knowledge_base_version": case.knowledge_base_version,
                "graph_version": case.graph_version,
                "release_id": case.release_id,
                "manifest_digest": case.manifest_digest,
                "index_build_id": case.index_build_id,
                "eval_release_id": case.eval_release_id,
            },
        )
        if self._session is not None:
            self._session.add_record(record)

        output: dict[str, Any] = {
            "case_id": case.case_id,
            "route": case.route,
            "status": case.status,
            "record_id": case.record_id,
            "trace_id": case.trace_id,
            "versions": {
                "code": case.code_version,
                "prompt": case.prompt_version,
                "model": case.model_version,
                "knowledge_base": case.knowledge_base_version,
                "graph": case.graph_version,
                "release_id": case.release_id,
                "manifest_digest": case.manifest_digest,
                "index_build_id": case.index_build_id,
                "eval_release_id": case.eval_release_id,
            },
            "metrics": {
                name: {"score": score, "explanation": None, "error": None}
                for name, score in deterministic_case_metrics(case, answer).items()
            },
        }
        for metric in self.metrics:
            kwargs: dict[str, Any] = {"response": answer}
            if metric.name == "answer_relevance":
                kwargs["prompt"] = question
            elif metric.name == "groundedness":
                kwargs.update(source=context, statement=answer)
            elif metric.name == "context_relevance":
                kwargs.update(question=question, context=context)
            result = metric.run(record=record, **kwargs)
            score = result.result
            output["metrics"][metric.name] = {
                "score": float(score) if score is not None else None,
                "explanation": result.multi_result,
                "error": result.error,
            }
            if self._session is not None:
                self._session.add_feedback(result)
        if self._tool_selection_metric is not None:
            if not case.tool_trace:
                output["metrics"]["tool_selection_judge"] = {
                    "score": None,
                    "explanation": None,
                    "error": "缺少工具 Trace，无法执行工具选择 Judge",
                }
            else:
                result = self._tool_selection_metric.run(record=record, trace=case.tool_trace)
                output["metrics"]["tool_selection_judge"] = {
                    "score": float(result.result) if result.result is not None else None,
                    "explanation": result.multi_result,
                    "error": result.error,
                }
                if self._session is not None:
                    self._session.add_feedback(result)
        return output


def load_cases(path: Path) -> list[TruLensCase]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TruLensEvaluationError(f"无法加载 TruLens 案例 {path}：{exc}") from exc
    if not isinstance(raw, list):
        raise TruLensEvaluationError("TruLens 案例必须是 JSON 数组")
    return [case_from_mapping(item) for item in raw]


def load_trace_cases(path: Path) -> list[TruLensCase]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TruLensEvaluationError(f"无法加载 Trace 文件 {path}：{exc}") from exc
    return traces_to_cases(raw)


def load_thresholds(path: Path) -> dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TruLensEvaluationError(f"无法加载 TruLens 阈值 {path}：{exc}") from exc
    if not isinstance(raw, dict):
        raise TruLensEvaluationError("TruLens 阈值必须是 JSON 对象")
    thresholds = {str(key): float(value) for key, value in raw.items()}
    if any(value < 0 or value > 1 for value in thresholds.values()):
        raise TruLensEvaluationError("TruLens 阈值必须位于 0 到 1 之间")
    return thresholds


def validate_thresholds(results: list[dict[str, Any]], thresholds: dict[str, float]) -> list[str]:
    """严格校验指标完整性；缺失指标、空分数和异常分数都会使门禁失败。"""

    failures: list[str] = []
    if not results:
        return ["没有评估 TruLens 案例"]
    for metric_name, minimum in thresholds.items():
        scores: list[float] = []
        for item in results:
            case_id = str(item.get("case_id", "<unknown>"))
            metric = item.get("metrics", {}).get(metric_name)
            if not isinstance(metric, dict) or "score" not in metric:
                failures.append(f"{case_id} 缺少指标 {metric_name}")
                continue
            score = metric.get("score")
            if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
                failures.append(f"{case_id} 指标 {metric_name} 没有有效分数")
                continue
            scores.append(float(score))
        if scores:
            average = sum(scores) / len(scores)
            if average < minimum:
                failures.append(f"{metric_name} {average:.4f} < {minimum:.4f}")
    return failures


def evaluation_run_summary(
    results: list[dict[str, Any]],
    thresholds: dict[str, float],
    *,
    source: str,
    dataset_version: str,
    run_id: str,
) -> dict[str, Any]:
    """生成一次评测运行的机器可读汇总。

    单案例结果适合定位问题，但不适合在 CI、报表或发布记录中判断整体结果。
    汇总同时保留数据集摘要、运行标识、版本覆盖率、各指标平均分和失败数量，
    让“这次评测究竟评了什么、使用了哪版数据、是否完整”可以被审计。
    """

    metric_averages: dict[str, float | None] = {}
    metric_coverage: dict[str, int] = {}
    for metric_name in thresholds:
        scores = [
            float(item["metrics"][metric_name]["score"])
            for item in results
            if isinstance(item.get("metrics", {}).get(metric_name), dict)
            and isinstance(item["metrics"][metric_name].get("score"), (int, float))
            and math.isfinite(float(item["metrics"][metric_name]["score"]))
        ]
        metric_coverage[metric_name] = len(scores)
        metric_averages[metric_name] = sum(scores) / len(scores) if scores else None

    status_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    for item in results:
        status = str(item.get("status", "UNKNOWN"))
        route = str(item.get("route", "UNKNOWN"))
        status_counts[status] = status_counts.get(status, 0) + 1
        route_counts[route] = route_counts.get(route, 0) + 1

    version_fields = ("code", "prompt", "model", "knowledge_base", "graph")
    version_coverage = {
        field: sum(
            bool(item.get("versions", {}).get(field))
            for item in results
            if isinstance(item.get("versions"), dict)
        )
        for field in version_fields
    }
    return {
        "run_id": run_id,
        "source": source,
        "dataset_version": dataset_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "case_count": len(results),
        "status_counts": status_counts,
        "route_counts": route_counts,
        "metric_averages": metric_averages,
        "metric_coverage": metric_coverage,
        "version_coverage": version_coverage,
        "threshold_count": len(thresholds),
    }


def file_fingerprint(path: Path) -> str:
    """计算评测输入文件摘要，不读取或输出文件中的业务内容。"""

    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise TruLensEvaluationError(f"无法读取评测输入文件 {path}：{exc}") from exc
    return f"sha256:{digest}"
