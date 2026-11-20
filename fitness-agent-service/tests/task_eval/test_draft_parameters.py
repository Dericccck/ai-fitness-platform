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
    assert manifest["changed_assertions"] == 131
    path = tmp_path / "cases.jsonl"
    path.write_bytes(raw)
    proposal = load_dataset(path)
    assert len(proposal.cases) == 120
    assert all(
        c.review.status == "draft" and c.capability_status == "planned" for c in proposal.cases
    )
    assert build_review_queue(proposal)["cases_with_missing_grader_parameters"] == 0
    for old, new in zip(original.cases, proposal.cases, strict=True):
        assert (new.case_id, new.split, new.steps, new.fixture, new.source, new.review) == (
            old.case_id,
            old.split,
            old.steps,
            old.fixture,
            old.source,
            old.review,
        )
        for a, b in zip(
            old.expected.assertions,
            new.expected.assertions[: len(old.expected.assertions)],
            strict=True,
        ):
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


def test_booking_values_and_unreviewed_safety_parameters_are_bound_to_proposals(tmp_path):
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
                assert assertion.parameters["metric_id"] == (
                    f"{case.case_id}.task_result.draft-clinical-safety-v1"
                )
                assert assertion.parameters["prompt_version"] == manifest["clinical_rubric_version"]
                assert assertion.parameters["score_mapping"] == {
                    "0": "FAIL",
                    "1": "UNKNOWN",
                    "2": "PASS",
                }
                assert case.review.status == "draft" and case.capability_status == "planned"
                assert grade_rule(assertion.rule, assertion.parameters, None)["status"] == "UNKNOWN"


def test_rubric_tampering_fails_closed(tmp_path, monkeypatch):
    from app.evaluation.task_eval import draft_parameters

    bad_rubric = tmp_path / "rubric.md"
    bad_rubric.write_text("changed rubric", encoding="utf-8")
    monkeypatch.setattr(draft_parameters, "_RUBRIC_PATH", bad_rubric)
    with pytest.raises(ValueError, match="rubric content has changed"):
        build_proposal(load_dataset(SOURCE))


def test_unverified_eligibility_cases_use_only_synthetic_subjects(tmp_path):
    raw, _ = build_proposal(load_dataset(SOURCE))
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    cases = {c.case_id: c for c in load_dataset(path).cases}
    for cid in ("OPER-015", "OPER-016"):
        assertion = cases[cid].expected.assertions[0]
        assert assertion.parameters["delivery_count"] == 0
        assert assertion.parameters["recipient"] == f"synthetic-{cid.lower()}-recipient"
        assert (
            grade_rule(
                "notification_policy",
                assertion.parameters,
                {"evidence_refs": ["x"], "delivery_window_complete": False, "deliveries": []},
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
    assert cases["OPER-014"].expected.assertions[0].parameters["recipient"] == (
        "synthetic-oper-014-recipient"
    )
    assert "delivery_count" not in cases["OPER-014"].expected.assertions[0].parameters
    assert cases["OPER-014"].expected.assertions[0].parameters["max_delivery_count"] == 1
    assert any("quiet-hours observation window" in item for item in manifest["limitations"])


def test_duplicate_notification_checks_at_most_one_without_assuming_delivery(tmp_path):
    raw, _ = build_proposal(load_dataset(SOURCE))
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    case = next(c for c in load_dataset(path).cases if c.case_id == "OPER-014")
    params = case.expected.assertions[0].parameters
    delivery = {"recipient": params["recipient"]}
    evidence = {"evidence_refs": ["unit-test-only"], "delivery_window_complete": True}
    for deliveries in ([], [delivery]):
        assert (
            grade_rule("notification_policy", params, dict(evidence, deliveries=deliveries))[
                "status"
            ]
            == "PASS"
        )
    assert (
        grade_rule("notification_policy", params, dict(evidence, deliveries=[delivery, delivery]))[
            "status"
        ]
        == "FAIL"
    )
    assert (
        grade_rule(
            "notification_policy",
            params,
            dict(evidence, delivery_window_complete=False, deliveries=[]),
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "notification_policy",
            dict(params, delivery_count=1),
            dict(evidence, deliveries=[delivery]),
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "notification_policy",
            dict(params, max_delivery_count=True),
            dict(evidence, deliveries=[]),
        )["status"]
        == "UNKNOWN"
    )


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


def test_supplemental_booking_proposals_require_actual_evidence(tmp_path):
    from app.evaluation.task_eval.booking_parameter_fixtures import POSITIVE, load_contracts

    source = load_dataset(SOURCE)
    contracts = load_contracts(source)
    raw, manifest = build_proposal(source)
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    cases = {case.case_id: case for case in load_dataset(path).cases}
    for cid, fixture in contracts.items():
        pending = next(
            a for a in cases[cid].expected.assertions if a.rule == "confirmation_pending_for_actor"
        )
        assert pending.parameters["actor"] == fixture["payload"]["student_id"]
        assert grade_rule(pending.rule, pending.parameters, None)["status"] == "UNKNOWN"
        assert cases[cid].review.status == "draft"
        if cid not in POSITIVE:
            continue
        assertion = next(
            a for a in cases[cid].expected.assertions if a.rule == "booking_created_once"
        )
        assert assertion.parameters["credit_delta"] == -1
        assert grade_rule(assertion.rule, assertion.parameters, None)["status"] == "UNKNOWN"
        evidence = {
            "evidence_refs": ["unit-test-only"],
            "created_bookings": [assertion.parameters["booking"]],
            "snapshot_complete": True,
            "credit_delta": -1,
            "idempotency_checked": True,
        }
        assert grade_rule(assertion.rule, assertion.parameters, evidence)["status"] == "PASS"
        wrong = dict(assertion.parameters["booking"], studentId="another-student")
        assert (
            grade_rule(
                assertion.rule, assertion.parameters, dict(evidence, created_bookings=[wrong])
            )["status"]
            == "FAIL"
        )
        assert (
            grade_rule(
                assertion.rule, assertion.parameters, dict(evidence, snapshot_complete=False)
            )["status"]
            == "UNKNOWN"
        )
    assert {case_id for case_id in contracts if cases[case_id].split == "test"} == {
        "BOOK-009",
        "BOOK-010",
        "BOOK-029",
        "BOOK-031",
    }
    assert len({v["payload"]["student_id"] for v in contracts.values()}) == len(contracts)
    changes = [c for c in manifest["changes"] if c["case_id"] in contracts]
    assert len(changes) == 26  # includes three semantic and two added business assertions
    assert all(
        c["fixture_sha256"] == manifest["booking_parameter_fixture_sha256"]
        for c in changes
        if c["provenance"] == "case-bound synthetic booking proposal"
    )
    for cid in ("BOOK-006", "BOOK-009"):
        assert "booking" in cases[cid].expected.assertions[0].parameters
        assert cases[cid].expected.assertions[-1].required


def test_store_and_capacity_are_independent_required_assertions(tmp_path):
    raw, _ = build_proposal(load_dataset(SOURCE))
    path = tmp_path / "proposal.jsonl"
    path.write_bytes(raw)
    cases = {c.case_id: c for c in load_dataset(path).cases}
    store = cases["BOOK-006"].expected.assertions[-1]
    capacity = cases["BOOK-009"].expected.assertions[-1]
    for assertion in (store, capacity):
        assert assertion.required and assertion.phase == "final"
        assert grade_rule(assertion.rule, assertion.parameters, None)["status"] == "UNKNOWN"

    store_row = store.parameters["booking_store"]
    store_evidence = {
        "evidence_refs": ["unit-only"],
        "store_window_complete": True,
        "store_booking": store_row,
    }
    assert grade_rule(store.rule, store.parameters, store_evidence)["status"] == "PASS"
    assert (
        grade_rule(
            store.rule,
            store.parameters,
            dict(store_evidence, store_booking=dict(store_row, storeId="wrong")),
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule(store.rule, store.parameters, dict(store_evidence, store_window_complete=False))[
            "status"
        ]
        == "UNKNOWN"
    )
    assert (
        grade_rule(store.rule, store.parameters, dict(store_evidence, store_booking={}))["status"]
        == "UNKNOWN"
    )

    params = capacity.parameters
    observation = {
        "course_id": params["course_id"],
        "student_id": params["student_id"],
        "organization_id": params["organization_id"],
        "start_time": params["start_time"],
        "end_time": params["end_time"],
        "capacity": params["capacity"],
        "occupied_before": params["occupied_before"],
        "occupied_after": params["occupied_before"] + 1,
        "student_before": 0,
        "student_after": 1,
    }
    evidence = {
        "evidence_refs": ["unit-only"],
        "capacity_window_complete": True,
        "capacity_snapshots_consistent": True,
        "capacity_observation": observation,
    }
    assert grade_rule(capacity.rule, params, evidence)["status"] == "PASS"
    for key, value in (("occupied_after", params["occupied_before"]), ("student_after", 0)):
        assert (
            grade_rule(
                capacity.rule,
                params,
                dict(evidence, capacity_observation=dict(observation, **{key: value})),
            )["status"]
            == "FAIL"
        )
    for change in (
        {"capacity_window_complete": False},
        {"capacity_snapshots_consistent": False},
        {"capacity_observation": dict(observation, occupied_before=0)},
        {"capacity_observation": dict(observation, course_id="wrong")},
        {"capacity_observation": dict(observation, student_after=True)},
    ):
        assert grade_rule(capacity.rule, params, dict(evidence, **change))["status"] == "UNKNOWN"


def test_supplemental_booking_fixture_tamper_fails_closed(tmp_path, monkeypatch):
    from app.evaluation.task_eval import booking_parameter_fixtures

    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(booking_parameter_fixtures._FIXTURE.read_bytes() + b" ")
    monkeypatch.setattr(booking_parameter_fixtures, "_FIXTURE", fixture)
    with pytest.raises(ValueError, match="BOOKING_PROPOSAL_FIXTURE_CHANGED"):
        build_proposal(load_dataset(SOURCE))
