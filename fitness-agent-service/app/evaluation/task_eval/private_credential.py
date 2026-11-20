"""Read a bounded, private model credential without following a final symlink."""

from __future__ import annotations

import os
import stat
from pathlib import Path

_MAX_BYTES = 4096


def read_private_credential(path: str | Path) -> str:
    """Validate the opened file itself; never include its content in errors."""
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("private credential unavailable")
    try:
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or not metadata.st_mode & stat.S_IRUSR
                or stat.S_IMODE(metadata.st_mode) & 0o177
                or not 16 <= metadata.st_size <= _MAX_BYTES
            ):
                raise ValueError("private credential unavailable")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise ValueError("private credential unavailable")
            credential = data.decode("utf-8").strip()
            if len(credential) < 16:
                raise ValueError("private credential unavailable")
            return credential
        finally:
            os.close(fd)
    except (OSError, UnicodeError) as exc:
        raise ValueError("private credential unavailable") from exc
