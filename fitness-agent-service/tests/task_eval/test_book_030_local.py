import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.evaluation.task_eval import book_030_local, dataset_local
from app.evaluation.task_eval.dataset import load_dataset


@pytest.fixture
def dataset():
    return load_dataset(Path(__file__).parents[2] / "evals/task_eval/datasets/v1.jsonl")


@pytest.fixture
def probe_report():
    before = {
        "inbox_total": 0,
        "inbox_processed": 0,
        "outbox_total": 0,
        "pending": 0,
        "recipients": None,
    }
    first = {
        "inbox_total": 1,
        "inbox_processed": 1,
        "outbox_total": 2,
        "pending": 2,
        "recipients": ["coach-1", "student-1"],
    }
    final = {**first, "pending": 0}
    empty = {
        "inbox_total": 0,
        "attempts_total": 0,
        "published": 0,
        "recipients": None,
        "matched_delivery": 0,
    }
    delivered = {
        "inbox_total": 2,
        "attempts_total": 2,
        "published": 2,
        "recipients": ["coach-1", "student-1"],
        "matched_delivery": 2,
    }
    case = {
        "case_id": "BOOK-030",
        "component_status": "OBSERVED",
        "task_status": "UNKNOWN",
        "event_source": "java_booking_outbox_publisher_via_disposable_rabbitmq",
        "duplicate_delivery": "same_bytes_twice_via_disposable_rabbitmq_to_python_consumer",
        "event_body_sha256": "a" * 64,
        "delivery_acks": [1, 1],
        "delivery_rejects": [0, 0],
        "mysql_observed_facts_unchanged": True,
        "mysql_before": {"a": 1},
        "mysql_after": {"a": 1},
        "audit_complete": False,
        "first_worker": {"claimed": 1, "processed": 1, "retried": 0},
        "second_worker": {"claimed": 0, "processed": 0, "retried": 0},
        "postgres_before": before,
        "postgres_after_first": first,
        "postgres_after_second": copy.deepcopy(first),
        "java_publisher_outbox": {
            "status": "PUBLISHED",
            "published": True,
            "unclaimed": True,
            "error_free": True,
            "attempt_count": 0,
        },
        "notification_delivery": {
            "channel": "IN_APP",
            "scope": "disposable_postgres_only_no_external_notification_adapter",
            "before": empty,
            "after_delivery": delivered,
            "after_post_delivery_replay": copy.deepcopy(delivered),
            "post_delivery_replay_acks": 1,
            "post_delivery_replay_rejects": 0,
            "event_after_post_delivery_replay": final,
        },
        "limitations": ["synthetic model"],
    }
    return {
        "status": "PASS",
        "scope": "cross_service_component_probe",
        "task_eval_status": "INCONCLUSIVE",
        "origin": "scripted_model_asgi_agent_java_http_real_postgres_mysql",
        "cases": {"duplicate_event": case},
    }


def test_reject_wrong_or_incomplete_evidence(probe_report):
    book_030_local._check_report(probe_report)
    for change in ("origin", "event", "delivery", "boolean", "missing"):
        report = copy.deepcopy(probe_report)
        case = report["cases"]["duplicate_event"]
        if change == "origin":
            report["origin"] = "reused_local_file"
        elif change == "event":
            case["event_source"] = "reconstructed_envelope"
        elif change == "delivery":
            case["notification_delivery"]["after_post_delivery_replay"]["inbox_total"] = 3
        elif change == "boolean":
            case["java_publisher_outbox"]["attempt_count"] = False
        else:
            del case["postgres_after_first"]
        with pytest.raises((ValueError, KeyError)):
            book_030_local._check_report(report)


@pytest.mark.asyncio
async def test_every_observation_starts_launcher_with_new_file(dataset, monkeypatch, probe_report):
    case = next(c for c in dataset.cases if c.case_id == "BOOK-030")
    output_paths = []

    async def launch(*args, **kwargs):
        assert args[2] == "--broker-duplicate-trial"
        path = Path(args[3])
        assert path.is_absolute() and not path.exists()
        path.write_text(json.dumps(probe_report), encoding="utf-8")
        output_paths.append(path)

        class Process:
            async def wait(self):
                return 0

        return Process()

    monkeypatch.setattr(book_030_local.asyncio, "create_subprocess_exec", launch)
    first = await book_030_local.observe(case)
    second = await book_030_local.observe(case)
    assert first["task_status"] == "UNKNOWN"
    assert len(first["report_sha256"]) == 64
    assert len(output_paths) == 2 and output_paths[0] != output_paths[1]
    assert not output_paths[0].exists() and not output_paths[1].exists()
    assert first == second


@pytest.mark.asyncio
async def test_nonzero_exit_and_changed_case_are_rejected(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "BOOK-030")

    async def launch(*args, **kwargs):
        class Process:
            async def wait(self):
                return 1

        return Process()

    monkeypatch.setattr(book_030_local.asyncio, "create_subprocess_exec", launch)
    with pytest.raises(ValueError, match="ISOLATED_PROBE_FAILED"):
        await book_030_local.observe(case)
    modified = case.model_copy(deep=True)
    modified.steps[0].message_ref = "changed"
    with pytest.raises(ValueError, match="CASE_CHANGED"):
        await book_030_local.observe(modified)


@pytest.mark.asyncio
async def test_dataset_opt_in_and_component_error(dataset, monkeypatch):
    case = next(c for c in dataset.cases if c.case_id == "BOOK-030")
    calls = []

    async def broken(current):
        calls.append(current.case_id)
        raise ValueError("private info")

    monkeypatch.setattr(book_030_local, "observe", broken)
    monkeypatch.delenv("TASK_EVAL_BOOK030_BROKER", raising=False)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]))
    assert calls == []
    assert "book030_broker" not in report["cases"][0]["observations"]
    monkeypatch.setenv("TASK_EVAL_BOOK030_BROKER", "1")
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]))
    assert calls == ["BOOK-030"]
    assert report["cases"][0]["task_status"] == "ERROR"
    assert "BOOK030_BROKER_COMPONENT_ERROR" in report["cases"][0]["reasons"]
    assert "private info" not in str(report)


@pytest.mark.asyncio
async def test_dataset_valid_opt_in_remains_inconclusive(dataset, monkeypatch, probe_report):
    case = next(c for c in dataset.cases if c.case_id == "BOOK-030")
    content = json.dumps(probe_report) + "\n"

    async def observed(current):
        assert current.case_id == "BOOK-030"
        return {
            "case_id": "BOOK-030",
            "component_status": "OBSERVED",
            "task_status": "UNKNOWN",
            "model_status": "UNKNOWN",
            "report_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "report_content": content,
            "event_body_sha256": "a" * 64,
            "event_source": "java_booking_outbox_publisher_via_disposable_rabbitmq",
            "notification_scope": "disposable_postgres_only_no_external_notification_adapter",
            "audit_complete": False,
        }

    monkeypatch.setenv("TASK_EVAL_BOOK030_BROKER", "1")
    monkeypatch.setattr(book_030_local, "observe", observed)
    report = await dataset_local.evaluate_dataset(replace(dataset, cases=[case]))
    row = report["cases"][0]
    assert row["task_status"] == "INCONCLUSIVE"
    assert row["observations"]["book030_broker"]["model_status"] == "UNKNOWN"
    assert report["summary"]["component_failures"] == 0
