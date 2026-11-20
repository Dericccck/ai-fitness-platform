"""Detect drift in the documented v1/plan comparison; never infer acceptance."""

import hashlib
import json
import re
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[2]
ROOT = SERVICE.parent
PLAN = ROOT / "docs/健身Agent评测系统落地方案_120条.md"
DATASET = SERVICE / "evals/task_eval/datasets/v1.jsonl"
AUDIT = SERVICE / "evals/task_eval/PLAN_EXAMPLE_ALIGNMENT.md"
PLAN_HASH = "86537dfeb7ee62f3519110b8096975f8eeed15a1ba11f20cce69756052dbb254"
DATASET_HASH = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"


def test_plan_examples_and_v1_audit_remain_bound_to_reviewed_inputs() -> None:
    plan = PLAN.read_bytes()
    dataset = DATASET.read_bytes()
    assert hashlib.sha256(plan).hexdigest() == PLAN_HASH
    assert hashlib.sha256(dataset).hexdigest() == DATASET_HASH
    audit = AUDIT.read_text()
    assert PLAN_HASH in audit and DATASET_HASH in audit
    section = plan.decode().split("## 6. 至少先落地的端到端样例", 1)[1].split("## 7.", 1)[0]
    ids = re.findall(r"^\| `([A-Z]+-\d{3})` \|", section, re.MULTILINE)
    assert ids == [
        "ROUTE-001",
        "RAG-001",
        "RAG-002",
        "BOOK-001",
        "BOOK-002",
        "BOOK-003",
        "OPS-001",
        "PRO-001",
        "SEC-001",
    ]
    assert all(f"| {example} |" in audit for example in ids)


def test_recorded_booking_id_conflicts_and_candidate_structure() -> None:
    cases = {case["case_id"]: case for case in map(json.loads, DATASET.read_text().splitlines())}
    one, two, three = (cases[f"BOOK-00{i}"] for i in range(1, 4))
    assert one["steps"][0]["message"] == "预约指定课程，确认后提交"
    assert sum(step["type"] == "decision" for step in two["steps"]) == 1
    assert not any(step["type"] == "inject_fault" for step in three["steps"])
    assert "询问门店" in cases["BOOK-011"]["expected"]["reference_points"][0]
    duplicate = cases["BOOK-026"]["steps"]
    assert sum(step["type"] == "decision" for step in duplicate) == 2
    assert any(step.get("fault") == "duplicate_decision" for step in duplicate)
    assert any(step.get("fault") == "drop_business_response" for step in cases["BOOK-027"]["steps"])
    for case_id in (
        "ROUT-001",
        "KNOW-001",
        "KNOW-011",
        "BOOK-011",
        "BOOK-026",
        "BOOK-027",
        "OPER-001",
        "OPER-009",
        "OPER-010",
        "SAFE-001",
    ):
        assert cases[case_id]["review"]["status"] == "draft"
        assert case_id in AUDIT.read_text()
