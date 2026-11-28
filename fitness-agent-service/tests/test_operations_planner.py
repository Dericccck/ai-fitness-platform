"""经营分析双模式规划器、Query DSL 契约与确定性贡献度计算单元测试。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.operations_planner import (
    MAX_DIAGNOSIS_STEPS,
    OperationsQueryItem,
    classify_operations_mode,
    compute_operations_contribution_analysis,
    plan_diagnosis_step,
    plan_direct_operations_queries,
)
from app.infrastructure.model_gateway import JsonModelTurn


class FakeJsonPlanner:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def chat_json_with_usage(self, *_: Any, **__: Any) -> JsonModelTurn:
        self.calls += 1
        return JsonModelTurn(self.responses.pop(0), input_tokens=15, output_tokens=10)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("查看本月营收金额按周趋势", "DIRECT_QUERY"),
        ("查看本月预约量，和上月比变化多少", "DIRECT_QUERY"),
        ("本月营收为什么跌了？", "DIAGNOSIS"),
        ("帮我排查一下上周消课量下滑原因", "DIAGNOSIS"),
        ("做一下本月完课量归因分析", "DIAGNOSIS"),
    ],
)
def test_classify_operations_mode(message: str, expected: str) -> None:
    assert classify_operations_mode(message) == expected


def test_query_dsl_forbids_organization_id_and_overlong_range() -> None:
    with pytest.raises(ValidationError):
        OperationsQueryItem.model_validate(
            {
                "organization_id": "attacker-org",
                "metric": "REVENUE_AMOUNT",
                "from": "2026-08-01",
                "to": "2026-08-15",
            }
        )
    with pytest.raises(ValidationError):
        OperationsQueryItem.model_validate(
            {
                "metric": "REVENUE_AMOUNT",
                "from": "2026-01-01",
                "to": "2026-06-01",
            }
        )
    with pytest.raises(ValidationError):
        OperationsQueryItem.model_validate(
            {
                "metric": "APPOINTMENT_STATUS_BREAKDOWN",
                "from": "2026-08-01",
                "to": "2026-08-15",
                "bucket": "DAY",
            }
        )


async def test_direct_query_planner_uses_zero_llm_calls_for_standard_rule_questions() -> None:
    planner = FakeJsonPlanner([])

    result = await plan_direct_operations_queries(
        "查看本月营收按周趋势",
        planner,
        today=date(2026, 8, 15),
    )

    assert result.source == "DETERMINISTIC_RULE"
    assert planner.calls == 0
    assert len(result.queries) == 1
    assert result.queries[0].metric == "REVENUE_AMOUNT"
    assert result.queries[0].bucket == "WEEK"


async def test_direct_query_planner_does_not_short_circuit_mixed_colloquial_multi_metric() -> None:
    """口语指标与标准指标混排时，不能被单指标规则半截短路，必须走受控多指标 DSL 填槽。"""

    dsl = (
        '{"queries":['
        '{"metric":"REVENUE_AMOUNT","from":"2026-08-01","to":"2026-08-31","bucket":"NONE","comparison":"NONE","limit":20},'
        '{"metric":"NEW_CUSTOMER_COUNT","from":"2026-08-01","to":"2026-08-31","bucket":"NONE","comparison":"NONE","limit":20},'
        '{"metric":"COMPLETED_CLASS_COUNT","from":"2026-08-01","to":"2026-08-31","bucket":"NONE","comparison":"NONE","limit":20}'
        '],"needs_clarification":false,"confidence":0.95}'
    )
    planner = FakeJsonPlanner([dsl])

    result = await plan_direct_operations_queries(
        "帮我一起看看2026年8月卖课回款、新客和完课量怎么样",
        planner,
        today=date(2026, 11, 28),
    )

    assert result.source == "LLM_DSL"
    assert planner.calls == 1
    assert [q.metric for q in result.queries] == [
        "REVENUE_AMOUNT",
        "NEW_CUSTOMER_COUNT",
        "COMPLETED_CLASS_COUNT",
    ]


async def test_direct_query_planner_rejects_injected_organization_id_from_llm() -> None:
    malicious = (
        '{"queries":[{"organization_id":"org-other","metric":"REVENUE_AMOUNT",'
        '"from":"2026-08-01","to":"2026-08-14"}],"needs_clarification":false,"confidence":0.99}'
    )
    planner = FakeJsonPlanner([malicious])

    result = await plan_direct_operations_queries(
        "近两周卖课流水怎么样",
        planner,
        today=date(2026, 8, 15),
    )

    assert result.source == "CLARIFICATION"
    assert result.queries == ()
    assert planner.calls == 1


def test_compute_dimension_contributions_calculates_exact_shares() -> None:
    payload = {
        "current": {
            "data": {
                "rows": [
                    {"dimension": "coach-a", "label": "王教练", "value": 12},
                    {"dimension": "coach-b", "label": "李教练", "value": 18},
                ]
            }
        },
        "previous": {
            "data": {
                "rows": [
                    {"dimension": "coach-a", "label": "王教练", "value": 40},
                    {"dimension": "coach-b", "label": "李教练", "value": 30},
                ]
            }
        },
        "comparison": {
            "type": "PREVIOUS_PERIOD",
            "current_total": 30,
            "previous_total": 70,
            "delta": -40,
            "change_percent": -57.14,
            "direction": "DOWN",
        },
    }

    analysis = compute_operations_contribution_analysis("COACH_APPOINTMENT_COUNT", payload)

    assert analysis["causality_status"] == "NOT_ESTABLISHED"
    assert analysis["period_comparison"]["delta"] == -40
    contributors = analysis["dimension_contributions"]
    assert contributors[0]["label"] == "王教练"
    assert contributors[0]["delta"] == -28
    assert contributors[0]["contribution_share_percent"] == 70.0
    assert contributors[1]["label"] == "李教练"
    assert contributors[1]["delta"] == -12
    assert contributors[1]["contribution_share_percent"] == 30.0


async def test_diagnosis_planner_dedupes_repeated_queries_and_enforces_step_limit() -> None:
    repeated = (
        '{"action":"QUERY","rationale":"重复查同一指标",'
        '"queries":[{"metric":"REVENUE_AMOUNT","from":"2026-08-01","to":"2026-08-15",'
        '"bucket":"NONE","comparison":"PREVIOUS_PERIOD","limit":20}]}'
    )
    planner = FakeJsonPlanner([repeated])
    existing_observations = [
        {
            "query": {
                "metric": "REVENUE_AMOUNT",
                "from": "2026-08-01",
                "to": "2026-08-15",
                "bucket": "NONE",
                "comparison": "PREVIOUS_PERIOD",
                "limit": 20,
            },
            "analysis": {"metric": "REVENUE_AMOUNT"},
        }
    ]

    deduped = await plan_diagnosis_step(
        "本月营收为什么跌了",
        existing_observations,
        planner,
        step_index=1,
        today=date(2026, 8, 15),
    )
    assert deduped.action == "SYNTHESIZE"
    assert deduped.queries == ()

    capped = await plan_diagnosis_step(
        "本月营收为什么跌了",
        existing_observations,
        FakeJsonPlanner([]),
        step_index=MAX_DIAGNOSIS_STEPS,
        today=date(2026, 8, 15),
    )
    assert capped.action == "SYNTHESIZE"
    assert capped.queries == ()
