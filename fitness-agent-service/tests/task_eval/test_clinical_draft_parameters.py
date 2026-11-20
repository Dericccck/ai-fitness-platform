from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.evaluation.task_eval import clinical_draft_parameters as clinical
from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.graders.rules import grade_rule

SOURCE = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


@pytest.mark.parametrize("case_id", ["SAFE-013", "SAFE-014", "SAFE-015"])
def test_draft_parameters_preserve_original_and_cannot_score_without_review(case_id):
    case = next(c for c in load_dataset(SOURCE).cases if c.case_id == case_id)
    before = deepcopy(case.model_dump(mode="json"))
    parameters = clinical.proposal_for_case(case)
    assert parameters == {
        "metric_id": f"{case_id}.task_result.draft-clinical-safety-v1",
        "prompt_version": "draft-clinical-safety-rubric-v1",
        "score_mapping": {"0": "FAIL", "1": "UNKNOWN", "2": "PASS"},
    }
    assert case.model_dump(mode="json") == before
    assert case.review.status == "draft" and case.capability_status == "planned"
    contract = dict(case.expected.assertions[0].parameters, **parameters)
    assert grade_rule("no_severe_unsafe_guidance", contract, None)["status"] == "UNKNOWN"
    assert (
        grade_rule("no_severe_unsafe_guidance", contract, {"evidence_refs": ["x"]})["status"]
        == "UNKNOWN"
    )


def test_rubric_or_case_tampering_cannot_be_parameterized(tmp_path, monkeypatch):
    case = next(c for c in load_dataset(SOURCE).cases if c.case_id == "SAFE-013")
    changed = case.model_copy(deep=True)
    changed.expected.reference_points.append("changed")
    with pytest.raises(ValueError, match="CLINICAL_DRAFT_CASE_CHANGED"):
        clinical.proposal_for_case(changed)
    changed = case.model_copy(deep=True)
    changed.case_id = "SAFE-012"
    with pytest.raises(ValueError, match="CLINICAL_DRAFT_CASE_CHANGED"):
        clinical.proposal_for_case(changed)
    path = tmp_path / "rubric.md"
    path.write_bytes(clinical.RUBRIC_PATH.read_bytes() + b" ")
    monkeypatch.setattr(clinical, "RUBRIC_PATH", path)
    with pytest.raises(ValueError, match="CLINICAL_DRAFT_RUBRIC_CHANGED"):
        clinical.proposal_for_case(case)
