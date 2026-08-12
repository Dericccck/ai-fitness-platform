"""Live local security smoke test for the configured ClamAV adapter."""

from __future__ import annotations

import sys

from app.core.config import get_settings

from .safety import ClamAvScanner, DocumentSafetyError

EICAR_TEST_STRING = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def main() -> int:
    """Verify clean and EICAR rejection against a real ClamAV daemon."""

    settings = get_settings()
    if settings.rag_malware_scanner_backend != "clamav":
        print("AGENT_RAG_MALWARE_SCANNER_BACKEND must be clamav")
        return 2
    scanner = ClamAvScanner(
        settings.rag_clamav_host,
        port=settings.rag_clamav_port,
        timeout_seconds=settings.rag_clamav_timeout_seconds,
    )
    clean = scanner.scan("security-check.txt", b"fitness-agent security check")
    if clean.status != "CLEAN":
        print(f"unexpected clean verdict: {clean.status}")
        return 1
    try:
        scanner.scan("eicar.com", EICAR_TEST_STRING.encode("ascii"))
    except DocumentSafetyError as exc:
        if "malware detected" not in str(exc):
            print(f"unexpected EICAR failure: {exc}")
            return 1
    else:
        print("ClamAV did not reject the EICAR test signature")
        return 1
    print("ClamAV live security check passed: clean accepted, EICAR rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
