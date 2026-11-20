"""BOOK-004 fixture and model-boundary tests; no real business or model evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.service import ConfirmationService
from app.evaluation.task_eval import booking_local as local
from app.evaluation.task_eval.agent_http import AgentHTTP
from app.evaluation.task_eval.booking_model_boundary import OneBookingModel
from app.evaluation.task_eval.budget_store import DurableBudget
from app.evaluation.task_eval.dev_sandbox_book_004 import (
    SetupThenModel,
    _fixture_bound_grading_case,
    _load,
    _observe_setup,
    _retain_store_failure,
    _slot_pilot,
    _slot_ready_for_diagnostic,
    _store_pilot,
    run,
)
from app.evaluation.task_eval.graders.rules import grade_rule
from tests.task_eval.test_booking_model_boundary import response_018


def test_positive_fixture_bundle_tampering_is_rejected(tmp_path, monkeypatch):
    from app.evaluation.task_eval import dev_sandbox_book_004

    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(dev_sandbox_book_004._POSITIVE_FIXTURE.read_bytes() + b" ")
    monkeypatch.setattr(dev_sandbox_book_004, "_POSITIVE_FIXTURE", fixture)
    with pytest.raises(ValueError, match="DEV_FIXTURE_CHANGED"):
        _load("BOOK-001")


def test_store_pilot_requires_user_selected_organization_and_observed_booking():
    _, fixture = _load("BOOK-006")
    selected = {"id": "org-1", "name": "test organization"}
    organization = {"snapshot_complete": True, "organization": selected}
    booking = {
        "organizationId": "org-1",
        "studentId": "student-1",
        "contractId": "contract-1",
        "coachId": "coach-1",
        "courseId": "course-1",
        "startTime": "2030-01-01T02:00:00Z",
        "endTime": "2030-01-01T03:00:00Z",
    }
    details = {"snapshot_complete": True, "created_bookings": [booking]}
    result = _store_pilot(organization, organization, details, fixture)
    assert result["status"] == "PASS" and result["mapping_independently_reviewed"] is False
    changed = {"snapshot_complete": True, "organization": {"id": "org-2", "name": "other"}}
    assert _store_pilot(organization, changed, details, fixture)["status"] == "UNKNOWN"
    wrong = {**booking, "organizationId": "org-2"}
    assert (
        _store_pilot(organization, organization, {**details, "created_bookings": [wrong]}, fixture)[
            "status"
        ]
        == "FAIL"
    )


def test_store_mismatch_remains_failure_after_audit_is_unavailable():
    from app.evaluation.task_eval.audit_observation import invalidate_failed_audit

    trial = {"task_status": "FAIL", "assertions": []}
    invalidate_failed_audit(trial)
    assert trial["task_status"] == "INCONCLUSIVE"
    _retain_store_failure(trial, {"status": "FAIL"})
    assert trial["task_status"] == "FAIL" and trial["reason"] == "STORE_OBSERVATION_MISMATCH"


def test_private_slot_pilot_reports_observation_without_claiming_settlement():
    _, fixture = _load()
    scope = {
        "organizationId": "org-1",
        "coachId": "coach-1",
        "startTime": "2030-01-01T02:00:00Z",
        "endTime": "2030-01-01T03:00:00Z",
    }
    row = {"id": "appt-1", **scope, "studentId": "student-1", "courseId": "course-1"}
    before = {"scope": scope, "snapshot_complete": True, "rows": []}
    after = {"scope": scope, "snapshot_complete": True, "rows": [row]}
    result = _slot_pilot(before, after, fixture["payload"])
    assert result["before_count"] == 0 and result["after_count"] == 1
    assert result["observed_exact_booking"] is True
    assert result["settlement_proven"] is False and result["status"] == "UNKNOWN"
    assert _slot_pilot(before, {**after, "scope": {}}, fixture["payload"])["status"] == "UNKNOWN"


def test_exposed_slot_diagnostic_requires_a_scoped_empty_snapshot():
    _, fixture = _load("BOOK-009")
    before = {
        "origin": "disposable_mysql_consistent_read",
        "snapshot_complete": True,
        "scope": {
            "organizationId": "org-1",
            "coachId": "coach-1",
            "startTime": "2030-01-01T02:00:00Z",
            "endTime": "2030-01-01T03:00:00Z",
        },
        "rows": [],
    }
    assert _slot_ready_for_diagnostic("BOOK-009", before, fixture["payload"])
    assert not _slot_ready_for_diagnostic(
        "BOOK-009", {**before, "rows": [{"id": "occupied"}]}, fixture["payload"]
    )
    assert not _slot_ready_for_diagnostic("BOOK-009", {**before, "scope": {}}, fixture["payload"])
    assert not _slot_ready_for_diagnostic("BOOK-009", None, fixture["payload"])


def test_fixture_grading_is_distinct_draft_and_preserves_source() -> None:
    original, fixture = _load()
    proposal, digest = _fixture_bound_grading_case(original, fixture)
    assert proposal.dataset_version == "book-004-fixture-grading-draft-v1"
    assert len(digest) == 64
    assert [step.type for step in original.steps] == [
        "chat",
        "assert",
        "decision",
        "await_terminal",
        "assert",
    ]
    assert [step.type for step in proposal.steps] == [
        "chat",
        "assert",
        "decision",
        "await_terminal",
        "decision",
        "assert",
    ]
    assert proposal.steps[4] == original.steps[2]
    source = {item.id: item for item in original.expected.assertions}
    draft = {item.id: item for item in proposal.expected.assertions}
    assert source["confirmation_pending"].parameters == {}
    assert "booking" not in source["task_result"].parameters
    pending = draft["confirmation_pending"]
    observation = {
        "evidence_refs": ["local://confirmation"],
        "actor": fixture["identity"]["student_id"],
        "action": "fitness.booking.create.v1",
        "authorization_status": "PENDING",
        "execution_status": "NOT_STARTED",
    }
    assert grade_rule(pending.rule, pending.parameters, observation)["status"] == "PASS"
    assert (
        grade_rule(pending.rule, pending.parameters, {**observation, "actor": "other"})["status"]
        == "FAIL"
    )
    booking = draft["task_result"]
    evidence = {
        "evidence_refs": ["local://booking"],
        "snapshot_complete": True,
        "created_bookings": [booking.parameters["booking"]],
        "credit_delta": -1,
        "idempotency_checked": False,
    }
    assert grade_rule(booking.rule, booking.parameters, evidence)["status"] == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        "BOOK-001",
        "BOOK-002",
        "BOOK-003",
        "BOOK-004",
        "BOOK-005",
        "BOOK-006",
        "BOOK-007",
        "BOOK-009",
    ],
)
async def test_frozen_case_and_explicit_context_reach_one_bounded_model_call(
    tmp_path: Path,
    profile: str,
) -> None:
    case, fixture = _load(profile)
    assert case.case_id == profile and case.steps[0].message == fixture["case_message"]
    ledger = DurableBudget(tmp_path / "budget.sqlite", limit_nano=1_000_000_000)
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"][-1]["content"] == case.steps[0].message
        assert any(
            item.get("role") == "assistant" and item.get("content") == fixture["setup_response"]
            for item in body["messages"]
        )
        assert ledger.snapshot()["pending"] == 1
        requests.append(request)
        return httpx.Response(200, json=response_018())

    model = OneBookingModel(
        "synthetic-credential",
        ledger,
        profile=profile,
        transport=httpx.MockTransport(respond),
        execute=True,
        confirmed_current_pricing=True,
    )
    repository = local.Repository()
    gateway = local.Gateway(repository, "approve")
    registry = build_fitness_tool_registry(cast(Any, gateway))
    service = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, local.TokenIssuer()),
    )
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = service
    app.state.supervisor = Supervisor(
        cast(Any, SetupThenModel(model, fixture)),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=service,
        intent_router=cast(Any, local.Router()),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            agent = AgentHTTP(client, "signed-context")
            setup = await agent.chat(fixture["setup_message"])
            assert setup["answer"] == fixture["setup_response"] and not requests
            confirmation = await agent.chat(case.steps[0].message)
            assert confirmation["status"] == "CONFIRMATION_REQUIRED"
            assert gateway.attempts == 0
            decision = await agent.decide(confirmation["confirmation_id"], "APPROVE")
            assert decision["authorization_status"] == "APPROVED"
            assert gateway.attempts == 1
            assert gateway.observe()["credit_delta"] == -1
    finally:
        gateway.db.close()
    assert len(requests) == model.ledger.attempts == 1
    assert ledger.snapshot()["pending"] == 0


@pytest.mark.asyncio
async def test_exposed_test_case_requires_explicit_diagnostic_opt_in(tmp_path: Path) -> None:
    ledger = DurableBudget(tmp_path / "budget.sqlite", limit_nano=1_000_000_000)
    model = OneBookingModel(
        "synthetic-credential",
        ledger,
        profile="BOOK-009",
        execute=True,
        confirmed_current_pricing=True,
    )
    with pytest.raises(ValueError, match="exposed test diagnostic"):
        await run(model)
    assert ledger.snapshot()["reserved_nano"] == 0


@pytest.mark.asyncio
async def test_changed_context_is_rejected_before_model_and_budget(tmp_path: Path) -> None:
    case, fixture = _load()
    ledger = DurableBudget(tmp_path / "budget.sqlite", limit_nano=1_000_000_000)
    model = OneBookingModel(
        "synthetic-credential",
        ledger,
        profile="BOOK-004",
        transport=httpx.MockTransport(lambda _: pytest.fail("no model call allowed")),
        execute=True,
        confirmed_current_pricing=True,
    )
    wrapper = SetupThenModel(model, fixture)
    await wrapper.chat_with_tools([{"role": "user", "content": fixture["setup_message"]}], tools=[])
    with pytest.raises(ValueError, match="DEV_CONTEXT_MISMATCH"):
        await wrapper.chat_with_tools(
            [{"role": "user", "content": case.steps[0].message}], tools=[]
        )
    assert ledger.snapshot()["reserved_nano"] == 0


def _setup_observations():
    source = {
        "run_id": "a" * 16,
        "mysql_container_id": "b" * 64,
        "observer_instance": "c" * 32,
        "observer_pid": 17,
        "audit_table": "fitness_eval_audit.events",
    }
    snapshot = {
        "origin": "disposable_mysql_consistent_read",
        "snapshot_complete": True,
        "witness_observed": True,
        "facts": {
            "appointments": 0,
            "remaining_hours": 2,
            "witness_appointments": 0,
            "witness_remaining_hours": 5,
        },
    }
    first = {
        "source": source,
        "records": [],
        "capture_start_monotonic_ns": 1,
        "capture_end_monotonic_ns": 2,
    }
    second = {
        "source": source.copy(),
        "records": [],
        "capture_start_monotonic_ns": 3,
        "capture_end_monotonic_ns": 4,
    }
    return snapshot, first, second


@pytest.mark.asyncio
async def test_setup_is_observed_before_case_and_never_proves_complete_window():
    _, fixture = _load()
    snapshot, first, second = _setup_observations()
    calls = []
    snapshots = iter([snapshot, snapshot])
    audits = iter([first, second])

    def read_snapshot(request_id):
        calls.append("snapshot")
        return next(snapshots)

    def observe():
        calls.append("audit")
        return next(audits)

    class Agent:
        async def chat(self, message):
            calls.append("chat")
            assert message == fixture["setup_message"]
            return {"answer": fixture["setup_response"], "tool_steps": 0}

    result = await _observe_setup(Agent(), fixture, read_snapshot, observe)
    assert calls == ["snapshot", "audit", "chat", "audit", "snapshot"]
    assert result["status"] == "OBSERVED_NO_CHANGE"
    assert result["audit_window_complete"] is False
    assert result["snapshot_facts_equal"] and result["observed_audit_counts_equal"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    ["snapshot", "audit", "rollback", "source", "window", "response", "tool_steps", "exception"],
)
async def test_setup_refuses_changed_or_incomplete_observations(fault):
    _, fixture = _load()
    snapshot, first, second = _setup_observations()
    after = {**snapshot, "facts": snapshot["facts"].copy()}
    if fault == "snapshot":
        after["facts"]["appointments"] = 1
    if fault == "audit":
        second["records"] = [{"table": "contract", "operation": "UPDATE", "count": 1}]
    if fault == "rollback":
        first["records"] = [{"table": "contract", "operation": "UPDATE", "count": 1}]
    if fault == "source":
        second["source"]["mysql_container_id"] = "d" * 64
    if fault == "window":
        second["capture_start_monotonic_ns"] = 1
    snapshots = iter([snapshot, after])
    audits = iter([first, second])

    class Agent:
        async def chat(self, message):
            if fault == "exception":
                raise RuntimeError("private content must not be reported")
            return {
                "answer": "wrong" if fault == "response" else fixture["setup_response"],
                "tool_steps": 1 if fault == "tool_steps" else 0,
            }

    result = await _observe_setup(Agent(), fixture, lambda _: next(snapshots), lambda: next(audits))
    assert result["status"] == "INCOMPLETE"
    assert result["audit_window_complete"] is False
    assert result["reason"] == "SETUP_OBSERVATION_OR_CONTEXT_INVALID"
