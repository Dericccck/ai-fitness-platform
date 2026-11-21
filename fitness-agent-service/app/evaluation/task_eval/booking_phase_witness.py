"""Opt-in source-bound approval-to-MySQL phase fence for disposable trials.

The repository returns only after the PostgreSQL decision transaction commits;
the local phase transition then precedes the resumed business tool. This is a
test harness observation, not independent attestation or release evidence.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.confirmation.repository import ConfirmationRepository

from .audit_observation import matching_final_integrity
from .mysql_restricted_audit import transition_approved


class PhaseWitnessRepository(ConfirmationRepository):
    def __init__(self, database: Any, *, expected_source: dict[str, Any]) -> None:
        super().__init__(database)
        self._expected_source = dict(expected_source)
        self._binding: tuple[str, str, str] | None = None
        self._phase_observation: dict[str, Any] | None = None
        self._phase_lock = asyncio.Lock()

    async def decide(
        self,
        confirmation_id: str,
        subject_user_id: str,
        organization_ids: Any,
        decision: str,
        decision_request_id: str,
        now: Any,
        trace_id: str | None,
        actor_user_id: str,
        actor_roles: Any,
    ) -> Any:
        record = await super().decide(
            confirmation_id,
            subject_user_id,
            organization_ids,
            decision,
            decision_request_id,
            now,
            trace_id,
            actor_user_id,
            actor_roles,
        )
        if decision != "APPROVE":
            return record
        binding = (record.id, record.request_id, decision_request_id)
        if (
            record.id != confirmation_id
            or record.subject_user_id != subject_user_id
            or actor_user_id != subject_user_id
            or record.organization_id != "org-1"
            or record.tool_id != "fitness.booking.create.v1"
            or record.authorization_status != "APPROVED"
            or record.decision_request_id != decision_request_id
            or record.approved_at is None
            or not all(isinstance(value, str) and value for value in binding)
        ):
            raise ValueError("PHASE_APPROVAL_BINDING_INVALID")
        async with self._phase_lock:
            if self._binding is not None:
                if self._binding != binding or self._phase_observation is None:
                    raise ValueError("PHASE_APPROVAL_BINDING_CHANGED")
                return record
            observed = await asyncio.to_thread(transition_approved)
            if (
                observed.get("source") != self._expected_source
                or observed.get("status") != "OBSERVED"
            ):
                raise ValueError("PHASE_OBSERVER_SOURCE_CHANGED")
            self._binding = binding
            self._phase_observation = observed
        return record

    def phase_observation(self) -> dict[str, Any]:
        if self._phase_observation is None:
            return {"status": "UNKNOWN", "reason": "APPROVAL_FENCE_NOT_OBSERVED"}
        observed = dict(self._phase_observation)
        observed["origin"] = "postgres_repository_return_then_disposable_mysql_phase_fence"
        observed["postgres_commit_independently_attested"] = False
        observed["release_evidence"] = False
        return observed


def scoped_phase_diagnostic(
    repository: PhaseWitnessRepository, initial: dict[str, Any], final: dict[str, Any]
) -> dict[str, Any]:
    observed = repository.phase_observation()
    if not matching_final_integrity(initial, final):
        return {"status": "UNKNOWN", "reason": "FINAL_AUDIT_INTEGRITY_UNVERIFIED"}
    if (
        observed.get("status") != "OBSERVED"
        or observed.get("source") != initial.get("source")
        or type(observed.get("pending_phase_events")) is not int
        or observed["pending_phase_events"] < 0
        or observed.get("phase_update_committed") is not True
    ):
        return {"status": "UNKNOWN", "reason": "APPROVAL_FENCE_NOT_VERIFIED"}
    return {
        **observed,
        "phase_result": "NO_PENDING_PHASE_WRITES"
        if observed["pending_phase_events"] == 0
        else "PENDING_PHASE_WRITES_OBSERVED",
        "scope": "guarded_dml_in_one_disposable_mysql_trial",
        "limitations": [
            "PostgreSQL commit, runtime code and isolation are not independently attested.",
            "The phase advances after approval, so a pending-phase write need not precede approval.",
            "Only guarded DML is fenced; external or privileged writers are not independently excluded.",
            "No model, full task or release acceptance is established by this observation.",
        ],
    }
