"""Only test keys: no assertion here creates a real human or trusted review."""

from copy import deepcopy
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.evaluation.task_eval import signed_evidence as signed
from app.evaluation.task_eval.release_gate import assess_release_gate, gate_policy_sha256
from tests.task_eval.test_release_gate import setup


def signed_fixture():
    dataset, policy, manifest, trials = setup()
    trials[0]["assertions"][0]["evidence_content_sha256"] = ["d" * 64]
    roles = ("policy", "report", "isolation", "annotator", "reviewer", "evidence")
    private = {role: Ed25519PrivateKey.generate() for role in roles}
    subjects = {"annotator": "test-only-a", "reviewer": "test-only-b"}
    trust = {
        "schema_version": "1",
        "allowed_policy_sha256": [gate_policy_sha256(policy)],
        "keys": {
            role: {
                "public_key": key.public_key().public_bytes_raw().hex(),
                "role": role,
                "subject": subjects.get(role, "test-only-" + role),
                "clinical_reviewer": role == "reviewer",
            }
            for role, key in private.items()
        },
    }

    def sign(role, claims):
        body = {"role": role, "key_id": role, "claims": claims}
        return {
            **body,
            "signature": private[role].sign(signed._DOMAIN + signed._canonical(body)).hex(),
        }

    policy_hash = gate_policy_sha256(policy)
    run_hash = signed._hash(
        {
            "dataset_sha256": dataset.sha256,
            "dataset_cases": [case.model_dump(mode="json") for case in dataset.cases],
            "manifest": manifest,
            "trials": trials,
            "policy_sha256": policy_hash,
        }
    )
    review_claims = {
        "case_sha256": signed._hash(dataset.cases[0].model_dump(mode="json")),
        "decision": "APPROVED",
    }
    evidence_claims = {
        "run_sha256": run_hash,
        "case_id": dataset.cases[0].case_id,
        "trial_id": trials[0]["trial_id"],
        "assertion_sha256": signed._hash(trials[0]["assertions"][0]),
        "evidence_verified": True,
    }
    manifest["attestations"] = [
        sign(
            "policy",
            {
                "dataset_sha256": dataset.sha256,
                "dataset_semantic_sha256": signed.dataset_semantic_sha256(dataset),
                "policy_sha256": policy_hash,
                "frozen_before_execution": True,
            },
        ),
        sign("report", {"run_sha256": run_hash}),
        sign(
            "isolation",
            {
                "run_sha256": run_hash,
                "environment": manifest["environment"],
                "isolation_verified": True,
            },
        ),
        sign("annotator", review_claims),
        sign("reviewer", review_claims),
        sign("evidence", evidence_claims),
        sign(
            "reviewer",
            {
                "run_sha256": run_hash,
                "case_id": dataset.cases[0].case_id,
                "trial_id": trials[0]["trial_id"],
                "assertion_sha256": signed._hash(trials[0]["assertions"][0]),
                "clinical_evidence_reviewed": True,
            },
        ),
    ]
    return dataset, policy, manifest, trials, trust


def test_test_signatures_validate_only_with_test_trust_and_full_binding(monkeypatch):
    dataset, policy, manifest, trials, trust = signed_fixture()
    # This patch emulates a deployment trust file and is strictly for this test.
    monkeypatch.setattr(signed, "_read_system_trust", lambda: trust)
    monkeypatch.setattr(signed, "_verified_content", lambda digest: digest == "d" * 64)
    assert assess_release_gate(dataset, manifest, trials, policy)["status"] == "PASS"


@pytest.mark.parametrize(
    "change",
    [
        "missing_signer",
        "alter_signature",
        "alter_trial",
        "alter_manifest",
        "alter_case",
        "untrusted_policy",
        "reviewer_not_clinical",
        "missing_content_digest",
        "untrusted_key",
        "same_principal",
    ],
)
def test_tamper_forgery_wrong_roles_or_missing_evidence_blocks(monkeypatch, change):
    dataset, policy, manifest, trials, trust = signed_fixture()
    if change == "missing_signer":
        manifest["attestations"] = [x for x in manifest["attestations"] if x["role"] != "reviewer"]
    elif change == "alter_signature":
        manifest["attestations"][0]["signature"] = "0" * 128
    elif change == "alter_trial":
        trials[0]["assertions"][0]["status"] = "FAIL"
    elif change == "alter_manifest":
        manifest["environment"] = "other-environment"
    elif change == "alter_case":
        case = dataset.cases[0]
        dataset = replace(dataset, cases=[case.model_copy(update={"family_id": "different"})])
    elif change == "untrusted_policy":
        trust["allowed_policy_sha256"] = ["f" * 64]
    elif change == "reviewer_not_clinical":
        trust["keys"]["reviewer"]["clinical_reviewer"] = False
    elif change == "missing_content_digest":
        trials[0]["assertions"][0].pop("evidence_content_sha256")
    elif change == "untrusted_key":
        manifest["attestations"][-1]["key_id"] = "untrusted"
    else:
        trust["keys"]["reviewer"]["subject"] = trust["keys"]["annotator"]["subject"]
    monkeypatch.setattr(signed, "_read_system_trust", lambda: trust)
    monkeypatch.setattr(signed, "_verified_content", lambda digest: digest == "d" * 64)
    assert assess_release_gate(dataset, manifest, trials, policy)["status"] == "BLOCKED"


def test_signed_claims_without_content_bytes_remain_blocked(monkeypatch):
    dataset, policy, manifest, trials, trust = signed_fixture()
    monkeypatch.setattr(signed, "_read_system_trust", lambda: trust)
    monkeypatch.setattr(signed, "_verified_content", lambda _digest: False)
    result = assess_release_gate(dataset, manifest, trials, policy)
    assert result["status"] == "BLOCKED"
    assert any(reason.startswith("ASSERTION_EVIDENCE_UNVERIFIED") for reason in result["reasons"])


def test_clinical_case_review_does_not_replace_review_of_actual_evidence(monkeypatch):
    dataset, policy, manifest, trials, trust = signed_fixture()
    manifest["attestations"] = [
        item
        for item in manifest["attestations"]
        if not item["claims"].get("clinical_evidence_reviewed")
    ]
    monkeypatch.setattr(signed, "_read_system_trust", lambda: trust)
    monkeypatch.setattr(signed, "_verified_content", lambda digest: digest == "d" * 64)
    result = assess_release_gate(dataset, manifest, trials, policy)
    assert result["status"] == "BLOCKED"
    assert any(reason.startswith("ASSERTION_EVIDENCE_UNVERIFIED") for reason in result["reasons"])


def test_policy_request_is_separate_from_post_run_signing_requests():
    dataset, policy, manifest, trials, _ = signed_fixture()
    policy_request = signed.policy_attestation_request(dataset, policy)
    requests = signed.attestation_requests(dataset, manifest, trials, policy)
    assert policy_request["role"] == "policy"
    assert "policy" not in {request["role"] for request in requests}
    assert {request["role"] for request in requests} == {
        "report",
        "isolation",
        "annotator",
        "reviewer",
        "evidence",
    }
    for request in [policy_request, *requests]:
        assert "signature" not in request


def test_policy_signature_cannot_be_reused_after_case_semantics_change():
    dataset, policy, manifest, trials = setup()
    policy_key = Ed25519PrivateKey.generate()
    report_key = Ed25519PrivateKey.generate()
    keys = {"policy": policy_key, "report": report_key}
    trust = {
        "schema_version": "1",
        "allowed_policy_sha256": [gate_policy_sha256(policy)],
        "keys": {
            role: {
                "public_key": key.public_key().public_bytes_raw().hex(),
                "role": role,
                "subject": role,
                "clinical_reviewer": False,
            }
            for role, key in keys.items()
        },
    }

    def sign(role, claims):
        return {
            "role": role,
            "key_id": role,
            "claims": claims,
            "signature": keys[role].sign(signed.signing_message(role, role, claims)).hex(),
        }

    frozen_claims = signed.policy_attestation_request(dataset, policy)["claims"]
    changed_case = dataset.cases[0].model_copy(update={"family_id": "changed-after-freeze"})
    changed = replace(dataset, cases=[changed_case])
    assert changed.sha256 == dataset.sha256
    manifest["attestations"] = [
        sign("policy", frozen_claims),
        sign("report", {"run_sha256": signed.run_sha256(changed, manifest, trials, policy)}),
    ]
    verifier = signed.SignedEvidenceVerifier(trust, manifest)
    assert not verifier.verify_report(changed, manifest, trials, policy)


def test_content_hash_checks_bytes_not_only_the_claim(tmp_path, monkeypatch):
    import hashlib
    import os

    location = tmp_path / "evidence"
    location.mkdir()
    content = b"synthetic evidence content"
    digest = hashlib.sha256(content).hexdigest()
    evidence = location / digest
    evidence.write_bytes(content)
    actual_fstat = os.fstat

    def simulated_admin_stat(fd):
        value = list(actual_fstat(fd))
        value[0] &= ~0o022
        value[4] = 0
        return os.stat_result(value)

    monkeypatch.setattr(signed, "_EVIDENCE_DIRECTORY", location)
    monkeypatch.setattr(signed.os, "geteuid", lambda: 501)
    monkeypatch.setattr(signed.os, "fstat", simulated_admin_stat)
    assert signed._verified_content(digest)
    evidence.write_bytes(b"changed")
    assert not signed._verified_content(digest)
    evidence.unlink()
    evidence.symlink_to(location / "other")
    assert not signed._verified_content(digest)


def test_macos_acl_check_denies_acl_or_unreadable_acl(tmp_path, monkeypatch):
    import errno
    import os

    class FakeFunction:
        def __init__(self, result, error=0):
            self.result, self.error = result, error

        def __call__(self, *args):
            signed.ctypes.set_errno(self.error)
            return self.result

    class FakeLib:
        def __init__(self, result, error):
            self.acl_get_fd_np = FakeFunction(result, error)
            self.acl_free = FakeFunction(0)

    monkeypatch.setattr(signed.sys, "platform", "darwin")
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        monkeypatch.setattr(signed.ctypes, "CDLL", lambda *a, **k: FakeLib(None, errno.ENOENT))
        assert signed._no_macos_acl(fd)
        monkeypatch.setattr(signed.ctypes, "CDLL", lambda *a, **k: FakeLib(1, 0))
        assert not signed._no_macos_acl(fd)
        monkeypatch.setattr(signed.ctypes, "CDLL", lambda *a, **k: FakeLib(None, errno.EACCES))
        assert not signed._no_macos_acl(fd)
    finally:
        os.close(fd)


def test_trust_and_evidence_reject_acl_on_root_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(signed.os, "geteuid", lambda: 501)
    monkeypatch.setattr(signed, "_no_macos_acl", lambda _fd: False)
    monkeypatch.setattr(signed, "_TRUST_FILE", tmp_path / "trust.json")
    with pytest.raises(ValueError, match="root directory"):
        signed._read_system_trust()
    monkeypatch.setattr(signed, "_EVIDENCE_DIRECTORY", tmp_path)
    assert not signed._verified_content("a" * 64)


def test_public_gate_does_not_accept_self_selected_keys_or_review_claims():
    dataset, policy, manifest, trials, _ = signed_fixture()
    manifest["trust"] = {"my_key": "trusted"}
    manifest["human_review_complete"] = True
    assert assess_release_gate(dataset, manifest, trials, policy)["status"] == "BLOCKED"


def test_trust_file_rejects_root_caller_and_user_writable_location(tmp_path, monkeypatch):
    monkeypatch.setattr(signed.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError):
        signed._read_system_trust()
    monkeypatch.setattr(signed.os, "geteuid", lambda: 999)
    path = tmp_path / "trust.json"
    path.write_text("{}")
    monkeypatch.setattr(signed, "_TRUST_FILE", path)
    with pytest.raises((ValueError, OSError)):
        signed._read_system_trust()


def test_trust_schema_rejects_duplicate_authorization_roles():
    _, _, _, _, trust = signed_fixture()
    trust = deepcopy(trust)
    trust["keys"]["report"]["subject"] = trust["keys"]["isolation"]["subject"]
    with pytest.raises(ValueError):
        signed.SignedEvidenceVerifier(trust, {})


def test_trust_schema_rejects_same_public_key_for_two_people():
    _, _, _, _, trust = signed_fixture()
    trust["keys"]["reviewer"]["public_key"] = trust["keys"]["annotator"]["public_key"]
    with pytest.raises(ValueError):
        signed.SignedEvidenceVerifier(trust, {})


def review_verifier_for(case_id, review_domains, *, clinical=False, extra_risk=None):
    """Generate only temporary test signatures; no real person is represented."""
    from app.evaluation.task_eval.contracts import Assertion, Review
    from app.evaluation.task_eval.dataset import load_dataset
    from tests.task_eval.test_release_gate import DATASET

    dataset = load_dataset(DATASET)
    source = next(case for case in dataset.cases if case.case_id == case_id)
    reviewed = source.model_copy(
        update={
            "review": Review(
                status="reviewed",
                annotator_ref="test-annotator",
                reviewer_ref="test-reviewer",
                reviewed_at="test-date",
                decision_ref="test-decision",
            )
        }
    )
    if extra_risk:
        extra = Assertion(
            id="test-extra", phase="final", rule="semantic", required=True, risk=extra_risk
        )
        expected = reviewed.expected.model_copy(
            update={"assertions": [*reviewed.expected.assertions, extra]}
        )
        reviewed = reviewed.model_copy(update={"expected": expected})
    keys = {role: Ed25519PrivateKey.generate() for role in ("annotator", "reviewer")}
    trust = {
        "schema_version": "1",
        "allowed_policy_sha256": ["a" * 64],
        "keys": {
            role: {
                "public_key": key.public_key().public_bytes_raw().hex(),
                "role": role,
                "subject": "test-" + role,
                "clinical_reviewer": clinical if role == "reviewer" else False,
                **({"review_domains": review_domains} if role == "reviewer" else {}),
            }
            for role, key in keys.items()
        },
    }
    claims = {"case_sha256": signed._hash(reviewed.model_dump(mode="json")), "decision": "APPROVED"}
    attestations = []
    for role, key in keys.items():
        attestations.append(
            {
                "role": role,
                "key_id": role,
                "claims": claims,
                "signature": key.sign(signed.signing_message(role, role, claims)).hex(),
            }
        )
    return reviewed, signed.SignedEvidenceVerifier(trust, {"attestations": attestations})


@pytest.mark.parametrize(
    ("case_id", "domain"),
    [
        ("SAFE-001", "security"),
        ("SAFE-010", "security"),
        ("SAFE-006", "business"),
        ("KNOW-016", "security"),
        ("KNOW-015", "knowledge"),
        ("BOOK-019", "business"),
        ("OPER-009", "business"),
    ],
)
def test_domain_reviewers_accept_appropriate_nonclinical_cases(case_id, domain):
    case, verifier = review_verifier_for(case_id, [domain])
    assert verifier.verify_human_review(case)


@pytest.mark.parametrize(
    ("case_id", "domain"),
    [
        ("SAFE-001", "business"),
        ("SAFE-010", "knowledge"),
        ("SAFE-006", "security"),
        ("SAFE-013", "security"),
        ("SAFE-014", "business"),
        ("SAFE-015", "knowledge"),
    ],
)
def test_inappropriate_specialization_cannot_certify_case(case_id, domain):
    case, verifier = review_verifier_for(case_id, [domain])
    assert not verifier.verify_human_review(case)


@pytest.mark.parametrize("case_id", ["SAFE-013", "SAFE-014", "SAFE-015"])
def test_dangerous_guidance_still_requires_clinical_review(case_id):
    case, verifier = review_verifier_for(case_id, [], clinical=True)
    assert verifier.verify_human_review(case)
    case, verifier = review_verifier_for(case_id, [])
    assert not verifier.verify_human_review(case)


def test_multi_risk_requires_all_domains_and_unknown_risk_fails_closed():
    case, verifier = review_verifier_for(
        "SAFE-013", ["security"], clinical=True, extra_risk="authorization"
    )
    assert verifier.verify_human_review(case)
    case, verifier = review_verifier_for("SAFE-013", [], clinical=True, extra_risk="authorization")
    assert not verifier.verify_human_review(case)
    case, verifier = review_verifier_for(
        "SAFE-013", ["security", "business", "knowledge"], clinical=True, extra_risk="unknown-risk"
    )
    assert not verifier.verify_human_review(case)


@pytest.mark.parametrize(
    "domains", [["security", "security"], ["made-up"], ["security", 1], "security"]
)
def test_invalid_review_qualifications_rejected(domains):
    with pytest.raises(ValueError):
        review_verifier_for("SAFE-001", domains)


def test_correct_signatures_cannot_replace_missing_clinical_safety_rule(monkeypatch):
    """A signed routing verdict is not a clinical verdict, even if risk-tagged."""
    dataset, policy, manifest, trials = setup()
    case = dataset.cases[0]
    assertion = case.expected.assertions[0].model_copy(update={"rule": "route_allowed"})
    dataset = replace(
        dataset,
        cases=[
            case.model_copy(
                update={"expected": case.expected.model_copy(update={"assertions": [assertion]})}
            )
        ],
    )
    trials[0]["assertions"][0].update(rule="route_allowed", evidence_content_sha256=["d" * 64])
    policy = policy.model_copy(update={"metric_coverage": {"route_allowed": 1.0}})
    manifest["gate_policy_sha256"] = gate_policy_sha256(policy)
    roles = ("policy", "report", "isolation", "annotator", "reviewer", "evidence")
    private = {role: Ed25519PrivateKey.generate() for role in roles}
    subjects = {"annotator": "test-only-a", "reviewer": "test-only-b"}
    trust = {
        "schema_version": "1",
        "allowed_policy_sha256": [gate_policy_sha256(policy)],
        "keys": {
            role: {
                "public_key": key.public_key().public_bytes_raw().hex(),
                "role": role,
                "subject": subjects.get(role, "test-only-" + role),
                "clinical_reviewer": role == "reviewer",
            }
            for role, key in private.items()
        },
    }
    requests = [signed.policy_attestation_request(dataset, policy)] + signed.attestation_requests(
        dataset, manifest, trials, policy
    )
    manifest["attestations"] = [
        {
            "role": request["role"],
            "key_id": request["role"],
            "claims": request["claims"],
            "signature": private[request["role"]]
            .sign(signed.signing_message(request["role"], request["role"], request["claims"]))
            .hex(),
        }
        for request in requests
    ]
    monkeypatch.setattr(signed, "_read_system_trust", lambda: trust)
    monkeypatch.setattr(signed, "_verified_content", lambda digest: digest == "d" * 64)
    result = assess_release_gate(dataset, manifest, trials, policy)
    assert result["status"] == "BLOCKED"
    assert "CLINICAL_ASSERTION_MISSING:SAFE-015" in result["reasons"]
    assert "REPORT_PROVENANCE_UNVERIFIED" not in result["reasons"]
    assert "ISOLATION_UNVERIFIED" not in result["reasons"]
    assert not any("EVIDENCE_UNVERIFIED" in reason for reason in result["reasons"])
