from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .io_utils import read_json, sha256_file
from .review_source import ReviewSource


class ReviewProject:
    def __init__(self, roots: Iterable[Path]) -> None:
        self.lock = threading.RLock()
        self.sources: dict[str, ReviewSource] = {}
        for index, root in enumerate(roots, start=1):
            metadata = read_json(root / "assets" / "metadata.json")
            digest = str(
                metadata.get("source", {}).get("sha256")
                or sha256_file(root / "output.md")
            )
            source_id = digest[:12]
            if source_id in self.sources:
                source_id = f"{source_id}-{index}"
            self.sources[source_id] = ReviewSource(root=root, id=source_id)

    def source(self, source_id: str) -> ReviewSource:
        try:
            return self.sources[source_id]
        except KeyError as error:
            raise KeyError(f"Unknown source: {source_id}") from error

    def catalog(self) -> dict[str, Any]:
        with self.lock:
            sources = [source.summary() for source in self.sources.values()]
        return {
            "schema_version": 1,
            "source_count": len(sources),
            "table_count": sum(source["table_count"] for source in sources),
            "reviewed_count": sum(source["reviewed_count"] for source in sources),
            "sources": sources,
        }
