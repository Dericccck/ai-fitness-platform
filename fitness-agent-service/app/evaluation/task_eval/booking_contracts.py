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
    "BOOK-002": "63ca36a756ed89200055dca8e41b5cabef9924e67eb4702e437e2a468a7c4372",
    "BOOK-003": "7a8d53f6039cf5c7c2d89f74cc2484eb8a0351e02f54e33d820eecc2afde135f",
    "BOOK-004": "487117b15effd8a2c196b1d1e9376bf5c29472f4b2b8c92f4e79dfc204ee423a",
    "BOOK-005": "dafd4fb0d5deadf6e31518ef49eb667b031b2e2f20a8cda1d47f149ff2dff236",
    "BOOK-006": "218e1512654cfc5a543a689761b36727c9756e289a9c8761eaa698c75fab290f",
    "BOOK-007": "f8702bda28066f0fb5432d0ce35d369219fc30eb37068c101dee9b5bfcec90ef",
    "BOOK-008": "b965d11214972517ec538f0d2ab1310b9f7f72aea181e09a0fef08925bef7592",
    "BOOK-010": "b5f58f01cc51a7f2f95984de964cc71406efdd1f06a2c6a522e4473c5d40c790",
    "BOOK-011": "f5b2e2a73dae4a519297ba1b6a68ca6c8d9373c747effa311018c312335c9106",
    "BOOK-012": "3f441ab46972271597ee8ff3710a8e080a726229d60cce79a9fc1d2b8fea2484",
    "BOOK-013": "03be8df3d99ec023c4b984335334497ca9f1504ef38b925369ba56689e04528f",
    "BOOK-014": "9e358deea748ec2e5e4b3e64d6e25b7b8bc686f61f0ef6ad6b8b7df56f4c8fbe",
    "BOOK-016": "6f82dcc261a549f16f6c72c8aea2876911c39421b5d583fe55c6306b7bc57c60",
    "BOOK-015": "9cc18e33ad8a648045f35473ed677ac9eeb49b3205c4dc003fd4def1f2030661",
    "BOOK-017": "d29a28f58a5de9beeab07845b863bfeb84e0b2f4bdabe7af60af8a647a5cb0dd",
    "BOOK-018": "d1caeb972f72a0755854df5232679e686a1196d915ccb66aaea62413e2a754f1",
    "BOOK-019": "2b4477f7845a90adbb31dc3c053fd15a5faac887db305ef7221bb19da9d9ff82",
    "BOOK-020": "f4ef68c3d3cb15a6e6393e67e2a051fa9f80deaf6a87f253df46065c5c59d28b",
    "BOOK-021": "f76bd7de73cc6a9ca3cf7cd8d785c3302c6efa3620c46dbcf0dff6ac44278a83",
    "BOOK-023": "94924f98146ca1cdb2c33ec58510c26e61e4e2c027ee7f43f38bec6ae4f67bb8",
    "BOOK-024": "a4dac1c91050fd86b00c0a343a36729b413db3f5c9c78a7da8f6e4bf60508372",
    "BOOK-025": "f56254c73cdee9630ae0834c0dab2b76c42c24825c76af2aabeea9b36393f954",
    "BOOK-026": "4fbe8ea6cb51cd6d00fab620efe6fb5177a37c84cad0c65b832572a1df5fcd89",
    "BOOK-027": "1e77576861e7b1f6a1a3dd9bbfa574580d71217c1f47d2911e2f8499344b60d2",
    "BOOK-028": "1c3ccb2523748f7a254215b163c8c3523279cee2515e59e1d5cbbf6a6d3c95d7",
    "BOOK-029": "4418b00043f59885fa4e1afa76c2eb0bea8c00382f8a28b498baaaea01fc942a",
    "BOOK-030": "2ee78395bad8b1c601effca3b14c28e595632b88fec179e59313edcf6ecbf334",
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
