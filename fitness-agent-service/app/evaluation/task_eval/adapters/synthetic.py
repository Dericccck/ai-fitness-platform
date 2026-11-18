"""Explicit in-memory observations for testing the evaluator itself.

Nothing in this module contacts the Agent or establishes business facts. Evidence is
injected by the caller and all resulting reports are marked synthetic.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class SyntheticAdapter:
    name = "synthetic_in_memory"
    mode = "contract"
    synthetic = True

    def __init__(self, evidence: dict[str, dict[str, dict[str, Any]]] | None = None):
        # case ID -> phase -> evidence. Each trial gets a fresh private copy.
        self._evidence = deepcopy(evidence or {})
        self._current: dict[str, dict[str, Any]] = {}

    def supports(self, case: Any) -> bool:
        return case.case_id in self._evidence

    def start(self, case: Any, trial_id: str) -> None:
        self._current = deepcopy(self._evidence[case.case_id])

    def step(self, step: Any) -> None:
        # No Agent actions are simulated or claimed. Only pre-supplied evidence
        # is exposed to grader contract tests at the named observation phase.
        return None

    def observe(self, phase: str) -> dict[str, Any] | None:
        return deepcopy(self._current.get(phase))

    def close(self) -> None:
        self._current = {}
