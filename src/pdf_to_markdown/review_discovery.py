from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .io_utils import read_json


def result_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not (resolved / "assets" / "metadata.json").is_file():
        raise ValueError(f"Not a PDF result directory: {resolved}")
    return resolved


def discover_batch(path: Path) -> list[Path]:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    return sorted(
        {metadata.parent.parent for metadata in root.glob("*/assets/metadata.json")}
    )


def project_sources(path: Path) -> list[Path]:
    resolved = path.expanduser().resolve()
    payload = read_json(resolved)
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise TypeError("Review project must contain a sources array")
    results = []
    for item in payload["sources"]:
        if not isinstance(item, dict) or not item.get("result_dir"):
            raise ValueError("Each project source requires result_dir")
        value = Path(str(item["result_dir"])).expanduser()
        results.append(
            result_directory(value if value.is_absolute() else resolved.parent / value)
        )
    return results


def resolve_results(
    results: Iterable[Path] = (),
    batches: Iterable[Path] = (),
    project: Path | None = None,
) -> list[Path]:
    candidates = [result_directory(path) for path in results]
    for batch in batches:
        candidates.extend(discover_batch(batch))
    if project:
        candidates.extend(project_sources(project))
    unique = list(dict.fromkeys(candidates))
    if not unique:
        raise ValueError("No PDF results selected; use --result, --batch, or --project")
    return unique
