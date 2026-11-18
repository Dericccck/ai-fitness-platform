"""Find lexical overlap candidates across dataset splits, without publishing case text.

This is a review aid, not a semantic leakage detector or release assessment.
Cases containing unresolved message references are excluded rather than compared
as if their visible text were the complete input.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from typing import Any

from .dataset import Dataset, load_dataset


def _normalize(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum()
    )


def _signature(case: Any) -> tuple[str, ...] | None:
    chats = [step for step in case.steps if step.type == "chat"]
    if not chats or any(step.message_ref or not step.message for step in chats):
        return None
    normalized = tuple(_normalize(step.message) for step in chats)
    return normalized if all(normalized) else None


def _ngrams(signature: tuple[str, ...]) -> set[str]:
    # Separators prevent a n-gram crossing turns from impersonating one message.
    return {
        message[index : index + 3]
        for message in signature
        for index in range(max(0, len(message) - 2))
    }


def audit_split_overlap(dataset: Dataset, *, threshold: float = 0.65) -> dict[str, Any]:
    """Return exact and similar visible-chat pairs; neither is a leakage verdict."""
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not 0 < threshold <= 1
    ):
        raise ValueError("threshold must be in (0, 1]")
    signatures = [(case, _signature(case)) for case in dataset.cases]
    comparable = [(case, sig) for case, sig in signatures if sig is not None]
    dev = [(case, sig) for case, sig in comparable if case.split == "dev"]
    test = [(case, sig) for case, sig in comparable if case.split == "test"]
    pairs: list[dict[str, Any]] = []
    for dev_case, dev_sig in dev:
        for test_case, test_sig in test:
            if dev_sig == test_sig:
                kind, similarity = "normalized_exact", 1.0
            else:
                left, right = _ngrams(dev_sig), _ngrams(test_sig)
                if not left or not right:
                    continue
                similarity = 2 * len(left & right) / (len(left) + len(right))
                if similarity < threshold:
                    continue
                kind = "character_trigram_dice"
            pairs.append(
                {
                    "dev_case_id": dev_case.case_id,
                    "test_case_id": test_case.case_id,
                    "kind": kind,
                    "similarity": round(similarity, 4),
                }
            )
    pairs.sort(key=lambda row: (-row["similarity"], row["dev_case_id"], row["test_case_id"]))
    return {
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": dataset.sha256,
        "threshold": threshold,
        "coverage": {
            split: {
                "total": sum(case.split == split for case, _ in signatures),
                "compared": sum(
                    case.split == split and sig is not None for case, sig in signatures
                ),
                "not_compared": sum(
                    case.split == split and sig is None for case, sig in signatures
                ),
            }
            for split in ("dev", "test")
        },
        "pairs": pairs,
        "limitations": [
            "Candidates require human review; lexical similarity is not a semantic leakage verdict.",
            "Cases without fully resolved chat messages are not compared, and fixtures and labels are not compared.",
            "No result marks a case reviewed or establishes release readiness.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--threshold", type=float, default=0.65)
    args = parser.parse_args()
    print(
        json.dumps(
            audit_split_overlap(load_dataset(args.dataset), threshold=args.threshold),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
