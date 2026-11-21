import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval.dataset import load_dataset
from app.evaluation.task_eval.review_packet import build_packet, render_markdown

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def inputs():
    original = load_dataset(ROOT / "evals/task_eval/datasets/v1.jsonl")
    proposal = load_dataset(ROOT / "evals/task_eval/draft_parameter_proposal_v2/cases.jsonl")
    manifest = json.dumps(
        {
            "dataset_sha256": original.sha256,
            "dataset_version": original.dataset_version,
            "mode": "dataset_local_exploratory",
            "split": "all",
            "run_id": "synthetic-run",
            "created_at": "2026-11-21T00:00:00Z",
        }
    ).encode()
    cases = b"\n".join(
        json.dumps(
            {
                "case_id": case.case_id,
                "split": case.split,
                "primary_scenario": case.primary_scenario,
                "task_status": "INCONCLUSIVE",
                "observations": {"component": {"status": "PASS"}},
            }
        ).encode()
        for case in original.cases
    )
    return original, proposal, manifest, cases


def test_packet_preserves_unreviewed_original_and_proposal(inputs):
    packet = build_packet(*inputs)
    assert packet["case_count"] == 120
    assert packet["purpose"] == "UNREVIEWED_CASE_AND_EVIDENCE_PREPARATION"
    assert all(row["human_review"] is None for row in packet["cases"])
    assert all(row["local_task_status"] == "INCONCLUSIVE" for row in packet["cases"])
    result = render_markdown(packet)
    assert "不是审核结论" in result and "BOOK-009" in result and "SAFE-015" in result


def test_packet_rejects_wrong_dataset_and_duplicate_report_rows(inputs):
    original, proposal, manifest, cases = inputs
    altered = json.loads(manifest)
    altered["dataset_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="REPORT_DATASET_MISMATCH"):
        build_packet(original, proposal, json.dumps(altered).encode(), cases)
    with pytest.raises(ValueError, match="REPORT_CASES_INVALID"):
        build_packet(original, proposal, manifest, cases + b"\n" + cases.splitlines()[0])


def test_packet_rejects_changed_case_input_and_false_task_pass(inputs):
    original, proposal, manifest, cases = inputs
    altered = proposal.cases[0].model_copy(deep=True)
    altered.steps[0].message = "different request"
    changed = replace(proposal, cases=[altered, *proposal.cases[1:]])
    with pytest.raises(ValueError, match="CASE_IDENTITY_MISMATCH"):
        build_packet(original, changed, manifest, cases)
    lines = cases.splitlines()
    first = json.loads(lines[0])
    first["task_status"] = "PASS"
    with pytest.raises(ValueError, match="REPORT_CASES_INVALID"):
        build_packet(
            original, proposal, manifest, b"\n".join([json.dumps(first).encode(), *lines[1:]])
        )
