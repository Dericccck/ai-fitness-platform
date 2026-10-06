import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.confirmation.cipher import AesGcmPayloadCipher, ConfirmationPayloadCipherError
from app.confirmation.service import ConfirmationService
from app.confirmation.token import ConfirmationTokenIssuer
from app.streaming.repository import ChatRunRepository
from tests.test_confirmation_token import approved_record


def test_keyring_is_read_only_explicit_and_never_visible_in_repr() -> None:
    old = AesGcmPayloadCipher(b"1" * 32, "v1")
    rotated = AesGcmPayloadCipher(b"2" * 32, "v2", {"v1": b"1" * 32})
    ciphertext = old.encrypt(b"private", associated_data="record-1")
    assert rotated.decrypt(ciphertext, associated_data="record-1", key_version="v1") == b"private"
    for version in ("v2", "missing", ""):
        with pytest.raises(ConfirmationPayloadCipherError):
            rotated.decrypt(ciphertext, associated_data="record-1", key_version=version)
    with pytest.raises(ConfirmationPayloadCipherError):
        rotated.decrypt(ciphertext, associated_data="record-2", key_version="v1")
    assert "111111" not in repr(rotated) and "222222" not in repr(rotated)
    with pytest.raises(TypeError):
        rotated.decryption_keys["other"] = b"3" * 32
    with pytest.raises(ConfirmationPayloadCipherError):
        AesGcmPayloadCipher(b"2" * 32, "v2", {"v2": b"1" * 32})


def test_keyring_base64_validation_rejects_malformed_key_without_echo() -> None:
    encoded = base64.urlsafe_b64encode(b"2" * 32).decode()
    cipher = AesGcmPayloadCipher.from_base64(
        encoded, "v2", {"v1": base64.urlsafe_b64encode(b"1" * 32).decode()}
    )
    assert cipher.key_version == "v2"
    with pytest.raises(ConfirmationPayloadCipherError) as error:
        AesGcmPayloadCipher.from_base64(encoded, "v2", {"v1": "invalid-SECRET%%%"})
    assert "SECRET" not in str(error.value)


async def test_old_approved_confirmation_decrypts_and_issues_new_body_bound_token() -> None:
    from dataclasses import replace

    from app.infrastructure.agent_context import AgentIdentity

    old = AesGcmPayloadCipher(b"1" * 32, "v1")
    payload = {"organizationId": "org-1", "studentId": "student-1", "title": "健身🏋️"}
    record = replace(
        approved_record(),
        payload_key_version="v1",
        credential_jti="jti-1",
        payload_ciphertext=old.encrypt(json.dumps(payload).encode(), associated_data="a" * 64),
    )
    repo = SimpleNamespace(
        get_for_subject=AsyncMock(return_value=record),
        claim_execution=AsyncMock(return_value=record),
    )
    service = ConfirmationService(
        repo,
        None,
        None,
        AesGcmPayloadCipher(b"2" * 32, "v2", {"v1": b"1" * 32}),
        ConfirmationTokenIssuer("s" * 32),
    )
    result = await service.prepare_execution(
        record.id,
        identity=AgentIdentity("coach-1", frozenset({"org-1"}), frozenset({"COACH"}), 0, 1),
        trace_id=None,
    )
    assert result.tool_input == payload
    claims = json.loads(base64.urlsafe_b64decode(result.confirmation_token.split(".")[0] + "=="))
    import hashlib

    from app.confirmation.request_body import gateway_confirmation_body

    assert (
        claims["request_body_sha256"]
        == hashlib.sha256(gateway_confirmation_body(record.tool_id, payload)).hexdigest()
    )
    assert claims["payload_hash"] == record.payload_hash


def test_old_chat_result_decrypts_using_record_version() -> None:
    old = AesGcmPayloadCipher(b"1" * 32, "v1")
    cipher = old.encrypt(b'{"answer":"private"}', associated_data="chat-run:run:owner")
    repo = ChatRunRepository(
        None,
        AesGcmPayloadCipher(b"2" * 32, "v2", {"v1": b"1" * 32}),
        timeout=240,
        retention_hours=24,
    )
    row = {
        "run_id": "run",
        "owner_hash": "owner",
        "status": "COMPLETED",
        "error_code": None,
        "key_version": "v1",
        "result_ciphertext": cipher,
    }
    assert repo._decode(row).result == {"answer": "private"}
    row["key_version"] = "unknown"
    with pytest.raises(ConfirmationPayloadCipherError):
        repo._decode(row)
