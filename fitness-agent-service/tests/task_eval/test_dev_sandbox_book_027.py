from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.evaluation.task_eval import dev_sandbox_book_027 as target
from app.infrastructure.gateway_client import GatewayClient, GatewayExecutionUnknownError
from tests.task_eval.test_dev_sandbox_book_026 import BOOKING, snapshot


def test_original_case_and_development_fixture_are_bound():
    case, fixture = target._load()
    assert case.case_id == "BOOK-027" and case.review.status == "draft"
    assert case.steps[0].message_ref == "planned-book-027.booking_request"
    assert fixture["payload"]["start_time"] == "2030-01-01T10:00:00+08:00"
    assert [step.type for step in case.steps] == [
        "chat",
        "assert",
        "inject_fault",
        "decision",
        "await_terminal",
        "assert",
    ]


@pytest.mark.asyncio
async def test_wrapper_discards_only_a_successful_business_response(monkeypatch):
    parent = AsyncMock(return_value=object())
    monkeypatch.setattr(GatewayClient, "create_booking", parent)
    gateway = target._DropResponseGateway.__new__(target._DropResponseGateway)
    gateway.armed = gateway.dropped = False
    gateway.attempts = 0
    gateway.arm()
    with pytest.raises(GatewayExecutionUnknownError):
        await gateway.create_booking(None, {})
    assert gateway.dropped and parent.await_count == 1
    with pytest.raises(ValueError):
        gateway.arm()


@pytest.mark.asyncio
async def test_business_failure_is_not_claimed_as_response_loss(monkeypatch):
    parent = AsyncMock(side_effect=RuntimeError("business failed"))
    monkeypatch.setattr(GatewayClient, "create_booking", parent)
    gateway = target._DropResponseGateway.__new__(target._DropResponseGateway)
    gateway.armed = gateway.dropped = False
    gateway.attempts = 0
    gateway.arm()
    with pytest.raises(RuntimeError):
        await gateway.create_booking(None, {})
    assert not gateway.dropped and parent.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation", ["none", "before", "audit", "not_dropped", "wrong_booking", "retries"]
)
async def test_response_loss_requires_commit_and_stops_predecision_violations(
    violation, monkeypatch
):
    case, fixture = target._load()
    state = {"count": 0, "decisions": 0, "snapshots": 0}
    record = SimpleNamespace(
        request_id="r",
        subject_user_id="student-1",
        tool_id="fitness.booking.create.v1",
        authorization_status="PENDING",
        execution_status="NOT_STARTED",
        payload_ciphertext=b"x",
        payload_hash="h",
        payload_key_version="v",
    )

    class Repository:
        async def get_for_subject(self, cid, subject, orgs):
            assert (cid, subject, orgs) == ("c", "student-1", ["org-1"])
            if state["decisions"]:
                record.authorization_status, record.execution_status = "APPROVED", "UNKNOWN"
            return record

    class Fault:
        dropped = False
        armed = False
        attempts = 0

        def arm(self):
            self.armed = True

    fault = Fault()

    class Agent:
        async def chat(self, message):
            assert message == fixture["message"]
            return {"status": "CONFIRMATION_REQUIRED", "confirmation_id": "c"}

        async def decide(self, cid, decision):
            assert (cid, decision, fault.armed) == ("c", "APPROVE", True)
            state["decisions"] += 1
            fault.attempts += 2 if violation == "retries" else 1
            state["count"] += 1
            fault.dropped = violation != "not_dropped"
            raise httpx.HTTPStatusError(
                "service unavailable",
                request=httpx.Request("POST", "http://localhost"),
                response=httpx.Response(503),
            )

        async def await_terminal(self, cid, **kwargs):
            assert kwargs["expected_authorization"] == "APPROVED"
            assert kwargs["expected_execution"] == {"UNKNOWN"}

    class Cipher:
        def decrypt(self, *args, **kwargs):
            return json.dumps(fixture["payload"])

    def take_snapshot(_):
        state["snapshots"] += 1
        return snapshot(1 if violation == "before" and state["snapshots"] == 2 else state["count"])

    def details():
        item = deepcopy(BOOKING)
        if violation == "wrong_booking":
            item["courseId"] = "wrong"
        return {"snapshot_complete": True, "created_bookings": [item] * state["count"]}

    monkeypatch.setattr(
        target,
        "committed_before_decision",
        lambda *_: [{"authorized_before_commit": False}] if violation == "audit" else [],
    )
    trial = await target._drive(
        case,
        fixture,
        Agent(),
        Repository(),
        SimpleNamespace(cipher=Cipher()),
        take_snapshot,
        details,
        lambda: {"records": [], "audit_window_complete": False},
        fault,
    )
    assertions = {a["id"]: a for a in trial["assertions"]}
    if violation in {"before", "audit"}:
        assert trial["task_status"] == "FAIL" and state["decisions"] == 0
    else:
        assert trial["task_status"] == "INCONCLUSIVE" and state["decisions"] == 1
        assert assertions["fault_observed"]["status"] == (
            "PASS" if violation == "none" else "UNKNOWN"
        )
        assert trial["response_loss"]["observed"] == (violation == "none")


def test_fixture_rejects_changed_case():
    case, _ = target._load()
    altered = case.model_copy(deep=True)
    altered.steps[2].fault = "business_timeout"
    with pytest.raises(ValueError, match="DEV_CASE_CHANGED"):
        target.load_book_027(altered)
