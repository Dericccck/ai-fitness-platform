import json
from pathlib import Path

from app.agent.operations_comparison_evaluation import main

SERVICE_ROOT = Path(__file__).parents[1]


def test_operations_comparison_evaluation_smoke_cases_pass() -> None:
    assert (
        main(
            [
                "--cases",
                str(SERVICE_ROOT / "evals" / "operations_comparison_smoke.json"),
                "--thresholds",
                str(SERVICE_ROOT / "evals" / "operations_comparison_thresholds.json"),
            ]
        )
        == 0
    )


def test_operations_comparison_evaluation_returns_failure_for_wrong_direction(
    tmp_path: Path,
) -> None:
    cases = [
        {
            "case_id": "wrong-direction",
            "current_from": "2026-08-01",
            "current_to": "2026-08-15",
            "previous_from": "2026-07-17",
            "previous_to": "2026-07-31",
            "current_values": [120],
            "previous_values": [100],
            "expected_current_total": 120,
            "expected_previous_total": 100,
            "expected_delta": 20,
            "expected_direction": "DOWN",
            "expected_change_percent": 20.0,
        }
    ]
    cases_path = tmp_path / "cases.json"
    thresholds_path = tmp_path / "thresholds.json"
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    thresholds_path.write_text(
        json.dumps({"min_pass_rate": 1.0, "max_failed_cases": 0, "max_invalid_cases": 0}),
        encoding="utf-8",
    )

    assert main(["--cases", str(cases_path), "--thresholds", str(thresholds_path)]) == 1
