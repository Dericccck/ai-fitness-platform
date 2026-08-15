from pathlib import Path

from app.agent.operations_policy_evaluation import main

SERVICE_ROOT = Path(__file__).parents[1]


def test_operations_policy_evaluation_smoke_cases_pass() -> None:
    assert (
        main(
            [
                "--cases",
                str(SERVICE_ROOT / "evals" / "operations_policy_smoke.json"),
                "--thresholds",
                str(SERVICE_ROOT / "evals" / "operations_policy_thresholds.json"),
            ]
        )
        == 0
    )
