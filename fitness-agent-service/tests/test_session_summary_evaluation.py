import json

from app.session_summary_evaluation import (
    SessionSummaryEvalCase,
    SessionSummaryEvalThresholds,
    aggregate_results,
    evaluate_case,
)


def test_session_summary_evaluation_accepts_safe_and_redacted_samples() -> None:
    safe = evaluate_case(
        SessionSummaryEvalCase(
            "safe",
            '{"summary":"用户希望力量训练；动态课程需要重新查询。"}',
            ("力量训练", "重新查询"),
        ),
        max_summary_chars=3000,
    )
    redacted = evaluate_case(
        SessionSummaryEvalCase(
            "redacted",
            '{"summary":"用户 token: demo-token-123456789，目标是减脂。"}',
            ("减脂",),
            expect_redaction=True,
        ),
        max_summary_chars=3000,
    )

    assert safe.passed is True
    assert redacted.passed is True
    assert redacted.redaction_applied is True
    assert aggregate_results([safe, redacted])["pass_rate"] == 1.0


def test_session_summary_evaluation_blocks_invalid_and_oversize_output() -> None:
    invalid = evaluate_case(SessionSummaryEvalCase("invalid", "not-json"), max_summary_chars=3000)
    oversize = evaluate_case(
        SessionSummaryEvalCase("oversize", json.dumps({"summary": "内容" * 10})),
        max_summary_chars=3,
    )
    thresholds = SessionSummaryEvalThresholds(
        max_summary_chars=3,
        min_pass_rate=1.0,
        max_invalid_cases=0,
        max_oversize_cases=0,
    )
    metrics = aggregate_results([invalid, oversize])

    assert invalid.invalid_json is True
    assert oversize.oversize is True
    failures = thresholds.validate(metrics)
    assert "invalid_cases" in " ".join(failures)
    assert "oversize_cases" in " ".join(failures)


def test_session_summary_evaluation_counts_missing_required_terms() -> None:
    result = evaluate_case(
        SessionSummaryEvalCase(
            "missing-term",
            '{"summary":"用户想进行力量训练。"}',
            ("重新查询",),
        ),
        max_summary_chars=3000,
    )

    assert result.passed is False
    assert result.missing_required_terms == 1
