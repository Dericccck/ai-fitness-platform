"""Short-lived, resource-limited parser process; never receives service secrets."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


def main() -> None:
    if sys.platform.startswith("linux"):
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (15, 17))
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    spec = importlib.util.spec_from_file_location(
        "fitness_media_metadata", Path(__file__).with_name("metadata.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        params = json.loads(sys.stdin.read(16384))
        width, height, duration = module.inspect_path(
            Path(params["path"]),
            declared_type=params["declared_type"],
            max_pixels=params["max_pixels"],
            max_duration_seconds=params["max_duration_seconds"],
            ffprobe_binary=params["ffprobe_binary"],
            probe_timeout_seconds=params["probe_timeout_seconds"],
        )
        print(
            json.dumps({"ok": True, "width": width, "height": height, "duration_seconds": duration})
        )
    except Exception:  # noqa: BLE001 - no untrusted content in logs
        print(json.dumps({"ok": False}))
        sys.exit(1)


if __name__ == "__main__":
    os.umask(0o077)
    main()
