"""Opt-in fresh Java service component tests mapped to frozen draft cases.

One offline Maven invocation tests a temporary source snapshot. The Java tests
mock the repository, and do not exercise real persistence, natural-language
understanding, external models, or production gateways. No cached JUnit file is
accepted: every observation must come from the current temporary invocation.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import booking_local as local
from .booking_contracts import matches_frozen_case
from .report import reduce_trial

_METHODS = {
    "BOOK-019": "expiredContractIsRejectedBeforeInsertAndReleasesLocks",
    "BOOK-020": "exhaustedContractIsRejectedBeforeInsertAndReleasesLocks",
    "BOOK-021": "studentCannotBookForAnotherStudent",
    "BOOK-023": "existingCoachAppointmentRejectsNewBookingWithoutWrite",
    "BOOK-024": "existingStudentAppointmentRejectsNewBookingWithoutWrite",
}
_TEST_SOURCE_SHA = "595702c658011ddf230076e8ae8c2819ab3b795a878f3bf200329ba44d2e31b6"
_TEST_REL = Path("src/test/java/com/shuyiwa/fitness/booking/service/BookingServiceTest.java")
_CLASS = "com.shuyiwa.fitness.booking.service.BookingServiceTest"
_ROOT = Path(__file__).resolve().parents[4]
_LOCK = threading.Lock()


@dataclass
class JavaSuiteScope:
    """One evaluation's fresh suite results, never reused in another scope."""

    run_id: str = field(default_factory=lambda: uuid4().hex)
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)


_SCOPE: contextvars.ContextVar[JavaSuiteScope | None] = contextvars.ContextVar(
    "task_eval_booking_java_scope", default=None
)


@contextmanager
def java_suite_scope() -> Iterator[None]:
    """Use once around an evaluation; child asyncio tasks inherit this scope."""
    token = _SCOPE.set(JavaSuiteScope())
    try:
        yield
    finally:
        _SCOPE.reset(token)


def supports(case: Any) -> bool:
    return (case.case_id in _METHODS or case.case_id == "BOOK-025") and matches_frozen_case(case)


def _fingerprint(root: Path) -> str:
    files = [root / "pom.xml", root / _TEST_REL]
    files.extend(sorted(p for p in (root / "src/main").rglob("*") if p.is_file()))
    h = hashlib.sha256()
    for path in files:
        if path.is_symlink():
            raise ValueError("symlink source")
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        h.update(len(name).to_bytes(4, "big"))
        h.update(name)
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def _valid_junit(path: Path) -> bool:
    """Accept only this complete five-method, zero-failure test report."""
    if not path.is_file() or path.stat().st_size > 1_000_000:
        return False
    root = ET.fromstring(path.read_bytes())
    if root.tag != "testsuite" or any(
        root.get(key) != value
        for key, value in {"tests": "5", "failures": "0", "errors": "0", "skipped": "0"}.items()
    ):
        return False
    tests = root.findall("testcase")
    return (
        len(tests) == 5
        and {test.get("name") for test in tests} == set(_METHODS.values())
        and all(test.get("classname") == _CLASS for test in tests)
        and all(
            not any(test.find(tag) is not None for tag in ("failure", "error", "skipped"))
            for test in tests
        )
    )


def _execute_suite(scope: JavaSuiteScope) -> dict[str, Any]:
    source = _ROOT / "fitness-booking-service"
    try:
        with _LOCK:
            if hashlib.sha256((source / _TEST_REL).read_bytes()).hexdigest() != _TEST_SOURCE_SHA:
                return {"status": "SKIPPED", "reason": "JAVA_TEST_CONTRACT_CHANGED"}
            fingerprint = _fingerprint(source)
            if fingerprint in scope.cache:
                return dict(scope.cache[fingerprint])
            with tempfile.TemporaryDirectory(prefix="fitness-booking-java-eval-") as directory:
                target = Path(directory)
                shutil.copyfile(source / "pom.xml", target / "pom.xml")
                shutil.copytree(source / "src/main", target / "src/main")
                dest = target / _TEST_REL
                dest.parent.mkdir(parents=True)
                shutil.copyfile(source / _TEST_REL, dest)
                if _fingerprint(target) != fingerprint:
                    raise ValueError("snapshot changed")
                environment = {
                    key: os.environ[key]
                    for key in ("PATH", "HOME", "JAVA_HOME")
                    if key in os.environ
                }
                command = [
                    "mvn",
                    "-q",
                    "-o",
                    "-s",
                    str(_ROOT / ".mvn/settings.xml"),
                    f"-Dmaven.repo.local={_ROOT / '.mvn/repository'}",
                    "-f",
                    str(target / "pom.xml"),
                    "-Dtest=BookingServiceTest#" + "+".join(_METHODS.values()),
                    "test",
                ]
                completed = subprocess.run(
                    command,
                    cwd=_ROOT,
                    env=environment,
                    capture_output=True,
                    timeout=90,
                    check=False,
                )
                reports = list((target / "target/surefire-reports").glob("TEST-*.xml"))
                if completed.returncode != 0 or len(reports) != 1 or not _valid_junit(reports[0]):
                    result: dict[str, Any] = {
                        "status": "ERROR",
                        "reason": "JAVA_SUITE_NOT_VERIFIED",
                    }
                elif _fingerprint(source) != fingerprint:
                    result = {"status": "ERROR", "reason": "JAVA_SOURCE_CHANGED_DURING_RUN"}
                else:
                    result = {
                        "status": "PASS",
                        "reason": "FRESH_JAVA_SUITE_PASSED",
                        "source_snapshot_sha256": fingerprint,
                        "method_count": 5,
                        "suite_run_id": scope.run_id,
                    }
                scope.cache[fingerprint] = result
                return dict(result)
    except (OSError, ValueError, subprocess.SubprocessError, ET.ParseError):
        return {"status": "ERROR", "reason": "JAVA_SUITE_UNAVAILABLE"}


async def run(case: Any, *, index: int = 0, seed: int = 0) -> dict[str, Any]:
    if not supports(case):
        raise ValueError("unsupported booking Java fixture")
    started = time.monotonic()
    assertions = [
        {
            "id": a.id,
            "rule": a.rule,
            "phase": a.phase,
            "required": a.required,
            "risk": a.risk,
            "applicable": a.applicable,
            "status": "UNKNOWN" if a.applicable else "NA",
            "reason": "FULL_TASK_NOT_EXECUTED" if a.applicable else "NOT_APPLICABLE",
        }
        for a in case.expected.assertions
    ]
    status = "SKIPPED"
    reason = "JAVA_SUITE_NOT_ENABLED"
    evidence: dict[str, Any] = {}
    executed = False
    if case.case_id == "BOOK-025":
        try:
            probe = await local.evaluate_case("conflict")
            executed = bool(probe.get("checks", {}).get("chat_api_succeeded"))
            if probe.get("status") == "ERROR":
                status, reason = "ERROR", "LOCAL_CONFLICT_PROBE_ERROR"
            else:
                for name, ok in probe.get("checks", {}).items():
                    assertions.append(
                        {
                            "id": "supplemental_" + name,
                            "rule": "confirmation_conflict_probe",
                            "phase": "final",
                            "required": True,
                            "risk": None,
                            "applicable": True,
                            "status": "PASS" if ok else "FAIL",
                            "reason": "LOCAL_PROBE_OBSERVED",
                        }
                    )
                status = reduce_trial(assertions, executed=executed)
                reason = "SCRIPTED_MODEL_AND_SYNTHETIC_CONFLICT"
            evidence = {"checks": probe.get("checks", {}), "business": probe.get("business", {})}
        except Exception:  # noqa: BLE001 - avoid leaking transport details
            status, reason = "ERROR", "LOCAL_CONFLICT_PROBE_ERROR"
    elif os.environ.get("TASK_EVAL_JAVA_COMPONENTS") == "1":
        scope = _SCOPE.get() or JavaSuiteScope()
        evidence = await asyncio.to_thread(_execute_suite, scope)
        status, reason = evidence["status"], evidence["reason"]
        if status == "PASS":
            executed = True
            assertions.append(
                {
                    "id": "supplemental_java_service_boundary",
                    "rule": "fresh_java_service_test",
                    "phase": "final",
                    "required": True,
                    "risk": None,
                    "applicable": True,
                    "status": "PASS",
                    "reason": "FRESH_JAVA_SUITE_PASSED",
                    "evidence_origin": "offline_maven_mock_repository",
                }
            )
            evidence = {**evidence, "selected_test_method": _METHODS[case.case_id]}
            status = reduce_trial(assertions, executed=True)
    return {
        "trial_id": uuid4().hex,
        "case_id": case.case_id,
        "family_id": case.family_id,
        "split": case.split,
        "primary_scenario": case.primary_scenario,
        "delivery_scope": case.delivery_scope,
        "capability_status": case.capability_status,
        "trial_index": index,
        "seed": seed,
        "executed": executed,
        "status": status,
        "reason": reason,
        "assertions": assertions,
        "evidence": evidence,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "result_origin": "java_booking_service_mock_repository"
        if case.case_id in _METHODS
        else "python_confirmation_with_synthetic_conflict",
        "limitations": [
            "DRAFT_CASE",
            "NO_MODEL_OR_CASE_CONVERSATION_EVALUATED",
            "BUSINESS_REPOSITORY_OR_GATEWAY_TEST_DOUBLE",
            "NO_INDEPENDENT_DATABASE_AUDIT",
            "BUSINESS_ELIGIBILITY_AND_ANSWER_NOT_FULLY_EVALUATED",
        ],
    }
