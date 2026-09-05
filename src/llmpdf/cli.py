from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .io_utils import read_json, relative_reference
from .models import PipelineConfig
from .pages import validate_pages
from .pipeline import TASKS, run_all, run_one_with_dependencies
from .sdk import ConfigurationError, ConversionError, ConvertOptions, convert


def add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pages",
        help="One-based PDF pages, for example 1-25 or 1,3,8-12",
    )
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--thinking", default="medium")
    parser.add_argument("--detection-dpi", type=int, default=96)
    parser.add_argument("--contact-sheet-size", type=int, default=8)
    parser.add_argument(
        "--agent-concurrency",
        type=int,
        default=5,
        help="Maximum concurrent Agent tasks across the pipeline (default and maximum: 5)",
    )
    parser.add_argument(
        "--find-concurrency",
        type=int,
        default=5,
        help="Concurrent Find Agent batches (default and maximum: 5)",
    )
    parser.add_argument(
        "--table-concurrency",
        type=int,
        default=5,
        help="Maximum concurrent bundled table-extraction jobs (default: 5; no built-in upper limit)",
    )
    parser.add_argument(
        "--image-concurrency",
        type=int,
        default=5,
        help="Image-side contribution to shared Agent queue workers (default: 5)",
    )
    parser.add_argument(
        "--analyze-images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Describe meaningful images and convert readable charts (default: enabled)",
    )
    parser.add_argument(
        "--image-model",
        default="gpt-5.6-terra",
        help="Image-analysis model (default: gpt-5.6-terra)",
    )
    parser.add_argument(
        "--image-thinking",
        default="medium",
        help="Image-analysis reasoning effort (default: medium)",
    )
    parser.add_argument("--image-render-dpi", type=int, default=240)
    parser.add_argument(
        "--image-max-patches",
        type=int,
        default=10_000,
        help="Maximum 32x32 patches per rendered model image (default: 10000)",
    )
    parser.add_argument(
        "--table-image-max-patches",
        type=int,
        default=30_000,
        help="Maximum 32x32 patches per table Agent image (default: 30000)",
    )
    parser.add_argument(
        "--agent-timeout-seconds",
        type=float,
        default=1800.0,
        help="Maximum duration of each Pi Agent call (default: 1800 seconds)",
    )
    parser.add_argument("--confidence-threshold", type=float, default=0.35)
    parser.add_argument("--pdftoppm", default="pdftoppm")
    parser.add_argument("--pi-executable", type=Path)
    parser.add_argument(
        "--llmpdf-table-executable",
        dest="table_executable",
        type=Path,
        help="Deprecated compatibility override for an external llmpdf-table executable",
    )
    parser.add_argument(
        "--docling-options-file",
        type=Path,
        help="JSON file merged into the default Docling PdfPipelineOptions",
    )
    parser.add_argument("--keep-sessions", action="store_true")
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep all intermediate files after a successful run (default: minimal retention)",
    )
    parser.add_argument(
        "--retain-docling-tables",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep Docling table Markdown under assets/docling-tables (default: enabled)",
    )
    parser.add_argument("--force", action="store_true")


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--batch-id",
        help="Optional batch directory name; output becomes OUTPUT_DIR/BATCH_ID/PDF_STEM",
    )
    add_runtime_arguments(parser)


def load_docling_options(path: Path | None) -> dict:
    if path is None:
        return {}
    value = read_json(path.resolve())
    if not isinstance(value, dict):
        raise TypeError("--docling-options-file must contain a JSON object")
    return value


def config_from_args(args: argparse.Namespace) -> PipelineConfig:
    pdf = args.pdf.resolve()
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    if not 0 <= args.confidence_threshold <= 1:
        raise ValueError("--confidence-threshold must be between 0 and 1")
    if not 1 <= args.agent_concurrency <= 5:
        raise ValueError("--agent-concurrency must be between 1 and 5")
    if not 1 <= args.find_concurrency <= 5:
        raise ValueError("--find-concurrency must be between 1 and 5")
    if args.table_concurrency < 1:
        raise ValueError("--table-concurrency must be positive")
    if args.image_concurrency < 1:
        raise ValueError("--image-concurrency must be positive")
    if args.agent_timeout_seconds <= 0:
        raise ValueError("--agent-timeout-seconds must be positive")
    if args.detection_dpi < 1 or args.contact_sheet_size < 1:
        raise ValueError("--detection-dpi and --contact-sheet-size must be positive")
    if args.image_render_dpi < 1:
        raise ValueError("--image-render-dpi must be positive")
    if args.image_max_patches < 1:
        raise ValueError("--image-max-patches must be positive")
    if args.table_image_max_patches < 1:
        raise ValueError("--table-image-max-patches must be positive")
    selected_pages = None
    if args.pages is not None:
        from pypdf import PdfReader

        selected_pages = validate_pages(args.pages, len(PdfReader(pdf).pages))
    output_dir = args.output_dir.resolve()
    batch_id = args.batch_id
    if batch_id:
        if Path(batch_id).name != batch_id or batch_id in {".", ".."}:
            raise ValueError("--batch-id must be a single directory name")
        output_dir = output_dir / batch_id / pdf.stem
    return PipelineConfig(
        pdf=pdf,
        output_dir=output_dir,
        selected_pages=selected_pages,
        model=args.model,
        thinking=args.thinking,
        detection_dpi=args.detection_dpi,
        contact_sheet_size=args.contact_sheet_size,
        agent_concurrency=args.agent_concurrency,
        find_concurrency=args.find_concurrency,
        table_concurrency=args.table_concurrency,
        image_concurrency=args.image_concurrency,
        agent_timeout_seconds=args.agent_timeout_seconds,
        confidence_threshold=args.confidence_threshold,
        pdftoppm=args.pdftoppm,
        pi_executable=args.pi_executable.resolve() if args.pi_executable else None,
        table_executable=args.table_executable.resolve()
        if args.table_executable
        else None,
        docling_options=load_docling_options(args.docling_options_file),
        keep_sessions=args.keep_sessions,
        keep_work=args.keep_work,
        show_progress=True,
        retain_docling_tables=args.retain_docling_tables,
        analyze_images=args.analyze_images,
        image_model=args.image_model,
        image_thinking=args.image_thinking,
        image_render_dpi=args.image_render_dpi,
        image_max_patches=args.image_max_patches,
        table_image_max_patches=args.table_image_max_patches,
        force=args.force,
    )


def options_from_args(args: argparse.Namespace) -> ConvertOptions:
    return ConvertOptions(
        pdf=args.pdf,
        output_root=args.output_dir,
        batch_id=args.batch_id,
        pages=args.pages,
        model=args.model,
        thinking=args.thinking,
        detection_dpi=args.detection_dpi,
        contact_sheet_size=args.contact_sheet_size,
        agent_concurrency=args.agent_concurrency,
        find_concurrency=args.find_concurrency,
        table_concurrency=args.table_concurrency,
        image_concurrency=args.image_concurrency,
        agent_timeout_seconds=args.agent_timeout_seconds,
        confidence_threshold=args.confidence_threshold,
        docling_options=load_docling_options(args.docling_options_file),
        pdftoppm=args.pdftoppm,
        pi_executable=args.pi_executable,
        table_executable=args.table_executable,
        keep_sessions=args.keep_sessions,
        keep_work=args.keep_work,
        show_progress=True,
        retain_docling_tables=args.retain_docling_tables,
        analyze_images=args.analyze_images,
        image_model=args.image_model,
        image_thinking=args.image_thinking,
        image_render_dpi=args.image_render_dpi,
        image_max_patches=args.image_max_patches,
        table_image_max_patches=args.table_image_max_patches,
        force=args.force,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llmpdf")
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser(
        "convert", help="Convert one PDF directly under code control"
    )
    convert_parser.add_argument("pdf", type=Path, help="Input PDF")
    convert_parser.add_argument(
        "--output-dir", required=True, type=Path, help="Output root"
    )
    convert_parser.add_argument(
        "--batch-id", help="Batch directory name; defaults to a UTC timestamp"
    )
    add_runtime_arguments(convert_parser)

    run_parser = subparsers.add_parser("run-all", help="Run the complete pipeline")
    add_common(run_parser)
    from .batch import add_convert_dir_arguments

    directory_parser = subparsers.add_parser(
        "convert-dir", help="Convert PDFs below a directory in place"
    )
    add_convert_dir_arguments(directory_parser)
    task_parser = subparsers.add_parser(
        "run-task", help="Run one task and its dependencies"
    )
    add_common(task_parser)
    task_parser.add_argument("task", choices=[task.name for task in TASKS])
    subparsers.add_parser("list-tasks", help="List task names and dependencies")
    review_parser = subparsers.add_parser(
        "review", help="Open the local llmPDF review web application"
    )
    review_parser.add_argument(
        "--result",
        action="append",
        default=[],
        type=Path,
        help="PDF result directory; repeat to load multiple PDFs",
    )
    review_parser.add_argument(
        "--batch",
        action="append",
        default=[],
        type=Path,
        help="Batch directory containing PDF result directories; repeatable",
    )
    review_parser.add_argument(
        "--project", type=Path, help="JSON review project containing result_dir sources"
    )
    review_parser.add_argument("--host", default="127.0.0.1")
    review_parser.add_argument("--port", type=int, default=8765)
    review_parser.add_argument("--no-open", action="store_true")
    rerun_parser = subparsers.add_parser(
        "rerun-tables",
        help="Regenerate tables from a successful minimal result directory",
    )
    rerun_parser.add_argument("result", type=Path, help="PDF result directory")
    return parser


def failed_task(config: PipelineConfig) -> dict | None:
    state_dir = config.work_dir / "tasks"
    if not state_dir.is_dir():
        return None
    failed = []
    for path in state_dir.glob("*.json"):
        try:
            state = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("status") == "failed":
            failed.append(state)
    return max(failed, key=lambda item: str(item.get("completed_at", "")), default=None)


def print_failure(
    error: Exception, config: PipelineConfig | None, command: str | None = None
) -> None:
    task = failed_task(config) if config else None
    batch_id = (
        config.output_dir.parent.name if config and command == "convert" else None
    )
    path_base = config.output_dir.resolve() if config else Path.cwd()
    output_root_path = (
        config.output_dir.parents[1] if config and command == "convert" else None
    )
    payload = {
        "status": "failed",
        "error_type": type(error).__name__,
        "error": str(error),
        "failed_task": task.get("task") if task else None,
        "path_base": "output_dir" if config else "cwd",
        "output_dir": relative_reference(config.output_dir, path_base)
        if config
        else None,
        "output_root": (
            relative_reference(output_root_path, path_base)
            if output_root_path is not None
            else None
        ),
        "batch_id": batch_id,
        "recovery": (
            f"Re-run convert with --batch-id {batch_id}. Completed tasks will be loaded from cache."
            if batch_id
            else "Run the same command again. Completed tasks will be loaded from cache."
            if config
            else "Correct the command arguments and run it again."
        ),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)


def main() -> None:
    arguments = sys.argv[1:]
    converter_args: list[str] = []
    if arguments[:1] == ["convert-dir"] and "--" in arguments:
        separator = arguments.index("--")
        converter_args = arguments[separator + 1 :]
        arguments = arguments[:separator]
    args = build_parser().parse_args(arguments)
    if args.command == "convert-dir":
        args.converter_args = converter_args
    if args.command == "list-tasks":
        for task in TASKS:
            dependencies = ", ".join(task.dependencies) or "-"
            print(f"{task.name}\t{dependencies}")
        return
    if args.command == "review":
        from .review import ReviewProject, resolve_results
        from .review_server import run_review_server

        try:
            roots = resolve_results(args.result, args.batch, args.project)
            run_review_server(
                ReviewProject(roots),
                host=args.host,
                port=args.port,
                open_browser=not args.no_open,
            )
        except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        return
    if args.command == "convert-dir":
        from .batch import run_convert_dir

        try:
            exit_code = run_convert_dir(args)
        except (OSError, ValueError, TypeError) as error:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        if exit_code:
            raise SystemExit(exit_code)
        return
    if args.command == "rerun-tables":
        from .rerun import rerun_tables

        try:
            payload = rerun_tables(args.result)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        return

    config = None
    try:
        if args.command == "convert":
            result = convert(options_from_args(args))
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return
        config = config_from_args(args)
        results = (
            run_all(config)
            if args.command == "run-all"
            else run_one_with_dependencies(config, args.task)
        )
    except ConversionError as error:
        payload = error.to_dict()
        if error.batch_id:
            payload["recovery"] = (
                f"Re-run convert with --batch-id {error.batch_id}. "
                "Completed tasks will be loaded from cache."
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:  # noqa: BLE001 - the CLI must report and exit on unknown failures
        if args.command == "convert":
            wrapped = ConfigurationError(str(error), error_type=type(error).__name__)
            print(
                json.dumps(wrapped.to_dict(), ensure_ascii=False, indent=2),
                file=sys.stderr,
            )
        else:
            print_failure(error, config, args.command)
        raise SystemExit(1) from None
    print(
        json.dumps(
            [result.__dict__ for result in results], ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
