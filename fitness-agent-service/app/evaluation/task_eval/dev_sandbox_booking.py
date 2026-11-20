"""One explicitly synthetic development trial in launcher-owned local services.

This is neither one of the planning benchmark's 120 cases nor release evidence.
No external model is invoked; snapshot observations are not a complete audit.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.fitness_tools import build_fitness_tool_registry
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
from app.infrastructure.model_gateway import ModelToolCall, ModelTurn

from .agent_http import AgentHTTP
from .audit_observation import (
    bind_verified_observer,
    committed_before_decision,
    invalidate_failed_audit,
    matching_final_integrity,
)
from .booking_local import Router
from .booking_signed_identity import _identity
from .cross_service_booking import _environment, _verify_database_deltas
from .dataset import load_dataset
from .dev_booking_observer import read_booking_details
from .mysql_booking_observer import snapshot_booking
from .mysql_restricted_audit import observe as observe_restricted_audit
from .mysql_restricted_audit import verify as verify_restricted_audit
from .step_kernel import StepState, execute_steps, prepare_steps

_DIR = Path(__file__).resolve().parents[3] / "evals/task_eval/dev_sandbox_v1"
_CASE_DIGEST = "4acffd6a435ded4f82fe50bd04b5dd0af6e8265dadd15b6196a8c968f16412e1"
_FIXTURE_DIGEST = "583f13a0aeb889a2941bfbdb60bc7ea70606920a5698d8e143ea2ba75e986170"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_identity() -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[4]
    result: dict[str, Any] = {"revision": "unknown", "working_tree_dirty": None}
    try:
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=normal"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout
        result.update(revision=revision, working_tree_dirty=bool(status))
    except (OSError, subprocess.SubprocessError):
        pass
    # This is a source fingerprint, not independent build or runtime attestation.
    result["driver_sha256"] = _digest(Path(__file__))
    result["step_kernel_sha256"] = _digest(Path(__file__).with_name("step_kernel.py"))
    result["booking_observer_sha256"] = _digest(Path(__file__).with_name("dev_booking_observer.py"))
    result["restricted_audit_sha256"] = _digest(
        Path(__file__).with_name("mysql_restricted_audit.py")
    )
    result["audit_observation_sha256"] = _digest(Path(__file__).with_name("audit_observation.py"))
    return result


def _load() -> tuple[Any, dict[str, Any]]:
    case_path, fixture_path = _DIR / "case.jsonl", _DIR / "fixture.json"
    if _digest(case_path) != _CASE_DIGEST or _digest(fixture_path) != _FIXTURE_DIGEST:
        raise ValueError("DEV_FIXTURE_CHANGED")
    dataset = load_dataset(case_path, check_quotas=False)
    if len(dataset.cases) != 1:
        raise ValueError("DEV_CASE_MISMATCH")
    case = dataset.cases[0]
    if case.case_id != "DEV-SANDBOX-BOOK-001" or case.review.status != "draft":
        raise ValueError("DEV_CASE_MISMATCH")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if datetime.fromisoformat(fixture["booking"]["start_time"]) <= datetime.now(UTC):
        raise ValueError("DEV_FIXTURE_EXPIRED")
    return case, fixture


class _FixedModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload, self.calls = payload, 0

    async def chat_with_tools(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]], **kwargs: Any
    ) -> ModelTurn:
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(
                content="",
                tool_calls=(
                    ModelToolCall(
                        call_id="dev-booking",
                        name="fitness_booking_create_v1",
                        arguments=self.payload,
                    ),
                ),
            )
        return ModelTurn(content="请求已处理。", tool_calls=())


async def _drive(
    case: Any,
    fixture: dict[str, Any],
    agent: AgentHTTP,
    repository: ConfirmationRepository,
    snapshot: Any,
    details: Any,
    audit_observe: Any = None,
) -> dict[str, Any]:
    """Drive the exact frozen development steps; arguments here are not a public adapter API."""
    requests = prepare_steps(case)
    initial = await asyncio.to_thread(snapshot, "dev-preflight")
    if not initial.get("snapshot_complete") or not initial.get("witness_observed"):
        raise ValueError("INITIAL_SNAPSHOT_INCOMPLETE")
    facts = initial["facts"]
    if (
        facts["appointments"],
        facts["remaining_hours"],
        facts["witness_appointments"],
        facts["witness_remaining_hours"],
    ) != (0, 2, 0, 5):
        raise ValueError("INITIAL_FIXTURE_MISMATCH")
    state = StepState()
    operations = execute_steps(
        case, requests, state, evidence_origin="local_snapshot_not_complete_audit"
    )
    request = next(operations, None)
    record: Any = None
    cid: str | None = None
    executed_steps: list[dict[str, Any]] = []
    snapshot_checks: list[dict[str, Any]] = []
    idempotency_checked = False
    repeated_decision: dict[str, Any] | None = None
    decision_count = 0
    failure: str | None = None
    audit_observations: list[dict[str, Any]] = []
    audit_before_chat: dict[str, Any] | None = None
    try:
        async with asyncio.timeout(case.timeouts.trial_seconds):
            if audit_observe is not None:
                initial_audit = await asyncio.to_thread(audit_observe)
                if not isinstance(initial_audit, dict):
                    raise ValueError("INVALID_AUDIT_OBSERVATION")
                audit_before_chat = initial_audit
                audit_observations.append({"phase": "before_chat", **initial_audit})
            while request is not None:
                step = request.step
                evidence = None
                async with asyncio.timeout(case.timeouts.step_seconds):
                    if request.kind == "observe":
                        if record is None or cid is None:
                            raise ValueError("CONFIRMATION_NOT_CAPTURED")
                        record = await repository.get_for_subject(
                            cid,
                            fixture["identity"]["student_id"],
                            [fixture["identity"]["organization_id"]],
                        )
                        current = await asyncio.to_thread(snapshot, record.request_id)
                        if not current.get("snapshot_complete") or not current.get(
                            "witness_observed"
                        ):
                            raise ValueError("SNAPSHOT_INCOMPLETE")
                        current_facts = current["facts"]
                        phase = step.phase
                        current_audit: dict[str, Any] | None = None
                        if audit_observe is not None:
                            observed_audit = await asyncio.to_thread(audit_observe)
                            if not isinstance(observed_audit, dict):
                                raise ValueError("INVALID_AUDIT_OBSERVATION")
                            current_audit = observed_audit
                            audit_observations.append({"phase": phase, **observed_audit})
                        try:
                            expected_deltas = _verify_database_deltas(
                                initial,
                                current,
                                "REJECT" if phase == "before_decision" else "APPROVE",
                            )
                            snapshot_checks.append(
                                {
                                    "phase": phase,
                                    "status": "MATCH",
                                    "deltas": expected_deltas,
                                    "authorization_status": record.authorization_status,
                                    "execution_status": record.execution_status,
                                }
                            )
                        except ValueError:
                            snapshot_checks.append(
                                {
                                    "phase": phase,
                                    "status": "MISMATCH",
                                    "authorization_status": record.authorization_status,
                                    "execution_status": record.execution_status,
                                }
                            )
                        snapshot_digest = hashlib.sha256(
                            json.dumps(
                                current_facts, sort_keys=True, separators=(",", ":")
                            ).encode()
                        ).hexdigest()
                        evidence = {
                            "evidence_refs": ["snapshot-sha256:" + snapshot_digest],
                            "before": {
                                "booking_count": facts["appointments"],
                                "credit_count": facts["remaining_hours"],
                            },
                            "after": {
                                "booking_count": current_facts["appointments"],
                                "credit_count": current_facts["remaining_hours"],
                            },
                            "audit_complete": False,
                            "actor": record.subject_user_id,
                            "action": record.tool_id,
                            "authorization_status": record.authorization_status,
                            "execution_status": record.execution_status,
                        }
                        if (
                            phase == "before_decision"
                            and decision_count == 0
                            and record.authorization_status == "PENDING"
                            and current_audit is not None
                        ):
                            # Check the monotonic confirmation state on both sides
                            # of the committed MySQL observation. A clean audit
                            # snapshot is not proof that all writers have drained.
                            after_audit = await repository.get_for_subject(
                                cid,
                                fixture["identity"]["student_id"],
                                [fixture["identity"]["organization_id"]],
                            )
                            if after_audit.authorization_status == "PENDING":
                                commits = committed_before_decision(
                                    audit_before_chat, current_audit
                                )
                                if commits:
                                    evidence["commits"] = commits
                                    audit_digest = hashlib.sha256(
                                        json.dumps(
                                            current_audit, sort_keys=True, separators=(",", ":")
                                        ).encode()
                                    ).hexdigest()
                                    evidence["evidence_refs"].append("audit-sha256:" + audit_digest)
                        if phase == "final":
                            detail = await asyncio.to_thread(details)
                            if detail.get("snapshot_complete") is not True:
                                raise ValueError("DETAIL_SNAPSHOT_INCOMPLETE")
                            detail_digest = hashlib.sha256(
                                json.dumps(
                                    detail["created_bookings"],
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode()
                            ).hexdigest()
                            evidence.update(
                                evidence_refs=[
                                    "snapshot-sha256:" + snapshot_digest,
                                    "details-sha256:" + detail_digest,
                                ],
                                snapshot_complete=True,
                                created_bookings=detail["created_bookings"],
                                credit_delta=current_facts["remaining_hours"]
                                - facts["remaining_hours"],
                                idempotency_checked=idempotency_checked,
                            )
                    elif step.type == "chat":
                        if not isinstance(step.message, str) or not step.message:
                            raise ValueError("DEV_MESSAGE_UNAVAILABLE")
                        response = await agent.chat(step.message)
                        cid = response.get("confirmation_id")
                        if response.get("status") != "CONFIRMATION_REQUIRED" or not isinstance(
                            cid, str
                        ):
                            raise ValueError("CONFIRMATION_MISSING")
                        record = await repository.get_for_subject(
                            cid,
                            fixture["identity"]["student_id"],
                            [fixture["identity"]["organization_id"]],
                        )
                    elif step.type == "decision" and step.decision == "APPROVE" and cid:
                        decision_count += 1
                        if decision_count == 1:
                            await agent.decide(cid, "APPROVE")
                        elif decision_count == 2 and record is not None:
                            before_retry = await asyncio.to_thread(snapshot, record.request_id)
                            before_details = await asyncio.to_thread(details)
                            decision = await agent.decide(cid, "APPROVE")
                            after_retry = await asyncio.to_thread(snapshot, record.request_id)
                            after_details = await asyncio.to_thread(details)
                            idempotency_checked = (
                                before_retry.get("snapshot_complete") is True
                                and after_retry.get("snapshot_complete") is True
                                and before_retry.get("witness_observed") is True
                                and after_retry.get("witness_observed") is True
                                and before_details.get("snapshot_complete") is True
                                and after_details.get("snapshot_complete") is True
                                and before_retry.get("facts") == after_retry.get("facts")
                                and before_details.get("created_bookings")
                                == after_details.get("created_bookings")
                                and decision.get("authorization_status") == "APPROVED"
                                and decision.get("execution_status") == "SUCCEEDED"
                            )
                            repeated_decision = {
                                "status": "MATCH" if idempotency_checked else "MISMATCH",
                                "scope": "same_confirmation_and_decision_request_id_snapshot_comparison",
                            }
                        else:
                            raise ValueError("UNSUPPORTED_DECISION_SEQUENCE")
                    elif (
                        step.type == "await_terminal"
                        and cid
                        and step.expected_authorization_status
                        and step.expected_execution_status
                        and step.timeout_seconds
                    ):
                        await agent.await_terminal(
                            cid,
                            expected_authorization=step.expected_authorization_status,
                            expected_execution={step.expected_execution_status},
                            timeout_seconds=step.timeout_seconds,
                        )
                    else:
                        raise ValueError("UNSUPPORTED_DEV_STEP")
                executed_steps.append(
                    {"index": request.index, "type": step.type, "status": "COMPLETED"}
                )
                try:
                    request = operations.send(evidence)
                except StopIteration:
                    request = None
                if any(
                    row.get("status") == "FAIL" and (row.get("required") or row.get("risk"))
                    for row in state.assertions.values()
                ):
                    failure = "REQUIRED_ASSERTION_FAILED"
                    break
    except TimeoutError:
        failure = "STEP_OR_TRIAL_TIMEOUT"
    except Exception:  # noqa: BLE001 - do not expose service responses or credentials
        failure = "STEP_OR_OBSERVER_ERROR"
    assertions = []
    for a in case.expected.assertions:
        assertions.append(
            state.assertions.get(
                a.id,
                {
                    "id": a.id,
                    "rule": a.rule,
                    "phase": a.phase,
                    "required": a.required,
                    "risk": a.risk,
                    "applicable": a.applicable,
                    "status": "UNKNOWN" if a.applicable else "NA",
                    "reason": "EVIDENCE_NOT_OBSERVED",
                },
            )
        )
    return {
        "case_id": case.case_id,
        "trial_id": uuid4().hex,
        "task_status": (
            "FAIL"
            if any(
                a.get("status") == "FAIL" and (a.get("required") or a.get("risk"))
                for a in assertions
            )
            else "INCONCLUSIVE"
        ),
        "executed_steps": executed_steps,
        "reason": failure,
        "assertions": assertions,
        "phase_observations": state.phase_observations,
        "snapshot_checks": snapshot_checks,
        "repeated_decision": repeated_decision,
        "audit_observations": audit_observations,
    }


async def run() -> dict[str, Any]:
    case, fixture = _load()
    settings, run_id, mysql_id, context_secret, token_secret = _environment()
    audit_initial = await asyncio.to_thread(verify_restricted_audit)
    audit_observer = bind_verified_observer(
        audit_initial, observe_restricted_audit, run_id=run_id, container_id=mysql_id
    )
    source_identity = _source_identity()
    db = Database(settings)
    gateway = GatewayClient(settings)
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
            cast(Any, _FixedModel(fixture["booking"])),
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
            trial = await _drive(case, fixture, agent, repo, snapshot, details, audit_observer)
        try:
            audit_final: dict[str, Any] = await asyncio.to_thread(verify_restricted_audit)
            if not matching_final_integrity(audit_initial, audit_final):
                raise ValueError("FINAL_AUDIT_INTEGRITY_UNVERIFIED")
        except Exception:  # noqa: BLE001 - retain the trial and explicitly invalidate audit integrity
            audit_final = {"status": "FAILED"}
            invalidate_failed_audit(trial)
        return {
            "status": "FAIL" if trial["task_status"] == "FAIL" else "INCOMPLETE",
            "origin": "synthetic_scripted_model_real_local_postgres_mysql_java",
            "dataset_version": case.dataset_version,
            "case_sha256": _CASE_DIGEST,
            "fixture_sha256": _FIXTURE_DIGEST,
            "source_identity": source_identity,
            "run_id": run_id,
            "review": "draft",
            "audit_complete": False,
            "audit_initial_integrity": audit_initial,
            "audit_final_integrity": audit_final,
            "trial": trial,
            "limitations": [
                "Fixed scripted model does not test language understanding.",
                "Snapshots do not prove complete transaction history or causal authorization.",
                "No independent report provenance or human review; not release evidence.",
            ],
        }
    finally:
        try:
            await gateway.close()
        finally:
            await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-file", required=True)
    args = parser.parse_args()
    try:
        with Path(args.report_file).open("x", encoding="utf-8") as stream:
            try:
                report = asyncio.run(run())
            except Exception:  # noqa: BLE001 - keep errors sanitized
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
