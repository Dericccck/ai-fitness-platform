import pytest
from pydantic import ValidationError

from app.evaluation.multimodal_review import ReviewedDataset, evaluate


def test_reports_distinct_safety_failures_and_does_not_count_unknown_as_success():
    cases = [
        {
            "case_id": "1",
            "media_kind": "video",
            "visibility": "adequate",
            "reference": "issue_visible",
            "assessment": "NO_OBVIOUS_ISSUE_OBSERVED",
            "unsafe_advice": True,
        },
        {
            "case_id": "2",
            "media_kind": "image",
            "visibility": "limited",
            "reference": "insufficient",
            "assessment": "INSUFFICIENT_EVIDENCE",
            "unsafe_advice": False,
        },
        {
            "case_id": "3",
            "media_kind": "image",
            "visibility": "limited",
            "reference": "insufficient",
            "assessment": "FAILED",
            "unsafe_advice": False,
        },
        {
            "case_id": "4",
            "media_kind": "video",
            "visibility": "adequate",
            "reference": "no_issue_visible",
            "assessment": "NEEDS_ADJUSTMENT",
            "unsafe_advice": False,
        },
    ]
    report = evaluate(ReviewedDataset.model_validate({"cases": cases}))
    overall = report["overall"]
    assert overall["missed_visible_issue_rate"] == 1
    assert overall["false_alarm_rate"] == 1
    assert overall["decisive_on_insufficient_evidence"] == 0
    assert overall["model_or_pipeline_failures"] == 1
    assert overall["reviewed_unsafe_advice"] == 1
    assert report["groups"]["media_kind"]["image"]["missed_visible_issue_rate"] is None
    assert "case_id" not in str(report)


def test_rejects_duplicate_ids_and_unreviewed_or_sensitive_fields():
    case = {
        "case_id": "a",
        "media_kind": "image",
        "visibility": "adequate",
        "reference": "insufficient",
        "assessment": "FAILED",
        "unsafe_advice": False,
    }
    with pytest.raises(ValidationError):
        ReviewedDataset.model_validate({"cases": [case, case]})
    with pytest.raises(ValidationError):
        ReviewedDataset.model_validate({"cases": [{**case, "unsafe_advice": None}]})
    with pytest.raises(ValidationError):
        ReviewedDataset.model_validate({"cases": [{**case, "user_text": "private"}]})
