"""Draft v2 proposals based on the user's clarified booking semantics.

The original data and v1 proposal remain untouched. This does not execute a
business service, verify a reviewer, freeze a test set or authorize release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .dataset import Dataset, load_dataset

SOURCE_SHA256 = "1973d22a5d3811b9e1b48d2e3af4f323c7edfbbaa24c5a389ae53cda5b0459fa"
SOURCE_CONTENT_SHA256 = "f8c559bb22ae89141c0ce5e6d05e605735a3c0c56ed29a2158a1a0415e37f458"
VERSION = "fitness-task-parameter-proposal-v2-private-booking"


def build_proposal(dataset: Dataset) -> tuple[bytes, dict[str, Any]]:
    content = json.dumps(
        [case.model_dump(mode="json") for case in dataset.cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if (
        dataset.sha256 != SOURCE_SHA256
        or len(dataset.cases) != 120
        or hashlib.sha256(content).hexdigest() != SOURCE_CONTENT_SHA256
    ):
        raise ValueError("PROPOSAL_SOURCE_CHANGED")
    output = []
    changed: list[str] = []
    for original in dataset.cases:
        if original.review.status != "draft" or original.capability_status != "planned":
            raise ValueError("PROPOSAL_REVIEW_STATE_CHANGED")
        case = original.model_copy(deep=True)
        case.dataset_version = VERSION
        assertions = {a.id: a for a in case.expected.assertions}
        if case.case_id in {"BOOK-006", "BOOK-009"}:
            task = assertions.get("task_result")
            if task is None or task.rule != "booking_created_once":
                raise ValueError("PROPOSAL_BOOKING_CONTRACT_CHANGED")
            booking = task.parameters.get("booking")
            keys = {
                "organizationId",
                "studentId",
                "contractId",
                "coachId",
                "courseId",
                "startTime",
                "endTime",
            }
            if (
                not isinstance(booking, dict)
                or set(booking) != keys
                or any(not isinstance(booking[key], str) or not booking[key] for key in keys)
            ):
                raise ValueError("PROPOSAL_BOOKING_FIXTURE_INVALID")
            if case.case_id == "BOOK-006":
                assertion = assertions.get("booking_store")
                if assertion is None or assertion.rule != "booking_store_matches":
                    raise ValueError("PROPOSAL_STORE_ASSERTION_CHANGED")
                assertion.rule = "booking_organization_store_matches"
                assertion.parameters = {"booking_store": dict(booking)}
            else:
                assertion = assertions.get("capacity_consumed")
                if assertion is None or assertion.rule != "booking_capacity_consumed_once":
                    raise ValueError("PROPOSAL_SLOT_ASSERTION_CHANGED")
                assertion.rule = "booking_private_slot_consumed_once"
                assertion.parameters = {
                    "slot": {key: booking[key] for key in keys if key != "contractId"}
                }
            changed.append(case.case_id)
        output.append(
            json.dumps(
                case.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    if changed != ["BOOK-006", "BOOK-009"]:
        raise ValueError("PROPOSAL_CASES_MISSING")
    raw = b"\n".join(output) + b"\n"
    return raw, {
        "schema_version": "1.0",
        "source_proposal_sha256": SOURCE_SHA256,
        "source_content_sha256": SOURCE_CONTENT_SHA256,
        "proposal_dataset_version": VERSION,
        "proposal_sha256": hashlib.sha256(raw).hexdigest(),
        "case_count": len(output),
        "changed_cases": changed,
        "business_input": [
            "User stated that each organization corresponds to one store.",
            "User stated BOOK-009 concerns a private-coach appointment slot, not group-class capacity.",
        ],
        "limitations": [
            "Unreviewed proposal only; no case approval, observation, evidence signature or release is created.",
            "A store request still needs independently verified resolution to the organization and a scoped booking observation.",
            "Slot success requires a scoped settled observation of the coach's overlapping appointments; snapshots alone cannot prove transaction settlement.",
            "BOOK-009 belongs to the exposed planning test split, not a confidential or frozen holdout.",
            "Legacy v1 store and capacity assumptions are superseded for these two cases, not retrospectively rewritten.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    raw, manifest = build_proposal(load_dataset(args.source))
    output = Path(args.output_dir)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    (output / "cases.jsonl").write_bytes(raw)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"case_count": manifest["case_count"], "proposal_sha256": manifest["proposal_sha256"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
