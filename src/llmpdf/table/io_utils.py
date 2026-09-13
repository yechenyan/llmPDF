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


def normalize_host_paths(content: str, artifact_root: Path, extra_paths: tuple[Path, ...] = ()) -> str:
    """Replace runtime host paths with references relative to the artifact root."""
    artifact_root = artifact_root.resolve()
    absolute_roots = {
        artifact_root,
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        Path(sys.prefix).resolve(),
        *(path.expanduser().resolve() for path in extra_paths),
    }
    absolute_roots = {root for root in absolute_roots if root != Path(root.anchor)}
    replacements = {
        str(root): relative_reference(root, artifact_root)
        for root in absolute_roots
    }
    for absolute, relative in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        content = content.replace(absolute, relative)
    return content


def normalize_jsonl_paths(path: Path, artifact_root: Path) -> None:
    """Rewrite host paths in a completed Agent JSONL log as relative references."""
    if not path.is_file():
        return
    content = normalize_host_paths(path.read_text(encoding="utf-8"), artifact_root)
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
