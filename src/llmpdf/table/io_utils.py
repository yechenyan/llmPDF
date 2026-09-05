from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def relative_reference(path: Path, root: Path) -> str:
    """Return a POSIX path relative to the generated artifact root."""
    return Path(os.path.relpath(path.resolve(), start=root.resolve())).as_posix()


def normalize_jsonl_paths(path: Path, artifact_root: Path) -> None:
    """Rewrite host paths in a completed Pi JSONL log as relative references."""
    if not path.is_file():
        return
    artifact_root = artifact_root.resolve()
    absolute_roots = {
        artifact_root,
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        Path(sys.prefix).resolve(),
    }
    replacements = {
        str(root): relative_reference(root, artifact_root)
        for root in absolute_roots
    }
    content = path.read_text(encoding="utf-8")
    for absolute, relative in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        content = content.replace(absolute, relative)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
