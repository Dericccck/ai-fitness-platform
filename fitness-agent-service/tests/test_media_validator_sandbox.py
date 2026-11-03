"""Verify the Unix-socket validator using real processes and temporary files."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from app.media.metadata import MediaInspectionUnavailable, MediaValidationError, inspect_stream


def test_sandbox_accepts_valid_image_and_fails_closed(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    socket = Path("/tmp") / f"fitness-validator-{uuid4().hex[:12]}.sock"
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "VALIDATOR_MEDIA_DIR": str(media),
        "VALIDATOR_SOCKET": str(socket),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "app.media.validator_main"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if socket.exists() or process.poll() is not None:
                break
            time.sleep(0.05)
        assert socket.exists() and process.poll() is None
        stream = io.BytesIO()
        Image.new("RGB", (8, 6), "red").save(stream, format="PNG")
        data = stream.getvalue()
        kwargs = {"validator_socket": str(socket), "validator_temp_dir": str(media)}
        result = inspect_stream(
            io.BytesIO(data),
            declared_type="image/png",
            expected_size=len(data),
            max_bytes=len(data),
            **kwargs,
        )
        assert (result.width, result.height, result.content_type) == (8, 6, "image/png")
        with pytest.raises(MediaValidationError):
            inspect_stream(
                io.BytesIO(data),
                declared_type="image/jpeg",
                expected_size=len(data),
                max_bytes=len(data),
                **kwargs,
            )
        assert list(media.iterdir()) == []
        outside = tmp_path / "outside"
        outside.write_bytes(data)
        symlink = media / "fitness-media-12345678.bin"
        symlink.symlink_to(outside)
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=str(socket)), base_url="http://validator"
        ) as client:
            response = client.post(
                "/inspect",
                json={
                    "filename": symlink.name,
                    "content_type": "image/png",
                    "size": len(data),
                    "max_pixels": 24000000,
                    "max_duration_seconds": 120.0,
                },
            )
            assert response.status_code == 503
            response = client.post(
                "/inspect",
                json={
                    "filename": "../outside",
                    "content_type": "image/png",
                    "size": len(data),
                    "max_pixels": 24000000,
                    "max_duration_seconds": 120.0,
                },
            )
            assert response.status_code == 422
        symlink.unlink()
    finally:
        process.terminate()
        process.wait(timeout=10)
        socket.unlink(missing_ok=True)
    with pytest.raises(MediaInspectionUnavailable):
        inspect_stream(
            io.BytesIO(data),
            declared_type="image/png",
            expected_size=len(data),
            max_bytes=len(data),
            **kwargs,
        )
    assert list(media.iterdir()) == []


def test_only_stale_regular_owned_files_are_cleaned(tmp_path: Path) -> None:
    from app.media.metadata import remove_stale_sandbox_files

    old = tmp_path / "fitness-media-12345678.bin"
    recent = tmp_path / "fitness-media-abcdefgh.bin"
    unrelated = tmp_path / "notes.txt"
    old.write_bytes(b"old")
    recent.write_bytes(b"new")
    unrelated.write_bytes(b"keep")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert remove_stale_sandbox_files(str(tmp_path)) == 1
    assert not old.exists() and recent.exists() and unrelated.exists()
