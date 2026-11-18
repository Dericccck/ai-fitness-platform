"""Run local component and protocol evaluations with explicit evidence boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .booking_local import evaluate_suite as evaluate_booking
from .booking_signed_identity import evaluate_signed_identity
from .component_notification import evaluate_notification_policy
from .component_operations import evaluate_operations_components
from .retrieval_lab import run_lab as evaluate_retrieval


async def evaluate_operations() -> dict[str, Any]:
    return evaluate_operations_components()


async def run_local_suite(selection: str = "all") -> dict[str, Any]:
    """Run independently isolated local fixtures; never contact external services."""
    functions = {
        "booking": evaluate_booking,
        "signed_identity": evaluate_signed_identity,
        "retrieval": evaluate_retrieval,
        "notification": evaluate_notification_policy,
        "operations": evaluate_operations,
    }
    if selection not in (*functions, "all"):
        raise ValueError("invalid local suite")
    selected = list(functions) if selection == "all" else [selection]
    suites: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    for name in selected:
        try:
            result = await functions[name]()
            suites[name] = result
            if name in {"booking", "signed_identity"}:
                statuses[name] = (
                    result["status"] if result["status"] in {"PASS", "FAIL"} else "INCOMPLETE"
                )
            elif name in {"notification", "operations"}:
                observed = [case["status"] for case in result["cases"]]
                statuses[name] = (
                    "FAIL"
                    if "FAIL" in observed
                    else "PASS"
                    if observed and all(s == "PASS" for s in observed)
                    else "INCOMPLETE"
                )
            else:
                assertions = [
                    case[metric]["status"]
                    for case in result["cases"]
                    for metric in ("retrieval", "acl", "answer")
                ]
                statuses[name] = (
                    "FAIL"
                    if "FAIL" in assertions
                    else "INCOMPLETE"
                    if not assertions or any(status not in {"PASS", "NA"} for status in assertions)
                    else "PASS"
                )
        except Exception:  # noqa: BLE001 - retain the other suites and avoid disclosing payloads
            suites[name] = {"status": "ERROR", "reason": "LOCAL_SUITE_ERROR"}
            statuses[name] = "INCOMPLETE"
    status = (
        "FAIL"
        if "FAIL" in statuses.values()
        else "INCOMPLETE"
        if "INCOMPLETE" in statuses.values()
        else "PASS"
    )
    app_root = Path(__file__).resolve().parents[2]
    source_paths = (
        "agent/supervisor.py",
        "api/routes/agent.py",
        "api/routes/confirmations.py",
        "infrastructure/agent_context.py",
        "confirmation/token.py",
        "rag/service.py",
        "notifications/preferences.py",
        "agent/operations_tools.py",
        "evaluation/task_eval/booking_local.py",
        "evaluation/task_eval/booking_signed_identity.py",
        "evaluation/task_eval/agent_http.py",
        "evaluation/task_eval/retrieval_lab.py",
        "evaluation/task_eval/component_retrieval.py",
        "evaluation/task_eval/component_notification.py",
        "evaluation/task_eval/component_operations.py",
        "evaluation/task_eval/graders/rules.py",
    )
    source_sha256 = {
        path: hashlib.sha256((app_root / path).read_bytes()).hexdigest() for path in source_paths
    }
    return {
        "schema_version": "1.0",
        "selection": selection,
        "mode": "local_contract",
        "status": status,
        "suite_statuses": statuses,
        "source_sha256": source_sha256,
        "limitations": [
            "Synthetic cases and test doubles; not a release gate or the 120-case task benchmark.",
            "No real model, production database, Java booking service, or notification delivery is evaluated.",
            "Signed-identity checks use ephemeral test keys and a local gateway substitute; they do not verify the Java gateway or deployment identity configuration.",
        ],
        "suites": suites,
    }
