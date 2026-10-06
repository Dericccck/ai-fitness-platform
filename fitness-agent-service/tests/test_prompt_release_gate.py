from copy import deepcopy

import pytest

from app.evaluation.prompt_regression import SCORER_VERSION, digest
from scripts.prompt_release_gate import validate


def evidence():
    snapshot = {"model": "test", "prompts": {}, "prompt_version": "v2"}
    dataset = {"cases": [{"id": "safe", "prompt_id": "router", "question": "预约"}]}
    rows = [{"case_id": "safe", "repeat": 0, "failures": []}]
    report = {
        "mode": "live_model_comparison",
        "scorer_version": SCORER_VERSION,
        "passed": True,
        "model": "test",
        "repetitions": 1,
        "candidate_digest": digest(snapshot),
        "dataset_digest": digest(dataset),
        "baseline_digest": "sha256:baseline",
        "changed_prompt_ids": ["router"],
        "results": {"baseline": rows, "candidate": rows},
    }
    return snapshot, dataset, report


def test_release_gate_requires_real_matching_complete_reviewed_evidence():
    snapshot, dataset, report = evidence()
    validate(snapshot, dataset, report, reviewed=True)
    with pytest.raises(ValueError):
        validate(snapshot, dataset, report, reviewed=False)
    for field, bad in (
        ("mode", "dataset_contract"),
        ("candidate_digest", "stale"),
        ("passed", False),
        ("changed_prompt_ids", []),
    ):
        changed = {**report, field: bad}
        with pytest.raises(ValueError):
            validate(snapshot, dataset, changed, reviewed=True)
    incomplete = deepcopy(report)
    incomplete["results"]["candidate"] = []
    with pytest.raises(ValueError):
        validate(snapshot, dataset, incomplete, reviewed=True)
