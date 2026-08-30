from scripts.ocr_live_check import (
    OcrLiveCheckError,
    validate_health_response,
    validate_parse_response,
)


def valid_payload() -> dict[str, object]:
    return {
        "contract_version": "ocr-service-v1",
        "media_type": "application/pdf",
        "warnings": [],
        "blocks": [
            {
                "kind": "TEXT",
                "content": "训练前进行动态热身。",
                "source_page": 2,
                "confidence": 0.96,
                "source_region": {"x": 0.1, "y": 0.2, "width": 0.7, "height": 0.3},
            }
        ],
    }


def test_validate_health_response_requires_ready_engine() -> None:
    assert validate_health_response("live", 200, {"status": "UP"}) == "进程可响应"
    assert (
        validate_health_response("ready", 200, {"status": "READY", "engine": "paddle"})
        == "模型已就绪"
    )


def test_validate_parse_response_returns_only_safe_summary() -> None:
    assert validate_parse_response(valid_payload()) == (1, 0.96, 0.96)


def test_validate_parse_response_rejects_missing_audit_evidence() -> None:
    payload = valid_payload()
    block = payload["blocks"][0]
    assert isinstance(block, dict)
    del block["source_region"]

    try:
        validate_parse_response(payload)
    except OcrLiveCheckError as exc:
        assert "source_region" in str(exc)
    else:
        raise AssertionError("缺少来源区域时必须失败关闭")
