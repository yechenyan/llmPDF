from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import as_completed
from pathlib import Path

from llmpdf.table import WORKFLOW_VERSION as TABLE_ENGINE_VERSION
from llmpdf.table.models import ExtractionJob
from llmpdf.table.prepare import prepare_job
from llmpdf.table.runner import PiConfig, run_prepared_jobs

from .image_analysis_task import prepare_image_jobs, run_prepared_image_job
from .io_utils import (
    read_json,
    relative_reference,
    relativize,
    sha256_file,
    write_json,
)
from .models import PipelineConfig, TaskResult
from .progress import report_progress
from .task import PipelineTask

TABLE_EXTRACTION_TARGET = (
    "Extract all genuine data tables on this page. "
    "If the page contains no tables, do not create any table directories."
)


def find_table_executable(explicit: Path | None = None) -> Path:
    """Resolve the optional legacy external llmpdf-table override.

    Normal pipeline execution uses the bundled ``llmpdf.table`` package and
    never calls this function.
    """
    candidates: list[Path] = []
    if explicit:
        resolved = explicit.resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise FileNotFoundError(
                f"configured llmpdf-table executable is invalid: {resolved}"
            )
        return resolved
    configured = os.environ.get("LLMPDF_TABLE_EXECUTABLE")
    if configured:
        resolved = Path(configured).expanduser().resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise FileNotFoundError(f"LLMPDF_TABLE_EXECUTABLE is invalid: {resolved}")
        return resolved
    found = shutil.which("llmpdf-table")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    searched = ", ".join(str(path) for path in candidates) or "PATH"
    raise FileNotFoundError(
        f"legacy llmpdf-table executable was not found; searched: {searched}"
    )


def run_bundled_table(
    jobs: list[dict],
    runs: Path,
    config: PipelineConfig,
    trailing_jobs: list[tuple[str, Callable[[], object]]] | None = None,
) -> dict:
    if config.early_table_futures is not None:
        return collect_early_table(jobs, runs, config, trailing_jobs)
    prepared = [
        prepare_job(
            ExtractionJob.from_dict(job, config.output_dir),
            runs,
            pdftoppm=config.pdftoppm,
            artifact_root=config.output_dir,
            max_image_patches=config.table_image_max_patches,
        )
        for job in jobs
    ]
    results = run_prepared_jobs(
        prepared,
        PiConfig(
            model=config.model,
            thinking=config.thinking,
            pi_executable=config.pi_executable,
            transport="auto",
            timeout_seconds=config.agent_timeout_seconds,
            keep_sessions=config.keep_sessions,
            artifact_root=config.output_dir,
        ),
        config.table_concurrency,
        executor=config.agent_executor,
        trailing_jobs=trailing_jobs,
        progress_callback=lambda kind, completed, total: report_progress(
            config, f"{kind} Agent tasks completed {completed}/{total}"
        ),
    )
    trailing_futures = results.pop("trailing_job_futures", None)
    if trailing_futures is not None:
        config.image_job_futures = trailing_futures
    write_json(runs / "run_summary.json", results)
    return results


def collect_early_table(
    jobs: list[dict],
    runs: Path,
    config: PipelineConfig,
    trailing_jobs: list[tuple[str, Callable[[], object]]] | None,
) -> dict:
    """Finish table work dynamically submitted while Find was running."""
    expected_pages = {int(job["page"]) for job in jobs}
    prepared_pages = set((config.early_table_jobs or {}).keys())
    if prepared_pages != expected_pages:
        missing = sorted(expected_pages - prepared_pages)
        extra = sorted(prepared_pages - expected_pages)
        raise RuntimeError(
            f"Dynamic Agent scheduler page mismatch; missing={missing}, extra={extra}"
        )
    started = time.time()
    executor = config.agent_executor
    assert executor is not None
    if trailing_jobs:
        config.image_job_futures = {
            identifier: executor.submit_task("image", identifier, callback)
            for identifier, callback in trailing_jobs
        }
    table_futures = dict(config.early_table_futures or {})
    results: dict[str, object] = {}
    for completed_count, future in enumerate(as_completed(table_futures), 1):
        results.update(future.result())
        report_progress(
            config,
            f"table Agent tasks completed {completed_count}/{len(table_futures)}",
        )
    results["parallel_wall_seconds"] = time.time() - started
    results["candidate_chain_count"] = len(table_futures)
    results["parse_group_count"] = len(table_futures)
    write_json(runs / "run_summary.json", results)
    if not config.keep_sessions:
        for prepared in (config.early_table_jobs or {}).values():
            (prepared.directory / "pi-session.jsonl").unlink(missing_ok=True)
            (prepared.directory / "merge-plan-session.jsonl").unlink(missing_ok=True)
    return results


def run_external_table(
    executable: Path, jobs_file: Path, runs: Path, config: PipelineConfig
) -> dict:
    jobs = read_json(jobs_file)
    runtime_jobs = []
    for job in jobs:
        value = dict(job)
        pdf = Path(str(value["pdf"]))
        value["pdf"] = str(
            pdf.resolve() if pdf.is_absolute() else (config.output_dir / pdf).resolve()
        )
        runtime_jobs.append(value)
    with tempfile.TemporaryDirectory(
        prefix="llmpdf-legacy-jobs-"
    ) as directory:
        runtime_jobs_file = Path(directory) / "jobs.json"
        write_json(runtime_jobs_file, runtime_jobs)
        command = [
            str(executable),
            "batch",
            "--jobs",
            str(runtime_jobs_file),
            "--output-dir",
            str(runs),
            "--concurrency",
            str(config.table_concurrency),
            "--model",
            config.model,
            "--thinking",
            config.thinking,
            "--transport",
            "auto",
            "--pdftoppm",
            config.pdftoppm,
        ]
        if config.pi_executable:
            command.extend(["--pi-executable", str(config.pi_executable)])
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"llmpdf-table batch failed: {completed.stderr.strip()}")
    summary = runs / "run_summary.json"
    return read_json(summary) if summary.is_file() else {}


def continuation_groups(runs: Path, jobs: list[dict]) -> list[dict]:
    """Return only continuation chains the table Agent actually accepted."""
    planned = []
    for path in sorted(runs.glob("page-*/merge_plan.json")):
        for pages in read_json(path).get("groups", []):
            normalized = [int(page) for page in pages]
            if len(normalized) > 1:
                planned.append(
                    {"leader_page": normalized[0], "pages": normalized}
                )
    if planned:
        return planned

    groups: list[dict] = []
    active: dict | None = None
    previous_page: int | None = None
    for job in jobs:
        page = int(job["page"])
        hinted = bool(job.get("may_merge_with_previous", False))
        consecutive = previous_page is not None and page == previous_page + 1
        if not hinted or not consecutive:
            active = None
        decision_file = runs / f"page-{page:04d}" / "continuation_decision.json"
        metrics = runs / f"page-{page:04d}" / "continuation_pi_metrics.json"
        accepted = False
        if decision_file.is_file():
            accepted = read_json(decision_file).get("merge_with_previous") is True
        elif metrics.is_file():
            accepted = read_json(metrics).get("merge_with_previous") is True
        if accepted:
            if active is None:
                active = {
                    "leader_page": int(previous_page),
                    "pages": [int(previous_page)],
                }
                groups.append(active)
            active["pages"].append(page)
        else:
            active = None
        previous_page = page
    return groups


class ExtractTablesTask(PipelineTask):
    name = "05-extract-tables"
    dependencies = ("04-candidate-pages", "07-collect-images")

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value.update(
            {
                "model": config.model,
                "thinking": config.thinking,
                "transport": "auto",
                "concurrency": config.table_concurrency,
                "agent_timeout_seconds": config.agent_timeout_seconds,
                "target": TABLE_EXTRACTION_TARGET,
                "continuation_logic_version": 6,
                "path_serialization_version": 3,
                "table_engine": TABLE_ENGINE_VERSION,
                "table_image_max_patches": config.table_image_max_patches,
            }
        )
        candidates = config.work_dir / "candidate-pages.json"
        if candidates.is_file():
            value["candidate_pages_sha256"] = sha256_file(candidates)
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        candidate_payload = read_json(config.work_dir / "candidate-pages.json")
        pages = [int(page) for page in candidate_payload["pages"]]
        merge_hints = candidate_payload.get("may_merge_with_previous", {})
        root = config.work_dir / "table-extraction"
        runs = root / "runs"
        root.mkdir(parents=True, exist_ok=True)
        jobs = [
            {
                "id": f"page-{page:04d}",
                "pdf": relative_reference(config.pdf, config.output_dir),
                "page": page,
                "target": TABLE_EXTRACTION_TARGET,
                "may_merge_with_previous": bool(merge_hints.get(str(page), False)),
            }
            for page in pages
        ]
        jobs_file = root / "jobs.json"
        write_json(jobs_file, jobs)
        trailing_jobs = None
        if (
            config.queue_images_with_tables
            and config.analyze_images
            and not config.table_executable
        ):
            prepared_images = prepare_image_jobs(config, use_table_assets=False)
            config.prepared_image_jobs = prepared_images
            trailing_jobs = [
                (
                    f"image-page-{page:04d}",
                    lambda prepared=prepared: run_prepared_image_job(prepared, config),
                )
                for prepared in prepared_images
                for page in [int(prepared[1])]
            ]
        report_progress(
            config,
            f"shared Agent queue: {len(jobs)} table page tasks, "
            f"{len(trailing_jobs or [])} image page tasks",
        )

        if not jobs:
            if trailing_jobs and config.agent_executor is not None:
                config.image_job_futures = {
                    identifier: config.agent_executor.submit_task(
                        "image", identifier, callback
                    )
                    for identifier, callback in trailing_jobs
                }
            summary = root / "summary.json"
            write_json(summary, {"candidate_pages": [], "table_count": 0, "runs": []})
            return TaskResult(
                self.name, "completed", [relativize(summary, config.output_dir)], {}
            )

        if config.table_executable:
            results = run_external_table(
                find_table_executable(config.table_executable), jobs_file, runs, config
            )
            mode = "legacy-external"
        else:
            results = run_bundled_table(
                jobs, runs, config, trailing_jobs=trailing_jobs
            )
            mode = "bundled"
        failures = [
            result
            for result in results.values()
            if isinstance(result, dict) and int(result.get("returncode", 0)) != 0
        ]
        (root / "stdout.txt").write_text(
            "table extraction mode: "
            f"{mode}\n{relativize(runs / 'run_summary.json', config.output_dir)}\n",
            encoding="utf-8",
        )
        (root / "stderr.txt").write_text(
            "\n".join(str(result.get("stderr", "")) for result in failures),
            encoding="utf-8",
        )
        if failures:
            raise RuntimeError(
                f"bundled table extraction failed for {len(failures)} job(s)"
            )

        table_dirs = [
            table_dir
            for page in pages
            for table_dir in sorted(
                (runs / f"page-{page:04d}" / "agent-output").glob("table_*")
            )
        ]
        valid = [
            directory
            for directory in table_dirs
            if (directory / "metadata.yaml").is_file()
        ]
        summary = root / "summary.json"
        write_json(
            summary,
            {
                "candidate_pages": pages,
                "table_count": len(valid),
                "table_directories": [
                    relativize(path, config.output_dir) for path in valid
                ],
                "continuation_groups": continuation_groups(runs, jobs),
                "engine": mode,
            },
        )
        return TaskResult(
            self.name,
            "completed",
            [
                relativize(summary, config.output_dir),
                relativize(runs, config.output_dir),
            ],
            {"candidate_pages": pages, "table_count": len(valid)},
        )
