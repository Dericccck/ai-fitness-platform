from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from app.api.routes import mobile_media
from app.core.config import Settings
from app.infrastructure.agent_context import AgentIdentity
from app.media.repository import MediaAccessError


def test_production_rejects_direct_media_upload():
    settings = Settings.model_construct(
        environment="production",
        multimodal_api_enabled=True,
        multimodal_enabled=True,
        mobile_auth_enabled=True,
        multimodal_upload_mode="direct",
    )
    with pytest.raises(ValueError, match="受控上传网关"):
        settings.validate_multimodal_api()


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, (0, 9, False)),
        ("bytes=2-4", (2, 4, True)),
        ("bytes=8-", (8, 9, True)),
        ("bytes=-3", (7, 9, True)),
        ("bytes=0-99", (0, 9, True)),
    ],
)
def test_range_contract(header, expected):
    assert mobile_media._preview_range(header, 10) == expected


@pytest.mark.parametrize(
    "header", ["bytes=-0", "bytes=10-", "bytes=5-3", "bytes=0-1,3-4", "items=0-1"]
)
def test_invalid_range_contract(header):
    with pytest.raises(ValueError):
        mobile_media._preview_range(header, 10)


async def test_preview_checks_revocation_between_chunks_and_closes_source(monkeypatch):
    identity = AgentIdentity("user-1", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999)
    asset_id = uuid4()
    asset = SimpleNamespace(
        size=2 * 1024 * 1024,
        content_type="video/mp4",
        revision=1,
        final_key="final/a/b",
        final_version="v1",
    )
    revoked = False
    source = io.BytesIO(b"x" * asset.size)

    class Repo:
        async def ready_asset(self, identity, asset_id):
            if revoked:
                raise MediaAccessError("deleted")
            return asset

    class Storage:
        def open_version_range(self, version, start, end):
            assert (start, end) == (0, asset.size - 1)
            return source

    class Auth:
        async def identity(self, ref):
            return identity

    async def auth(request, response):
        return "session", identity

    monkeypatch.setattr(mobile_media, "_auth", auth)
    monkeypatch.setattr(mobile_media, "auth_service", lambda request: Auth())
    request = SimpleNamespace(
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(multimodal_upload_mode="gateway"),
                media_service=SimpleNamespace(repository=Repo(), storage=Storage()),
            )
        ),
    )
    result = await mobile_media.preview_content(asset_id, request, Response())
    assert result.headers["cache-control"] == "private, no-store"
    iterator = result.body_iterator
    assert len(await anext(iterator)) == 1024 * 1024
    revoked = True
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert source.closed


async def test_invalid_range_does_not_open_storage(monkeypatch):
    identity = AgentIdentity("user-1", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999)

    async def auth(request, response):
        return "session", identity

    class Repo:
        async def ready_asset(self, identity, asset_id):
            return SimpleNamespace(size=10)

    monkeypatch.setattr(mobile_media, "_auth", auth)
    request = SimpleNamespace(
        headers={"range": "bytes=15-"},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(multimodal_upload_mode="gateway"),
                media_service=SimpleNamespace(repository=Repo(), storage=None),
            )
        ),
    )
    with pytest.raises(HTTPException) as exc:
        await mobile_media.preview_content(uuid4(), request, Response())
    assert exc.value.status_code == 416 and exc.value.headers["Content-Range"] == "bytes */10"


async def test_partial_preview_rechecks_session_and_returns_range_headers(monkeypatch):
    identity = AgentIdentity("user-1", frozenset({"org"}), frozenset({"STUDENT"}), 1, 9999999999)
    asset = SimpleNamespace(
        size=10, content_type="video/mp4", revision=1, final_key="final/a/b", final_version="v1"
    )
    source = io.BytesIO(b"345")

    class Repo:
        async def ready_asset(self, identity, asset_id):
            return asset

    class Storage:
        def open_version_range(self, version, start, end):
            assert (start, end) == (3, 5)
            return source

    class Auth:
        async def identity(self, ref):
            return None

    async def auth(request, response):
        return "session", identity

    monkeypatch.setattr(mobile_media, "_auth", auth)
    monkeypatch.setattr(mobile_media, "auth_service", lambda request: Auth())
    request = SimpleNamespace(
        headers={"range": "bytes=3-5"},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(multimodal_upload_mode="gateway"),
                media_service=SimpleNamespace(repository=Repo(), storage=Storage()),
            )
        ),
    )
    result = await mobile_media.preview_content(uuid4(), request, Response())
    assert result.status_code == 206 and result.headers["content-range"] == "bytes 3-5/10"
    assert [chunk async for chunk in result.body_iterator] == []
    assert source.closed
