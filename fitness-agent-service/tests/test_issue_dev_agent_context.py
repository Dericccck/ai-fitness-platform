import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.infrastructure.agent_context import AgentContextVerificationError, AgentContextVerifier
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


def test_issue_token_defaults_to_local_organization_admin_role() -> None:
    token = issue_token(
        secret="local-context-secret",
        subject="local-admin",
        organization_id="org-demo",
        now=1_800_000_000,
    )
    payload = token.split(".", 1)[0]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))

    assert claims["roles"] == ["ORGANIZATION_ADMIN"]
    assert claims["alg"] == "HS256"
    assert claims["kid"] == "legacy"
    assert claims["capabilities"] == []
    assert claims["qualifications"] == []


def test_issue_token_supports_allowlisted_student_role() -> None:
    """预约联调可签发真实学员上下文，但不能借此生成系统管理员权限。"""

    token = issue_token(
        secret="local-context-secret",
        subject="student-001",
        organization_id="org-demo",
        role="STUDENT",
        now=1_800_000_000,
    )
    payload = token.split(".", 1)[0]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))

    assert claims["sub"] == "student-001"
    assert claims["roles"] == ["STUDENT"]


def test_issue_token_rejects_role_outside_local_allowlist() -> None:
    with pytest.raises(DevContextIssuerError, match="role 只允许"):
        issue_token(
            secret="local-context-secret",
            subject="system-admin",
            organization_id="org-demo",
            role="SYSTEM_ADMIN",
        )


def test_issue_token_rejects_ttl_above_gateway_limit() -> None:
    with pytest.raises(DevContextIssuerError):
        issue_token(
            secret="local-context-secret",
            subject="local-admin",
            organization_id="org-demo",
            ttl_seconds=301,
        )


def test_agent_verifier_accepts_retired_key_during_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.infrastructure.agent_context.time.time", lambda: 1_800_000_000)
    token = issue_token(
        secret="retired-context-secret",
        subject="local-admin",
        organization_id="org-demo",
        key_id="v1",
        now=1_800_000_000,
    )

    identity = AgentContextVerifier(
        "active-context-secret",
        signing_key_id="v2",
        signing_key_ring={"v1": "retired-context-secret"},
    ).verify(token)

    assert identity.subject == "local-admin"


def test_agent_verifier_rejects_unknown_key_id_without_fallback() -> None:
    token = issue_token(
        secret="active-context-secret",
        subject="local-admin",
        organization_id="org-demo",
        key_id="deleted-key",
        now=1_800_000_000,
    )

    with pytest.raises(AgentContextVerificationError, match="AgentContext 声明无效"):
        AgentContextVerifier("active-context-secret", signing_key_id="v2").verify(token)


def test_agent_verifier_accepts_rs256_with_public_key_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.infrastructure.agent_context.time.time", lambda: 1_800_000_000)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = issue_token(
        secret="issuer-only-secret",
        subject="local-admin",
        organization_id="org-demo",
        key_id="rsa-v1",
        now=1_800_000_000,
    )
    payload_part = token.split(".", 1)[0]
    payload = base64.urlsafe_b64decode(payload_part + "==")
    claims = json.loads(payload)
    claims["alg"] = "RS256"
    payload = json.dumps(claims, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_part = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    signature = private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    rsa_token = (
        payload_part + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )
    public_key_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    identity = AgentContextVerifier(
        "",
        signing_algorithm="RS256",
        signing_key_id="rsa-v1",
        verification_public_key_ring={"rsa-v1": public_key_pem},
    ).verify(rsa_token)

    assert identity.subject == "local-admin"


def test_cli_requires_explicit_local_dev_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FITNESS_DEV_CONTEXT_ISSUER", raising=False)
    monkeypatch.setattr("sys.argv", ["issue_dev_agent_context.py", "--org-id", "org-demo"])

    assert main() == 2
