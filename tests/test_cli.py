import json
from pathlib import Path

import pytest

from pdf_to_markdown.cli import (
    build_parser,
    config_from_args,
    options_from_args,
    print_failure,
)
from pdf_to_markdown.io_utils import write_json
from pdf_to_markdown.models import PipelineConfig
from pdf_to_markdown.sdk import build_pipeline_config


def test_default_table_concurrency_is_five() -> None:
    config = PipelineConfig(pdf=Path("input.pdf"), output_dir=Path("result"))
    assert config.agent_concurrency == 5
    assert config.find_concurrency == 5
    assert config.table_concurrency == 5
    assert config.image_concurrency == 5
    assert config.analyze_images is True
    assert config.image_model == "gpt-5.6-terra"
    assert config.image_thinking == "medium"
    assert config.image_render_dpi == 240
    assert config.image_max_patches == 10_000
    assert config.retain_docling_tables is True
    assert config.agent_timeout_seconds == 1800.0
    assert config.keep_work is False


def test_cli_default_table_concurrency_is_five() -> None:
    args = build_parser().parse_args(
        ["run-all", "--pdf", "input.pdf", "--output-dir", "result"]
    )
    assert args.table_concurrency == 5
    assert args.image_concurrency == 5
    assert args.image_model == "gpt-5.6-terra"
    assert args.image_thinking == "medium"
    assert args.image_max_patches == 10_000
    assert args.retain_docling_tables is True
    assert args.agent_timeout_seconds == 1800.0
    assert args.keep_work is False


def test_cli_can_keep_full_work_directory() -> None:
    args = build_parser().parse_args(
        [
            "convert",
            "input.pdf",
            "--output-dir",
            "result",
            "--keep-work",
        ]
    )
    assert args.keep_work is True


def test_cli_accepts_image_patch_budget() -> None:
    args = build_parser().parse_args(
        [
            "convert",
            "input.pdf",
            "--output-dir",
            "result",
            "--image-max-patches",
            "8000",
        ]
    )
    assert args.image_max_patches == 8000


def test_cli_accepts_page_selection() -> None:
    args = build_parser().parse_args(
        ["convert", "input.pdf", "--output-dir", "result", "--pages", "1,3,8-12"]
    )
    assert args.pages == "1,3,8-12"


def test_cli_exposes_table_rerun_command() -> None:
    args = build_parser().parse_args(["rerun-tables", "result/pdf"])
    assert args.result == Path("result/pdf")


def test_cli_can_disable_retained_docling_tables() -> None:
    args = build_parser().parse_args(
        [
            "convert",
            "input.pdf",
            "--output-dir",
            "result",
            "--no-retain-docling-tables",
        ]
    )
    assert args.retain_docling_tables is False


def test_cli_does_not_impose_an_upper_limit() -> None:
    args = build_parser().parse_args(
        [
            "run-all",
            "--pdf",
            "input.pdf",
            "--output-dir",
            "result",
            "--table-concurrency",
            "100",
        ]
    )
    assert args.table_concurrency == 100


def test_cli_rejects_find_concurrency_above_five(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    args = build_parser().parse_args(
        [
            "run-all",
            "--pdf",
            str(pdf),
            "--output-dir",
            str(tmp_path / "result"),
            "--find-concurrency",
            "6",
        ]
    )
    with pytest.raises(
        ValueError, match="--find-concurrency must be between 1 and 5"
    ):
        config_from_args(args)


def test_cli_rejects_global_agent_concurrency_above_five(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    args = build_parser().parse_args(
        [
            "run-all",
            "--pdf",
            str(pdf),
            "--output-dir",
            str(tmp_path / "result"),
            "--agent-concurrency",
            "6",
        ]
    )
    with pytest.raises(
        ValueError, match="--agent-concurrency must be between 1 and 5"
    ):
        config_from_args(args)


def test_batch_id_adds_batch_and_pdf_parent_directories(tmp_path: Path) -> None:
    pdf = tmp_path / "example.pdf"
    pdf.write_bytes(b"%PDF-test")
    args = build_parser().parse_args(
        [
            "run-all",
            "--pdf",
            str(pdf),
            "--output-dir",
            str(tmp_path / "results"),
            "--batch-id",
            "batch-20260831-120000",
        ]
    )
    config = config_from_args(args)
    assert config.output_dir == (
        tmp_path / "results" / "batch-20260831-120000" / "example"
    )


def test_convert_uses_positional_pdf_and_automatic_batch(tmp_path: Path) -> None:
    pdf = tmp_path / "example.pdf"
    pdf.write_bytes(b"%PDF-test")
    args = build_parser().parse_args(
        ["convert", str(pdf), "--output-dir", str(tmp_path / "results")]
    )
    config, batch_id, _output_root = build_pipeline_config(options_from_args(args))
    assert config.pdf == pdf
    assert config.output_dir.parent.parent == tmp_path / "results"
    assert batch_id.startswith("batch-")
    assert config.output_dir.parent.name == batch_id
    assert config.output_dir.name == "example"


def test_cli_loads_docling_options_json(tmp_path: Path) -> None:
    pdf = tmp_path / "example.pdf"
    pdf.write_bytes(b"%PDF-test")
    options_file = tmp_path / "docling.json"
    options_file.write_text('{"do_ocr": true, "images_scale": 3.0}', encoding="utf-8")
    args = build_parser().parse_args(
        [
            "convert",
            str(pdf),
            "--output-dir",
            str(tmp_path / "results"),
            "--docling-options-file",
            str(options_file),
            "--keep-sessions",
            "--retain-docling-tables",
        ]
    )
    options = options_from_args(args)
    assert options.docling_options == {"do_ocr": True, "images_scale": 3.0}
    assert options.keep_sessions is True
    assert options.retain_docling_tables is True


def test_main_agent_command_has_been_removed() -> None:
    commands = build_parser()._subparsers._group_actions[0].choices
    assert "agent" not in commands


def test_cli_exposes_directory_conversion_command() -> None:
    commands = build_parser()._subparsers._group_actions[0].choices
    assert "convert-dir" in commands


def test_convert_failure_reports_reusable_batch(tmp_path: Path, capsys) -> None:
    pdf = tmp_path / "example.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(
        pdf=pdf,
        output_dir=tmp_path / "results" / "batch-test" / "example",
    )
    write_json(
        config.work_dir / "tasks" / "05-extract-tables.json",
        {
            "task": "05-extract-tables",
            "status": "failed",
            "completed_at": "2026-09-02T12:00:00+00:00",
        },
    )
    print_failure(RuntimeError("unknown failure"), config, "convert")
    payload = json.loads(capsys.readouterr().err)
    assert payload["failed_task"] == "05-extract-tables"
    assert payload["batch_id"] == "batch-test"
    assert "--batch-id batch-test" in payload["recovery"]
