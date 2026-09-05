from __future__ import annotations

from collections.abc import Iterable
from .agent_scheduler import PriorityAgentExecutor
from .assets_task import CollectAssetsTask
from .detection_task import CandidatePagesTask, DetectTablesTask
from .docling_task import DoclingTask
from .extraction_task import ExtractTablesTask
from .image_analysis_task import AnalyzeImagesTask
from .images_task import CollectImagesTask
from .merge_task import MergeMarkdownTask
from .metrics_task import MetricsTask
from .models import PipelineConfig, TaskResult
from .preflight import run_preflight
from .progress import report_progress
from .retention import minimize_successful_result
from .run_status import RunStatusTracker
from .screenshots_task import ScreenshotsTask
from .task import PipelineTask
from .validate_task import ValidateTask

TASKS: tuple[PipelineTask, ...] = (
    DoclingTask(),
    ScreenshotsTask(),
    DetectTablesTask(),
    CandidatePagesTask(),
    CollectImagesTask(),
    ExtractTablesTask(),
    CollectAssetsTask(),
    AnalyzeImagesTask(),
    MergeMarkdownTask(),
    ValidateTask(),
    MetricsTask(),
)
TASK_BY_NAME = {task.name: task for task in TASKS}


def dependency_order(task_name: str) -> list[PipelineTask]:
    ordered: list[PipelineTask] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in {task.name for task in ordered}:
            return
        if name in visiting:
            raise ValueError(f"Cyclic task dependency at {name}")
        task = TASK_BY_NAME.get(name)
        if task is None:
            raise KeyError(f"Unknown task: {name}")
        visiting.add(name)
        for dependency in task.dependencies:
            visit(dependency)
        visiting.remove(name)
        ordered.append(task)

    visit(task_name)
    return ordered


def run_tasks(
    config: PipelineConfig,
    tasks: Iterable[PipelineTask],
    *,
    finalize_status: bool = True,
) -> list[TaskResult]:
    selected = tuple(tasks)
    config.work_dir.mkdir(parents=True, exist_ok=True)
    tracker = RunStatusTracker(config, len(selected))
    config.status_tracker = tracker
    tracker.start()
    scheduler_holder: dict[str, PriorityAgentExecutor] = {}

    def scheduler_progress(message: str) -> None:
        report_progress(config, message)
        executor = scheduler_holder.get("executor")
        if executor is not None:
            tracker.scheduler_updated(executor.snapshot(), message)

    try:
        run_preflight(config, selected)
        workers = config.agent_concurrency
        selected_names = {task.name for task in selected}
        image_task = next(
            (task for task in selected if task.name == "07-analyze-images"), None
        )
        with PriorityAgentExecutor(
            max_workers=workers,
            kind_limits={
                "find": config.find_concurrency,
                "table": config.table_concurrency,
                "image": config.image_concurrency,
            },
            progress=scheduler_progress,
        ) as executor:
            scheduler_holder["executor"] = executor
            config.agent_executor = executor
            tracker.scheduler_updated(executor.snapshot(), "Agent queue started")
            config.dynamic_agent_scheduling = {
                "03-detect-tables",
                "05-extract-tables",
            }.issubset(selected_names) and not config.parse_table_executable
            config.queue_images_with_tables = {
                "05-extract-tables",
                "07-analyze-images",
            }.issubset(selected_names) and not (
                image_task is not None and image_task.is_cached(config)
            )
            results = []
            total = len(selected)
            for index, task in enumerate(selected, 1):
                tracker.stage_started(index, task.name)
                report_progress(
                    config, f"stage {index}/{total} started: {task.name}"
                )
                result = task.execute(config)
                tracker.stage_finished(result)
                report_progress(
                    config,
                    f"stage {index}/{total} {result.status}: {task.name}",
                )
                results.append(result)
            tracker.scheduler_updated(executor.snapshot(), "Agent queue finished")
            if finalize_status:
                tracker.complete()
            return results
    except BaseException as error:
        tracker.fail(error)
        raise
    finally:
        config.agent_executor = None
        config.queue_images_with_tables = False
        config.prepared_image_jobs = None
        config.image_job_futures = None
        config.dynamic_agent_scheduling = False
        config.early_table_jobs = None
        config.early_table_futures = None


def run_all(config: PipelineConfig) -> list[TaskResult]:
    results = run_tasks(config, TASKS, finalize_status=False)
    tracker = config.status_tracker
    try:
        if not config.keep_sessions:
            for session in config.work_dir.glob("**/*session*.jsonl"):
                session.unlink(missing_ok=True)
        if not config.keep_work and not config.keep_sessions:
            report_progress(config, "minimizing successful work directory")
            cleanup = minimize_successful_result(config)
            if cleanup is not None:
                results.append(cleanup)
                report_progress(config, "minimal retention completed")
        if tracker is not None:
            tracker.complete()
        return results
    except BaseException as error:
        if tracker is not None:
            tracker.fail(error, "11-minimize-work")
        raise


def run_one_with_dependencies(
    config: PipelineConfig, task_name: str
) -> list[TaskResult]:
    return run_tasks(config, dependency_order(task_name))
