from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import jwks_live_check


def test_dual_jwks_check_validates_both_key_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float]] = []

    class FakeProvider:
        def __init__(self, url: str, *, timeout_seconds: float) -> None:
            calls.append((url, timeout_seconds))

        def get_public_key(self, key_id: str) -> object:
            return SimpleNamespace(key_id=key_id)

    monkeypatch.setattr(jwks_live_check, "JwksPublicKeyProvider", FakeProvider)

    result = jwks_live_check.run_dual_check(
        "https://auth.internal/context-jwks",
        "context-v1",
        "https://auth.internal/confirmation-jwks",
        "confirmation-v1",
        2.0,
    )

    assert result == 0
    assert calls == [
        ("https://auth.internal/context-jwks", 2.0),
        ("https://auth.internal/confirmation-jwks", 2.0),
    ]


def test_dual_jwks_check_requires_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProvider:
        def __init__(self, url: str, *, timeout_seconds: float) -> None:
            del url, timeout_seconds

        def get_public_key(self, key_id: str) -> object:
            del key_id
            return object()

    monkeypatch.setattr(jwks_live_check, "JwksPublicKeyProvider", FakeProvider)

    assert (
        jwks_live_check.run_dual_check(
            "https://auth.internal/context-jwks",
            "context-v1",
            "",
            "confirmation-v1",
            2.0,
        )
        == 2
    )
