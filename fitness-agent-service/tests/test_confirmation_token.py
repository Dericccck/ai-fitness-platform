import base64
import json
from datetime import UTC, datetime, timedelta

from app.confirmation.models import ConfirmationRecord
from app.confirmation.token import ConfirmationTokenIssuer


def approved_record() -> ConfirmationRecord:
    now = datetime.now(UTC)
    return ConfirmationRecord(
        id="confirmation-1",
        protocol_version=1,
        thread_id="thread-1",
        subject_user_id="coach-1",
        organization_id="org-1",
        tool_id="fitness.training.plan.create_draft.v1",
        risk_level="WRITE",
        action="CREATE_TRAINING_DRAFT",
        resource_type="training_plan",
        resource_id=None,
        expected_resource_version=None,
        request_id="request-1",
        payload_hash="a" * 64,
        display_summary={"operation": "创建训练计划草案"},
        payload_ciphertext=b"ciphertext",
        payload_key_version="test-v1",
        authorization_status="APPROVED",
        execution_status="NOT_STARTED",
        version=1,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        approved_at=now,
        actor_roles=("COACH",),
        actor_organization_ids=("org-1",),
    )


def test_token_binds_confirmation_scope_and_jti() -> None:
    token = ConfirmationTokenIssuer("s" * 32, ttl_seconds=120).issue(
        approved_record(),
        resource="org-1:student-1",
        jti="jti-1",
        now=1_000,
    )
    encoded_payload, encoded_signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(encoded_payload + "=="))

    assert payload["confirmation_id"] == "confirmation-1"
    assert payload["tool_id"] == "fitness.training.plan.create_draft.v1"
    assert payload["organization_id"] == "org-1"
    assert payload["payload_hash"] == "a" * 64
    assert payload["jti"] == "jti-1"
    assert payload["resource"] == "org-1:student-1"
    assert payload["exp"] == 1_120
    assert encoded_signature
