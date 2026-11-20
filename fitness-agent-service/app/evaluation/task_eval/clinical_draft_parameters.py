"""Case-bound clinical rubric parameter proposals, never professional judgments.

Original cases and source identities remain untouched. The caller must retain
all draft/review restrictions; these values do not prove safety or approval.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

RUBRIC_VERSION = "draft-clinical-safety-rubric-v1"
RUBRIC_PATH = (
    Path(__file__).resolve().parents[3]
    / "evals/task_eval/draft_parameter_proposal_v1/CLINICAL_RUBRIC_DRAFT.md"
)
RUBRIC_SHA256 = "d37ec03f8330f7d853f462b4bb3580a05cd028662a853e31614bbda43b40ca0a"
CASE_SHA256 = {
    "SAFE-013": "f2e0fde7400209637289a498df6ee8eb66ca119d16ada2881f648e59cff8a067",
    "SAFE-014": "2ab1538b16ce49f666fb2c1c4fbcbcd8b1c5c3cc109625439e37d492a00fbb58",
    "SAFE-015": "f247674d7ec97babd0cdc29de510498a71f54d9e364c0cc7d0c906577898a4b6",
}


def proposal_for_case(case: Any) -> dict[str, Any]:
    """Return only new draft grader fields for an exact original case."""
    expected = CASE_SHA256.get(case.case_id)
    raw = json.dumps(
        case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    if expected is None or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("CLINICAL_DRAFT_CASE_CHANGED")
    if hashlib.sha256(RUBRIC_PATH.read_bytes()).hexdigest() != RUBRIC_SHA256:
        raise ValueError("CLINICAL_DRAFT_RUBRIC_CHANGED")
    assertion = case.expected.assertions[0]
    if (
        len(case.expected.assertions) != 1
        or assertion.id != "task_result"
        or assertion.phase != "final"
        or assertion.rule != "no_severe_unsafe_guidance"
        or assertion.risk != "unsafe_guidance"
        or assertion.required is not True
        or assertion.applicable is not True
        or case.review.status != "draft"
        or case.capability_status != "planned"
    ):
        raise ValueError("CLINICAL_DRAFT_CASE_CHANGED")
    return {
        "metric_id": f"{case.case_id}.task_result.draft-clinical-safety-v1",
        "prompt_version": RUBRIC_VERSION,
        "score_mapping": {"0": "FAIL", "1": "UNKNOWN", "2": "PASS"},
    }
