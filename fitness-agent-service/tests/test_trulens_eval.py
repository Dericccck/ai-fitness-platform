import pytest

from app.evaluation.trulens_eval import (
    TruLensCase,
    deterministic_case_metrics,
    evaluation_run_summary,
    trace_to_case,
    validate_thresholds,
)


def test_threshold_validation_rejects_missing_metric_instead_of_skipping() -> None:
    failures = validate_thresholds(
        [{"case_id": "case-1", "metrics": {"answer_non_empty": {"score": 1.0}}}],
        {"answer_non_empty": 1.0, "groundedness": 0.6},
    )

    assert "case-1 缺少指标 groundedness" in failures


def test_trace_is_converted_to_case_with_context_tool_and_versions() -> None:
    case = trace_to_case(
        {
            "spans": [
                {
                    "name": "fitness.agent.request",
                    "trace_id": "trace-1",
                    "attributes": {
                        "ai.observability.span_type": "record_root",
                        "ai.observability.record_id": "record-1",
                        "ai.observability.record_root.input": "训练后怎么拉伸？",
                        "ai.observability.record_root.output": "请整理活动。[证据1]",
                        "fitness.agent.route": "FITNESS_COACHING",
                        "fitness.agent.status": "SUCCEEDED",
                        "fitness.agent.code_version": "commit-1",
                        "fitness.agent.prompt_version": "prompt-2",
                        "fitness.agent.knowledge_base_version": "kb-3",
                        "fitness.agent.model": "deepseek-test",
                        "fitness.agent.graph_version": "graph-4",
                    },
                },
                {
                    "name": "fitness.agent.retrieval",
                    "attributes": {
                        "ai.observability.retrieval.retrieved_contexts": ["拉伸证据"],
                    },
                },
                {
                    "name": "fitness.agent.tool",
                    "attributes": {"fitness.agent.tool_id": "fitness.rag.search.v1"},
                },
            ]
        }
    )

    assert case.record_id == "record-1"
    assert case.contexts == ("拉伸证据",)
    assert case.tool_trace == "fitness.rag.search.v1"
    assert case.code_version == "commit-1"
    assert case.prompt_version == "prompt-2"
    assert case.knowledge_base_version == "kb-3"
    assert case.graph_version == "graph-4"


def test_deterministic_safety_metrics_cover_negative_cases() -> None:
    case = TruLensCase(
        case_id="unauthorized",
        question="越权查询",
        answer="没有权限，已拒绝访问。[证据1]",
        expectations={"authorization_required": True, "rag_permission_boundary": True},
    )

    metrics = deterministic_case_metrics(case, case.answer)

    assert metrics["authorization_boundary"] == 1.0
    assert metrics["rag_permission_boundary"] == 1.0


def test_trace_without_spans_is_rejected() -> None:
    with pytest.raises(Exception, match="没有可转换的 Span"):
        trace_to_case({"spans": []})


def test_trace_without_content_or_version_linkage_is_rejected() -> None:
    with pytest.raises(Exception, match="可评测的输入或输出"):
        trace_to_case(
            {
                "spans": [
                    {
                        "name": "fitness.agent.request",
                        "trace_id": "trace-1",
                        "attributes": {
                            "ai.observability.record_id": "record-1",
                        },
                    }
                ]
            }
        )


def test_evaluation_run_summary_contains_coverage_and_counts() -> None:
    results = [
        {
            "case_id": "case-1",
            "route": "FITNESS_COACHING",
            "status": "SUCCEEDED",
            "versions": {
                "code": "commit-1",
                "prompt": "prompt-1",
                "model": "model-1",
                "knowledge_base": "kb-1",
                "graph": "graph-1",
            },
            "metrics": {"answer_non_empty": {"score": 1.0}},
        }
    ]

    summary = evaluation_run_summary(
        results,
        {"answer_non_empty": 1.0},
        source="cases",
        dataset_version="sha256:test",
        run_id="run-1",
    )

    assert summary["case_count"] == 1
    assert summary["metric_coverage"] == {"answer_non_empty": 1}
    assert summary["version_coverage"]["graph"] == 1
