from types import SimpleNamespace

from app.evaluation.task_eval.component_routing import evaluate_routing


def case(case_id, message, allowed):
    return SimpleNamespace(
        case_id=case_id,
        split="dev",
        primary_scenario="routing",
        subcategory="explicit_intent",
        review=SimpleNamespace(status="draft"),
        expected=SimpleNamespace(allowed_routes=allowed),
        steps=[SimpleNamespace(type="chat", message=message)],
    )


def test_uses_actual_router_and_keeps_unlabeled_cases():
    dataset = SimpleNamespace(
        cases=[case("a", "帮我预约一次私教课", ["BOOKING"]), case("b", "任意问题", [])],
        dataset_version="draft",
        sha256="abc",
    )
    report = evaluate_routing(dataset, split="dev")
    assert report["scope"] == "deterministic_first_stage_router_only"
    assert report["counts"] == {"PASS": 1, "FAIL": 0, "SKIPPED": 1, "ERROR": 0}
    assert report["planned_coverage"] == 0.5


def test_all_routing_subcategories_are_accounted_for_without_inventing_labels():
    ambiguous = case("ambiguous", "帮我改一下时间", [])
    ambiguous.subcategory = "clarification"
    multi_intent = case("multi", "先了解训练，再预约", [])
    multi_intent.subcategory = "multi_intent"
    dataset = SimpleNamespace(
        cases=[ambiguous, multi_intent], dataset_version="draft", sha256="abc"
    )
    report = evaluate_routing(dataset, split="dev")
    assert report["counts"] == {"PASS": 0, "FAIL": 0, "SKIPPED": 2, "ERROR": 0}
    assert {row["subcategory"] for row in report["cases"]} == {"clarification", "multi_intent"}
    assert all(row["reason"] == "MISSING_INPUT_OR_ROUTE_LABEL" for row in report["cases"])
