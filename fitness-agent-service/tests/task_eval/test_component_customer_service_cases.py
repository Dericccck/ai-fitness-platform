from dataclasses import replace
from pathlib import Path

from app.agent.routing import deterministic_route_decision
from app.evaluation.task_eval import component_customer_service_cases as probes
from app.evaluation.task_eval.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evals/task_eval/datasets/v1.jsonl"


def test_complaint_guidance_does_not_force_ticket_write():
    report = probes.evaluate_customer_service_case_observations(load_dataset(DATASET), split="dev")
    row = report["cases"][0]
    assert row["component_status"] == "PASS"
    assert row["task_status"] == "UNKNOWN"
    assert row["observed"]["ticket_write_available"] is False
    assert row["observed"]["forced_tool"] is None


def test_complaint_negation_not_routed_as_positive_request():
    assert deterministic_route_decision("我不想投诉") is None


def test_case_change_is_unknown():
    dataset = load_dataset(DATASET)
    case = next(c for c in dataset.cases if c.case_id == "OPER-019")
    changed = case.model_copy(
        update={"expected": case.expected.model_copy(update={"reference_points": ["other"]})}
    )
    report = probes.evaluate_customer_service_case_observations(
        replace(dataset, cases=[changed]), split="dev"
    )
    assert report["cases"][0]["component_status"] == "UNKNOWN"


def test_probe_detects_routing_regression(monkeypatch):
    monkeypatch.setattr(probes, "deterministic_route_decision", lambda message: None)
    report = probes.evaluate_customer_service_case_observations(load_dataset(DATASET), split="dev")
    assert report["cases"][0]["component_status"] == "FAIL"


def test_complaint_negation_and_clause_boundaries():
    for message in ("我不想投诉", "我不要投诉", "我没说我要投诉", "我没有说我要投诉"):
        decision = deterministic_route_decision(message)
        assert decision is None or all(i.intent != "BUSINESS_RULE_QA" for i in decision.intents)
    decision = deterministic_route_decision("我没说我要投诉，后来我要投诉")
    assert decision is not None
    assert [(i.route, i.intent) for i in decision.intents] == [
        ("CUSTOMER_SERVICE", "BUSINESS_RULE_QA")
    ]


def test_complaint_and_booking_preserve_read_before_write_and_confirmation():
    for message in (
        "教练迟到，我要投诉，也帮我预约明天的课",
        "帮我预约明天下午私教课，然后我要投诉",
    ):
        decision = deterministic_route_decision(message)
        assert decision is not None
        assert [(i.route, i.intent) for i in decision.intents] == [
            ("CUSTOMER_SERVICE", "BUSINESS_RULE_QA"),
            ("BOOKING", "CREATE_BOOKING"),
        ]
        assert not decision.intents[0].is_write
        assert decision.intents[1].is_write


def test_negated_complaint_does_not_suppress_independent_booking():
    decision = deterministic_route_decision("我不要投诉，但帮我预约一次")
    assert decision is not None
    assert [(i.route, i.intent) for i in decision.intents] == [("BOOKING", "CREATE_BOOKING")]


def test_explicit_ticket_creation_retains_existing_priority():
    decision = deterministic_route_decision("请提交工单反馈预约状态异常，我要投诉")
    assert decision is not None
    assert [(i.route, i.intent) for i in decision.intents] == [
        ("CUSTOMER_SERVICE", "CREATE_TICKET")
    ]
    assert decision.intents[0].is_write
