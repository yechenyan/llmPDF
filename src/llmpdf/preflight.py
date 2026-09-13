from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from llmpdf.table.pi_runtime import find_pi

if TYPE_CHECKING:
    from .models import PipelineConfig
    from .task import PipelineTask


def _executable(value: str | Path) -> Path | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        resolved = candidate.resolve()
        return resolved if resolved.is_file() and os.access(resolved, os.X_OK) else None
    found = shutil.which(str(value))
    return Path(found).resolve() if found else None


def _check_output_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".preflight-", delete=True):
            pass
    except OSError as error:
        raise PermissionError(f"Output directory is not writable: {path}") from error


def run_preflight(config: PipelineConfig, tasks: Iterable[PipelineTask]) -> None:
    if not config.pdf.is_file() or config.pdf.suffix.lower() != ".pdf":
        raise FileNotFoundError(
            f"PDF does not exist or is not a .pdf file: {config.pdf}"
        )
    try:
        with config.pdf.open("rb") as stream:
            stream.read(1)
    except OSError as error:
        raise PermissionError(f"PDF is not readable: {config.pdf}") from error
    if config.selected_pages is not None:
        from pypdf import PdfReader

        page_count = len(PdfReader(config.pdf).pages)
        if not config.selected_pages:
            raise ValueError("selected_pages must select at least one page")
        if tuple(sorted(set(config.selected_pages))) != config.selected_pages:
            raise ValueError("selected_pages must be sorted and unique")
        if config.selected_pages[0] < 1 or config.selected_pages[-1] > page_count:
            raise ValueError(
                f"selected_pages must be between 1 and PDF page count {page_count}"
            )

    _check_output_directory(config.output_dir)
    pending = {task.name for task in tasks if not task.is_cached(config)}

    if "01-docling" in pending and importlib.util.find_spec("docling") is None:
        raise RuntimeError("Docling is not installed")

    needs_pdftoppm = bool(pending & {"02-screenshots", "05-extract-tables"}) or (
        "07-analyze-images" in pending and config.analyze_images
    )
    if needs_pdftoppm and _executable(config.pdftoppm) is None:
        raise FileNotFoundError(f"pdftoppm executable was not found: {config.pdftoppm}")

    if (
        "05-extract-tables" in pending
        and config.table_executable is not None
        and _executable(config.table_executable) is None
    ):
        raise FileNotFoundError(
            f"llmpdf-table executable is invalid: {config.table_executable}"
        )

    if config.agent_backend not in {"pi", "claude-code"}:
        raise ValueError(f"Unknown agent backend: {config.agent_backend}")
    needs_pi = bool(pending & {"03-detect-tables", "05-extract-tables"}) or (
        "07-analyze-images" in pending and config.analyze_images
    )
    if needs_pi and config.agent_backend == "claude-code":
        if _executable(config.claude_executable or "claude") is None:
            raise FileNotFoundError("Claude Code is unavailable; install claude or provide claude_executable")
        return
    if (
        needs_pi
        and config.pi_executable is not None
        and _executable(config.pi_executable) is None
    ):
        raise FileNotFoundError(f"Pi executable is invalid: {config.pi_executable}")
    if needs_pi and config.agent_backend == "claude-code":
        if _executable(config.claude_executable or "claude") is None:
            raise FileNotFoundError("Claude Code is unavailable; install claude or provide claude_executable")
        return
    if (
        needs_pi
        and config.pi_executable is None
        and find_pi() is None
        and _executable("npx") is None
    ):
        raise FileNotFoundError(
            "Pi is not cached and npx is unavailable; install Node.js or provide pi_executable"
        )
