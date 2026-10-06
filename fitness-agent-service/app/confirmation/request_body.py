"""确认信封与 HTTP 字节分别绑定，不要求 Java 重算 Python 的 JSON 规范化。"""

from typing import Any

from app.confirmation.normalization import canonical_json_bytes


def gateway_confirmation_body(tool_id: str, payload: dict[str, Any]) -> bytes:
    # 训练日的持久化字段沿用历史 snake_case，Java HTTP 契约使用 dayId。
    # 其他 Gateway 写工具的持久化参数已经来自与执行相同的 Payload builder。
    if tool_id == "fitness.training.day.record_execution.v1":
        payload = training_execution_payload(payload["day_id"], payload)
    return canonical_json_bytes(payload)


def training_execution_payload(day_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"dayId": day_id, "status": payload["status"], "note": payload.get("note")}
