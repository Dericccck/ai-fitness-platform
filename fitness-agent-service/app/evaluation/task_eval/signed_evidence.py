"""Verification of independently signed evaluation attestations.

The trust root is a fixed, administrator-owned system file, never a report field,
CLI option, or environment variable. Signatures authenticate delegated statements;
they do not themselves prove reviewer competence, network isolation or time.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROLES = frozenset({"policy", "report", "isolation", "annotator", "reviewer", "evidence"})
_REVIEW_DOMAINS = frozenset({"clinical", "security", "business", "knowledge"})
_RISK_DOMAINS = {
    "unsafe_guidance": "clinical",
    "medical": "clinical",
    "unsafe_training": "clinical",
    "authorization": "security",
    "privacy": "security",
    "cross_tenant": "security",
    "prompt_injection": "security",
    "data_leak": "security",
    "unconfirmed_write": "business",
    "duplicate_charge": "business",
}
_RISK_RULE_DOMAINS = {
    "no_severe_unsafe_guidance": "clinical",
    "authorization_denied_no_leak": "security",
    "no_unconfirmed_business_write": "business",
}
_SAFETY_DOMAINS = {
    "cross_tenant": "security",
    "prompt_injection": "security",
    "unconfirmed_write": "business",
    "unsafe_guidance": "clinical",
}
_HIGH_RISK_DEFAULTS = {
    "routing": "security",
    "booking": "business",
    "operations": "business",
    "knowledge": "knowledge",
}
_TRUST_FILE = Path(
    "/private/etc/fitness-task-eval/release-trust.json"
    if sys.platform == "darwin"
    else "/etc/fitness-task-eval/release-trust.json"
)
_EVIDENCE_DIRECTORY = Path(
    "/private/var/lib/fitness-task-eval/evidence"
    if sys.platform == "darwin"
    else "/var/lib/fitness-task-eval/evidence"
)
_DOMAIN = b"fitness-task-eval-attestation-v1\x00"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def signing_message(role: str, key_id: str, claims: dict[str, Any]) -> bytes:
    """Return domain-separated bytes for an independent signer; never sign here."""
    if (
        role not in _ROLES
        or not isinstance(key_id, str)
        or not key_id
        or not isinstance(claims, dict)
    ):
        raise ValueError("invalid signing request")
    return _DOMAIN + _canonical({"role": role, "key_id": key_id, "claims": claims})


def run_sha256(
    dataset: Any, manifest: dict[str, Any], trials: list[dict[str, Any]], policy: Any
) -> str:
    """Hash the complete semantic dataset, unsigned manifest, trials and policy."""
    from .release_gate import gate_policy_sha256

    return _hash(
        {
            "dataset_sha256": dataset.sha256,
            "dataset_cases": [case.model_dump(mode="json") for case in dataset.cases],
            "manifest": {key: value for key, value in manifest.items() if key != "attestations"},
            "trials": trials,
            "policy_sha256": gate_policy_sha256(policy),
        }
    )


def dataset_semantic_sha256(dataset: Any) -> str:
    """Bind parsed cases and version before execution, not just a claimed file hash."""
    return _hash(
        {
            "dataset_version": dataset.dataset_version,
            "cases": [case.model_dump(mode="json") for case in dataset.cases],
        }
    )


def policy_attestation_request(dataset: Any, policy: Any) -> dict[str, Any]:
    """Prepare the policy authority's request before execution, never sign it."""
    from .release_gate import gate_policy_sha256

    return {
        "role": "policy",
        "claims": {
            "dataset_sha256": dataset.sha256,
            "dataset_semantic_sha256": dataset_semantic_sha256(dataset),
            "policy_sha256": gate_policy_sha256(policy),
            "frozen_before_execution": True,
        },
    }


def attestation_requests(
    dataset: Any, manifest: dict[str, Any], trials: list[dict[str, Any]], policy: Any
) -> list[dict[str, Any]]:
    """Prepare claims for separate signers to inspect; this never signs or approves."""
    run_hash = run_sha256(dataset, manifest, trials, policy)
    requests = [
        {"role": "report", "claims": {"run_sha256": run_hash}},
        {
            "role": "isolation",
            "claims": {
                "run_sha256": run_hash,
                "environment": manifest.get("environment"),
                "isolation_verified": True,
            },
        },
    ]
    for case in dataset.cases:
        if (
            case.split != policy.split
            or case.delivery_scope != "in_scope"
            or case.review.status != "reviewed"
        ):
            continue
        claims = {"case_sha256": _hash(case.model_dump(mode="json")), "decision": "APPROVED"}
        requests.extend(
            [
                {"role": "annotator", "subject": case.review.annotator_ref, "claims": claims},
                {"role": "reviewer", "subject": case.review.reviewer_ref, "claims": claims},
            ]
        )
    cases_by_id = {case.case_id: case for case in dataset.cases}
    for trial in trials:
        for assertion in trial.get("assertions", []):
            if (
                assertion.get("applicable") is True
                and (assertion.get("required") is True or assertion.get("risk"))
                and assertion.get("status") in {"PASS", "FAIL"}
            ):
                requests.append(
                    {
                        "role": "evidence",
                        "claims": {
                            "run_sha256": run_hash,
                            "case_id": trial.get("case_id"),
                            "trial_id": trial.get("trial_id"),
                            "assertion_sha256": _hash(assertion),
                            "evidence_verified": True,
                        },
                    }
                )
                case = cases_by_id.get(trial.get("case_id"))
                if case is not None and _requires_clinical_evidence(case, assertion):
                    requests.append(
                        {
                            "role": "reviewer",
                            "required_review_domain": "clinical",
                            "claims": {
                                "run_sha256": run_hash,
                                "case_id": case.case_id,
                                "trial_id": trial.get("trial_id"),
                                "assertion_sha256": _hash(assertion),
                                "clinical_evidence_reviewed": True,
                            },
                        }
                    )
    return requests


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate trust key")
        result[key] = value
    return result


def _no_macos_acl(fd: int) -> bool:
    """Reject extended ACLs, which may grant writes beyond Unix mode bits.

    On macOS acl_get_fd_np returns ENOENT when no extended ACL exists. Treat
    every other error as untrusted; inspecting only mode bits is insufficient.
    Linux POSIX ACL permissions are constrained by the mode's group mask.
    """
    if sys.platform != "darwin":
        return True
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        get_acl = libc.acl_get_fd_np
        get_acl.argtypes = [ctypes.c_int, ctypes.c_int]
        get_acl.restype = ctypes.c_void_p
        free_acl = libc.acl_free
        free_acl.argtypes = [ctypes.c_void_p]
        free_acl.restype = ctypes.c_int
        ctypes.set_errno(0)
        acl = get_acl(fd, 0x100)  # ACL_TYPE_EXTENDED on Darwin
        if acl:
            free_acl(acl)
            return False
        return ctypes.get_errno() == errno.ENOENT
    except (AttributeError, OSError):
        return False


def _admin_directory(fd: int) -> bool:
    info = os.fstat(fd)
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == 0
        and not stat.S_IMODE(info.st_mode) & 0o022
        and _no_macos_acl(fd)
    )


def _required_review_domains(case: Any) -> frozenset[str] | None:
    """Derive domains solely from frozen case taxonomy; unknown risks block."""
    domains: set[str] = set()
    if case.primary_scenario == "safety":
        domain = _SAFETY_DOMAINS.get(case.subcategory)
        if domain is None:
            return None
        domains.add(domain)
    for assertion in case.expected.assertions:
        if assertion.risk is not None:
            domain = _RISK_DOMAINS.get(assertion.risk)
            if domain is None:
                return None
            domains.add(domain)
        rule_domain = _RISK_RULE_DOMAINS.get(assertion.rule)
        if rule_domain:
            domains.add(rule_domain)
    if case.metadata.risk_level == "high" and not domains:
        default_domain = _HIGH_RISK_DEFAULTS.get(case.primary_scenario)
        if default_domain is None:
            return None
        domains.add(default_domain)
    return frozenset(domains)


def _requires_clinical_evidence(case: Any, assertion: dict[str, Any]) -> bool:
    """Case review alone is insufficient for a medical/unsafe output verdict."""
    risk = assertion.get("risk")
    return (
        (case.primary_scenario == "safety" and case.subcategory == "unsafe_guidance")
        or assertion.get("rule") == "no_severe_unsafe_guidance"
        or (isinstance(risk, str) and _RISK_DOMAINS.get(risk) == "clinical")
    )


def _read_system_trust() -> dict[str, Any]:
    """Open an admin-controlled trust root with descriptor-based no-symlink checks."""
    if os.geteuid() == 0:
        raise ValueError("release evaluator must not run as root")
    parts = _TRUST_FILE.parts
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        if not _admin_directory(fd):
            raise ValueError("trust root directory is not admin controlled")
        for part in parts[1:-1]:
            new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new_fd
            if not _admin_directory(fd):
                raise ValueError("trust directory is not admin controlled")
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) & 0o022
                or not _no_macos_acl(file_fd)
                or info.st_size > 1_000_000
            ):
                raise ValueError("trust file is not admin controlled")
            with os.fdopen(file_fd, "rb", closefd=False) as stream:
                raw = stream.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("trust file too large")
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)
    data = json.loads(raw, object_pairs_hook=_unique_pairs)
    if not isinstance(data, dict):
        raise TypeError("invalid trust root")
    return data


def _verified_content(digest: str) -> bool:
    """Verify bounded bytes in the separately managed content-addressed store."""
    if not _HEX.fullmatch(digest) or os.geteuid() == 0:
        return False
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        if not _admin_directory(fd):
            return False
        for part in _EVIDENCE_DIRECTORY.parts[1:]:
            new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new_fd
            if not _admin_directory(fd):
                return False
        file_fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) & 0o022
                or not _no_macos_acl(file_fd)
                or info.st_size > 10_000_000
            ):
                return False
            hasher = hashlib.sha256()
            size = 0
            with os.fdopen(file_fd, "rb", closefd=False) as stream:
                while chunk := stream.read(65536):
                    size += len(chunk)
                    if size > 10_000_000:
                        return False
                    hasher.update(chunk)
            return size == info.st_size and hasher.hexdigest() == digest
        finally:
            os.close(file_fd)
    except OSError:
        return False
    finally:
        os.close(fd)


class SignedEvidenceVerifier:
    """Verify role-separated signatures against deployment-controlled keys.

    Constructing this class directly is not a public gate capability; the public
    gate creates it only from the fixed admin-owned file. It is testable with a
    locally generated key set but never creates or signs human attestations.
    """

    def __init__(self, trust: dict[str, Any], manifest: dict[str, Any]) -> None:
        if (
            set(trust) != {"schema_version", "allowed_policy_sha256", "keys"}
            or trust["schema_version"] != "1"
        ):
            raise ValueError("invalid trust schema")
        policies, keys = trust["allowed_policy_sha256"], trust["keys"]
        if (
            not isinstance(policies, list)
            or not policies
            or any(not isinstance(x, str) or not _HEX.fullmatch(x) for x in policies)
            or len(set(policies)) != len(policies)
        ):
            raise ValueError("invalid trusted policy set")
        if not isinstance(keys, dict) or not keys:
            raise ValueError("invalid trusted keys")
        principals: dict[str, str] = {}
        public_keys: set[str] = set()
        for key_id, key in keys.items():
            if (
                not isinstance(key_id, str)
                or not key_id
                or not isinstance(key, dict)
                or not {"public_key", "role", "subject", "clinical_reviewer"} <= set(key)
                or not set(key)
                <= {"public_key", "role", "subject", "clinical_reviewer", "review_domains"}
                or not isinstance(key["public_key"], str)
                or not _HEX.fullmatch(key["public_key"])
                or key["role"] not in _ROLES
                or not isinstance(key["subject"], str)
                or not key["subject"]
                or type(key["clinical_reviewer"]) is not bool
                or (key["clinical_reviewer"] and key["role"] != "reviewer")
            ):
                raise ValueError("invalid trusted key")
            domains = key.get("review_domains", [])
            if (
                not isinstance(domains, list)
                or any(not isinstance(item, str) or item not in _REVIEW_DOMAINS for item in domains)
                or len(set(domains)) != len(domains)
                or (domains and key["role"] != "reviewer")
            ):
                raise ValueError("invalid reviewer qualifications")
            subject, role = key["subject"], key["role"]
            if key["public_key"] in public_keys:
                raise ValueError("trusted public keys must be unique")
            public_keys.add(key["public_key"])
            if subject in principals and principals[subject] != role:
                raise ValueError("principal may not hold conflicting gate roles")
            principals[subject] = role
        attestations = manifest.get("attestations", [])
        if not isinstance(attestations, list) or len(attestations) > 10000:
            raise ValueError("invalid attestations")
        self.policies, self.keys, self.attestations = set(policies), keys, attestations
        self.run_sha256: str | None = None

    def _verify(
        self,
        role: str,
        claims: dict[str, Any],
        *,
        subject: str | None = None,
        required_domains: frozenset[str] = frozenset(),
    ) -> bool:
        for item in self.attestations:
            if (
                not isinstance(item, dict)
                or set(item) != {"role", "key_id", "claims", "signature"}
                or item.get("role") != role
                or item.get("claims") != claims
            ):
                continue
            key_id, signature = item.get("key_id"), item.get("signature")
            if (
                not isinstance(key_id, str)
                or not isinstance(signature, str)
                or not re.fullmatch(r"[0-9a-f]{128}", signature)
            ):
                continue
            key = self.keys.get(key_id)
            permitted_domains = set(key.get("review_domains", [])) if key else set()
            if key and key["clinical_reviewer"]:
                permitted_domains.add("clinical")
            if (
                not key
                or key["role"] != role
                or (subject is not None and key["subject"] != subject)
                or not required_domains <= permitted_domains
            ):
                continue
            try:
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(key["public_key"])).verify(
                    bytes.fromhex(signature), signing_message(role, key_id, claims)
                )
                return True
            except (InvalidSignature, ValueError, TypeError):
                continue
        return False

    def verify_report(
        self, dataset: Any, manifest: dict[str, Any], trials: list[dict[str, Any]], policy: Any
    ) -> bool:
        from .release_gate import gate_policy_sha256

        policy_hash = gate_policy_sha256(policy)
        if policy_hash not in self.policies:
            return False
        run_hash = run_sha256(dataset, manifest, trials, policy)
        frozen = {
            "dataset_sha256": dataset.sha256,
            "dataset_semantic_sha256": dataset_semantic_sha256(dataset),
            "policy_sha256": policy_hash,
            "frozen_before_execution": True,
        }
        if not self._verify("policy", frozen) or not self._verify(
            "report", {"run_sha256": run_hash}
        ):
            return False
        self.run_sha256 = run_hash
        return True

    def verify_isolation(self, manifest: dict[str, Any]) -> bool:
        return self.run_sha256 is not None and self._verify(
            "isolation",
            {
                "run_sha256": self.run_sha256,
                "environment": manifest.get("environment"),
                "isolation_verified": True,
            },
        )

    def verify_human_review(self, case: Any) -> bool:
        review = case.review
        if (
            not review.annotator_ref
            or not review.reviewer_ref
            or review.annotator_ref == review.reviewer_ref
        ):
            return False
        claims = {"case_sha256": _hash(case.model_dump(mode="json")), "decision": "APPROVED"}
        required_domains = _required_review_domains(case)
        if required_domains is None:
            return False
        return self._verify("annotator", claims, subject=review.annotator_ref) and self._verify(
            "reviewer", claims, subject=review.reviewer_ref, required_domains=required_domains
        )

    def verify_assertion(self, case: Any, trial: dict[str, Any], assertion: dict[str, Any]) -> bool:
        digests = assertion.get("evidence_content_sha256")
        refs = assertion.get("evidence_ref_sha256")
        if (
            self.run_sha256 is None
            or not isinstance(digests, list)
            or not digests
            or not isinstance(refs, list)
            or len(digests) != len(refs)
            or any(not isinstance(x, str) or not _HEX.fullmatch(x) for x in digests)
            or any(not _verified_content(x) for x in digests)
        ):
            return False
        evidence_verified = self._verify(
            "evidence",
            {
                "run_sha256": self.run_sha256,
                "case_id": case.case_id,
                "trial_id": trial.get("trial_id"),
                "assertion_sha256": _hash(assertion),
                "evidence_verified": True,
            },
        )
        if not evidence_verified:
            return False
        if _requires_clinical_evidence(case, assertion):
            return self._verify(
                "reviewer",
                {
                    "run_sha256": self.run_sha256,
                    "case_id": case.case_id,
                    "trial_id": trial.get("trial_id"),
                    "assertion_sha256": _hash(assertion),
                    "clinical_evidence_reviewed": True,
                },
                required_domains=frozenset({"clinical"}),
            )
        return True


def system_verifier(manifest: dict[str, Any]) -> SignedEvidenceVerifier | None:
    try:
        return SignedEvidenceVerifier(_read_system_trust(), manifest)
    except Exception:  # noqa: BLE001 - no trust config, bad trust, or unsafe path must fail closed
        return None
