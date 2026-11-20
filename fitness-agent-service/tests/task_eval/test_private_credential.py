from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evaluation.task_eval import private_credential
from app.evaluation.task_eval.private_credential import read_private_credential


def test_private_file_is_read_and_stripped(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_text("  synthetic-evaluation-key\n")
    path.chmod(0o600)
    assert read_private_credential(path) == "synthetic-evaluation-key"


@pytest.mark.parametrize("kind", ["symlink", "public", "oversized", "invalid_utf8", "short"])
def test_untrusted_credential_is_rejected(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "key"
    path.write_bytes(b"synthetic-evaluation-key")
    path.chmod(0o600)
    if kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    elif kind == "public":
        path.chmod(0o644)
    elif kind == "oversized":
        path.write_bytes(b"x" * 4097)
    elif kind == "invalid_utf8":
        path.write_bytes(b"\xff" * 20)
    else:
        path.write_bytes(b"short")
    with pytest.raises(ValueError, match="private credential unavailable"):
        read_private_credential(path)


def test_relative_path_and_directory_are_rejected(tmp_path: Path) -> None:
    for path in (Path("relative-key"), tmp_path):
        with pytest.raises(ValueError, match="private credential unavailable"):
            read_private_credential(path)


def test_nonowner_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "key"
    path.write_text("synthetic-evaluation-key")
    path.chmod(0o600)
    original = private_credential.os.fstat

    def foreign_owner(fd: int) -> SimpleNamespace:
        info = original(fd)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=info.st_uid + 1, st_size=info.st_size)

    monkeypatch.setattr(private_credential.os, "fstat", foreign_owner)
    with pytest.raises(ValueError, match="private credential unavailable"):
        read_private_credential(path)
