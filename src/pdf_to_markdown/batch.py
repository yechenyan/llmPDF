from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .io_utils import read_json


@dataclass(frozen=True)
class BatchJob:
    pdf: Path

    @property
    def output_dir(self) -> Path:
        return self.pdf.parent

    @property
    def status_path(self) -> Path:
        return self.output_dir / "work" / "status.json"

    @property
    def validation_path(self) -> Path:
        return self.output_dir / "work" / "diagnostics" / "validation.json"

    @property
    def metadata_path(self) -> Path:
        return self.output_dir / "assets" / "metadata.json"

    @property
    def markdown_path(self) -> Path:
        return self.output_dir / "output.md"


class BatchLogger:
    def __init__(self, path: Path, flush_lines: int = 300) -> None:
        self.path = path
        self.flush_lines = flush_lines
        self.lines = 0
        self.handle: Any = None
        self.file_error_reported = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a", encoding="utf-8")

    def emit(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)
        if self.handle is None:
            return
        try:
            self.handle.write(message + "\n")
            self.lines += 1
            if self.lines % self.flush_lines == 0:
                self.handle.flush()
                os.fsync(self.handle.fileno())
        except OSError as error:
            if not self.file_error_reported:
                print(
                    f"[pdf-to-markdown] batch log disabled after write error: {error}",
                    file=sys.stderr,
                    flush=True,
                )
                self.file_error_reported = True
            try:
                self.handle.close()
            except OSError:
                pass
            self.handle = None

    def log(self, message: str, label: str = "batch") -> None:
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        rendered = label if label.startswith("【") else f"[{label}]"
        self.emit(f"[{stamp}] {rendered} {message}")

    def close(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
        finally:
            self.handle = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_jobs(
    root: Path, recursive: bool = True
) -> tuple[list[BatchJob], list[str]]:
    candidates = root.rglob("*") if recursive else root.iterdir()
    pdfs = sorted(
        (
            path.resolve()
            for path in candidates
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: str(path).casefold(),
    )
    by_parent: dict[Path, list[Path]] = {}
    for pdf in pdfs:
        by_parent.setdefault(pdf.parent, []).append(pdf)
    jobs: list[BatchJob] = []
    warnings: list[str] = []
    for parent, grouped in by_parent.items():
        if len(grouped) != 1:
            warnings.append(
                f"{parent}: in-place output requires exactly one PDF per directory; "
                f"found {len(grouped)}"
            )
            continue
        jobs.append(BatchJob(grouped[0]))
    return jobs, warnings


def completed_run(job: BatchJob) -> tuple[bool, str]:
    try:
        status = read_json(job.status_path)
        if status.get("status") != "completed":
            return False, "status.json is absent or not completed"
        validation = read_json(job.validation_path)
        if validation.get("status") != "passed":
            return False, "validation is absent or not passed"
        metadata = read_json(job.metadata_path)
        expected_sha = str((metadata.get("source") or {}).get("sha256") or "")
        if not expected_sha:
            return False, "metadata source SHA-256 is absent"
        if expected_sha != sha256_file(job.pdf):
            return False, "source PDF changed after the completed run"
        if not job.markdown_path.is_file() or job.markdown_path.stat().st_size == 0:
            return False, "validated output.md is absent or empty"
    except (OSError, ValueError, AttributeError):
        return False, "completion metadata is absent or invalid"
    return True, "validated converter result exists"


def status_summary(job: BatchJob) -> str:
    try:
        status = read_json(job.status_path)
    except (OSError, ValueError):
        return "status.json pending"
    pipeline = status.get("pipeline") or {}
    agents = status.get("agents") or {}
    fields = [f"status={status.get('status', 'unknown')}"]
    if pipeline.get("current_stage"):
        fields.append(f"stage={pipeline['current_stage']}")
    if status.get("elapsed_seconds") is not None:
        fields.append(f"elapsed={status['elapsed_seconds']}s")
    fields.append(
        f"pipeline={pipeline.get('successful_tasks', 0)} ok/"
        f"{pipeline.get('failed_tasks', 0)} failed"
    )
    fields.append(
        f"agents={agents.get('successful_tasks', 0)} ok/"
        f"{agents.get('failed_tasks', 0)} failed"
    )
    return " ".join(fields)


async def _relay(
    reader: asyncio.StreamReader, logger: BatchLogger, label: str, channel: str
) -> None:
    while line := await reader.readline():
        text = line.decode(errors="replace").rstrip()
        if text:
            logger.log(f"[{channel}] {text}", label)


async def _heartbeat(
    job: BatchJob,
    process: asyncio.subprocess.Process,
    interval: float,
    logger: BatchLogger,
    label: str,
) -> None:
    while process.returncode is None:
        await asyncio.sleep(interval)
        logger.log(f"[status] {status_summary(job)}", label)


def _print_status(job: BatchJob, logger: BatchLogger, label: str) -> None:
    try:
        status = read_json(job.status_path)
    except (OSError, ValueError):
        status = {"status": "missing", "path": str(job.status_path)}
    heading = f"{label} STATUS_JSON {job.status_path}"
    logger.emit("")
    logger.emit(f"===== {heading} =====")
    rendered = json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True)
    for line in rendered.splitlines():
        logger.emit(line)
    logger.emit(f"===== END {heading} =====")
    logger.emit("")


async def run_job(
    job: BatchJob,
    *,
    position: int,
    total: int,
    status_interval: float,
    converter_args: list[str],
    logger: BatchLogger,
) -> bool:
    label = f"【{position}/{total}】"
    command = [
        sys.executable,
        "-m",
        "pdf_to_markdown.cli",
        "run-all",
        "--pdf",
        str(job.pdf),
        "--output-dir",
        str(job.output_dir),
        *converter_args,
    ]
    logger.log(f"starting {job.pdf}", label)
    logger.log(f"command: {shlex.join(command)}", label)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    process = await asyncio.create_subprocess_exec(
        *command,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None
    relays = [
        asyncio.create_task(_relay(process.stdout, logger, label, "stdout")),
        asyncio.create_task(_relay(process.stderr, logger, label, "stderr")),
    ]
    monitor = asyncio.create_task(
        _heartbeat(job, process, status_interval, logger, label)
    )
    try:
        returncode = await process.wait()
        await asyncio.gather(*relays)
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
    _print_status(job, logger, label)
    complete, reason = completed_run(job)
    if returncode == 0 and complete:
        logger.log(f"completed -> {job.markdown_path}", label)
        return True
    logger.log(f"failed (exit={returncode}; {reason}); rerun will resume", label)
    return False


async def run_queue(
    jobs: list[BatchJob],
    *,
    workers: int,
    status_interval: float,
    converter_args: list[str],
    logger: BatchLogger,
) -> tuple[int, int]:
    queue: asyncio.Queue[tuple[int, BatchJob]] = asyncio.Queue()
    for position, job in enumerate(jobs, 1):
        queue.put_nowait((position, job))
    succeeded = 0
    failed = 0
    result_lock = asyncio.Lock()

    async def worker(slot: int) -> None:
        nonlocal succeeded, failed
        logger.log(f"slot {slot + 1}/{workers} started")
        while True:
            try:
                position, job = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                ok = await run_job(
                    job,
                    position=position,
                    total=len(jobs),
                    status_interval=status_interval,
                    converter_args=converter_args,
                    logger=logger,
                )
                async with result_lock:
                    succeeded += int(ok)
                    failed += int(not ok)
                    logger.log(
                        f"progress {succeeded + failed}/{len(jobs)}; "
                        f"succeeded={succeeded}; failed={failed}; "
                        f"queued={queue.qsize()}"
                    )
            finally:
                queue.task_done()

    slot_count = min(workers, len(jobs))
    await asyncio.gather(
        *(asyncio.create_task(worker(slot)) for slot in range(slot_count))
    )
    return succeeded, failed


def add_convert_dir_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("directory", type=Path, help="Directory containing PDFs")
    parser.add_argument("--jobs", type=int, default=2, help="Concurrent PDF jobs")
    parser.add_argument("--status-interval", type=float, default=30.0)
    parser.add_argument("--log-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--recursive", action=argparse.BooleanOptionalAction, default=True
    )


def run_convert_dir(args: argparse.Namespace) -> int:
    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if args.jobs < 1 or args.status_interval <= 0:
        raise ValueError("--jobs and --status-interval must be positive")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    converter_args = list(getattr(args, "converter_args", []))
    forbidden = {"--pdf", "--output-dir", "--batch-id"}.intersection(converter_args)
    if forbidden:
        managed = ", ".join(sorted(forbidden))
        raise ValueError(f"convert-dir manages these options: {managed}")
    log_file = (args.log_file or root / "log.md").expanduser().resolve()
    logger = BatchLogger(log_file)
    started = time.monotonic()
    try:
        logger.log(f"combined output log: {log_file}")
        jobs, warnings = discover_jobs(root, recursive=args.recursive)
        for warning in warnings:
            logger.log(warning, "skip")
        pending_with_reasons: list[tuple[BatchJob, str]] = []
        skipped = 0
        force_all = "--force" in converter_args
        for job in jobs:
            complete, reason = completed_run(job)
            if complete and not force_all:
                skipped += 1
                logger.log(f"complete, skipping {job.pdf}", "skip")
            else:
                pending_with_reasons.append(
                    (job, "forced" if force_all else reason)
                )
        if args.limit is not None:
            pending_with_reasons = pending_with_reasons[: args.limit]
        pending = [job for job, _reason in pending_with_reasons]
        for position, (job, reason) in enumerate(pending_with_reasons, 1):
            logger.log(
                f"pending: {reason}; pdf={job.pdf}",
                f"【{position}/{len(pending_with_reasons)}】",
            )
        logger.log(
            f"discovered={len(jobs)} pending={len(pending)} complete={skipped} "
            f"invalid-directories={len(warnings)} jobs={args.jobs}"
        )
        succeeded = failed = 0
        interrupted = False
        if pending and not args.dry_run:
            try:
                succeeded, failed = asyncio.run(
                    run_queue(
                    pending,
                    workers=args.jobs,
                    status_interval=args.status_interval,
                        converter_args=converter_args,
                        logger=logger,
                    )
                )
            except KeyboardInterrupt:
                interrupted = True
                logger.log("interrupted; run the same command again to resume")
        payload = {
            "status": (
                "interrupted"
                if interrupted
                else "failed"
                if failed or warnings
                else "completed"
            ),
            "directory": str(root),
            "discovered": len(jobs),
            "pending": len(pending),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "invalid_directories": len(warnings),
            "dry_run": bool(args.dry_run),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "log_file": str(log_file),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 130 if interrupted else 1 if failed or warnings else 0
    finally:
        logger.close()
