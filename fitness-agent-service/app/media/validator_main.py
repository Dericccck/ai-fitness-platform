"""Offline media validator, served only over a private Unix socket.

Mount the upload directory read-only, run without a network, and do not inject
service credentials. Inputs are basenames of files created by the API.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.media.metadata import MediaValidationError, inspect_stream

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_slots = threading.BoundedSemaphore(2)


class InspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filename: str = Field(pattern=r"^fitness-media-[a-z0-9_]{8}\.bin$")
    content_type: Literal["image/jpeg", "image/png", "image/webp", "video/mp4", "video/quicktime"]
    size: int = Field(gt=0, le=100 * 1024 * 1024)
    max_pixels: int = Field(gt=0, le=24_000_000)
    max_duration_seconds: float = Field(ge=2, le=120)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/inspect")
def inspect(payload: InspectRequest) -> dict[str, object]:
    if not _slots.acquire(blocking=False):
        raise HTTPException(503, "validator busy")
    try:
        root = Path(os.environ.get("VALIDATOR_MEDIA_DIR", "/var/fitness-media"))
        path = root / payload.filename
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != payload.size:
                    raise MediaValidationError("媒体大小无效")
                result = inspect_stream(
                    stream,
                    declared_type=payload.content_type,
                    expected_size=payload.size,
                    max_bytes=payload.size,
                    max_pixels=payload.max_pixels,
                    max_duration_seconds=payload.max_duration_seconds,
                )
        except MediaValidationError:
            raise HTTPException(422, "invalid media") from None
        except OSError:
            raise HTTPException(503, "media unavailable") from None
        return {
            "content_type": result.content_type,
            "size": result.size,
            "width": result.width,
            "height": result.height,
            "duration_seconds": result.duration_seconds,
            "sha256": result.sha256,
        }
    finally:
        _slots.release()


if __name__ == "__main__":
    os.umask(0o077)
    socket = os.environ.get("VALIDATOR_SOCKET", "/run/fitness-validator/validator.sock")
    uvicorn.run(app, uds=socket, access_log=False, log_level="warning")
