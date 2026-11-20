"""Case-bound synthetic outreach fixtures and explicitly partial local observations.

No database, external notification channel, model, or actual user is contacted.
The original planning dataset is not altered; all fixtures remain unreviewed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from .component_notification_worker import evaluate_notification_worker
from .component_outreach_cases import evaluate_outreach_case_observations
from .dataset import Dataset, load_dataset
from .graders.rules import grade_rule

_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "evals/task_eval/dev_fixtures/outreach-009-016-draft-v1.json"
)
_FIXTURE_SHA256 = "d83368a0f8a04ca488efa4595d70884923108dff513b55cb14d96b60d065efb9"
_SOURCE_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"


def load_contracts(dataset: Dataset) -> list[dict[str, Any]]:
    raw = _FIXTURE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _FIXTURE_SHA256:
        raise ValueError("OUTREACH_FIXTURE_CHANGED")
    fixture = json.loads(raw)
    cases = fixture.get("cases")
    if (
        dataset.sha256 != _SOURCE_SHA256
        or fixture.get("dataset_sha256") != _SOURCE_SHA256
        or not isinstance(cases, list)
        or len(cases) != 8
    ):
        raise ValueError("OUTREACH_SOURCE_CHANGED")
    originals = {c.case_id: c for c in dataset.cases}
    for index, contract in enumerate(cases, start=9):
        cid = f"OPER-{index:03}"
        case = originals.get(cid)
        if case is None or contract.get("case_id") != cid:
            raise ValueError("OUTREACH_CASE_MISSING")
        encoded = json.dumps(
            case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if (
            hashlib.sha256(encoded).hexdigest() != contract.get("source_case_sha256")
            or case.split != contract.get("split")
            or case.review.status != "draft"
            or case.capability_status != "planned"
        ):
            raise ValueError("OUTREACH_CASE_CHANGED")
    return cases


def _component_grade(contract: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Grade only a completed in-memory probe, never a production delivery window."""
    observed = row.get("observed")
    if row.get("component_status") not in {"PASS", "FAIL"} or not isinstance(observed, dict):
        return {"status": "UNKNOWN", "reason": "IN_MEMORY_PROBE_INCOMPLETE"}
    deliveries = observed.get("deliveries")
    if not isinstance(deliveries, list) or not all(isinstance(x, dict) for x in deliveries):
        return {"status": "UNKNOWN", "reason": "IN_MEMORY_DELIVERY_EVIDENCE_MISSING"}
    if any(x.get("organization") != contract["organization"] for x in deliveries):
        return {"status": "FAIL", "reason": "SYNTHETIC_ORGANIZATION_MISMATCH"}
    grade = grade_rule(
        "notification_policy",
        {"delivery_count": contract["expected_delivery_count"], "recipient": contract["subject"]},
        {
            "evidence_refs": ["in-memory-worker:test-only"],
            "delivery_window_complete": True,
            "deliveries": deliveries,
        },
    )
    if row["component_status"] == "FAIL" and grade["status"] == "PASS":
        return {"status": "FAIL", "reason": "WORKER_POLICY_OR_STATUS_MISMATCH"}
    return grade


async def evaluate(dataset: Dataset, *, case_ids: frozenset[str] | None = None) -> dict[str, Any]:
    contracts = load_contracts(dataset)
    available_ids = {contract["case_id"] for contract in contracts}
    if case_ids is not None and not case_ids <= available_ids:
        raise ValueError("OUTREACH_CASE_SELECTION_INVALID")
    selected = available_ids if case_ids is None else case_ids
    rows = []
    partial_by_id: dict[str, dict[str, Any]] = {}
    if selected & {"OPER-014", "OPER-015", "OPER-016"}:
        outreach = await evaluate_outreach_case_observations(
            dataset, split="test", case_ids=frozenset(selected)
        )
        partial_by_id = {row["case_id"]: row for row in outreach["cases"]}
    for contract in contracts:
        cid = contract["case_id"]
        if cid not in selected:
            continue
        result: dict[str, Any] = {
            "case_id": cid,
            "split": contract["split"],
            "fixture": contract,
            "expected": {
                "recipient": contract["subject"],
                "delivery_count": contract["expected_delivery_count"],
            },
            "task_status": "UNKNOWN",
        }
        if cid <= "OPER-013":
            report = await evaluate_notification_worker(
                subject=contract["subject"], organization=contract["organization"], case_id=cid
            )
            matches = [r for r in report["cases"] if r["case_id"] == cid]
            if len(matches) != 1:
                result["component_grade"] = {"status": "UNKNOWN", "reason": "PROBE_RESULT_MISSING"}
            else:
                result["component_grade"] = _component_grade(contract, matches[0])
                result["observation"] = matches[0]
        else:
            # The existing 014 target-list check, 015 disabled preference, and
            # 016 missing-subject check are necessary components only. They do
            # not prove repeated delivery, a real consent mapping, or eligibility.
            partial = partial_by_id.get(cid)
            result["partial_observation"] = partial
            result["component_grade"] = (
                {"status": "FAIL", "reason": "NECESSARY_COMPONENT_FAILED"}
                if partial and partial.get("component_status") == "FAIL"
                else {"status": "UNKNOWN", "reason": "FULL_POLICY_BOUNDARY_NOT_OBSERVED"}
            )
        rows.append(result)
    return {
        "scope": "draft_synthetic_outreach_fixtures_and_in_memory_components",
        "fixture_sha256": _FIXTURE_SHA256,
        "dataset_sha256": _SOURCE_SHA256,
        "cases": rows,
        "limitations": [
            "Draft identities and expected counts are case-specific proposed fixtures, not reviewed production policy.",
            "The in-memory delivery window ends with the isolated worker call and proves no external delivery or production audit.",
            "Staleness is supplied by a fake repository. Event eligibility and consent withdrawal mappings remain unverified.",
            "014–016 are incomplete; test fixtures were authored after seeing the test cases and need leakage review.",
        ],
    }


def main() -> int:
    dataset_path = Path(__file__).resolve().parents[3] / "evals/task_eval/datasets/v1.jsonl"
    report = asyncio.run(evaluate(load_dataset(dataset_path)))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # A successful invocation means the available probes did not detect a
    # regression. It does not mean these draft tasks passed acceptance.
    return int(any(row["component_grade"]["status"] == "FAIL" for row in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())
