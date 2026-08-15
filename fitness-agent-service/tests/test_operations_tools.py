from datetime import date

import pytest

from app.agent.operations_tools import OperationsMetricToolInput


def test_operations_input_only_accepts_metric_catalog() -> None:
    data = OperationsMetricToolInput(
        organization_id="org-1",
        metric="APPOINTMENT_COUNT",
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 15),
    )

    assert data.metric == "APPOINTMENT_COUNT"
    assert data.from_date == date(2026, 8, 1)


def test_operations_input_rejects_sql_like_extra_fields() -> None:
    with pytest.raises(ValueError):
        OperationsMetricToolInput(
            organization_id="org-1",
            metric="APPOINTMENT_COUNT",
            sql="SELECT * FROM appointment",
        )


def test_operations_input_rejects_long_range() -> None:
    with pytest.raises(ValueError):
        OperationsMetricToolInput(
            organization_id="org-1",
            metric="APPOINTMENT_COUNT",
            from_date=date(2026, 1, 1),
            to_date=date(2026, 4, 10),
        )
