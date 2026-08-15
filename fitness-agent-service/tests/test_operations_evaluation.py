import json
from pathlib import Path

from app.agent.operations_evaluation import main

SERVICE_ROOT = Path(__file__).parents[1]


def test_operations_trend_evaluation_smoke_cases_pass() -> None:
    assert (
        main(
            [
                "--cases",
                str(SERVICE_ROOT / "evals" / "operations_trend_smoke.json"),
                "--thresholds",
                str(SERVICE_ROOT / "evals" / "operations_trend_thresholds.json"),
            ]
        )
        == 0
    )


def test_operations_trend_evaluation_returns_failure_when_expectation_is_wrong(
    tmp_path: Path,
) -> None:
    cases = [
        {
            "case_id": "wrong-direction",
            "metric": "APPOINTMENT_COUNT",
            "bucket": "DAY",
            "from": "2026-08-01",
            "to": "2026-08-02",
            "rows": [
                {"dimension": "2026-08-01", "label": "2026-08-01", "value": 1},
                {"dimension": "2026-08-02", "label": "2026-08-02", "value": 3},
            ],
            "expected_trend_available": True,
            "expected_direction": "DOWN",
            "expected_series": [
                {"bucket": "2026-08-01", "value": 1},
                {"bucket": "2026-08-02", "value": 3},
            ],
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
