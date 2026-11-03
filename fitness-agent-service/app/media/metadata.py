"""有界检查原始媒体；只读取元数据，不抽帧、不转码。"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, BinaryIO

from PIL import Image, UnidentifiedImageError


class MediaValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MediaMetadata:
    content_type: str
    size: int
    width: int
    height: int
    duration_seconds: float | None
    sha256: str


def _probe_video(path: Path, *, timeout: float, binary: str) -> dict[str, Any]:
    """仅读取限定字段，限制输出、执行时间及可使用的FFmpeg协议。"""
    cmd = [
        binary,
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        "mov",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        str(path),
    ]
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise MediaValidationError("视频元数据检查不可用") from exc
    data = bytearray()
    deadline = monotonic() + timeout
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise MediaValidationError("视频元数据检查超时")
                if not selector.select(remaining):
                    raise MediaValidationError("视频元数据检查超时")
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > 131072:
                    raise MediaValidationError("视频元数据过大")
        if process.wait(timeout=max(deadline - monotonic(), 0.01)) != 0:
            raise MediaValidationError("视频格式无效")
        result = json.loads(data)
        if not isinstance(result, dict):
            raise MediaValidationError("视频元数据无效")
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, MediaValidationError):
            raise
        raise MediaValidationError("视频元数据无效") from exc
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout:
            process.stdout.close()


def inspect_stream(
    stream: BinaryIO,
    *,
    declared_type: str,
    expected_size: int,
    max_bytes: int,
    max_pixels: int = 24_000_000,
    max_duration_seconds: float = 120,
    ffprobe_binary: str = "ffprobe",
    probe_timeout_seconds: float = 10,
    validator_socket: str = "",
    validator_temp_dir: str = "",
) -> MediaMetadata:
    """从已固定版本的OSS流读取有限字节，并在退出时删除临时文件。"""
    if not 0 < expected_size <= max_bytes <= 100 * 1024 * 1024:
        raise MediaValidationError("媒体大小无效或超过限制")
    if declared_type not in {
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "video/quicktime",
    }:
        raise MediaValidationError("媒体类型不受支持")
    digest = hashlib.sha256()
    size = 0
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="fitness-media-", suffix=".bin", delete=False, dir=validator_temp_dir or None
        ) as output:
            path = Path(output.name)
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes or size > expected_size:
                    raise MediaValidationError("媒体文件超过声明大小")
                digest.update(chunk)
                output.write(chunk)
        if size != expected_size:
            raise MediaValidationError("媒体文件不完整")
        if validator_socket:
            return _inspect_in_sandbox(
                path,
                validator_socket,
                declared_type,
                size,
                digest.hexdigest(),
                max_pixels,
                max_duration_seconds,
                probe_timeout_seconds,
            )
        worker = Path(__file__).with_name("metadata_worker.py")
        resolved_probe = (
            shutil.which(ffprobe_binary) if declared_type.startswith("video/") else None
        )
        if declared_type.startswith("video/") and not resolved_probe:
            raise MediaValidationError("视频元数据检查不可用")
        payload = {
            "path": str(path),
            "declared_type": declared_type,
            "max_pixels": max_pixels,
            "max_duration_seconds": max_duration_seconds,
            "ffprobe_binary": resolved_probe or "ffprobe",
            "probe_timeout_seconds": probe_timeout_seconds,
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-I", str(worker)],
                input=json.dumps(payload),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=probe_timeout_seconds + 10,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                cwd="/",
                check=False,
            )
            result = json.loads(completed.stdout)
            if completed.returncode or not result.get("ok"):
                raise MediaValidationError("媒体格式或元数据不符合要求")
            width, height = int(result["width"]), int(result["height"])
            duration = result.get("duration_seconds")
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise MediaValidationError("媒体尺寸不符合要求")
            if declared_type.startswith("video/") and (
                not isinstance(duration, (int, float)) or not 2 <= duration <= max_duration_seconds
            ):
                raise MediaValidationError("视频时长不符合要求")
            return MediaMetadata(declared_type, size, width, height, duration, digest.hexdigest())
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
            raise MediaValidationError("媒体元数据检查不可用或超时") from exc
    except OSError as exc:
        raise MediaInspectionUnavailable("媒体临时存储不可用") from exc
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def inspect_path(
    path: Path,
    *,
    declared_type: str,
    max_pixels: int,
    max_duration_seconds: float,
    ffprobe_binary: str,
    probe_timeout_seconds: float,
) -> tuple[int, int, float | None]:
    if declared_type.startswith("image/"):
        try:
            with Image.open(path) as image:
                formats = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
                content_type = formats.get(image.format or "")
                width, height = image.size
                if (
                    content_type != declared_type
                    or width <= 0
                    or height <= 0
                    or width * height > max_pixels
                ):
                    raise MediaValidationError("图片格式或尺寸不符合要求")
                image.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise MediaValidationError("图片格式无效") from exc
        return (width, height, None)
    with path.open("rb") as source:
        header = source.read(12)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise MediaValidationError("视频文件格式无效")
    info = _probe_video(path, timeout=probe_timeout_seconds, binary=ffprobe_binary)
    try:
        streams = [item for item in info["streams"] if item.get("codec_type") == "video"]
        if len(streams) != 1 or streams[0].get("codec_name") not in {"h264", "hevc"}:
            raise ValueError("unsupported video streams")
        width, height = int(streams[0]["width"]), int(streams[0]["height"])
        duration = float(info["format"]["duration"])
        if (
            width <= 0
            or height <= 0
            or width * height > max_pixels
            or not 2 <= duration <= max_duration_seconds
        ):
            raise ValueError("video bounds")
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise MediaValidationError("视频编码、尺寸或时长不符合要求") from exc
    return (width, height, duration)


class MediaInspectionUnavailable(RuntimeError):
    """媒体隔离服务不可达，不表示用户素材无效。"""


def _inspect_in_sandbox(
    path: Path,
    socket: str,
    declared_type: str,
    size: int,
    sha256: str,
    max_pixels: int,
    max_duration: float,
    timeout: float,
) -> MediaMetadata:
    import httpx

    try:
        import time

        transport = httpx.HTTPTransport(uds=socket, retries=0)
        deadline = time.monotonic() + timeout + 15
        with (
            httpx.Client(transport=transport, timeout=timeout + 15, trust_env=False) as client,
            client.stream(
                "POST",
                "http://validator/inspect",
                json={
                    "filename": path.name,
                    "content_type": declared_type,
                    "size": size,
                    "max_pixels": max_pixels,
                    "max_duration_seconds": max_duration,
                },
            ) as response,
        ):
            if response.status_code == 422:
                raise MediaValidationError("媒体格式或元数据不符合要求")
            if response.status_code != 200:
                raise MediaInspectionUnavailable("媒体隔离服务不可用")
            data = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    raise MediaInspectionUnavailable("媒体隔离服务响应超时")
                data.extend(chunk)
                if len(data) > 4096:
                    raise MediaInspectionUnavailable("媒体隔离服务响应过大")
        result = json.loads(data)
        if (
            result.get("sha256") != sha256
            or result.get("size") != size
            or result.get("content_type") != declared_type
        ):
            raise MediaInspectionUnavailable("媒体隔离服务返回不一致")
        width, height = int(result["width"]), int(result["height"])
        duration = result.get("duration_seconds")
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise MediaInspectionUnavailable("媒体隔离服务返回不一致")
        if declared_type.startswith("video/") and (
            not isinstance(duration, (int, float)) or not 2 <= duration <= max_duration
        ):
            raise MediaInspectionUnavailable("媒体隔离服务返回不一致")
        return MediaMetadata(declared_type, size, width, height, duration, sha256)
    except MediaValidationError:
        raise
    except MediaInspectionUnavailable:
        raise
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise MediaInspectionUnavailable("媒体隔离服务不可用") from exc


def remove_stale_sandbox_files(directory: str, *, older_than_seconds: int = 900) -> int:
    """清理异常退出留下的临时文件；正常请求始终自行删除。"""
    import re
    import stat
    import time

    if not directory or older_than_seconds < 300:
        return 0
    root = Path(directory)
    now = time.time()
    removed = 0
    try:
        for path in root.iterdir():
            if not re.fullmatch(r"fitness-media-[a-z0-9_]{8}\.bin", path.name):
                continue
            try:
                info = path.lstat()
                if (
                    stat.S_ISREG(info.st_mode)
                    and info.st_uid == os.getuid()
                    and now - info.st_mtime > older_than_seconds
                ):
                    path.unlink(missing_ok=True)
                    removed += 1
            except FileNotFoundError:
                continue
    except FileNotFoundError:
        return 0
    return removed
