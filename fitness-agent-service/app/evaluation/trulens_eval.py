"""用于已脱敏 Supervisor 追踪记录的 TruLens 离线评估器。

该运行器刻意与请求路径分离。确定性的策略、授权、确认和检索门仍是发布依据；TruLens
指标补充语义质量信号，并持久化用于比较。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.evaluation.telemetry import redact_text


class TruLensEvaluationError(RuntimeError):
    """可选 TruLens 评估环境不完整时抛出。"""


@dataclass(frozen=True)
class TruLensCase:
    case_id: str
    question: str
    answer: str
    contexts: tuple[str, ...] = ()
    route: str = "UNKNOWN"
    tool_trace: str = ""


def case_from_mapping(data: dict[str, Any]) -> TruLensCase:
    return TruLensCase(
        case_id=str(data["case_id"]),
        question=str(data["question"]),
        answer=str(data["answer"]),
        contexts=tuple(str(item) for item in data.get("contexts", [])),
        route=str(data.get("route", "UNKNOWN")),
        tool_trace=str(data.get("tool_trace", "")),
    )


def _answer_non_empty(response: str) -> float:
    return 1.0 if response.strip() else 0.0


def _answer_has_citation(response: str) -> float:
    return 1.0 if "[证据" in response or "[evidence" in response.lower() else 0.0


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
        # 离线运行器使用 TruLens 的关系连接器，而不是其实验性的全局 OTEL provider；
        # 该 provider 已由服务负责管理。
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
                raise TruLensEvaluationError(
                    "启用 --judge 时必须提供 TRULENS_JUDGE_API_KEY"
                )
            try:
                from trulens.providers.openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise TruLensEvaluationError(
                    "TruLens OpenAI provider 未安装；请运行 uv sync --extra eval"
                ) from exc
            provider_factory: Any = OpenAI
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
                name="tool_selection",
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
                "tool_trace": redact_text(case.tool_trace, max_chars=500),
                "contexts": context,
            },
        )
        if self._session is not None:
            self._session.add_record(record)

        output: dict[str, Any] = {"case_id": case.case_id, "route": case.route, "metrics": {}}
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
        if self._tool_selection_metric is not None and case.tool_trace:
            result = self._tool_selection_metric.run(record=record, trace=case.tool_trace)
            output["metrics"][self._tool_selection_metric.name] = {
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


def load_thresholds(path: Path) -> dict[str, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TruLensEvaluationError(f"无法加载 TruLens 阈值 {path}：{exc}") from exc
    return {str(key): float(value) for key, value in raw.items()}


def validate_thresholds(results: list[dict[str, Any]], thresholds: dict[str, float]) -> list[str]:
    failures: list[str] = []
    if not results:
        return ["没有评估 TruLens 案例"]
    for metric_name, minimum in thresholds.items():
        scores = [
            item["metrics"][metric_name]["score"]
            for item in results
            if metric_name in item["metrics"] and item["metrics"][metric_name]["score"] is not None
        ]
        if not scores:
            continue
        average = sum(scores) / len(scores)
        if average < minimum:
            failures.append(f"{metric_name} {average:.4f} < {minimum:.4f}")
    return failures
