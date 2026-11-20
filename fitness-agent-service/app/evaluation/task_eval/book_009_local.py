"""Fresh private-slot concurrency observation for the exposed draft BOOK-009."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
from pathlib import Path
from typing import Any

from .booking_contracts import matches_frozen_case

_ROOT = Path(__file__).resolve().parents[3]
_LAUNCHER = _ROOT / "scripts/task_eval_mysql_booking_observation.sh"


def _check(raw: bytes) -> dict[str, Any]:
    if len(raw) > 65536:
        raise ValueError("PRIVATE_SLOT_OUTPUT_TOO_LARGE")
    lines = raw.decode("utf-8").splitlines()
    if len(lines) != 2:
        raise ValueError("PRIVATE_SLOT_OUTPUT_INVALID")
    preflight, result = (json.loads(line) for line in lines)
    if (
        not isinstance(preflight, dict)
        or preflight.get("dependency_status") != "VERIFIED_DISPOSABLE_LOCAL_MYSQL"
        or not isinstance(result, dict)
        or set(result)
        != {
            "status",
            "scope",
            "origin",
            "source_sha256",
            "source_attestation",
            "model",
            "gateway_http",
            "audit_complete",
            "task_status",
        }
        or result.get("status") != "PASS"
        or result.get("scope") != "java_private_coach_slot_concurrency"
        or result.get("origin") != "java_booking_service_real_mysql"
        or result.get("source_attestation") != "NOT_INDEPENDENTLY_VERIFIED"
        or result.get("model") != "not_exercised"
        or result.get("gateway_http") != "not_exercised"
        or result.get("audit_complete") is not False
        or result.get("task_status") != "UNKNOWN"
        or not isinstance(result.get("source_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", result["source_sha256"]) is None
    ):
        raise ValueError("PRIVATE_SLOT_OBSERVATION_INVALID")
    return result


async def observe(case: Any) -> dict[str, Any]:
    if case.case_id != "BOOK-009" or case.split != "test" or not matches_frozen_case(case):
        raise ValueError("CASE_CHANGED")
    launcher_sha256 = hashlib.sha256(_LAUNCHER.read_bytes()).hexdigest()
    process = await asyncio.create_subprocess_exec(
        "bash",
        str(_LAUNCHER),
        "--coach-slot-concurrency",
        cwd=_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        raw, _ = await asyncio.wait_for(process.communicate(), timeout=900)
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
    if process.returncode != 0:
        raise ValueError("PRIVATE_SLOT_COMPONENT_FAILED")
    if hashlib.sha256(_LAUNCHER.read_bytes()).hexdigest() != launcher_sha256:
        raise ValueError("PRIVATE_SLOT_LAUNCHER_CHANGED")
    result = _check(raw)
    return {
        "case_id": case.case_id,
        "component_status": "OBSERVED",
        "task_status": "UNKNOWN",
        "source_sha256": result["source_sha256"],
        "launcher_sha256": launcher_sha256,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "scope": "java_booking_service_real_mysql_two_student_private_slot_race",
        "model_status": "UNKNOWN",
        "audit_complete": False,
        "blind_holdout_valid": False,
    }
