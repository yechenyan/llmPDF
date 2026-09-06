from __future__ import annotations

import gzip
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .io_utils import read_json, relative_reference, sha256_file, write_json
from .models import PipelineConfig, TaskResult


_PRESERVED_WORK_NAMES = {
    "diagnostics",
    "metrics.json",
    "review",
    "run-blocks.json.gz",
    "run-manifest.json",
    "status.json",
    "table-code",
}


def _tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _gzip_blocks(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    with source.open("rb") as input_stream, temporary.open("wb") as raw_output:
        with gzip.GzipFile(fileobj=raw_output, mode="wb", mtime=0) as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
    temporary.replace(destination)


def _preserve_table_code(config: PipelineConfig) -> Path:
    """Retain only the Agent-authored extractor code and its page mapping."""
    table_payload = read_json(config.work_dir / "table-assets" / "tables.json")
    target_root = config.work_dir / "table-code"
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True)
    records: list[dict[str, Any]] = []
    for table in table_payload.get("tables", []):
        table_id = str(table["id"])
        relative_source = str((table.get("internal") or {}).get("extractor") or "")
        source = config.output_dir / relative_source
        if not source.is_file():
            raise FileNotFoundError(
                f"Missing generated extractor for {table_id}: {source}"
            )
        destination = target_root / table_id / "extract.py"
        destination.parent.mkdir(parents=True)
        shutil.copy2(source, destination)
        records.append(
            {
                "id": table_id,
                "page": int(table["page"]),
                "source_pages": [
                    int(page) for page in table.get("source_pages", [table["page"]])
                ],
                "page_bboxes": dict(table.get("page_bboxes") or {}),
                "extractor": relative_reference(destination, config.output_dir),
                "sha256": sha256_file(destination),
            }
        )
    index = target_root / "tables.json"
    write_json(index, {"schema_version": 1, "tables": records})
    return index


def restore_run_blocks(result_dir: Path, manifest: dict[str, Any]) -> Path:
    relative = str(manifest["retained"]["blocks"])
    source = result_dir / relative
    if not source.is_file():
        raise FileNotFoundError(f"Missing retained document blocks: {source}")
    expected = str(manifest["retained"].get("blocks_sha256") or "")
    if expected and sha256_file(source) != expected:
        raise ValueError("Retained document blocks do not match run-manifest.json")
    destination = result_dir / "work" / "docling" / "blocks.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(source, "rb") as input_stream, destination.open("wb") as output:
        shutil.copyfileobj(input_stream, output)
    return destination


def build_run_manifest(config: PipelineConfig) -> dict[str, Any]:
    metadata = read_json(config.assets_dir / "metadata.json")
    candidate_path = config.work_dir / "candidate-pages.json"
    candidates = read_json(candidate_path) if candidate_path.is_file() else {}
    image_manifest_path = config.work_dir / "image-analysis" / "images.json"
    image_manifest = (
        read_json(image_manifest_path) if image_manifest_path.is_file() else {}
    )
    blocks_source = config.work_dir / "docling" / "blocks.json"
    blocks_target = config.work_dir / "run-blocks.json.gz"
    if not blocks_source.is_file():
        raise FileNotFoundError(f"Missing document blocks: {blocks_source}")
    _gzip_blocks(blocks_source, blocks_target)
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source": {
            **metadata.get("source", {}),
            "path": relative_reference(config.pdf, config.output_dir),
        },
        "table_detection": {
            "pages": list(candidates.get("pages") or []),
            "sources": dict(candidates.get("sources") or {}),
            "may_merge_with_previous": dict(
                candidates.get("may_merge_with_previous") or {}
            ),
        },
        "ignored_picture_blocks": list(
            image_manifest.get("ignored_picture_blocks") or []
        ),
        "configuration": {
            "selected_pages": list(config.selected_pages)
            if config.selected_pages is not None
            else None,
            "model": config.model,
            "thinking": config.thinking,
            "agent_concurrency": config.agent_concurrency,
            "find_concurrency": config.find_concurrency,
            "table_concurrency": config.table_concurrency,
            "image_concurrency": config.image_concurrency,
            "image_render_dpi": config.image_render_dpi,
            "image_max_patches": config.image_max_patches,
            "table_image_max_patches": config.table_image_max_patches,
            "agent_timeout_seconds": config.agent_timeout_seconds,
            "pdftoppm": config.pdftoppm,
            "retain_docling_tables": config.retain_docling_tables,
        },
        "retained": {
            "blocks": relative_reference(blocks_target, config.output_dir),
            "blocks_sha256": sha256_file(blocks_target),
            "metrics": "work/metrics.json",
            "status": "work/status.json",
            "metadata": "assets/metadata.json",
            "table_code": "work/table-code/tables.json",
        },
    }


def minimize_successful_result(config: PipelineConfig) -> TaskResult | None:
    """Remove reproducible intermediates after a validated successful run."""
    validation_path = config.work_dir / "diagnostics" / "validation.json"
    metrics_path = config.work_dir / "metrics.json"
    metadata_path = config.assets_dir / "metadata.json"
    output_path = config.output_dir / "output.md"
    required = (validation_path, metrics_path, metadata_path, output_path)
    if not all(path.is_file() for path in required):
        return None
    validation = read_json(validation_path)
    if validation.get("status") != "passed":
        return None

    before = _tree_size(config.work_dir)
    _preserve_table_code(config)
    manifest_path = config.work_dir / "run-manifest.json"
    write_json(manifest_path, build_run_manifest(config))

    for child in tuple(config.work_dir.iterdir()):
        if child.name in _PRESERVED_WORK_NAMES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)

    after = _tree_size(config.work_dir)
    return TaskResult(
        "11-minimize-work",
        "completed",
        ["work/run-manifest.json"],
        {
            "bytes_before": before,
            "bytes_after": after,
            "bytes_removed": max(0, before - after),
        },
    )
