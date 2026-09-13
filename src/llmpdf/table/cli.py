from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .io_utils import relative_reference, write_json
from .models import DEFAULT_TARGET, ExtractionJob
from .prepare import (
    DEFAULT_MAX_IMAGE_PATCHES,
    DEFAULT_TARGET_SMALL_TEXT_PX,
    prepare_job,
)
from .runner import PiConfig, run_prepared_job, run_prepared_jobs
from .table_guard import inspect_csv


def add_prepare_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--page", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--job-id")
    parser.add_argument("--target-small-text-px", type=int, default=DEFAULT_TARGET_SMALL_TEXT_PX)
    parser.add_argument("--image-max-patches", type=int, default=DEFAULT_MAX_IMAGE_PATCHES)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--pdftoppm", default="pdftoppm")


def add_pi_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--thinking", default="medium")
    parser.add_argument("--agent-backend", choices=("pi", "claude-code"), default="pi")
    parser.add_argument("--claude-executable", type=Path)
    parser.add_argument("--pi-executable", type=Path)
    parser.add_argument("--agent-dir", type=Path)
    parser.add_argument("--transport", choices=("sse", "auto", "websocket"), default="auto")
    parser.add_argument("--agent-timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--keep-sessions", action="store_true")


def pi_config(args: argparse.Namespace) -> PiConfig:
    if args.agent_timeout_seconds <= 0:
        raise ValueError("agent_timeout_seconds must be positive")
    return PiConfig(
        model=args.model,
        thinking=args.thinking,
        agent_backend=args.agent_backend,
        claude_executable=args.claude_executable.expanduser().resolve()
        if args.claude_executable else None,
        pi_executable=args.pi_executable.resolve() if args.pi_executable else None,
        agent_dir=args.agent_dir.resolve() if args.agent_dir else None,
        transport=args.transport,
        timeout_seconds=args.agent_timeout_seconds,
        keep_sessions=args.keep_sessions,
    )


def extraction_job(args: argparse.Namespace) -> ExtractionJob:
    return ExtractionJob(
        pdf=args.pdf.resolve(),
        page=args.page,
        target=args.target,
        job_id=args.job_id,
    )


def prepare_from_args(args: argparse.Namespace):
    return prepare_job(
        extraction_job(args),
        args.output_dir,
        target_small_text_px=args.target_small_text_px,
        python_executable=args.python,
        pdftoppm=args.pdftoppm,
        max_image_patches=args.image_max_patches,
    )


def command_prepare(args: argparse.Namespace) -> int:
    prepared = prepare_from_args(args)
    print(relative_reference(prepared.directory, args.output_dir))
    return 0


def command_run(args: argparse.Namespace) -> int:
    result = run_prepared_job(args.job_dir, pi_config(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["returncode"] != 0)


def command_extract(args: argparse.Namespace) -> int:
    prepared = prepare_from_args(args)
    result = run_prepared_job(prepared.directory, pi_config(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["returncode"] != 0)


def command_batch(args: argparse.Namespace) -> int:
    jobs_file = args.jobs.resolve()
    payload = json.loads(jobs_file.read_text(encoding="utf-8"))
    jobs = [ExtractionJob.from_dict(item, jobs_file.parent) for item in payload]
    prepared = [
        prepare_job(
            job,
            args.output_dir,
            target_small_text_px=args.target_small_text_px,
            python_executable=args.python,
            pdftoppm=args.pdftoppm,
            max_image_patches=args.image_max_patches,
        )
        for job in jobs
    ]
    results = run_prepared_jobs(
        prepared,
        pi_config(args),
        args.concurrency or len(prepared),
    )
    summary = args.output_dir.resolve() / "run_summary.json"
    write_json(summary, results)
    print(relative_reference(summary, args.output_dir))
    failures = [
        result
        for key, result in results.items()
        if key != "parallel_wall_seconds" and isinstance(result, dict) and result["returncode"] != 0
    ]
    return int(bool(failures))


def command_inspect(args: argparse.Namespace) -> int:
    result = inspect_csv(args.pdf, args.csv)
    json_output = args.json_output or args.csv.with_suffix(".guard.json")
    result.write_json(json_output)
    print(result.to_text())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmpdf-table")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="Prepare one PDF page and its prompt")
    add_prepare_options(prepare_parser)
    prepare_parser.set_defaults(handler=command_prepare)

    run_parser = subparsers.add_parser("run", help="Run Pi for one prepared job")
    run_parser.add_argument("--job-dir", required=True, type=Path)
    add_pi_options(run_parser)
    run_parser.set_defaults(handler=command_run)

    extract_parser = subparsers.add_parser("extract", help="Prepare and run one PDF page")
    add_prepare_options(extract_parser)
    add_pi_options(extract_parser)
    extract_parser.set_defaults(handler=command_extract)

    batch_parser = subparsers.add_parser("batch", help="Prepare and run jobs from a JSON file")
    batch_parser.add_argument("--jobs", required=True, type=Path)
    batch_parser.add_argument("--output-dir", required=True, type=Path)
    batch_parser.add_argument("--concurrency", type=int)
    batch_parser.add_argument("--target-small-text-px", type=int, default=DEFAULT_TARGET_SMALL_TEXT_PX)
    batch_parser.add_argument("--image-max-patches", type=int, default=DEFAULT_MAX_IMAGE_PATCHES)
    batch_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    batch_parser.add_argument("--pdftoppm", default="pdftoppm")
    add_pi_options(batch_parser)
    batch_parser.set_defaults(handler=command_batch)

    inspect_parser = subparsers.add_parser("inspect", help="Run the spatial reference check")
    inspect_parser.add_argument("--pdf", required=True, type=Path)
    inspect_parser.add_argument("--csv", required=True, type=Path)
    inspect_parser.add_argument("--json-output", type=Path)
    inspect_parser.set_defaults(handler=command_inspect)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.handler(args))


if __name__ == "__main__":
    main()
