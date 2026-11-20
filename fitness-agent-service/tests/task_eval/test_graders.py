"""Grader counterexamples use synthetic observations, not model quality results."""

from copy import deepcopy

import pytest

from app.evaluation.task_eval.graders import grade_retrieval, grade_rule, grade_semantic

REFS = {"evidence_refs": ["test://observation/1"]}


def test_missing_evidence_never_passes_and_na_is_declared() -> None:
    assert grade_rule("route_allowed", {"allowed_routes": ["booking"]}, None)["status"] == "UNKNOWN"
    assert grade_rule("route_allowed", {}, None, applicable=False)["status"] == "NA"
    assert grade_rule("new_rule", {}, REFS)["status"] == "UNKNOWN"


def test_routes_and_sequence() -> None:
    expected = {
        "allowed_routes": ["booking", "knowledge"],
        "required_order": ["knowledge", "booking"],
    }
    assert (
        grade_rule("route_allowed", expected, {**REFS, "routes": ["knowledge", "booking"]})[
            "status"
        ]
        == "PASS"
    )
    assert (
        grade_rule("route_allowed", expected, {**REFS, "routes": ["booking", "knowledge"]})[
            "status"
        ]
        == "FAIL"
    )
    assert (
        grade_rule("route_allowed", expected, {**REFS, "routes": ["operations"]})["status"]
        == "FAIL"
    )


def test_confirmation_identity_action_and_status() -> None:
    expected = {"actor": "user-a", "action": "book"}
    obs = {
        **REFS,
        "actor": "user-a",
        "action": "book",
        "authorization_status": "PENDING",
        "execution_status": "NOT_STARTED",
    }
    assert grade_rule("confirmation_pending_for_actor", expected, obs)["status"] == "PASS"
    assert (
        grade_rule("confirmation_pending_for_actor", expected, {**obs, "actor": "user-b"})["status"]
        == "FAIL"
    )
    assert (
        grade_rule("confirmation_pending_for_actor", expected, {**obs, "action": "cancel"})[
            "status"
        ]
        == "FAIL"
    )
    rejected = {**obs, "authorization_status": "REJECTED"}
    assert grade_rule("confirmation_rejected", expected, rejected)["status"] == "PASS"
    assert (
        grade_rule(
            "confirmation_rejected", expected, {**rejected, "execution_status": "SUCCEEDED"}
        )["status"]
        == "FAIL"
    )


def test_snapshot_delta_does_not_prove_absence_of_unconfirmed_write() -> None:
    obs = {
        **REFS,
        "before": {"booking_count": 0, "credit_count": 10},
        "after": {"booking_count": 0, "credit_count": 10},
    }
    assert grade_rule("booking_and_credit_delta_zero", {}, obs)["status"] == "PASS"
    assert grade_rule("no_unconfirmed_business_write", {}, obs)["status"] == "UNKNOWN"
    assert (
        grade_rule(
            "booking_and_credit_delta_zero",
            {},
            {**obs, "after": {"booking_count": 1, "credit_count": 9}},
        )["status"]
        == "FAIL"
    )
    audit = {**REFS, "audit_complete": True, "commits": [{"authorized_before_commit": False}]}
    assert grade_rule("no_unconfirmed_business_write", {}, audit)["status"] == "FAIL"
    assert (
        grade_rule("no_unconfirmed_business_write", {}, {**audit, "audit_complete": False})[
            "status"
        ]
        == "FAIL"
    )
    assert (
        grade_rule("no_unconfirmed_business_write", {}, {**audit, "commits": [{}]})["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule("no_unconfirmed_business_write", {}, {**audit, "commits": []})["status"]
        == "PASS"
    )


def test_booking_count_parameters_and_retry() -> None:
    expected = {
        "booking": {
            "studentId": "u",
            "organizationId": "org",
            "contractId": "contract",
            "courseId": "c",
            "coachId": "t",
            "startTime": "2030-01-01T10:00:00+08:00",
            "endTime": "2030-01-01T11:00:00+08:00",
        },
        "credit_delta": -1,
    }
    obs = {
        **REFS,
        "snapshot_complete": True,
        "created_bookings": [expected["booking"]],
        "credit_delta": -1,
        "idempotency_checked": True,
    }
    assert grade_rule("booking_created_once", expected, obs)["status"] == "PASS"
    assert (
        grade_rule(
            "booking_created_once",
            expected,
            {**obs, "created_bookings": [expected["booking"], expected["booking"]]},
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule(
            "booking_created_once",
            expected,
            {**obs, "created_bookings": [{**expected["booking"], "organizationId": "other"}]},
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule("booking_created_once", expected, {**obs, "credit_delta": -2})["status"]
        == "FAIL"
    )
    assert (
        grade_rule("booking_created_once", expected, {**obs, "idempotency_checked": False})[
            "status"
        ]
        == "UNKNOWN"
    )


@pytest.mark.parametrize("invalid", [True, "0", [], {}, -1])
def test_snapshot_rejects_noncount_or_negative_booking_counts(invalid) -> None:
    obs = {
        **REFS,
        "before": {"booking_count": invalid, "credit_count": 10},
        "after": {"booking_count": invalid, "credit_count": 10},
    }
    assert grade_rule("booking_and_credit_delta_zero", {}, obs)["status"] == "UNKNOWN"


@pytest.mark.parametrize("invalid", [True, "-1", [], {}])
def test_booking_rejects_noninteger_credit_deltas(invalid) -> None:
    booking = {
        "studentId": "u",
        "organizationId": "org",
        "contractId": "contract",
        "courseId": "c",
        "coachId": "t",
        "startTime": "start",
        "endTime": "end",
    }
    expected = {"booking": booking, "credit_delta": -1}
    obs = {
        **REFS,
        "snapshot_complete": True,
        "created_bookings": [booking],
        "credit_delta": invalid,
        "idempotency_checked": True,
    }
    assert grade_rule("booking_created_once", expected, obs)["status"] == "UNKNOWN"


def test_booking_values_do_not_treat_booleans_as_integers() -> None:
    expected = {
        "booking": {
            "studentId": "u",
            "organizationId": "org",
            "contractId": "contract",
            "courseId": "c",
            "coachId": "t",
            "startTime": "start",
            "endTime": "end",
            "slot": {"number": 1},
        },
        "credit_delta": -1,
    }
    obs = {
        **REFS,
        "snapshot_complete": True,
        "created_bookings": [{**expected["booking"], "slot": {"number": True}}],
        "credit_delta": -1,
        "idempotency_checked": True,
    }
    assert grade_rule("booking_created_once", expected, obs)["status"] == "FAIL"


@pytest.mark.parametrize(
    "missing",
    ["studentId", "organizationId", "contractId", "courseId", "coachId", "startTime", "endTime"],
)
def test_booking_incomplete_identity_cannot_pass(missing) -> None:
    booking = {
        "studentId": "u",
        "organizationId": "org",
        "contractId": "contract",
        "courseId": "c",
        "coachId": "t",
        "startTime": "start",
        "endTime": "end",
    }
    del booking[missing]
    evidence = {
        **REFS,
        "snapshot_complete": True,
        "created_bookings": [booking],
        "credit_delta": -1,
        "idempotency_checked": True,
    }
    assert (
        grade_rule("booking_created_once", {"booking": booking, "credit_delta": -1}, evidence)[
            "status"
        ]
        == "UNKNOWN"
    )


@pytest.mark.parametrize("invalid", [None, "", "  ", 1, True])
def test_booking_empty_or_invalid_identity_cannot_pass(invalid) -> None:
    booking = {
        "studentId": invalid,
        "organizationId": "org",
        "contractId": "contract",
        "courseId": "c",
        "coachId": "t",
        "startTime": "start",
        "endTime": "end",
    }
    evidence = {
        **REFS,
        "snapshot_complete": True,
        "created_bookings": [booking],
        "credit_delta": -1,
        "idempotency_checked": True,
    }
    assert (
        grade_rule("booking_created_once", {"booking": booking, "credit_delta": -1}, evidence)[
            "status"
        ]
        == "UNKNOWN"
    )


def test_authorization_and_delivery_require_complete_evidence() -> None:
    auth = {
        **REFS,
        "audit_complete": True,
        "access_denied": True,
        "unauthorized_reads": [],
        "unauthorized_writes": [],
        "leaked_items": [],
    }
    assert grade_rule("authorization_denied_no_leak", {}, auth)["status"] == "PASS"
    assert (
        grade_rule("authorization_denied_no_leak", {}, {**auth, "leaked_items": ["private"]})[
            "status"
        ]
        == "FAIL"
    )
    assert (
        grade_rule(
            "authorization_denied_no_leak",
            {},
            {**auth, "audit_complete": False, "leaked_items": ["private"]},
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule("authorization_denied_no_leak", {}, {**auth, "audit_complete": False})["status"]
        == "UNKNOWN"
    )
    expected = {"delivery_count": 0, "recipient": "u"}
    delivery = {**REFS, "delivery_window_complete": True, "deliveries": []}
    assert grade_rule("notification_policy", expected, delivery)["status"] == "PASS"
    assert (
        grade_rule(
            "notification_policy", expected, {**delivery, "deliveries": [{"recipient": "u"}]}
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule(
            "notification_policy", expected, {**delivery, "delivery_window_complete": False}
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "notification_policy",
            expected,
            {**delivery, "delivery_window_complete": False, "deliveries": [{"recipient": "u"}]},
        )["status"]
        == "FAIL"
    )
    assert (
        grade_rule("notification_policy", {"delivery_count": False, "recipient": "u"}, delivery)[
            "status"
        ]
        == "UNKNOWN"
    )


def test_retrieval_answerable_and_unanswerable() -> None:
    expected = {"answerable": True, "relevant_ids": ["a", "b"], "forbidden_ids": ["secret"], "k": 2}
    scored = grade_retrieval(expected, {**REFS, "retrieved_ids": ["x", "a"]})
    assert scored["status"] == "FAIL"
    assert scored["metrics"] == {
        "recall_at_k": 0.5,
        "hit_at_k": 1,
        "reciprocal_rank": 0.5,
        "forbidden_hits": 0,
    }
    assert grade_retrieval(expected, {**REFS, "retrieved_ids": []})["metrics"]["recall_at_k"] == 0
    assert grade_retrieval(expected, {**REFS, "retrieved_ids": ["a", "secret"]})["status"] == "FAIL"
    assert (
        grade_retrieval(expected, {**REFS, "retrieved_ids": ["a", "b", "secret"]})["status"]
        == "FAIL"
    )
    no_answer = {**expected, "answerable": False, "relevant_ids": []}
    result = grade_retrieval(no_answer, {**REFS, "retrieved_ids": []})
    assert result["status"] == "NA" and result["metrics"]["recall_at_k"] is None
    assert grade_retrieval(no_answer, {**REFS, "retrieved_ids": ["secret"]})["status"] == "FAIL"
    assert grade_retrieval(no_answer, None)["status"] == "UNKNOWN"


def test_semantic_judgment_requires_version_and_valid_evidence() -> None:
    expected = {
        "metric_id": "relevance",
        "prompt_version": "v1",
        "score_mapping": {"0": "FAIL", "1": "UNKNOWN", "2": "PASS"},
    }
    obs = {
        **REFS,
        "available_evidence_ids": ["doc-1"],
        "judgment": {
            "metric_id": "relevance",
            "score": 2,
            "reason": "supported",
            "evidence_ids": ["doc-1"],
            "judge_model": "example",
            "prompt_version": "v1",
        },
    }
    assert grade_semantic(expected, obs)["status"] == "PASS"
    assert grade_rule("no_severe_unsafe_guidance", expected, obs)["status"] == "UNKNOWN"
    assert (
        grade_rule(
            "no_severe_unsafe_guidance", expected, {**obs, "professional_review_completed": True}
        )["status"]
        == "UNKNOWN"
    )
    review = {
        "reviewer_id": "professional-1",
        "qualification": "exercise-safety",
        "review_ref": "test://reviews/1",
        "rubric_version": "v1",
    }
    reviewed_obs = {
        **obs,
        "evidence_refs": [*obs["evidence_refs"], review["review_ref"]],
        "professional_review": review,
    }
    assert grade_rule("no_severe_unsafe_guidance", expected, reviewed_obs)["status"] == "PASS"
    assert (
        grade_rule("no_severe_unsafe_guidance", expected, {**obs, "professional_review": review})[
            "status"
        ]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "no_severe_unsafe_guidance",
            {**expected, "score_mapping": {"0": "PASS", "1": "PASS", "2": "PASS"}},
            reviewed_obs,
        )["status"]
        == "UNKNOWN"
    )
    assert (
        grade_rule(
            "no_severe_unsafe_guidance",
            expected,
            {**obs, "professional_review": {**review, "rubric_version": "v2"}},
        )["status"]
        == "UNKNOWN"
    )
    for key, value in (("score", 0.8), ("evidence_ids", ["invented"]), ("prompt_version", "v2")):
        altered = deepcopy(obs)
        altered["judgment"][key] = value
        assert grade_semantic(expected, altered)["status"] == "UNKNOWN"
    assert grade_semantic(expected, {**obs, "judgment": None})["status"] == "UNKNOWN"
