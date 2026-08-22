import base64
import json

import pytest

from app.infrastructure.agent_context import AgentContextVerifier
from scripts.issue_dev_agent_context import DevContextIssuerError, issue_token, main


def test_issue_token_is_accepted_by_agent_context_verifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.infrastructure.agent_context.time.time", lambda: 1_800_000_000)
    token = issue_token(
        secret="local-context-secret",
        subject="local-admin",
        organization_id="org-demo",
        ttl_seconds=300,
        now=1_800_000_000,
    )

    identity = AgentContextVerifier("local-context-secret").verify(token)

    assert identity.subject == "local-admin"
    assert identity.organization_ids == frozenset({"org-demo"})
    assert identity.roles == frozenset({"ORGANIZATION_ADMIN"})
    assert identity.expires_at == 1_800_000_300


def test_issue_token_contains_only_fixed_local_admin_role() -> None:
    token = issue_token(
        secret="local-context-secret",
        subject="local-admin",
        organization_id="org-demo",
        now=1_800_000_000,
    )
    payload = token.split(".", 1)[0]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))

    assert claims["roles"] == ["ORGANIZATION_ADMIN"]
    assert claims["capabilities"] == []
    assert claims["qualifications"] == []


def test_issue_token_rejects_ttl_above_gateway_limit() -> None:
    with pytest.raises(DevContextIssuerError):
        issue_token(
            secret="local-context-secret",
            subject="local-admin",
            organization_id="org-demo",
            ttl_seconds=301,
        )


def test_cli_requires_explicit_local_dev_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FITNESS_DEV_CONTEXT_ISSUER", raising=False)
    monkeypatch.setattr("sys.argv", ["issue_dev_agent_context.py", "--org-id", "org-demo"])

    assert main() == 2
