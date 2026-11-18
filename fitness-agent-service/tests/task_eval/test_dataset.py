import json
from pathlib import Path

import pytest

from app.evaluation.task_eval.dataset import DatasetValidationError, load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def _write(tmp_path, rows):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def _rows():
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]


def test_planned_dataset_has_required_counts_and_is_not_frozen():
    dataset = load_dataset(DATASET)
    assert len(dataset.cases) == 120
    assert dataset.coverage["dev"]["cases"] == 80
    assert dataset.coverage["test"]["cases"] == 40
    assert len(dataset.sha256) == 64
    assert all(
        c.review.status == "draft" and c.capability_status == "planned" for c in dataset.cases
    )
    assert any("not a frozen" in w for w in dataset.warnings)
    with pytest.raises(DatasetValidationError, match="not reviewed"):
        load_dataset(DATASET, require_reviewed_test=True)


def test_duplicate_and_family_leak_are_rejected(tmp_path):
    rows = _rows()
    rows[1]["case_id"] = rows[0]["case_id"]
    rows[1]["family_id"] = rows[0]["family_id"]
    rows[1]["split"] = "test"
    with pytest.raises(DatasetValidationError) as error:
        load_dataset(_write(tmp_path, rows), check_quotas=False)
    assert "duplicate case_id" in str(error.value)
    assert "crosses splits" in str(error.value)


def test_unknown_fields_invalid_steps_and_missing_assertion_phase_rejected(tmp_path):
    rows = _rows()
    rows[0]["unknown_field"] = 1
    rows[1]["steps"][0]["type"] = "imaginary"
    rows[2]["expected"]["assertions"][0]["phase"] = "missing"
    with pytest.raises(DatasetValidationError) as error:
        load_dataset(_write(tmp_path, rows), check_quotas=False)
    assert "unknown_field" in str(error.value)
    assert "imaginary" in str(error.value)
    assert "no corresponding assert step" in str(error.value)


def test_quota_and_unsubstantiated_review_are_rejected(tmp_path):
    rows = _rows()
    rows.pop()
    with pytest.raises(DatasetValidationError, match="expected 5, got 4"):
        load_dataset(_write(tmp_path, rows))
    rows = _rows()
    rows[0]["review"] = {"status": "reviewed"}
    with pytest.raises(DatasetValidationError, match="require actual annotation"):
        load_dataset(_write(tmp_path, rows), check_quotas=False)


def test_booking_message_references_have_planned_fixture_templates():
    dataset = load_dataset(DATASET)
    fixture_path = DATASET.parents[1] / "fixtures/planning-v1.json"
    fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
    by_id = {fixture["id"]: fixture for fixture in fixtures["fixtures"]}
    for case in dataset.cases:
        for step in case.steps:
            if step.message:
                assert "fixture" not in step.message.lower()
            if step.message_ref:
                definition = by_id[case.fixture.id]["message_refs"][step.message_ref]
                assert set(definition["required_values"]) == {
                    "course",
                    "coach",
                    "absolute_datetime",
                }
                assert all(
                    "{" + key + "}" in definition["template"]
                    for key in definition["required_values"]
                )
