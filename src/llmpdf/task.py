from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .io_utils import (
    read_json,
    normalize_output_text,
    relative_reference,
    sha256_file,
    sha256_json,
    write_json,
)
from .models import PipelineConfig, TaskResult


class PipelineTask(ABC):
    name: str
    dependencies: tuple[str, ...] = ()

    def state_path(self, config: PipelineConfig) -> Path:
        return config.work_dir / "tasks" / f"{self.name}.json"

    def signature_payload(self, config: PipelineConfig) -> dict[str, Any]:
        return {
            "pipeline_version": __version__,
            "pdf": relative_reference(config.pdf, config.output_dir),
            "pdf_sha256": sha256_file(config.pdf),
            "selected_pages": list(config.selected_pages)
            if config.selected_pages
            else None,
            "task": self.name,
            **({"agent_backend": config.agent_backend} if config.agent_backend != "pi" and self.name in {
                "03-detect-tables", "05-extract-tables", "07-analyze-images", "10-metrics"
            } else {}),
        }

    def signature(self, config: PipelineConfig) -> str:
        return sha256_json(self.signature_payload(config))

    def is_cached(self, config: PipelineConfig) -> bool:
        path = self.state_path(config)
        if config.force or not path.is_file():
            return False
        try:
            state = read_json(path)
        except (OSError, ValueError):
            return False
        outputs = state.get("outputs")
        if not isinstance(outputs, list):
            return False
        outputs_valid = True
        for output in outputs:
            if not isinstance(output, str):
                outputs_valid = False
                break
            candidate = config.output_dir / output
            try:
                if not candidate.exists():
                    outputs_valid = False
                    break
                if candidate.is_file() and candidate.stat().st_size == 0:
                    outputs_valid = False
                    break
                if candidate.is_file() and candidate.suffix.lower() == ".json":
                    read_json(candidate)
            except (OSError, ValueError):
                outputs_valid = False
                break
        return (
            state.get("status") == "completed"
            and state.get("signature") == self.signature(config)
            and outputs_valid
        )

    def execute(self, config: PipelineConfig) -> TaskResult:
        if self.is_cached(config):
            state = read_json(self.state_path(config))
            return TaskResult(
                self.name, "cached", state.get("outputs", []), state.get("details", {})
            )
        started = time.time()
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = self.run(config)
        except Exception as exc:
            write_json(
                self.state_path(config),
                {
                    "task": self.name,
                    "status": "failed",
                    "signature": self.signature(config),
                    "started_at": started_at,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_seconds": time.time() - started,
                    "error": normalize_output_text(f"{type(exc).__name__}: {exc}", config),
                },
            )
            raise
        write_json(
            self.state_path(config),
            {
                "task": self.name,
                "status": "completed",
                "signature": self.signature(config),
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": time.time() - started,
                "outputs": result.outputs,
                "details": result.details,
            },
        )
        return result

    @abstractmethod
    def run(self, config: PipelineConfig) -> TaskResult:
        raise NotImplementedError
