import json

import httpx
import pytest

from scripts.agent_end_to_end_benchmark import BenchmarkConfig, benchmark, validate


@pytest.mark.parametrize(
    "endpoint", ["http://remote.test", "https://u:p@host.test", "https://host.test?q=secret"]
)
def test_rejects_unsafe_token_transport(endpoint: str) -> None:
    with pytest.raises(ValueError):
        validate(BenchmarkConfig(endpoint, "secret"))


@pytest.mark.parametrize("disconnect", [False, True])
@pytest.mark.parametrize("snapshot_sequence", [False, True])
async def test_full_stream_and_disconnect_only_get_recovery(
    disconnect: bool, snapshot_sequence: bool
) -> None:
    methods = []
    answer = "PRIVATE_ANSWER_NOT_FOR_REPORT"

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.headers["x-agent-context"] == "PRIVATE_CONTEXT"
        result = {"answer": answer, "status": "COMPLETED", "request_usage": {"model_calls": 2}}
        if request.method == "GET":
            return httpx.Response(200, json={"status": "COMPLETED", "response": result})
        payload = json.loads(request.content)
        frames = []
        for index, (event, data) in enumerate(
            [
                ("run.started", {}),
                ("answer.delta", {"text": answer}),
                ("run.completed", {"response": result}),
            ],
            start=1,
        ):
            sequence = (
                0 if snapshot_sequence and event in {"run.started", "run.completed"} else index
            )
            body = {"run_id": payload["run_id"], "event": event, "sequence": sequence, "data": data}
            frames.append(f"event: {event}\ndata: {json.dumps(body)}\n\n")
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text="".join(frames)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await benchmark(
            BenchmarkConfig(
                "https://agent.test", "PRIVATE_CONTEXT", samples=1, disconnect_sample=disconnect
            ),
            client=client,
        )
    assert report["passed"] is True
    assert report["results"][0]["recovered"] is disconnect
    assert methods == (["POST", "GET"] if disconnect else ["POST"])
    assert answer not in json.dumps(report) and "PRIVATE_CONTEXT" not in repr(report)


async def test_http_failure_is_sanitized_and_not_reposted() -> None:
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(503, text="PRIVATE_UPSTREAM_ERROR")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await benchmark(
            BenchmarkConfig("https://agent.test", "token", samples=1), client=client
        )
    assert not report["passed"] and methods == ["POST"]
    assert "PRIVATE_UPSTREAM_ERROR" not in repr(report)
