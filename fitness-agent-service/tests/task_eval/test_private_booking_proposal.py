import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.private_booking_proposal import build_proposal
from app.evaluation.task_eval.review_queue import build_review_queue

ROOT = Path(__file__).resolve().parents[2] / "evals/task_eval"


def test_v2_is_reproducible_and_keeps_source_cases_unreviewed():
    source = load_dataset(ROOT / "draft_parameter_proposal_v1/cases.jsonl")
    raw, manifest = build_proposal(source)
    assert raw == (ROOT / "draft_parameter_proposal_v2/cases.jsonl").read_bytes()
    assert manifest == json.loads((ROOT / "draft_parameter_proposal_v2/manifest.json").read_text())
    target = load_dataset(ROOT / "draft_parameter_proposal_v2/cases.jsonl")
    cases = {case.case_id: case for case in target.cases}
    assert len(cases) == 120
    assert all(case.review.status == "draft" for case in target.cases)
    assert build_review_queue(target)["cases_with_missing_grader_parameters"] == 0
    store = next(a for a in cases["BOOK-006"].expected.assertions if a.id == "booking_store")
    slot = next(a for a in cases["BOOK-009"].expected.assertions if a.id == "capacity_consumed")
    assert store.rule == "booking_organization_store_matches"
    assert "storeId" not in store.parameters["booking_store"]
    assert slot.rule == "booking_private_slot_consumed_once"
    assert "coachId" in slot.parameters["slot"] and "capacity" not in slot.parameters
    old = {case.case_id: case for case in source.cases}
    assert (
        next(a for a in old["BOOK-006"].expected.assertions if a.id == "booking_store").rule
        == "booking_store_matches"
    )
    assert (
        next(a for a in old["BOOK-009"].expected.assertions if a.id == "capacity_consumed").rule
        == "booking_capacity_consumed_once"
    )


def test_proposal_rejects_modified_source_identity():
    dataset = load_dataset(ROOT / "draft_parameter_proposal_v1/cases.jsonl")
    with pytest.raises(ValueError, match="PROPOSAL_SOURCE_CHANGED"):
        build_proposal(replace(dataset, sha256="0" * 64))
    changed = list(dataset.cases)
    changed[0] = changed[0].model_copy(deep=True)
    changed[0].expected.reference_points.append("altered")
    with pytest.raises(ValueError, match="PROPOSAL_SOURCE_CHANGED"):
        build_proposal(replace(dataset, cases=changed))
