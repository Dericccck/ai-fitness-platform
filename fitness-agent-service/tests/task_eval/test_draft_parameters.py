import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.draft_parameters import build_proposal
from app.evaluation.task_eval.graders.rules import grade_rule
from app.evaluation.task_eval.review_queue import build_review_queue

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "evals/task_eval/datasets/v1.jsonl"
PROPOSAL = ROOT / "evals/task_eval/draft_parameter_proposal_v1"


def test_generated_proposal_is_reproducible_and_remains_unreviewed(tmp_path):
    original = load_dataset(SOURCE)
    raw, manifest = build_proposal(original)
    assert raw == (PROPOSAL / "cases.jsonl").read_bytes()
    assert manifest == json.loads((PROPOSAL / "manifest.json").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == manifest["proposal_dataset_sha256"]
    assert manifest["changed_assertions"] == 104
    path = tmp_path / "cases.jsonl"
    path.write_bytes(raw)
    proposal = load_dataset(path)
    assert len(proposal.cases) == 120
    assert all(
        c.review.status == "draft" and c.capability_status == "planned" for c in proposal.cases
    )
    assert build_review_queue(proposal)["cases_with_missing_grader_parameters"] == 18
    for old, new in zip(original.cases, proposal.cases, strict=True):
        assert (new.case_id, new.split, new.steps, new.fixture, new.source, new.review) == (
            old.case_id,
            old.split,
            old.steps,
            old.fixture,
            old.source,
            old.review,
        )
        for a, b in zip(old.expected.assertions, new.expected.assertions, strict=True):
            assert (a.id, a.phase, a.rule, a.risk, a.required) == (
                b.id,
                b.phase,
                b.rule,
                b.risk,
                b.required,
            )
            assert all(b.parameters[k] == v for k, v in a.parameters.items())
            if b.rule == "semantic":
                assert grade_rule(b.rule, b.parameters, None)["status"] == "UNKNOWN"


def test_only_frozen_input_can_generate_proposal():
    original = load_dataset(SOURCE)
    altered = original.cases[0].model_copy(deep=True)
    altered.expected.reference_points.append("unverified")
    with pytest.raises(ValueError, match="frozen planning content"):
        build_proposal(replace(original, cases=[altered, *original.cases[1:]]))
    with pytest.raises(ValueError, match="source dataset"):
        build_proposal(replace(original, sha256="0" * 64))


def test_booking_values_are_bound_to_fixture_and_safety_is_not_auto_parameterized(tmp_path):
    raw, manifest = build_proposal(load_dataset(SOURCE))
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    dataset = load_dataset(path)
    cases = {case.case_id: case for case in dataset.cases}
    for cid in ("BOOK-008", "BOOK-018", "BOOK-026", "BOOK-027", "BOOK-030"):
        assertion = next(
            a for a in cases[cid].expected.assertions if a.rule == "confirmation_pending_for_actor"
        )
        assert assertion.parameters == {"actor": "student-1", "action": "fitness.booking.create.v1"}
        assert len(manifest["fixture_sha256"][cid]) == 64
    book = next(
        a for a in cases["BOOK-008"].expected.assertions if a.rule == "booking_created_once"
    )
    assert book.parameters["booking"]["startTime"] == "2030-01-01T02:00:00Z"
    assert book.parameters["credit_delta"] == -1
    for case in dataset.cases:
        for assertion in case.expected.assertions:
            if assertion.rule == "no_severe_unsafe_guidance":
                assert "metric_id" not in assertion.parameters
                assert grade_rule(assertion.rule, assertion.parameters, None)["status"] == "UNKNOWN"


def test_rubric_tampering_fails_closed(tmp_path, monkeypatch):
    from app.evaluation.task_eval import draft_parameters

    bad_rubric = tmp_path / "rubric.md"
    bad_rubric.write_text("changed rubric", encoding="utf-8")
    monkeypatch.setattr(draft_parameters, "_RUBRIC_PATH", bad_rubric)
    with pytest.raises(ValueError, match="rubric content has changed"):
        build_proposal(load_dataset(SOURCE))


def test_unverified_eligibility_cases_do_not_invent_recipients(tmp_path):
    raw, _ = build_proposal(load_dataset(SOURCE))
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    cases = {c.case_id: c for c in load_dataset(path).cases}
    for cid in ("OPER-015", "OPER-016"):
        assertion = cases[cid].expected.assertions[0]
        assert assertion.parameters["delivery_count"] == 0
        assert "recipient" not in assertion.parameters
        assert (
            grade_rule(
                "notification_policy",
                assertion.parameters,
                {"evidence_refs": ["x"], "delivery_window_complete": True, "deliveries": []},
            )["status"]
            == "UNKNOWN"
        )


def test_outreach_parameters_are_bound_to_synthetic_fixture_and_window(tmp_path):
    from app.evaluation.task_eval.outreach_draft_fixtures import load_contracts

    original = load_dataset(SOURCE)
    contracts = {row["case_id"]: row for row in load_contracts(original)}
    raw, manifest = build_proposal(original)
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    cases = {c.case_id: c for c in load_dataset(path).cases}
    for index in range(9, 14):
        cid = f"OPER-{index:03}"
        parameters = cases[cid].expected.assertions[0].parameters
        assert parameters["recipient"] == contracts[cid]["subject"]
        assert parameters["delivery_count"] == contracts[cid]["expected_delivery_count"]
        assert cases[cid].review.status == "draft"
        change = next(c for c in manifest["changes"] if c["case_id"] == cid)
        assert change["fixture_sha256"] == manifest["outreach_fixture_sha256"]
        assert grade_rule("notification_policy", parameters, None)["status"] == "UNKNOWN"
        assert (
            grade_rule(
                "notification_policy",
                parameters,
                {
                    "evidence_refs": ["synthetic-window"],
                    "delivery_window_complete": False,
                    "deliveries": [],
                },
            )["status"]
            == "UNKNOWN"
        )
        assert (
            grade_rule(
                "notification_policy",
                parameters,
                {
                    "evidence_refs": ["synthetic-window"],
                    "delivery_window_complete": True,
                    "deliveries": [{"recipient": "wrong"}],
                },
            )["status"]
            == "FAIL"
        )
    assert "recipient" not in cases["OPER-014"].expected.assertions[0].parameters
    assert "delivery_count" not in cases["OPER-014"].expected.assertions[0].parameters
    assert any("quiet-hours observation window" in item for item in manifest["limitations"])


def test_modified_outreach_fixture_is_rejected(tmp_path, monkeypatch):
    from app.evaluation.task_eval import outreach_draft_fixtures

    path = tmp_path / "outreach.json"
    path.write_bytes(outreach_draft_fixtures._FIXTURE_PATH.read_bytes() + b" ")
    monkeypatch.setattr(outreach_draft_fixtures, "_FIXTURE_PATH", path)
    with pytest.raises(ValueError, match="OUTREACH_FIXTURE_CHANGED"):
        build_proposal(load_dataset(SOURCE))


def test_book_030_fixture_and_case_tampering_fail_closed(tmp_path, monkeypatch):
    from app.evaluation.task_eval import booking_test_030

    original = load_dataset(SOURCE)
    case = next(c for c in original.cases if c.case_id == "BOOK-030")
    fixture = booking_test_030.load(case)
    assert fixture["payload"]["student_id"] == "student-1"
    assert fixture["payload"]["organization_id"] == "org-1"
    altered = case.model_copy(deep=True)
    altered.steps[0].message_ref = "planned-other.booking_request"
    with pytest.raises(ValueError, match="TEST_CASE_CHANGED"):
        booking_test_030.load(altered)
    fixture_file = tmp_path / "book-030.json"
    fixture_file.write_bytes(booking_test_030._FIXTURE.read_bytes() + b" ")
    monkeypatch.setattr(booking_test_030, "_FIXTURE", fixture_file)
    with pytest.raises(ValueError, match="TEST_FIXTURE_CHANGED"):
        build_proposal(original)
