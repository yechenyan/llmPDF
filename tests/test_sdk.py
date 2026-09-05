from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from llmpdf import sdk
from llmpdf.io_utils import write_json
from llmpdf.models import TaskResult
from llmpdf.sdk import (
    ConfigurationError,
    ConvertOptions,
    TaskExecutionError,
    convert,
)


def make_outputs(config) -> None:
    config.assets_dir.mkdir(parents=True)
    (config.output_dir / "output.md").write_text("done\n", encoding="utf-8")
    write_json(
        config.assets_dir / "metadata.json",
        {
            "tables": [
                {
                    "id": "table-0001",
                    "lineage": {
                        "action": "replaced_docling_table",
                        "docling_table_ids": ["docling-table-0001"],
                    },
                }
            ],
            "docling_tables": [
                {
                    "id": "docling-table-0001",
                    "retained": True,
                    "replacement_table_ids": ["table-0001"],
                }
            ],
            "images": [{"id": "image-0001"}],
        },
    )
    write_json(
        config.work_dir / "diagnostics" / "validation.json",
        {"status": "passed"},
    )
    write_json(
        config.work_dir / "metrics.json",
        {
            "tokens": {
                "total": {
                    "noncached_input_tokens": 100,
                    "cached_input_tokens": 40,
                    "input_tokens": 140,
                    "output_tokens": 20,
                    "total_tokens": 160,
                    "cache_hit_ratio": 0.2857,
                    "pi_api_price_estimate_usd": 0.12,
                },
                "detection": {"total_tokens": 30},
                "table_extraction": {"total_tokens": 130},
                "detection_batches": [{"batch": "batch-001"}],
                "table_pages": [{"page": 1}],
            },
            "billing": {
                "provider": "openai-codex",
                "billing_mode": "chatgpt_codex_plan",
                "actual_openai_charge_usd": None,
                "pi_api_price_estimate_usd": 0.12,
            },
        },
    )


def test_convert_returns_paths_counts_tokens_and_price(
    tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")

    def fake_run(config):
        make_outputs(config)
        return [TaskResult("fake", "completed", ["output.md"])]

    monkeypatch.setattr(sdk, "run_all", fake_run)
    result = convert(
        ConvertOptions(
            pdf=pdf,
            output_root=tmp_path / "results",
            batch_id="batch-test",
        )
    )
    assert result.status == "completed"
    assert result.table_count == 1
    assert result.image_count == 1
    assert result.docling_table_count == 1
    assert result.retained_docling_table_count == 1
    assert result.table_lineage["tables"][0]["id"] == "table-0001"
    assert result.total_tokens == 160
    assert result.usage.total.cached_input_tokens == 40
    assert result.estimated_price_usd == 0.12
    assert result.billing.actual_openai_charge_usd is None
    payload = result.to_dict()
    assert payload["path_base"] == "output_dir"
    assert payload["output_dir"] == "."
    assert payload["output_markdown"] == "output.md"
    assert payload["assets_dir"] == "assets"
    assert payload["metadata"] == "assets/metadata.json"
    assert not Path(payload["source_pdf"]).is_absolute()
    assert payload["total_tokens"] == 160
    assert payload["estimated_price_usd"] == 0.12


def test_convert_rejects_missing_pdf(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as captured:
        convert(ConvertOptions(pdf=tmp_path / "missing.pdf", output_root=tmp_path))
    assert captured.value.error_type == "FileNotFoundError"


def test_sdk_resolves_page_expression_against_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=100, height=100)
    with pdf.open("wb") as stream:
        writer.write(stream)

    config, _batch_id, _root = sdk.build_pipeline_config(
        ConvertOptions(
            pdf=pdf,
            output_root=tmp_path / "results",
            pages="4-5,1,4",
        )
    )
    assert config.selected_pages == (1, 4, 5)


def test_sdk_rejects_page_beyond_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pdf.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(ConfigurationError, match="exceeds PDF page count 1"):
        sdk.build_pipeline_config(
            ConvertOptions(pdf=pdf, output_root=tmp_path / "results", pages="2")
        )


def test_convert_rejects_invalid_explicit_executable(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    with pytest.raises(ConfigurationError) as captured:
        convert(
            ConvertOptions(
                pdf=pdf,
                output_root=tmp_path / "results",
                pi_executable=tmp_path / "missing-pi",
            )
        )
    assert "Pi executable is invalid" in str(captured.value)


def test_convert_wraps_unknown_task_failure_and_preserves_batch(
    tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")

    def fail(config):
        write_json(
            config.work_dir / "tasks" / "05-extract-tables.json",
            {
                "task": "05-extract-tables",
                "status": "failed",
                "completed_at": "2026-09-02T12:00:00+00:00",
            },
        )
        raise RuntimeError("unknown failure")

    monkeypatch.setattr(sdk, "run_all", fail)
    with pytest.raises(TaskExecutionError) as captured:
        convert(
            ConvertOptions(
                pdf=pdf,
                output_root=tmp_path / "results",
                batch_id="batch-test",
            )
        )
    assert captured.value.failed_task == "05-extract-tables"
    assert captured.value.batch_id == "batch-test"
    assert isinstance(captured.value.__cause__, RuntimeError)


def test_converter_and_options_are_mutually_exclusive(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")

    class Converter:
        def convert(self, _path):
            return None

    with pytest.raises(ConfigurationError):
        convert(
            ConvertOptions(
                pdf=pdf,
                output_root=tmp_path,
                docling_options={"do_ocr": True},
                document_converter=Converter(),
            )
        )
