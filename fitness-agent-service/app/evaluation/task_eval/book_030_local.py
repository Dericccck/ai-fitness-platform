"""Opt-in fresh isolated BOOK-030 broker probe; never an end-to-end task pass."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import stat
import tempfile
from pathlib import Path
from typing import Any

from .booking_contracts import matches_frozen_case

_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_LAUNCHER = _SERVICE_ROOT / "scripts/task_eval_cross_service_booking.sh"
_TIMEOUT_SECONDS = 900


def _same(value: Any, expected: Any) -> bool:
    return json.dumps(value, sort_keys=True) == json.dumps(expected, sort_keys=True)


def _read_report(path: Path) -> tuple[dict[str, Any], str, str]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 512 * 1024:
            raise ValueError("INVALID_REPORT_FILE")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(512 * 1024 + 1)
        if len(raw) != metadata.st_size:
            raise ValueError("REPORT_SIZE_CHANGED")
        report = json.loads(raw)
        if not isinstance(report, dict):
            raise TypeError("INVALID_REPORT")
        return report, hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")
    finally:
        os.close(fd)


def _check_report(report: dict[str, Any]) -> dict[str, Any]:
    if (
        report.get("status") != "PASS"
        or report.get("scope") != "cross_service_component_probe"
        or report.get("task_eval_status") != "INCONCLUSIVE"
        or report.get("origin") != "scripted_model_asgi_agent_java_http_real_postgres_mysql"
    ):
        raise ValueError("WRONG_REPORT_ORIGIN")
    case: dict[str, Any] = report["cases"]["duplicate_event"]
    if (
        case["case_id"] != "BOOK-030"
        or case["component_status"] != "OBSERVED"
        or case["task_status"] != "UNKNOWN"
        or case["event_source"] != "java_booking_outbox_publisher_via_disposable_rabbitmq"
        or case["duplicate_delivery"]
        != "same_bytes_twice_via_disposable_rabbitmq_to_python_consumer"
        or not isinstance(case["event_body_sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", case["event_body_sha256"])
        or not _same(case["delivery_acks"], [1, 1])
        or not _same(case["delivery_rejects"], [0, 0])
        or case["mysql_observed_facts_unchanged"] is not True
        or not _same(case["mysql_before"], case["mysql_after"])
        or case["audit_complete"] is not False
        or not _same(case["first_worker"], {"claimed": 1, "processed": 1, "retried": 0})
        or not _same(case["second_worker"], {"claimed": 0, "processed": 0, "retried": 0})
        or not _same(
            case["postgres_before"],
            {
                "inbox_total": 0,
                "inbox_processed": 0,
                "outbox_total": 0,
                "pending": 0,
                "recipients": None,
            },
        )
        or not _same(
            case["postgres_after_first"],
            {
                "inbox_total": 1,
                "inbox_processed": 1,
                "outbox_total": 2,
                "pending": 2,
                "recipients": ["coach-1", "student-1"],
            },
        )
        or not _same(case["postgres_after_first"], case["postgres_after_second"])
    ):
        raise ValueError("DUPLICATE_EVENT_EVIDENCE_MISMATCH")
    publisher = case["java_publisher_outbox"]
    if not _same(
        publisher,
        {
            "status": "PUBLISHED",
            "published": True,
            "unclaimed": True,
            "error_free": True,
            "attempt_count": 0,
        },
    ):
        raise ValueError("PUBLISHER_EVIDENCE_MISMATCH")
    delivery = case["notification_delivery"]
    if (
        delivery["channel"] != "IN_APP"
        or delivery["scope"] != "disposable_postgres_only_no_external_notification_adapter"
        or not _same(delivery["after_delivery"], delivery["after_post_delivery_replay"])
        or not _same(
            delivery["after_delivery"],
            {
                "inbox_total": 2,
                "attempts_total": 2,
                "published": 2,
                "recipients": ["coach-1", "student-1"],
                "matched_delivery": 2,
            },
        )
        or not _same(
            delivery["before"],
            {
                "inbox_total": 0,
                "attempts_total": 0,
                "published": 0,
                "recipients": None,
                "matched_delivery": 0,
            },
        )
        or not _same(delivery["post_delivery_replay_acks"], 1)
        or not _same(delivery["post_delivery_replay_rejects"], 0)
        or not _same(
            delivery["event_after_post_delivery_replay"],
            {
                "inbox_total": 1,
                "inbox_processed": 1,
                "outbox_total": 2,
                "pending": 0,
                "recipients": ["coach-1", "student-1"],
            },
        )
    ):
        raise ValueError("NOTIFICATION_EVIDENCE_MISMATCH")
    return case


async def observe(case: Any) -> dict[str, Any]:
    if case.case_id != "BOOK-030" or not matches_frozen_case(case):
        raise ValueError("CASE_CHANGED")
    with tempfile.TemporaryDirectory(prefix="fitness-eval-book030-") as directory:
        report_path = Path(directory) / "report.json"
        process = await asyncio.create_subprocess_exec(
            "bash",
            str(_LAUNCHER),
            "--broker-duplicate-trial",
            str(report_path),
            cwd=_SERVICE_ROOT,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            code = await asyncio.wait_for(process.wait(), timeout=_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(process.wait()), timeout=30)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        if code != 0:
            raise ValueError("ISOLATED_PROBE_FAILED")
        report, report_sha256, report_content = _read_report(report_path)
        duplicate = _check_report(report)
        return {
            "case_id": case.case_id,
            "component_status": "OBSERVED",
            "task_status": "UNKNOWN",
            "model_status": "UNKNOWN",
            "report_sha256": report_sha256,
            "report_content": report_content,
            "event_body_sha256": duplicate["event_body_sha256"],
            "event_source": duplicate["event_source"],
            "notification_scope": duplicate["notification_delivery"]["scope"],
            "audit_complete": False,
            "limitations": duplicate["limitations"],
        }
