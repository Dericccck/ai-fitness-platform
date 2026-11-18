from typing import Any

import pytest

from app.evaluation.task_eval import booking_signed_identity as signed


async def test_signed_identity_and_confirmation_flow() -> None:
    result = await signed.evaluate_signed_identity()
    assert result["status"] == "PASS", result
    assert all(result["checks"].values())


async def test_invalid_confirmation_signature_cannot_write(monkeypatch: pytest.MonkeyPatch) -> None:
    original = signed.SignedGateway._verify_confirmation

    def corrupt(self: Any, token: str, payload: dict[str, Any]) -> None:
        original(self, token.split(".")[0] + "." + signed._encode(b"bad-signature"), payload)

    monkeypatch.setattr(signed.SignedGateway, "_verify_confirmation", corrupt)
    # The real confirmation API must never report a successful write if the
    # independently checked gateway credential is rejected.
    result = await signed.evaluate_signed_identity()
    assert result["status"] == "FAIL"
    assert result["checks"]["no_write_on_gateway_error"] is True
    assert result["checks"]["owner_approval_succeeded"] is False
