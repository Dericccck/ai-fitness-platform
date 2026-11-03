"""确认信封与 HTTP 字节分别绑定，不要求 Java 重算 Python 的 JSON 规范化。"""

from typing import Any

from app.confirmation.normalization import canonical_json_bytes


def gateway_confirmation_body(
    tool_id: str,
    payload: dict[str, Any],
    *,
    contract_version: str = "v1",
    resource_id: str | None = None,
) -> bytes:
    if contract_version not in {"v1", "1"}:
        raise ValueError("不支持该确认 Payload 构造版本")
    if contract_version == "1":
        from fitness_tool_contracts import build_gateway_command

        arguments: dict[str, Any] = {"payload": payload}
        if tool_id in {
            "fitness.training.plan.submit_review.v1",
            "fitness.training.plan.review.v1",
            "fitness.training.plan.publish.v1",
        }:
            if not resource_id:
                raise ValueError("训练动作缺少已绑定的资源")
            arguments = {"plan_id": resource_id}
            if tool_id == "fitness.training.plan.review.v1":
                arguments["payload"] = payload
        if tool_id == "fitness.training.day.record_execution.v1":
            day_id = payload.get("day_id")
            suffix = f":{day_id}" if isinstance(day_id, str) and day_id else ""
            if not suffix or not resource_id or not resource_id.endswith(suffix):
                raise ValueError("训练日动作缺少一致的已绑定资源")
            plan_id = resource_id[: -len(suffix)]
            if not plan_id:
                raise ValueError("训练日动作缺少已绑定的计划")
            arguments = {
                "plan_id": plan_id,
                "day_id": day_id,
                "payload": {"status": payload["status"], "note": payload.get("note")},
            }
        body = build_gateway_command(tool_id, arguments).body
        if body is None:
            raise ValueError("确认写动作必须构造确定的请求体")
        return body
    # 训练日的持久化字段沿用历史 snake_case，Java HTTP 契约使用 dayId。
    # 其他 Gateway 写工具的持久化参数已经来自与执行相同的 Payload builder。
    if tool_id == "fitness.training.day.record_execution.v1":
        payload = training_execution_payload(payload["day_id"], payload)
    return canonical_json_bytes(payload)


def training_execution_payload(day_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"dayId": day_id, "status": payload["status"], "note": payload.get("note")}
