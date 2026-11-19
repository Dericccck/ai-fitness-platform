from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
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
from app.evaluation.task_eval import dev_sandbox_book_026 as target
from app.evaluation.task_eval.agent_http import AgentHTTP
from app.evaluation.task_eval.booking_model_boundary import OneBookingModel
from app.evaluation.task_eval.budget_store import DurableBudget
from tests.task_eval.test_booking_model_boundary import response_018


def snapshot(count: int) -> dict[str, Any]:
    return {
        "snapshot_complete": True,
        "witness_observed": True,
        "facts": {
            "appointments": count,
            "remaining_hours": 2 - count,
            "witness_appointments": 0,
            "witness_remaining_hours": 5,
            "witness_contract_count": 1,
            "global_contracts": 2,
            "global_remaining_hours": 7 - count,
            "global_appointments": count,
            "global_create_operations": count,
            "global_reschedule_operations": 0,
            "global_cancel_operations": 0,
            "global_audits": count,
            "global_consumptions": count,
            "global_outbox": count,
        },
    }


BOOKING = {
    "organizationId": "org-1",
    "studentId": "student-1",
    "contractId": "contract-1",
    "coachId": "coach-1",
    "courseId": "course-1",
    "startTime": "2030-01-01T02:00:00Z",
    "endTime": "2030-01-01T03:00:00Z",
}


def test_case_and_fixture_bound():
    case, fixture = target._load()
    assert case.case_id == "BOOK-026" and case.review.status == "draft"
    assert fixture["payload"]["start_time"] == "2030-01-01T10:00:00+08:00"
    assert [step.type for step in case.steps] == [
        "chat",
        "assert",
        "inject_fault",
        "decision",
        "decision",
        "await_terminal",
        "assert",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation", ["none", "audit", "before", "partial", "duplicate", "payload"]
)
async def test_exact_duplicate_sequence_and_safe_failures(violation, monkeypatch):
    case, fixture = target._load()
    calls = []
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
                record.authorization_status, record.execution_status = "APPROVED", "SUCCEEDED"
            return record

    class Agent:
        async def chat(self, message):
            calls.append("chat")
            return {"status": "CONFIRMATION_REQUIRED", "confirmation_id": "c"}

        async def decide(self, cid, decision):
            assert (cid, decision) == ("c", "APPROVE")
            calls.append("approve")
            state["decisions"] += 1
            if state["decisions"] == 1 or violation == "duplicate":
                state["count"] += 1
            return {"authorization_status": "APPROVED", "execution_status": "SUCCEEDED"}

        async def await_terminal(self, cid, **kwargs):
            assert kwargs["expected_authorization"] == "APPROVED"
            assert kwargs["expected_execution"] == {"SUCCEEDED"}
            calls.append("terminal")

    class Cipher:
        def decrypt(self, *args, **kwargs):
            return json.dumps(
                {**fixture["payload"], **({"course_id": "other"} if violation == "payload" else {})}
            )

    def take_snapshot(_):
        state["snapshots"] += 1
        if violation == "before" and state["snapshots"] == 2:
            return snapshot(1)
        current = snapshot(state["count"])
        if violation == "partial" and state["snapshots"] == 2:
            del current["facts"]["global_contracts"]
        return current

    def details():
        return {
            "snapshot_complete": True,
            "created_bookings": [deepcopy(BOOKING) for _ in range(state["count"])],
        }

    def audit():
        return {"records": [], "audit_window_complete": False}

    monkeypatch.setattr(
        target,
        "committed_before_decision",
        lambda *args: [{"authorized_before_commit": False}] if violation == "audit" else [],
    )
    trial = await target._drive(
        case,
        fixture,
        Agent(),
        Repository(),
        SimpleNamespace(cipher=Cipher()),
        take_snapshot,
        details,
        audit,
    )
    assertions = {a["id"]: a["status"] for a in trial["assertions"]}
    if violation == "payload":
        assert calls == ["chat"] and trial["reason"] == "STEP_OR_OBSERVER_ERROR"
    elif violation == "partial":
        assert calls == ["chat"] and trial["reason"] == "PRE_DECISION_SNAPSHOT_UNVERIFIED"
        assert trial["task_status"] == "INCONCLUSIVE"
    elif violation in {"audit", "before"}:
        assert calls == ["chat"] and trial["task_status"] == "FAIL"
        assert (
            assertions[
                "no_write_before" if violation == "audit" else "supplemental_pre_decision_snapshot"
            ]
            == "FAIL"
        )
    else:
        assert calls == ["chat", "approve", "approve", "terminal"]
        assert [step["index"] for step in trial["executed_steps"]] == list(range(7))
        assert assertions["fault_observed"] == "PASS" and assertions["task_result"] == "UNKNOWN"
        assert assertions["no_write_before"] == "UNKNOWN"
        assert assertions["supplemental_single_effect"] == (
            "FAIL" if violation == "duplicate" else "PASS"
        )
        assert trial["task_status"] == ("FAIL" if violation == "duplicate" else "INCONCLUSIVE")


@pytest.mark.asyncio
async def test_real_supervisor_with_mock_provider_performs_one_paid_boundary_call(tmp_path):
    case, fixture = target._load()
    repo = local.Repository()
    gateway = local.Gateway(repo, "approve")
    gateway.db.close()
    gateway.db = sqlite3.connect(":memory:", check_same_thread=False)
    gateway.db.executescript(
        "CREATE TABLE credits (remaining INTEGER NOT NULL); INSERT INTO credits VALUES (10); CREATE TABLE bookings (payload TEXT NOT NULL); CREATE TABLE audit (authorized_before_commit INTEGER NOT NULL);"
    )
    registry = build_fitness_tool_registry(cast(Any, gateway))
    confirmation = ConfirmationService(
        cast(Any, repo),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, local.TokenIssuer()),
    )
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    calls = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=response_018())

    model = OneBookingModel(
        "mock-book-026-credential",
        budget,
        transport=httpx.MockTransport(provider),
        execute=True,
        confirmed_current_pricing=True,
        profile="BOOK-018",
    )
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = confirmation
    app.state.supervisor = Supervisor(
        cast(Any, model),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=confirmation,
        intent_router=cast(Any, local.Router()),
    )
    app.include_router(agent_router)
    app.include_router(confirmation_router)

    def take_snapshot(_):
        return snapshot(len(gateway.observe()["created_bookings"]))

    def details():
        return {
            "snapshot_complete": True,
            "created_bookings": gateway.observe()["created_bookings"],
        }

    def audit():
        return {"records": [], "audit_window_complete": False}

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            trial = await target._drive(
                case,
                fixture,
                AgentHTTP(client, "signed-context"),
                repo,
                confirmation,
                take_snapshot,
                details,
                audit,
            )
    finally:
        gateway.db.close()
    assert len(calls) == model.ledger.attempts == 1
    assert trial["reason"] is None and trial["task_status"] == "INCONCLUSIVE"
    assert trial["duplicate_decision"] == {
        "armed": True,
        "observed": True,
        "decision_count": 2,
        "snapshot_match": True,
    }
    assert gateway.attempts == 1
    assert "mock-book-026-credential" not in json.dumps(trial)


def test_dataset_drift_rejected(tmp_path, monkeypatch):
    path = tmp_path / "changed.jsonl"
    path.write_bytes(target._DATASET.read_bytes() + b"\n")
    monkeypatch.setattr(target, "_DATASET", path)
    with pytest.raises(ValueError, match="DEV_DATASET_CHANGED"):
        target._load()


@pytest.mark.asyncio
async def test_actual_runtime_refuses_mock_transport_before_starting_services(tmp_path):
    budget = DurableBudget(tmp_path / "budget.sqlite", limit_nano=10_000_000_000)
    model = OneBookingModel(
        "synthetic-test-credential",
        budget,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        execute=True,
        confirmed_current_pricing=True,
        profile="BOOK-018",
    )
    with pytest.raises(ValueError, match="unsupported model boundary"):
        await target.run(model)
    assert model.ledger.attempts == 0


@pytest.mark.parametrize("options", [[], ["--credential-file", "/missing"], ["--unknown"]])
def test_shell_refuses_incomplete_real_model_options_before_docker(tmp_path, options):
    import os
    import subprocess

    marker = tmp_path / "docker_called"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    fake_docker.chmod(0o700)
    script = Path(__file__).parents[2] / "scripts/task_eval_cross_service_booking.sh"
    proc = subprocess.run(
        [
            "bash",
            str(script),
            "--book-026-real-model-trial",
            str(tmp_path / "report.json"),
            *options,
        ],
        env=dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}"),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode != 0 and not marker.exists()
