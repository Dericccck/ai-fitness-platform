from __future__ import annotations

import json
import os
import subprocess
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
from app.evaluation.task_eval import dev_sandbox_book_018 as target
from app.evaluation.task_eval.agent_http import AgentHTTP
from app.evaluation.task_eval.booking_model_boundary import OneBookingModel
from app.evaluation.task_eval.budget_store import DurableBudget
from tests.task_eval.test_booking_model_boundary import response_018


def test_frozen_dataset_and_fixture_are_loaded() -> None:
    case, fixture = target._load()
    assert case.case_id == "BOOK-018"
    assert fixture["payload"]["start_time"] == "2030-01-01T10:00:00+08:00"
    assert [step.type for step in case.steps] == [
        "chat",
        "assert",
        "decision",
        "await_terminal",
        "assert",
    ]


def test_dataset_drift_rejected(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "changed.jsonl"
    path.write_bytes(target._DATASET.read_bytes() + b"\n")
    monkeypatch.setattr(target, "_DATASET", path)
    with pytest.raises(ValueError, match="DEV_DATASET_CHANGED"):
        target._load()


@pytest.mark.asyncio
@pytest.mark.parametrize("observed_violation", [False, True])
async def test_exact_reject_sequence_and_missing_evidence(
    monkeypatch: pytest.MonkeyPatch, observed_violation: bool
) -> None:
    case, fixture = target._load()
    events: list[tuple[str, str]] = []
    state = {"auth": "PENDING"}
    record = SimpleNamespace(
        request_id="r",
        subject_user_id="student-1",
        tool_id="fitness_booking_create_v1",
        authorization_status="PENDING",
        execution_status="NOT_STARTED",
        payload_ciphertext=b"x",
        payload_hash="h",
        payload_key_version="v",
    )

    class Repository:
        async def get_for_subject(self, cid: str, subject: str, orgs: list[str]) -> Any:
            assert (cid, subject, orgs) == ("c", "student-1", ["org-1"])
            record.authorization_status = state["auth"]
            return record

    class Agent:
        async def chat(self, message: str) -> dict[str, str]:
            events.append(("chat", message))
            return {"status": "CONFIRMATION_REQUIRED", "confirmation_id": "c"}

        async def decide(self, cid: str, decision: str) -> None:
            events.append(("decision", decision))
            state["auth"] = "REJECTED"

        async def await_terminal(self, cid: str, **kwargs: Any) -> None:
            events.append(("await", kwargs["expected_authorization"]))

    class Cipher:
        def decrypt(self, *args: Any, **kwargs: Any) -> str:
            return json.dumps(fixture["payload"])

    facts = {
        "appointments": 0,
        "remaining_hours": 2,
        "witness_appointments": 0,
        "witness_remaining_hours": 5,
    }

    def snapshot(request_id: str) -> dict[str, Any]:
        return {"snapshot_complete": True, "witness_observed": True, "facts": deepcopy(facts)}

    def audit() -> dict[str, Any]:
        return {"records": [], "audit_window_complete": False}

    monkeypatch.setattr(target, "_verify_database_deltas", lambda *args: {})
    monkeypatch.setattr(
        target,
        "committed_before_decision",
        lambda *args: [{"authorized_before_commit": False}] if observed_violation else [],
    )
    trial = await target._drive(
        case, fixture, Agent(), Repository(), SimpleNamespace(cipher=Cipher()), snapshot, audit
    )
    if observed_violation:
        assert events == [("chat", fixture["message"])]
        assert [s["index"] for s in trial["executed_steps"]] == [0, 1]
        assert trial["task_status"] == "FAIL"
        assertions = {a["id"]: a["status"] for a in trial["assertions"]}
        assert assertions["no_write_before"] == "FAIL"
        assert assertions["net_zero"] == "UNKNOWN"
        return
    assert events == [("chat", fixture["message"]), ("decision", "REJECT"), ("await", "REJECTED")]
    assert [s["index"] for s in trial["executed_steps"]] == list(range(5))
    assert trial["task_status"] == "INCONCLUSIVE"
    assertions = {a["id"]: a["status"] for a in trial["assertions"]}
    assert assertions["no_write_before"] == "UNKNOWN"
    assert assertions["task_result"] == "UNKNOWN"
    assert assertions["net_zero"] == "PASS"


@pytest.mark.asyncio
async def test_mismatched_payload_prevents_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    case, fixture = target._load()
    calls: list[str] = []

    class Agent:
        async def chat(self, message: str) -> dict[str, str]:
            return {"status": "CONFIRMATION_REQUIRED", "confirmation_id": "c"}

        async def decide(self, *args: Any) -> None:
            calls.append("decision")

    class Repository:
        async def get_for_subject(self, *args: Any) -> Any:
            return SimpleNamespace(
                payload_ciphertext=b"x", payload_hash="h", payload_key_version="v"
            )

    class Cipher:
        def decrypt(self, *args: Any, **kwargs: Any) -> str:
            payload = {**fixture["payload"], "course_id": "other"}
            return json.dumps(payload)

    def snapshot(request_id: str) -> dict[str, Any]:
        return {
            "snapshot_complete": True,
            "witness_observed": True,
            "facts": {
                "appointments": 0,
                "remaining_hours": 2,
                "witness_appointments": 0,
                "witness_remaining_hours": 5,
            },
        }

    trial = await target._drive(
        case, fixture, Agent(), Repository(), SimpleNamespace(cipher=Cipher()), snapshot, dict
    )
    assert trial["reason"] == "STEP_OR_OBSERVER_ERROR"
    assert not trial["executed_steps"]
    assert all(a["status"] == "UNKNOWN" for a in trial["assertions"])
    assert calls == []


def _model_args(tmp_path: Path) -> tuple[Any, DurableBudget]:
    key_file = tmp_path / "test-key"
    key_file.write_text("synthetic-dedicated-credential", encoding="utf-8")
    key_file.chmod(0o600)
    budget_file = tmp_path / "budget.sqlite"
    budget = DurableBudget(budget_file, limit_nano=10_000_000_000, opening_nano=10)
    budget.reserve("prior", 100)
    budget.finish("prior", usage_known=True)
    return (
        SimpleNamespace(
            credential_file=str(key_file),
            budget_file=str(budget_file),
            budget_limit_nano=10_000_000_000,
            budget_opening_nano=10,
            minimum_reserved_nano=110,
            confirm_current_pricing=True,
        ),
        budget,
    )


def test_real_model_setup_opens_existing_private_budget_without_calling_provider(
    tmp_path: Path,
) -> None:
    args, budget = _model_args(tmp_path)
    model = target._real_model(args)
    assert model.profile == "BOOK-018" and model._transport is None
    assert model.ledger.attempts == 0
    assert budget.snapshot()["reserved_nano"] == 110
    assert "synthetic-dedicated-credential" not in json.dumps(model.diagnostics())


@pytest.mark.asyncio
async def test_real_supervisor_driver_rejects_booking_with_one_mock_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, fixture = target._load()
    repository = local.Repository()
    gateway = local.Gateway(repository, "approve")
    registry = build_fitness_tool_registry(cast(Any, gateway))
    confirmation = ConfirmationService(
        cast(Any, repository),
        registry,
        cast(Any, gateway),
        AesGcmPayloadCipher(key=b"x" * 32, key_version="local"),
        cast(Any, local.TokenIssuer()),
    )
    budget = DurableBudget(tmp_path / "integration-budget.sqlite", limit_nano=10_000_000_000)
    calls = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert budget.snapshot()["pending"] == 1
        return httpx.Response(200, json=response_018())

    model = OneBookingModel(
        "mock-book-018-credential",
        budget,
        transport=httpx.MockTransport(provider),
        execute=True,
        confirmed_current_pricing=True,
        profile="BOOK-018",
    )
    supervisor = Supervisor(
        cast(Any, model),
        registry,
        checkpointer=InMemorySaver(),
        confirmation_service=confirmation,
        intent_router=cast(Any, local.Router()),
    )
    app = FastAPI()
    app.state.context_verifier = local.Verifier()
    app.state.confirmation_service = confirmation
    app.state.supervisor = supervisor
    app.include_router(agent_router)
    app.include_router(confirmation_router)
    facts = {
        "appointments": 0,
        "remaining_hours": 2,
        "witness_appointments": 0,
        "witness_remaining_hours": 5,
    }

    def snapshot(_: str) -> dict[str, Any]:
        assert gateway.attempts == 0
        return {"snapshot_complete": True, "witness_observed": True, "facts": facts}

    def audit() -> dict[str, Any]:
        return {"records": [], "audit_window_complete": False}

    monkeypatch.setattr(target, "_verify_database_deltas", lambda *args: {})
    monkeypatch.setattr(target, "committed_before_decision", lambda *args: [])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local") as client:
            trial = await target._drive(
                case,
                fixture,
                AgentHTTP(client, "signed-context"),
                repository,
                confirmation,
                snapshot,
                audit,
            )
    finally:
        gateway.db.close()
    assert len(calls) == model.ledger.attempts == 1
    assert trial["reason"] is None and trial["task_status"] == "INCONCLUSIVE"
    assert [step["index"] for step in trial["executed_steps"]] == list(range(5))
    assert budget.snapshot()["reserved_nano"] == 204_800_000
    assert not budget.snapshot()["frozen"] and gateway.attempts == 0
    assert "mock-book-018-credential" not in json.dumps(trial)


@pytest.mark.parametrize(
    "issue", ["permissions", "symlink", "missing_budget", "history", "pricing", "frozen"]
)
def test_real_model_setup_refuses_insecure_or_unverified_state(tmp_path: Path, issue: str) -> None:
    args, budget = _model_args(tmp_path)
    if issue == "permissions":
        Path(args.credential_file).chmod(0o644)
    elif issue == "symlink":
        link = tmp_path / "key-link"
        link.symlink_to(args.credential_file)
        args.credential_file = str(link)
    elif issue == "missing_budget":
        args.budget_file = str(tmp_path / "missing.sqlite")
    elif issue == "history":
        args.minimum_reserved_nano = 111
    elif issue == "frozen":
        budget.reserve("uncertain", 50)
        budget.finish("uncertain", usage_known=False)
    else:
        args.confirm_current_pricing = False
    with pytest.raises(ValueError):
        target._real_model(args)
    assert budget.snapshot()["reserved_nano"] == (160 if issue == "frozen" else 110)


@pytest.mark.parametrize(
    "options",
    [
        ["--real-model"],
        ["--credential-file", "/fixture"],
        ["--confirm-current-pricing"],
    ],
)
def test_cli_rejects_partial_model_options_before_creating_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, options: list[str]
) -> None:
    report = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["book-018", "--report-file", str(report), *options])
    with pytest.raises(SystemExit) as exc:
        target.main()
    assert exc.value.code == 2 and not report.exists()


def test_cli_does_not_leak_model_setup_exceptions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    report = tmp_path / "report.json"
    marker = "private-exception-and-provider-output"

    def failure(_: Any) -> Any:
        raise ValueError(marker)

    monkeypatch.setattr(target, "_real_model", failure)
    monkeypatch.setattr(
        "sys.argv",
        [
            "book-018",
            "--report-file",
            str(report),
            "--real-model",
            "--confirm-current-pricing",
            "--credential-file",
            "/a",
            "--budget-file",
            "/b",
            "--budget-limit-nano",
            "100",
            "--budget-opening-nano",
            "0",
            "--minimum-reserved-nano",
            "0",
        ],
    )
    assert target.main() == 1
    assert marker not in report.read_text() + capsys.readouterr().out
    assert json.loads(report.read_text())["reason"] == "PREFLIGHT_OR_TRIAL_SETUP_FAILED"


@pytest.mark.parametrize("options", [[], ["--credential-file", "/missing"], ["--unknown"]])
def test_shell_validates_model_options_before_starting_docker(
    tmp_path: Path, options: list[str]
) -> None:
    marker = tmp_path / "docker_called"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    fake_docker.chmod(0o700)
    script = Path(__file__).parents[2] / "scripts/task_eval_cross_service_booking.sh"
    proc = subprocess.run(
        [
            "bash",
            str(script),
            "--book-018-real-model-trial",
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
