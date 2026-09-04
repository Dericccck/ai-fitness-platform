from typing import Any

import pytest

from app.core.config import Settings
from app.infrastructure.reranker import RerankerClient


async def test_local_reranker_warmup_runs_minimal_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RerankerClient(
        Settings(
            _env_file=None,
            reranker_backend="local",
            reranker_model_path="/models/bge-reranker-base",
        )
    )
    calls: list[tuple[str, list[str], int | None]] = []

    def record(query: str, documents: list[str], top_n: int | None) -> list[Any]:
        calls.append((query, documents, top_n))
        return []

    monkeypatch.setattr(client, "_rerank_local", record)

    await client.warmup_local_model()

    assert calls == [
        (
            "健身重排模型预热",
            ["健身重排模型预热"],
            1,
        )
    ]


async def test_remote_reranker_warmup_is_noop() -> None:
    client = RerankerClient(Settings(_env_file=None))

    await client.warmup_local_model()
