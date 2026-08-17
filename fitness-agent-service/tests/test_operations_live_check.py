from typing import Any

import pytest

from scripts.operations_live_check import OperationsLiveCheckError, _response_summary


def test_operations_live_check_accepts_completed_real_tool_response() -> None:
    assert _response_summary(
        {
            "route": "OPERATIONS",
            "status": "COMPLETED",
            "tool_steps": 1,
            "answer": "本月净营收为 30000 元。\n按周呈上升趋势。",
        }
    ) == ("OPERATIONS", "COMPLETED", 1, "本月净营收为 30000 元。 按周呈上升趋势。")


@pytest.mark.parametrize(
    ("payload", "error_fragment"),
    [
        (
            {"route": "FITNESS_COACHING", "status": "COMPLETED", "tool_steps": 1, "answer": "x"},
            "未进入 OPERATIONS",
        ),
        (
            {
                "route": "OPERATIONS",
                "status": "CONFIRMATION_REQUIRED",
                "tool_steps": 0,
                "answer": "",
            },
            "未完成",
        ),
        (
            {"route": "OPERATIONS", "status": "COMPLETED", "tool_steps": 0, "answer": "x"},
            "没有完成真实工具调用",
        ),
        (
            {"route": "OPERATIONS", "status": "COMPLETED", "tool_steps": 1, "answer": ""},
            "空的经营分析结果",
        ),
    ],
)
def test_operations_live_check_rejects_unsafe_or_incomplete_response(
    payload: dict[str, Any], error_fragment: str
) -> None:
    with pytest.raises(OperationsLiveCheckError, match=error_fragment):
        _response_summary(payload)
