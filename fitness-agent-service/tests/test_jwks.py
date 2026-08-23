import base64

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.infrastructure.jwks import JwksPublicKeyProvider, JwksUnavailableError


def _base64url(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    encoded = value.to_bytes(length, "big")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def test_jwks_provider_parses_rsa_key_and_reuses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    document = {
        "keys": [
            {
                "kid": "rsa-v1",
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "n": _base64url(public_numbers.n),
                "e": _base64url(public_numbers.e),
            }
        ]
    }
    calls = 0

    def fake_get(url: str, timeout: float) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=document, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.infrastructure.jwks.httpx.get", fake_get)
    provider = JwksPublicKeyProvider("https://issuer.test/.well-known/jwks.json")

    assert provider.get_public_key("rsa-v1") is not None
    assert provider.get_public_key("rsa-v1") is not None
    assert calls == 1


def test_jwks_provider_fails_closed_when_refresh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("issuer unavailable")

    monkeypatch.setattr("app.infrastructure.jwks.httpx.get", fake_get)
    provider = JwksPublicKeyProvider("https://issuer.test/.well-known/jwks.json")

    with pytest.raises(JwksUnavailableError, match="JWKS is unavailable"):
        provider.get_public_key("rsa-v1")
