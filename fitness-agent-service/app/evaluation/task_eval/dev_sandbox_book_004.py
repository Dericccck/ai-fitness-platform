"""BOOK-004 original utterance with an explicit synthetic setup turn.

The setup is scripted fixture context, not model-selected course availability.
The real model sees the original case utterance and the earlier synthetic turn.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import BookingCreateToolInput, build_fitness_tool_registry
from app.agent.supervisor import Supervisor
from app.api.routes.agent import router as agent_router
from app.api.routes.confirmations import router as confirmation_router
from app.confirmation.cipher import AesGcmPayloadCipher
from app.confirmation.repository import ConfirmationRepository
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.infrastructure.agent_context import AgentContextVerifier
from app.infrastructure.database import Database
from app.infrastructure.gateway_client import GatewayClient
from app.infrastructure.model_gateway import ModelTurn

from .agent_http import AgentHTTP
from .audit_observation import (
    bind_verified_observer,
    invalidate_failed_audit,
    matching_final_integrity,
)
from .booking_contracts import matches_frozen_case
from .booking_local import Router
from .booking_model_boundary import OneBookingModel, expected_payload
from .booking_signed_identity import _identity
from .cross_service_booking import _environment
from .dataset import load_dataset
from .dev_booking_observer import read_booking_details
from .dev_sandbox_book_018 import _real_model
from .dev_sandbox_booking import _drive, _source_identity
from .draft_parameters import _booking
from .mysql_audit_lab import _EVENTS, _TABLES
from .mysql_booking_observer import snapshot_booking
from .mysql_restricted_audit import observe as observe_restricted_audit
from .mysql_restricted_audit import verify as verify_restricted_audit

_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = _ROOT / "evals/task_eval/dev_fixtures/book-004-context-v1.json"
_FIXTURE_SHA256 = "5f2eb910e5c548b6271cdebdca423db8547697539484af74416a7979168e6375"
_DATASET_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"
_GRADING_VERSION = "book-004-fixture-grading-draft-v1"


def _load() -> tuple[Any, dict[str, Any]]:
    dataset_path = _ROOT / "evals/task_eval/datasets/v1.jsonl"
    if hashlib.sha256(dataset_path.read_bytes()).hexdigest() != _DATASET_SHA256:
        raise ValueError("DEV_DATASET_CHANGED")
    dataset = load_dataset(dataset_path)
    cases = [case for case in dataset.cases if case.case_id == "BOOK-004"]
    if len(cases) != 1 or not matches_frozen_case(cases[0]):
        raise ValueError("DEV_CASE_MISMATCH")
    case = cases[0]
    raw = _FIXTURE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _FIXTURE_SHA256:
        raise ValueError("DEV_FIXTURE_CHANGED")
    fixture = json.loads(raw)
    if (
        case.split != "dev"
        or case.review.status != "draft"
        or case.capability_status != "planned"
        or [step.type for step in case.steps]
        != ["chat", "assert", "decision", "await_terminal", "assert"]
        or case.steps[0].message != fixture["case_message"]
        or fixture["case_id"] != "BOOK-004"
        or fixture["version"] != "1.0"
        or fixture["status"] != "synthetic_dev_draft"
        or fixture["identity"] != {"organization_id": "org-1", "student_id": "student-1"}
        or not expected_payload(fixture["payload"], profile="BOOK-004")
        or datetime.fromisoformat(fixture["payload"]["start_time"]) <= datetime.now(UTC)
    ):
        raise ValueError("DEV_FIXTURE_INCONSISTENT")
    BookingCreateToolInput.model_validate(fixture["payload"])
    return case, {**fixture, "booking": fixture["payload"], "sha256": _FIXTURE_SHA256}


def _fixture_bound_grading_case(case: Any, fixture: dict[str, Any]) -> tuple[Any, str]:
    """Bind provisional grader parameters without mutating the frozen source case."""
    if case.case_id != "BOOK-004" or fixture.get("sha256") != _FIXTURE_SHA256:
        raise ValueError("DEV_GRADING_CASE_MISMATCH")
    result = case.model_copy(deep=True)
    by_id = {assertion.id: assertion for assertion in result.expected.assertions}
    if (
        set(by_id) != {"task_result", "confirmation_pending", "no_write_before", "net_zero_before"}
        or by_id["task_result"].rule != "booking_created_once"
        or by_id["confirmation_pending"].rule != "confirmation_pending_for_actor"
        or by_id["confirmation_pending"].parameters
        or set(by_id["task_result"].parameters) != {"reference_points"}
    ):
        raise ValueError("DEV_GRADING_CONTRACT_CHANGED")
    result.dataset_version = _GRADING_VERSION
    # Add an explicit retry to this proposed trial so the shared booking grader
    # can verify idempotency; it is not a step in the frozen source case.
    result.steps.insert(4, result.steps[2].model_copy(deep=True))
    by_id["task_result"].parameters.update(booking=_booking(fixture["payload"]), credit_delta=-1)
    by_id["confirmation_pending"].parameters.update(
        actor=fixture["identity"]["student_id"], action="fitness.booking.create.v1"
    )
    raw = json.dumps(
        result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return result, hashlib.sha256(raw).hexdigest()


class SetupThenModel:
    """First answer is a fixture; the second turn uses the fixed real-model boundary."""

    def __init__(self, model: Any, fixture: dict[str, Any]) -> None:
        self.model = model
        self.fixture = fixture
        self.calls = 0

    async def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: Any) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            if messages[-1].get("content") != self.fixture["setup_message"]:
                raise ValueError("DEV_SETUP_MISMATCH")
            return ModelTurn(content=self.fixture["setup_response"], tool_calls=())
        if self.calls != 2:
            raise ValueError("DEV_MODEL_CALL_LIMIT")
        if messages[-1].get("content") != self.fixture["case_message"] or not any(
            message.get("role") == "assistant"
            and message.get("content") == self.fixture["setup_response"]
            for message in messages
        ):
            raise ValueError("DEV_CONTEXT_MISMATCH")
        return cast(ModelTurn, await self.model.chat_with_tools(messages, **kwargs))


def _audit_counts(observation: Any) -> dict[tuple[str, str], int]:
    if not isinstance(observation, dict) or not isinstance(observation.get("records"), list):
        raise TypeError("SETUP_AUDIT_INVALID")
    counts: dict[tuple[str, str], int] = {}
    for row in observation["records"]:
        if not isinstance(row, dict) or set(row) != {"table", "operation", "count"}:
            raise ValueError("SETUP_AUDIT_INVALID")
        table, operation, count = row["table"], row["operation"], row["count"]
        if (
            not isinstance(table, str)
            or table not in _TABLES
            or not isinstance(operation, str)
            or operation not in _EVENTS
            or type(count) is not int
            or count <= 0
            or (table, operation) in counts
        ):
            raise ValueError("SETUP_AUDIT_INVALID")
        counts[(table, operation)] = count
    return counts


def _snapshot_hash(snapshot: Any) -> str:
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("origin") != "disposable_mysql_consistent_read"
        or snapshot.get("snapshot_complete") is not True
        or snapshot.get("witness_observed") is not True
        or not isinstance(snapshot.get("facts"), dict)
        or not snapshot["facts"]
        or any(
            type(snapshot["facts"].get(key)) is not int
            for key in (
                "appointments",
                "remaining_hours",
                "witness_appointments",
                "witness_remaining_hours",
            )
        )
    ):
        raise ValueError("SETUP_SNAPSHOT_INVALID")
    raw = json.dumps(snapshot["facts"], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


async def _observe_setup(
    agent: Any, fixture: dict[str, Any], snapshot: Any, observer: Any
) -> dict[str, Any]:
    """Abort before the model if setup observations differ or are untrustworthy.

    Equal snapshots/counts only show no observed change. They do not close the
    audit window or prove that no transaction can later commit.
    """
    result: dict[str, Any] = {"status": "INCOMPLETE", "audit_window_complete": False}
    try:
        before = await asyncio.to_thread(snapshot, "dev-setup-preflight")
        before_audit = await asyncio.to_thread(observer)
        result["before_snapshot_sha256"] = _snapshot_hash(before)
        before_counts = _audit_counts(before_audit)
        response = await agent.chat(fixture["setup_message"])
        after_audit = await asyncio.to_thread(observer)
        after = await asyncio.to_thread(snapshot, "dev-setup-preflight")
        result["after_snapshot_sha256"] = _snapshot_hash(after)
        after_counts = _audit_counts(after_audit)
        times = (
            before_audit.get("capture_start_monotonic_ns"),
            before_audit.get("capture_end_monotonic_ns"),
            after_audit.get("capture_start_monotonic_ns"),
            after_audit.get("capture_end_monotonic_ns"),
        )
        if (
            not isinstance(before_audit.get("source"), dict)
            or before_audit["source"] != after_audit.get("source")
            or set(before_audit["source"])
            != {"run_id", "mysql_container_id", "observer_instance", "observer_pid", "audit_table"}
            or not isinstance(before_audit["source"].get("run_id"), str)
            or re.fullmatch(r"[0-9a-f]{16}", before_audit["source"]["run_id"]) is None
            or not isinstance(before_audit["source"].get("mysql_container_id"), str)
            or re.fullmatch(r"[0-9a-f]{64}", before_audit["source"]["mysql_container_id"]) is None
            or not isinstance(before_audit["source"].get("observer_instance"), str)
            or re.fullmatch(r"[0-9a-f]{32}", before_audit["source"]["observer_instance"]) is None
            or type(before_audit["source"].get("observer_pid")) is not int
            or before_audit["source"]["observer_pid"] <= 0
            or before_audit["source"].get("audit_table") != "fitness_eval_audit.events"
            or any(type(value) is not int or value <= 0 for value in times)
        ):
            raise ValueError("SETUP_AUDIT_SOURCE_OR_WINDOW_INVALID")
        start, before_end, after_start, end = cast(tuple[int, int, int, int], times)
        if not start <= before_end < after_start <= end:
            raise ValueError("SETUP_AUDIT_SOURCE_OR_WINDOW_INVALID")
        result["observed_audit_counts_equal"] = before_counts == after_counts
        result["snapshot_facts_equal"] = (
            result["before_snapshot_sha256"] == result["after_snapshot_sha256"]
        )
        if (
            not isinstance(response, dict)
            or response.get("answer") != fixture["setup_response"]
            or type(response.get("tool_steps")) is not int
            or response["tool_steps"] != 0
        ):
            raise ValueError("DEV_SETUP_NOT_ESTABLISHED")
        if not result["snapshot_facts_equal"] or not result["observed_audit_counts_equal"]:
            raise ValueError("SETUP_OBSERVATIONS_CHANGED")
        result["status"] = "OBSERVED_NO_CHANGE"
    except Exception:  # noqa: BLE001 - service errors may include private content
        result["reason"] = "SETUP_OBSERVATION_OR_CONTEXT_INVALID"
    return result


async def run(model: OneBookingModel) -> dict[str, Any]:
    original, fixture = _load()
    case, grading_case_sha256 = _fixture_bound_grading_case(original, fixture)
    if (
        type(model) is not OneBookingModel
        or model.profile != "BOOK-004"
        or model._transport is not None
    ):
        raise ValueError("unsupported model boundary")
    settings, run_id, mysql_id, context_secret, token_secret = _environment()
    initial = await asyncio.to_thread(verify_restricted_audit)
    observer = bind_verified_observer(
        initial, observe_restricted_audit, run_id=run_id, container_id=mysql_id
    )
    identity = _source_identity()
    identity["book_004_driver_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    identity["booking_model_boundary_sha256"] = hashlib.sha256(
        Path(__file__).with_name("booking_model_boundary.py").read_bytes()
    ).hexdigest()
    db, gateway = Database(settings), GatewayClient(settings)
    try:
        repo = ConfirmationRepository(db)
        registry = build_fitness_tool_registry(gateway)
        confirmation = ConfirmationService(
            repo,
            registry,
            gateway,
            AesGcmPayloadCipher(key=secrets.token_bytes(32), key_version="eval"),
            ConfirmationTokenIssuer(secret=token_secret, signing_key_id="eval"),
        )
        app = FastAPI()
        app.state.context_verifier = AgentContextVerifier(context_secret, signing_key_id="eval")
        app.state.confirmation_service = confirmation
        app.state.supervisor = Supervisor(
            cast(Any, SetupThenModel(model, fixture)),
            registry,
            checkpointer=InMemorySaver(),
            confirmation_service=confirmation,
            intent_router=cast(Any, Router()),
        )
        app.include_router(agent_router)
        app.include_router(confirmation_router)

        def snapshot(request_id: str) -> dict[str, Any]:
            return snapshot_booking(
                run_id=run_id,
                container_id=mysql_id,
                organization_id="org-1",
                student_id="student-1",
                contract_id="contract-1",
                request_id=request_id,
                witness_organization_id="org-2",
                witness_student_id="student-2",
                witness_contract_id="contract-2",
            )

        def details() -> dict[str, Any]:
            return read_booking_details(
                run_id=run_id,
                container_id=mysql_id,
                organization_id="org-1",
                student_id="student-1",
                contract_id="contract-1",
            )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://agent.local"
        ) as client:
            agent = AgentHTTP(client, _identity(context_secret, subject="student-1", org="org-1"))
            setup = await _observe_setup(agent, fixture, snapshot, observer)
            if setup["status"] == "OBSERVED_NO_CHANGE":
                trial = await _drive(case, fixture, agent, repo, snapshot, details, observer)
            else:
                trial = {
                    "case_id": case.case_id,
                    "task_status": "INCONCLUSIVE",
                    "reason": "SETUP_OBSERVATION_OR_CONTEXT_INVALID",
                    "assertions": [],
                    "executed_steps": [],
                }
        try:
            final = await asyncio.to_thread(verify_restricted_audit)
            if not matching_final_integrity(initial, final):
                raise ValueError("FINAL_AUDIT_INTEGRITY_UNVERIFIED")
        except Exception:  # noqa: BLE001
            final = {"status": "FAILED"}
            invalidate_failed_audit(trial)
        return {
            "status": "FAIL" if trial["task_status"] == "FAIL" else "INCOMPLETE",
            "origin": "synthetic_context_real_model_real_local_postgres_mysql_java",
            "dataset_version": original.dataset_version,
            "dataset_sha256": _DATASET_SHA256,
            "fixture_sha256": _FIXTURE_SHA256,
            "grading_contract": {
                "version": _GRADING_VERSION,
                "case_sha256": grading_case_sha256,
                "status": "DRAFT_UNREVIEWED",
                "source_case_modified": False,
                "added_step": "repeat_same_approval_after_terminal",
            },
            "source_identity": identity,
            "run_id": run_id,
            "review": "draft",
            "audit_complete": False,
            "audit_initial_integrity": initial,
            "audit_final_integrity": final,
            "setup_observation": setup,
            "trial": trial,
            "model_diagnostics": model.diagnostics(),
            "limitations": [
                "The prior selected-course conversation is scripted synthetic fixture context, not an original case condition or a real availability search.",
                "Grader parameters are a separate unreviewed fixture-bound proposal; they are not part of the frozen original case.",
                "The fixture-bound proposal adds a duplicate approval after terminal status to observe idempotency; the original case has no retry step.",
                "Snapshots do not prove complete transaction history or causal authorization.",
                "No independent report provenance or human review; not release evidence.",
                "Local reservation is not a provider-side spending cap.",
            ],
        }
    finally:
        await gateway.close()
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--real-model", required=True, action="store_true")
    parser.add_argument("--confirm-current-pricing", required=True, action="store_true")
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--budget-file", required=True)
    parser.add_argument("--budget-limit-nano", type=int, required=True)
    parser.add_argument("--budget-opening-nano", type=int, required=True)
    parser.add_argument("--minimum-reserved-nano", type=int, required=True)
    args = parser.parse_args()
    try:
        with Path(args.report_file).open("x", encoding="utf-8") as stream:
            try:
                model = _real_model(args, profile="BOOK-004")
                report = asyncio.run(run(model))
            except Exception:  # noqa: BLE001
                report = {"status": "ERROR", "reason": "PREFLIGHT_OR_TRIAL_SETUP_FAILED"}
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"status": report["status"]}))
        return {"INCOMPLETE": 3, "FAIL": 2}.get(report["status"], 1)
    except (OSError, ValueError):
        print(json.dumps({"status": "ERROR", "reason": "REPORT_WRITE_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
