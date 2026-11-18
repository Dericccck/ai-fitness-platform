"""Fingerprint the exact draft cases for which local booking fixtures were authored.

A fixture synthesizes missing context and a scripted model; matching only the ID
or message risks silently applying it to a changed actor, assertion or trigger.
Any case edit must be reviewed against the fixture before updating its digest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_DRAFT_CASE_SHA256 = {
    "BOOK-001": "528342793447397659e0e59856143118acf2549f111904a76b11861b4adfb628",
    "BOOK-004": "487117b15effd8a2c196b1d1e9376bf5c29472f4b2b8c92f4e79dfc204ee423a",
    "BOOK-007": "f8702bda28066f0fb5432d0ce35d369219fc30eb37068c101dee9b5bfcec90ef",
    "BOOK-010": "b5f58f01cc51a7f2f95984de964cc71406efdd1f06a2c6a522e4473c5d40c790",
    "BOOK-013": "03be8df3d99ec023c4b984335334497ca9f1504ef38b925369ba56689e04528f",
    "BOOK-017": "d29a28f58a5de9beeab07845b863bfeb84e0b2f4bdabe7af60af8a647a5cb0dd",
    "BOOK-018": "d1caeb972f72a0755854df5232679e686a1196d915ccb66aaea62413e2a754f1",
    "BOOK-026": "4fbe8ea6cb51cd6d00fab620efe6fb5177a37c84cad0c65b832572a1df5fcd89",
    "BOOK-027": "1e77576861e7b1f6a1a3dd9bbfa574580d71217c1f47d2911e2f8499344b60d2",
    "BOOK-028": "1c3ccb2523748f7a254215b163c8c3523279cee2515e59e1d5cbbf6a6d3c95d7",
    "BOOK-029": "4418b00043f59885fa4e1afa76c2eb0bea8c00382f8a28b498baaaea01fc942a",
    "BOOK-031": "4ac10b5203c63171c549d747e04fd9a29c8528e4d89bb941956e64bb23f9ca3a",
    "BOOK-032": "99d5e900b83124ab43031e1cfc086ca6f3aeb8befcf9c8e4d9e5f24a4625885c",
    "BOOK-033": "c05b97c26adbc288642412fb81e8a665a8d7bc35d72f61711851ea4baf22c4fa",
    "BOOK-034": "a7d91daa2e08b30eac113aa855fb3b58b64d6fdfea01fa1124ce7b9d4fe9aafd",
    "BOOK-035": "a53771f59658bb11a13e8a399fd17eccbf35831e691f7d78e9989eca1294f280",
}


def matches_frozen_case(case: Any) -> bool:
    expected = _DRAFT_CASE_SHA256.get(case.case_id)
    if expected is None:
        return False
    serialized = json.dumps(
        case.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest() == expected
