from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from app.evaluation.task_eval import booking_cases, booking_java_cases, booking_local, dataset_local
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl"
IDS = ["BOOK-019", "BOOK-020", "BOOK-021", "BOOK-023", "BOOK-024"]


def case(case_id: str) -> Any:
    return next(c for c in load_dataset(DATASET).cases if c.case_id == case_id)


def report() -> bytes:
    root = ET.Element("testsuite", tests="5", failures="0", errors="0", skipped="0")
    for method in booking_java_cases._METHODS.values():
        ET.SubElement(root, "testcase", classname=booking_java_cases._CLASS, name=method)
    return ET.tostring(root)


@pytest.mark.parametrize("case_id", IDS + ["BOOK-025"])
def test_case_drift_disables_binding(case_id: str) -> None:
    changed = case(case_id).model_copy(deep=True)
    changed.expected.reference_points = ["different expectation"]
    assert not booking_cases.supports(changed)


@pytest.mark.parametrize("case_id", IDS)
async def test_java_case_is_skipped_without_explicit_run(
    monkeypatch: pytest.MonkeyPatch, case_id: str
) -> None:
    monkeypatch.delenv("TASK_EVAL_JAVA_COMPONENTS", raising=False)
    assert (await booking_cases.run(case(case_id)))["status"] == "SKIPPED"


@pytest.mark.parametrize("case_id", IDS)
async def test_java_result_is_distinct_per_case_but_task_unknown(
    monkeypatch: pytest.MonkeyPatch, case_id: str
) -> None:
    monkeypatch.setenv("TASK_EVAL_JAVA_COMPONENTS", "1")
    monkeypatch.setattr(
        booking_java_cases,
        "_execute_suite",
        lambda _: {
            "status": "PASS",
            "reason": "FRESH_JAVA_SUITE_PASSED",
            "method_count": 5,
        },
    )
    trial = await booking_cases.run(case(case_id))
    assert trial["status"] == "INCONCLUSIVE" and trial["executed"]
    assert trial["evidence"]["selected_test_method"] == booking_java_cases._METHODS[case_id]
    assert trial["assertions"][0]["status"] == "UNKNOWN"
    assert "BUSINESS_REPOSITORY_OR_GATEWAY_TEST_DOUBLE" in trial["limitations"]


def test_junit_requires_exact_methods_and_zero_failures(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    path.write_bytes(report())
    assert booking_java_cases._valid_junit(path)
    root = ET.fromstring(report())
    root[0].set("name", root[1].get("name", ""))
    path.write_bytes(ET.tostring(root))
    assert not booking_java_cases._valid_junit(path)
    root = ET.fromstring(report())
    root[0].append(ET.Element("skipped"))
    path.write_bytes(ET.tostring(root))
    assert not booking_java_cases._valid_junit(path)
    root = ET.fromstring(report())
    root.set("failures", "1")
    path.write_bytes(ET.tostring(root))
    assert not booking_java_cases._valid_junit(path)


def test_suite_runs_once_per_run_id_and_cannot_reuse_old_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Path] = []

    def invoke(command: list[str], **_: Any) -> Any:
        pom = Path(command[command.index("-f") + 1])
        seen.append(pom.parent)
        dest = pom.parent / "target/surefire-reports/TEST-BookingServiceTest.xml"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(report())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(booking_java_cases.subprocess, "run", invoke)
    scope = booking_java_cases.JavaSuiteScope()
    next_scope = booking_java_cases.JavaSuiteScope()
    first = booking_java_cases._execute_suite(scope)
    assert first["status"] == "PASS"
    assert booking_java_cases._execute_suite(scope)["status"] == "PASS"
    assert len(seen) == 1
    second = booking_java_cases._execute_suite(next_scope)
    assert second["status"] == "PASS" and first["suite_run_id"] != second["suite_run_id"]
    assert len(seen) == 2 and seen[0] != seen[1]
    assert not any(p.exists() for p in seen)


def test_changed_java_test_contract_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(booking_java_cases, "_TEST_SOURCE_SHA", "0" * 64)
    result = booking_java_cases._execute_suite(booking_java_cases.JavaSuiteScope())
    assert result == {"status": "SKIPPED", "reason": "JAVA_TEST_CONTRACT_CHANGED"}


def test_valid_report_cannot_override_failed_maven_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def invoke(command: list[str], **_: Any) -> Any:
        pom = Path(command[command.index("-f") + 1])
        dest = pom.parent / "target/surefire-reports/TEST-BookingServiceTest.xml"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(report())
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(booking_java_cases.subprocess, "run", invoke)
    result = booking_java_cases._execute_suite(booking_java_cases.JavaSuiteScope())
    assert result == {"status": "ERROR", "reason": "JAVA_SUITE_NOT_VERIFIED"}


async def test_each_dataset_evaluation_creates_a_fresh_suite_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TASK_EVAL_JAVA_COMPONENTS", "1")
    monkeypatch.setenv("TASK_EVAL_JAVA_RUN_ID", "a" * 32)
    seen: list[Path] = []

    def invoke(command: list[str], **_: Any) -> Any:
        pom = Path(command[command.index("-f") + 1])
        seen.append(pom.parent)
        dest = pom.parent / "target/surefire-reports/TEST-BookingServiceTest.xml"
        dest.parent.mkdir(parents=True)
        dest.write_bytes(report())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(booking_java_cases.subprocess, "run", invoke)
    dataset = load_dataset(DATASET)
    selected = replace(dataset, cases=[case("BOOK-019"), case("BOOK-020")])
    first = await dataset_local.evaluate_dataset(selected, split="dev")
    second = await dataset_local.evaluate_dataset(selected, split="dev")

    def ids(result: dict[str, Any]) -> list[str]:
        return [
            row["observations"]["booking_protocol"]["evidence"]["suite_run_id"]
            for row in result["cases"]
        ]

    first_ids, second_ids = ids(first), ids(second)
    assert len(seen) == 2
    assert first_ids[0] == first_ids[1] and second_ids[0] == second_ids[1]
    assert first_ids[0] != second_ids[0] and first_ids[0] != "a" * 32
    assert booking_java_cases._SCOPE.get() is None


async def test_conflict_case_observes_no_write_and_no_success() -> None:
    trial = await booking_cases.run(case("BOOK-025"))
    assert trial["status"] == "INCONCLUSIVE"
    checks = trial["evidence"]["checks"]
    assert checks["conflict_not_success"] and checks["no_business_change"]
    assert trial["assertions"][0]["status"] == "UNKNOWN"


async def test_conflict_wrongly_reported_success_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    original = booking_local.Gateway.create_booking

    async def ignore_conflict(self: Any, context: Any, payload: Any) -> Any:
        self.scenario = "approve"
        return await original(self, context, payload)

    monkeypatch.setattr(booking_local.Gateway, "create_booking", ignore_conflict)
    trial = await booking_cases.run(case("BOOK-025"))
    assert trial["status"] == "FAIL"
    assert not trial["evidence"]["checks"]["conflict_not_success"]
