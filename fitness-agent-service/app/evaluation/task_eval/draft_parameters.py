"""Build a reproducible, unreviewed parameter proposal from frozen draft cases.

This produces no observations or judgments. Original planning cases remain unchanged;
proposal identities intentionally differ and are not accepted by frozen-case runners.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .booking_dev_026 import load as load_book_026
from .booking_dev_027 import load as load_book_027
from .booking_dev_fixtures import load_book_018
from .booking_test_008 import load as load_book_008
from .booking_test_030 import load as load_book_030
from .dataset import Dataset, load_dataset
from .knowledge_draft_corpus import CORPUS_SHA256, load_corpus
from .outreach_draft_fixtures import _FIXTURE_SHA256 as OUTREACH_FIXTURE_SHA256
from .outreach_draft_fixtures import load_contracts as load_outreach_contracts

_SOURCE_SHA256 = "1d187f5abca4dab754db29059f9ff95a7897719fde6ce88de6c92448d25ab446"
_CASE_CONTENT_SHA256 = "fb2b0533fe6e0512332f1ec577cdb3e5bcd4bb9efdd0de729f94edf845b1b664"
_PROPOSAL_VERSION = "fitness-task-parameter-proposal-v1"
_RUBRIC = "draft-evidence-rubric-v1"
_RUBRIC_PATH = (
    Path(__file__).resolve().parents[3] / "evals/task_eval/draft_parameter_proposal_v1/RUBRIC.md"
)
_RUBRIC_SHA256 = "c516a43c5ce7ca20b670eb588e490d4a46b780434c63dbc306140cc8bfe5ce71"
_MAPPING = {"0": "FAIL", "1": "UNKNOWN", "2": "PASS"}
_ACTION = "fitness.booking.create.v1"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _booking(payload: dict[str, Any]) -> dict[str, str]:
    """Translate fixed fixture input into the observed API booking representation."""
    result = {
        "organizationId": payload["organization_id"],
        "studentId": payload["student_id"],
        "contractId": payload["contract_id"],
        "coachId": payload["coach_id"],
        "courseId": payload["course_id"],
    }
    for name, source in (("startTime", "start_time"), ("endTime", "end_time")):
        value = datetime.fromisoformat(payload[source])
        if value.utcoffset() is None:
            raise ValueError("fixture time must have a timezone")
        result[name] = value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return result


def build_proposal(dataset: Dataset) -> tuple[bytes, dict[str, Any]]:
    """Return complete draft JSONL and its provenance without changing input cases."""
    if dataset.sha256 != _SOURCE_SHA256 or dataset.dataset_version != "fitness-task-planning-v1":
        raise ValueError("source dataset does not match the frozen planning version")
    if (
        hashlib.sha256(_json_bytes([c.model_dump(mode="json") for c in dataset.cases])).hexdigest()
        != _CASE_CONTENT_SHA256
    ):
        raise ValueError("loaded cases differ from the frozen planning content")
    if hashlib.sha256(_RUBRIC_PATH.read_bytes()).hexdigest() != _RUBRIC_SHA256:
        raise ValueError("draft rubric content has changed")
    cases_by_id = {case.case_id: case for case in dataset.cases}
    if len(dataset.cases) != 120 or any(
        c.source.type != "synthetic"
        or c.review.status != "draft"
        or c.capability_status != "planned"
        for c in dataset.cases
    ):
        raise ValueError("proposal requires all 120 unreviewed synthetic planning cases")
    # These loaders check the exact case and on-disk fixture SHA before providing values.
    fixtures = {
        "BOOK-018": load_book_018(cases_by_id["BOOK-018"]),
        "BOOK-026": load_book_026(cases_by_id["BOOK-026"]),
        "BOOK-027": load_book_027(cases_by_id["BOOK-027"]),
        "BOOK-030": load_book_030(cases_by_id["BOOK-030"]),
        "BOOK-008": load_book_008(cases_by_id["BOOK-008"]),
    }
    fixture_hashes = {
        "BOOK-018": fixtures["BOOK-018"]["sha256"],
        "BOOK-026": fixtures["BOOK-026"]["sha256"],
        "BOOK-027": fixtures["BOOK-027"]["sha256"],
        "BOOK-030": fixtures["BOOK-030"]["sha256"],
        "BOOK-008": "af8d99889743e2ab2fd8d31ed7755103120901dff842b761f3fd52c7393300f2",
    }
    knowledge = load_corpus(dataset)
    outreach = {item["case_id"]: item for item in load_outreach_contracts(dataset)}
    changes: list[dict[str, Any]] = []
    output = []
    for original in dataset.cases:
        case = original.model_copy(deep=True)
        case.dataset_version = _PROPOSAL_VERSION
        case.metadata.annotation_version = "draft-parameter-proposal-v1"
        case.metadata.notes += " Parameter proposal only; no execution evidence, expert approval, or release readiness."
        for assertion in case.expected.assertions:
            proposed: dict[str, Any] = {}
            provenance = ""
            if assertion.rule == "semantic" and assertion.applicable:
                points = assertion.parameters.get("reference_points")
                if (
                    not isinstance(points, list)
                    or not points
                    or not all(isinstance(p, str) and p.strip() for p in points)
                    or any(
                        k in assertion.parameters
                        for k in ("metric_id", "prompt_version", "score_mapping")
                    )
                ):
                    raise ValueError("semantic assertion is not the expected unparameterized draft")
                proposed = {
                    "metric_id": f"{case.case_id}.{assertion.id}.draft-v1",
                    "prompt_version": _RUBRIC,
                    "score_mapping": dict(_MAPPING),
                }
                provenance = "original reference_points and draft rubric"
            elif case.case_id in knowledge and assertion.rule == "required_evidence_recalled":
                proposed = {
                    "answerable": True,
                    "relevant_ids": [knowledge[case.case_id]["document_id"]],
                    "forbidden_ids": [],
                    "k": 1,
                    "min_recall_at_k": 1.0,
                }
                provenance = "synthetic per-case document and draft retrieval policy"
            elif case.case_id in fixtures and assertion.rule in (
                "confirmation_pending_for_actor",
                "confirmation_rejected",
            ):
                payload = fixtures[case.case_id].get(
                    "payload", fixtures[case.case_id].get("first_payload")
                )
                if not isinstance(payload, dict) or not isinstance(payload.get("student_id"), str):
                    raise ValueError("fixture identity missing")
                proposed = {"actor": payload["student_id"], "action": _ACTION}
                provenance = "frozen synthetic fixture and booking tool contract"
            elif (
                case.case_id in {f"OPER-{index:03}" for index in range(9, 14)}
                and assertion.rule == "notification_policy"
            ):
                contract = outreach[case.case_id]
                count = contract["expected_delivery_count"]
                recipient = contract["subject"]
                if (
                    type(count) is not int
                    or count not in (0, 1)
                    or not isinstance(recipient, str)
                    or recipient != f"synthetic-{case.case_id.lower()}-recipient"
                    or contract["contract_status"] != "draft_unreviewed"
                    or contract["eligibility_or_consent_verified"] is not False
                ):
                    raise ValueError("invalid synthetic outreach contract")
                proposed = {"delivery_count": count, "recipient": recipient}
                provenance = "case-bound synthetic outreach draft fixture"
            elif (
                case.case_id in {"OPER-015", "OPER-016"} and assertion.rule == "notification_policy"
            ):
                # These cases explicitly prohibit the particular delivery. Their
                # recipient and complete observation window remain unresolved.
                proposed = {"delivery_count": 0}
                provenance = "original planning reference prohibits delivery"
            elif case.case_id == "BOOK-008" and assertion.rule == "booking_created_once":
                proposed = {
                    "booking": _booking(fixtures["BOOK-008"]["first_payload"]),
                    "credit_delta": -1,
                }
                provenance = "frozen first-candidate fixture and local one-credit protocol"
            if proposed:
                if set(assertion.parameters) & set(proposed):
                    raise ValueError("refusing to overwrite existing assertion parameters")
                assertion.parameters.update(proposed)
                changes.append(
                    {
                        "case_id": case.case_id,
                        "assertion_id": assertion.id,
                        "fields": sorted(proposed),
                        "provenance": provenance,
                        "fixture_sha256": fixture_hashes.get(case.case_id)
                        if provenance.startswith("frozen")
                        else OUTREACH_FIXTURE_SHA256
                        if provenance == "case-bound synthetic outreach draft fixture"
                        else CORPUS_SHA256
                        if provenance == "synthetic per-case document and draft retrieval policy"
                        else None,
                    }
                )
        output.append(_json_bytes(case.model_dump(mode="json")))
    raw = b"\n".join(output) + b"\n"
    return raw, {
        "schema_version": "1.0",
        "status": "draft_unreviewed_not_release_eligible",
        "source_dataset_sha256": dataset.sha256,
        "proposal_dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "proposal_dataset_version": _PROPOSAL_VERSION,
        "rubric_version": _RUBRIC,
        "rubric_sha256": _RUBRIC_SHA256,
        "case_count": len(output),
        "changed_assertions": len(changes),
        "fixture_sha256": fixture_hashes,
        "knowledge_corpus_sha256": CORPUS_SHA256,
        "outreach_fixture_sha256": OUTREACH_FIXTURE_SHA256,
        "limitations": [
            "No model judgments, independent observations, human approvals, or attestations are created.",
            "Knowledge records and relevance are synthetic unreviewed fixtures, not clinical advice or real cancellation policy.",
            "Draft rubric and original reference points require review; field presence does not establish validity.",
            "BOOK-008 and BOOK-030 test fixtures share synthetic identity and booking parameters with development fixtures; split leakage review is required.",
            "Outreach recipients and counts are synthetic unreviewed proposals; the OPER-010 zero count applies only to the immediate local quiet-hours observation window, not to later permitted delivery.",
            "OPER-012 delivery eligibility and consent are not verified; OPER-014–016 remain incomplete and are not established as passing.",
            "Frozen original-case runners cannot execute proposal case identities without explicit integration.",
        ],
        "changes": changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    raw, manifest = build_proposal(load_dataset(args.dataset))
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "cases.jsonl").write_bytes(raw)
    (directory / "manifest.json").write_bytes(_json_bytes(manifest) + b"\n")
    print(
        json.dumps(
            {
                "case_count": manifest["case_count"],
                "changed_assertions": manifest["changed_assertions"],
                "proposal_dataset_sha256": manifest["proposal_dataset_sha256"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
