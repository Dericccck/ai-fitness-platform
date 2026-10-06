import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    "origin", ["https://*.fitness.test", "https://fitness.test/path", "https://u:p@fitness.test"]
)
def test_auth_rejects_non_exact_origins(origin: str) -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            browser_auth_enabled=True,
            browser_auth_upstream_url="https://issuer.test/sessions",
            browser_auth_upstream_service_token="test-only-token",
            browser_auth_allowed_origins=[origin],
        )


def test_auth_rejects_production_insecure_cookie() -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            environment="production",
            browser_auth_enabled=True,
            browser_auth_upstream_url="https://issuer.test/sessions",
            browser_auth_upstream_service_token="test-only-token",
            browser_auth_allowed_origins=["https://fitness.test"],
            browser_auth_cookie_secure=False,
        )
