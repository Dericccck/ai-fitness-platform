import json
from uuid import uuid4

import httpx
import pytest

from scripts.multimodal_acceptance_check import AcceptanceError, run_check, validate_target


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "https://user:secret@example.org",
        "https://example.org/x",
        "https://example.org/?token=x",
        "file:///tmp/data",
    ],
)
def test_rejects_unsafe_target(url):
    with pytest.raises(AcceptanceError):
        validate_target(url)


def test_accepts_https_or_loopback():
    assert validate_target("https://example.org/") == "https://example.org"
    assert validate_target("http://127.0.0.1:8080/") == "http://127.0.0.1:8080"


@pytest.mark.parametrize("mode", ["movement", "knowledge"])
def test_flow_checks_sources_and_requests_cleanup(tmp_path, mode):
    conversation, upload, asset, deletion = (str(uuid4()) for _ in range(4))
    state = {"run": None, "calls": []}
    sample = tmp_path / "sample.png"
    sample.write_bytes(b"test-media")

    def handler(request):
        path, method = request.url.path, request.method
        state["calls"].append((method, path))
        assert request.headers["Authorization"] == "Bearer test-token"
        if path.endswith("/capabilities"):
            return httpx.Response(200, json={"knowledge_analysis_enabled": True})
        if path.endswith("/conversations") and method == "POST":
            return httpx.Response(201, json={"conversation_id": conversation})
        if path.endswith("/media/uploads") and method == "POST":
            return httpx.Response(
                201,
                json={
                    "upload_id": upload,
                    "asset_id": asset,
                    "upload_mode": "gateway",
                    "upload": None,
                },
            )
        if path.endswith(f"/uploads/{upload}/content"):
            assert request.read() == b"test-media"
            return httpx.Response(200, json={"status": "READY", "asset_id": asset})
        if path.endswith("/runs") and method == "POST":
            body = json.loads(request.read())
            assert body["analysis_mode"] == mode and body["media_ids"] == [asset]
            state["run"] = body["run_id"]
            return httpx.Response(202, json={"run_id": body["run_id"]})
        if path.endswith(f"/runs/{state['run']}"):
            schema = "fitness-knowledge.v1" if mode == "knowledge" else "movement-analysis.v1"
            return httpx.Response(
                200,
                json={
                    "status": "COMPLETED",
                    "response": {
                        "report": {"schema_version": schema, "source_assets": [{"asset_id": asset}]}
                    },
                },
            )
        if path.endswith(f"/conversations/{conversation}/messages"):
            schema = "fitness-knowledge.v1" if mode == "knowledge" else "movement-analysis.v1"
            return httpx.Response(
                200,
                json={"messages": [{"role": "assistant", "content": {"schema_version": schema}}]},
            )
        if path.endswith(f"/media/{asset}") and method == "DELETE":
            return httpx.Response(202, json={"deletion_id": deletion})
        if path.endswith(f"/deletions/{deletion}"):
            return httpx.Response(200, json={"status": "RUNNING", "cleared_once": False})
        if path.endswith(f"/conversations/{conversation}") and method == "DELETE":
            return httpx.Response(202, json={"status": "PENDING"})
        raise AssertionError((method, path))

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://test",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        run_check(client, sample, "image/png", mode, 10)
    assert ("DELETE", f"/api/v1/mobile/agent/media/{asset}") in state["calls"]
    assert ("DELETE", f"/api/v1/mobile/agent/conversations/{conversation}") in state["calls"]


def test_rejects_direct_upload_and_cleans_own_conversation(tmp_path):
    conversation, asset, upload, deletion = (str(uuid4()) for _ in range(4))
    sample = tmp_path / "sample.png"
    sample.write_bytes(b"test")
    calls = []

    def handler(request):
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/capabilities"):
            return httpx.Response(200, json={})
        if path.endswith("/conversations") and request.method == "POST":
            return httpx.Response(201, json={"conversation_id": conversation})
        if path.endswith("/media/uploads"):
            return httpx.Response(
                201,
                json={
                    "upload_mode": "direct",
                    "upload": {"url": "https://example.com"},
                    "asset_id": asset,
                    "upload_id": upload,
                },
            )
        if path.endswith(f"/media/{asset}"):
            return httpx.Response(202, json={"deletion_id": deletion})
        if path.endswith(f"/deletions/{deletion}"):
            return httpx.Response(200, json={"status": "PENDING"})
        if path.endswith(f"/conversations/{conversation}"):
            return httpx.Response(202, json={})
        raise AssertionError(path)

    with (
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://test") as client,
        pytest.raises(AcceptanceError, match="服务端中转"),
    ):
        run_check(client, sample, "image/png", "movement", 10)
    assert ("DELETE", f"/api/v1/mobile/agent/conversations/{conversation}") in calls
    assert not any(method == "PUT" for method, _ in calls)


def test_failed_model_is_not_retried_and_cleanup_is_requested(tmp_path):
    conversation, upload, asset, deletion = (str(uuid4()) for _ in range(4))
    state = {"run": None, "submissions": 0, "deleted": False}
    sample = tmp_path / "sample.png"
    sample.write_bytes(b"test")

    def handler(request):
        path, method = request.url.path, request.method
        if path.endswith("/capabilities"):
            return httpx.Response(200, json={"knowledge_analysis_enabled": True})
        if path.endswith("/conversations") and method == "POST":
            return httpx.Response(201, json={"conversation_id": conversation})
        if path.endswith("/media/uploads"):
            return httpx.Response(
                201,
                json={
                    "upload_id": upload,
                    "asset_id": asset,
                    "upload_mode": "gateway",
                    "upload": None,
                },
            )
        if path.endswith(f"/uploads/{upload}/content"):
            return httpx.Response(200, json={"status": "READY", "asset_id": asset})
        if path.endswith("/runs") and method == "POST":
            state["submissions"] += 1
            state["run"] = json.loads(request.read())["run_id"]
            return httpx.Response(202, json={"run_id": state["run"]})
        if path.endswith(f"/runs/{state['run']}"):
            return httpx.Response(200, json={"status": "FAILED", "error_code": "MODEL_FAILED"})
        if path.endswith(f"/media/{asset}"):
            return httpx.Response(202, json={"deletion_id": deletion})
        if path.endswith(f"/deletions/{deletion}"):
            return httpx.Response(200, json={"status": "PENDING"})
        if path.endswith(f"/conversations/{conversation}"):
            state["deleted"] = True
            return httpx.Response(202, json={})
        raise AssertionError(path)

    with (
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://test") as client,
        pytest.raises(AcceptanceError, match="MODEL_FAILED"),
    ):
        run_check(client, sample, "image/png", "knowledge", 10)
    assert state["submissions"] == 1 and state["deleted"]
