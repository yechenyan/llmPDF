from __future__ import annotations

import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .assets_task import CollectAssetsTask
from .extraction_task import ExtractTablesTask
from .io_utils import read_json, sha256_file, write_json
from .merge_task import MergeMarkdownTask
from .metrics_task import combined_usage, usage_from_pi_log
from .models import PipelineConfig
from .preflight import run_preflight
from .retention import minimize_successful_result, restore_run_blocks
from .validate_task import ValidateTask


def _resolve_source(result_dir: Path, manifest: dict[str, Any]) -> Path:
    configured = Path(str(manifest["source"]["path"])).expanduser()
    source = configured if configured.is_absolute() else result_dir / configured
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source PDF is unavailable: {source}")
    expected = str(manifest["source"].get("sha256") or "")
    if expected and sha256_file(source) != expected:
        raise ValueError("Source PDF does not match run-manifest.json")
    return source


def _table_usage(config: PipelineConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary = read_json(config.work_dir / "table-extraction" / "summary.json")
    pages: list[dict[str, Any]] = []
    for page in [int(value) for value in summary.get("candidate_pages", [])]:
        job_dir = config.work_dir / "table-extraction" / "runs" / f"page-{page:04d}"
        metrics = [read_json(path) for path in sorted(job_dir.glob("*metrics.json"))]
        usage = combined_usage(
            [usage_from_pi_log(path) for path in sorted(job_dir.glob("*pi.jsonl"))]
        )
        item: dict[str, Any] = {
            "page": page,
            "elapsed_seconds": round(
                sum(float(value.get("elapsed_seconds", 0)) for value in metrics), 3
            ),
            "usage": usage,
        }
        covered = sorted(
            {int(found) for value in metrics for found in value.get("pages", [])}
        )
        if covered:
            item["pages"] = covered
        pages.append(item)
    return pages, combined_usage([item["usage"] for item in pages])


def _update_metrics_after_rerun(
    config: PipelineConfig, previous: dict[str, Any], wall_seconds: float
) -> None:
    table_pages, table_usage = _table_usage(config)
    tokens = previous.setdefault("tokens", {})
    detection = dict(tokens.get("detection") or {})
    image_analysis = dict(tokens.get("image_analysis") or {})
    tokens["table_pages"] = table_pages
    tokens["table_extraction"] = table_usage
    tokens["total"] = combined_usage([detection, table_usage, image_analysis])
    timing = previous.setdefault("timing", {})
    timing["table_agent_work_seconds"] = round(
        sum(float(item["elapsed_seconds"]) for item in table_pages), 3
    )
    timing["table_agent_work_minutes"] = round(
        float(timing["table_agent_work_seconds"]) / 60, 3
    )
    previous.setdefault("reruns", []).append(
        {
            "kind": "tables",
            "completed_at": datetime.now(UTC).isoformat(),
            "wall_seconds": round(wall_seconds, 3),
            "pages": [item["page"] for item in table_pages],
            "usage": table_usage,
        }
    )
    billing = previous.setdefault("billing", {})
    billing["pi_api_price_estimate_usd"] = tokens["total"].get(
        "pi_api_price_estimate_usd", 0
    )
    write_json(config.work_dir / "metrics.json", previous)


def rerun_tables(result: Path, *, claude_executable: Path | None = None) -> dict[str, Any]:
    result_dir = result.expanduser().resolve()
    manifest_path = result_dir / "work" / "run-manifest.json"
    metadata_path = result_dir / "assets" / "metadata.json"
    metrics_path = result_dir / "work" / "metrics.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing minimal run manifest: {manifest_path}")
    review_root = result_dir / "work" / "review"
    if (review_root / "draft.json").is_file():
        raise RuntimeError(
            "Table rerun is disabled after review edits exist; create a new result "
            "to avoid overwriting review decisions"
        )
    source_snapshot = review_root / "source"
    if source_snapshot.is_dir():
        shutil.rmtree(source_snapshot)
    manifest = read_json(manifest_path)
    metadata = read_json(metadata_path)
    previous_metrics = read_json(metrics_path)
    source = _resolve_source(result_dir, manifest)
    settings = manifest.get("configuration") or {}
    config = PipelineConfig(
        pdf=source,
        output_dir=result_dir,
        selected_pages=(
            tuple(int(page) for page in settings["selected_pages"])
            if settings.get("selected_pages") is not None
            else None
        ),
        agent_backend=str(settings.get("agent_backend") or "pi"),
        claude_executable=claude_executable.expanduser().resolve()
        if claude_executable is not None else None,
        model=str(settings.get("model") or "gpt-5.6-sol"),
        thinking=str(settings.get("thinking") or "medium"),
        table_concurrency=int(settings.get("table_concurrency") or 5),
        agent_timeout_seconds=float(settings.get("agent_timeout_seconds") or 1800),
        pdftoppm=str(settings.get("pdftoppm") or "pdftoppm"),
        retain_docling_tables=bool(settings.get("retain_docling_tables", True)),
        analyze_images=False,
        keep_work=False,
        force=True,
    )
    detection = manifest.get("table_detection") or {}
    write_json(
        config.work_dir / "candidate-pages.json",
        {
            "schema_version": 1,
            "pages": list(detection.get("pages") or []),
            "sources": dict(detection.get("sources") or {}),
            "may_merge_with_previous": dict(
                detection.get("may_merge_with_previous") or {}
            ),
        },
    )
    restore_run_blocks(result_dir, manifest)
    write_json(
        config.work_dir / "image-analysis" / "images.json",
        {
            "schema_version": 1,
            "images": list(metadata.get("images") or []),
            "ignored_picture_blocks": list(
                manifest.get("ignored_picture_blocks") or []
            ),
            "pages": [],
        },
    )
    run_preflight(config, (ExtractTablesTask(),))
    for stale in (
        config.work_dir / "table-extraction",
        config.work_dir / "table-assets",
    ):
        if stale.exists():
            shutil.rmtree(stale)
    backup = config.work_dir / "rerun-backup"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result_dir / "output.md", backup / "output.md")
    shutil.copy2(metadata_path, backup / "metadata.json")
    for name in ("tables", "docling-tables"):
        source_dir = config.assets_dir / name
        if source_dir.is_dir():
            shutil.copytree(source_dir, backup / name)
    started = time.perf_counter()
    try:
        ExtractTablesTask().run(config)
        CollectAssetsTask().run(config)
        MergeMarkdownTask().run(config)
        ValidateTask().run(config)
        report = read_json(config.work_dir / "diagnostics" / "validation.json")
        if report.get("status") != "passed":
            raise RuntimeError("Validation failed after table rerun")
    except Exception:
        shutil.copy2(backup / "output.md", result_dir / "output.md")
        shutil.copy2(backup / "metadata.json", metadata_path)
        for name in ("tables", "docling-tables"):
            destination = config.assets_dir / name
            if destination.exists():
                shutil.rmtree(destination)
            source_dir = backup / name
            if source_dir.is_dir():
                shutil.copytree(source_dir, destination)
        raise
    wall_seconds = time.perf_counter() - started
    _update_metrics_after_rerun(config, previous_metrics, wall_seconds)
    cleanup = minimize_successful_result(config)
    current_metadata = read_json(metadata_path)
    return {
        "status": "completed",
        "result_dir": ".",
        "source_pdf": str(manifest["source"]["path"]),
        "table_count": len(current_metadata.get("tables") or []),
        "elapsed_seconds": round(wall_seconds, 3),
        "total_tokens": read_json(metrics_path)["tokens"]["table_extraction"][
            "total_tokens"
        ],
        "validation": report["status"],
        "cleanup": cleanup.details if cleanup else None,
    }
