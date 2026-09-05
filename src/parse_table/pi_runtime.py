from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .io_utils import write_json


PI_PACKAGE = "@mariozechner/pi-coding-agent@0.73.1"


def find_pi(package: str = PI_PACKAGE) -> Path | None:
    expected_version = package.rsplit("@", 1)[-1]
    cache_root = Path.home() / ".npm" / "_npx"
    for package_json in sorted(
        cache_root.glob("*/node_modules/@mariozechner/pi-coding-agent/package.json")
    ):
        try:
            metadata = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("version") != expected_version:
            continue
        executable = package_json.parents[2] / ".bin" / "pi"
        if executable.exists():
            return executable.resolve()
    return None


@contextmanager
def pi_environment(agent_dir: Path | None = None, transport: str = "auto"):
    environment = os.environ.copy()
    environment["PI_OFFLINE"] = "1"
    if transport == "auto":
        if agent_dir is not None:
            environment["PI_CODING_AGENT_DIR"] = str(agent_dir.resolve())
        yield environment
        return

    source = agent_dir or Path(
        environment.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    )
    with tempfile.TemporaryDirectory(prefix="pdf-to-markdown-pi-") as directory:
        runtime = Path(directory)
        if source.is_dir():
            for item in source.iterdir():
                if item.name == "settings.json":
                    continue
                (runtime / item.name).symlink_to(
                    item.resolve(), target_is_directory=item.is_dir()
                )
        settings: dict = {}
        settings_path = source / "settings.json"
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                settings = {}
        settings["transport"] = transport
        write_json(runtime / "settings.json", settings)
        environment["PI_CODING_AGENT_DIR"] = str(runtime)
        yield environment
