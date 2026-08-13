from datetime import UTC, datetime, timedelta

import pytest

from app.confirmation.models import ConfirmationAction, ConfirmationRecord, ConfirmationStateError


def action() -> ConfirmationAction:
    return ConfirmationAction(
        tool_id="fitness.training.plan.create_draft.v1",
        organization_id="org-1",
        action="CREATE_TRAINING_DRAFT",
        resource_type="training_plan",
        resource_id=None,
        expected_resource_version=None,
        request_id="request-1",
        payload_hash="a" * 64,
        risk_level="WRITE",
        display_summary={"title": "减脂训练计划"},
        payload_ciphertext=b"encrypted-payload",
        payload_key_version="test-v1",
        thread_id="fitness:thread",
        subject_user_id="student-1",
    )


def record() -> ConfirmationRecord:
    now = datetime.now(UTC)
    return ConfirmationRecord(
        id="confirmation-1",
        protocol_version=1,
        thread_id="fitness:thread",
        subject_user_id="student-1",
        organization_id="org-1",
        tool_id=action().tool_id,
        risk_level="WRITE",
        action=action().action,
        resource_type="training_plan",
        resource_id=None,
        expected_resource_version=None,
        request_id="request-1",
        payload_hash="a" * 64,
        display_summary={"title": "减脂训练计划"},
        payload_ciphertext=b"encrypted-payload",
        payload_key_version="test-v1",
        authorization_status="PENDING",
        execution_status="NOT_STARTED",
        version=0,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def test_confirmation_state_keeps_authorization_and_execution_separate() -> None:
    now = datetime.now(UTC)
    approved = record().approve(now, "decision-1")

    assert approved.authorization_status == "APPROVED"
    assert approved.execution_status == "NOT_STARTED"

    with pytest.raises(ConfirmationStateError):
        approved.claim_execution(now)

    issued = approved.issue_credential("jti-1", now)
    running = issued.claim_execution(now)
    succeeded = running.finish_success(now)

    assert succeeded.execution_status == "SUCCEEDED"
    assert succeeded.credential_consumed_at == now
    with pytest.raises(ConfirmationStateError):
        running.claim_execution(now)


def test_expired_confirmation_cannot_be_approved_or_executed() -> None:
    now = datetime.now(UTC)
    expired = record()._replace(expires_at=now - timedelta(seconds=1))

    with pytest.raises(ConfirmationStateError):
        expired.approve(now, "decision-1")

    with pytest.raises(ConfirmationStateError):
        expired.issue_credential("jti-1", now)

    with pytest.raises(ConfirmationStateError):
        expired.expire(now - timedelta(seconds=2))


def test_retryable_failure_can_be_requeued_without_changing_approval() -> None:
    now = datetime.now(UTC)
    failed = record().approve(now, "decision-1").issue_credential("jti-1", now).claim_execution(now)
    retryable = failed.finish_failure(now, "GATEWAY_TIMEOUT", retryable=True)
    requeued = retryable.requeue_retryable()

    assert requeued.authorization_status == "APPROVED"
    assert requeued.execution_status == "NOT_STARTED"
    assert requeued.credential_jti is None
    assert requeued.credential_consumed_at is None
