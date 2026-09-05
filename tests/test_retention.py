import gzip
from pathlib import Path

from pdf_to_markdown.io_utils import read_json, write_json
from pdf_to_markdown.models import PipelineConfig
from pdf_to_markdown.retention import minimize_successful_result, restore_run_blocks


def make_successful_result(tmp_path: Path) -> PipelineConfig:
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "result")
    config.assets_dir.mkdir(parents=True)
    (config.output_dir / "output.md").write_text("done\n", encoding="utf-8")
    write_json(
        config.assets_dir / "metadata.json",
        {
            "source": {
                "file": pdf.name,
                "path": "../source.pdf",
                "sha256": "test",
                "page_count": 1,
            },
            "tables": [],
            "images": [],
        },
    )
    write_json(
        config.work_dir / "diagnostics" / "validation.json", {"status": "passed"}
    )
    write_json(config.work_dir / "metrics.json", {"tokens": {"total": {}}})
    write_json(config.work_dir / "status.json", {"status": "running"})
    write_json(
        config.work_dir / "candidate-pages.json",
        {
            "pages": [1],
            "sources": {"vision": [1]},
            "may_merge_with_previous": {"1": False},
        },
    )
    write_json(
        config.work_dir / "image-analysis" / "images.json",
        {"images": [], "ignored_picture_blocks": ["logo-1"]},
    )
    write_json(
        config.work_dir / "docling" / "blocks.json",
        {"page_count": 1, "blocks": [{"id": "block-1"}]},
    )
    extractor = config.work_dir / "table-assets" / "table-0001" / "extract.py"
    extractor.parent.mkdir(parents=True)
    extractor.write_text("print('table')\n", encoding="utf-8")
    write_json(
        config.work_dir / "table-assets" / "tables.json",
        {
            "tables": [
                {
                    "id": "table-0001",
                    "page": 1,
                    "source_pages": [1],
                    "internal": {
                        "extractor": "work/table-assets/table-0001/extract.py"
                    },
                }
            ]
        },
    )
    (config.work_dir / "table-extraction" / "runs").mkdir(parents=True)
    (config.work_dir / "table-extraction" / "runs" / "pi.jsonl").write_text(
        "large log", encoding="utf-8"
    )
    write_json(config.work_dir / "review" / "draft.json", {"tables": {}})
    return config


def test_minimal_retention_removes_reproducible_work_and_restores_blocks(
    tmp_path: Path,
) -> None:
    config = make_successful_result(tmp_path)
    result = minimize_successful_result(config)
    assert result is not None
    assert result.task == "11-minimize-work"
    assert not (config.work_dir / "table-extraction").exists()
    assert not (config.work_dir / "table-assets").exists()
    assert (config.work_dir / "table-code" / "table-0001" / "extract.py").read_text(
        encoding="utf-8"
    ) == "print('table')\n"
    assert not (config.work_dir / "docling").exists()
    assert (config.work_dir / "metrics.json").is_file()
    assert read_json(config.work_dir / "status.json")["status"] == "running"
    assert not (config.work_dir / "cleanup.json").exists()
    assert (config.work_dir / "review" / "draft.json").is_file()
    manifest = read_json(config.work_dir / "run-manifest.json")
    assert manifest["table_detection"]["pages"] == [1]
    assert manifest["ignored_picture_blocks"] == ["logo-1"]
    assert manifest["configuration"]["selected_pages"] is None
    assert manifest["retained"]["table_code"] == "work/table-code/tables.json"
    assert manifest["retained"]["status"] == "work/status.json"
    assert read_json(config.work_dir / "table-code" / "tables.json")["tables"][0][
        "source_pages"
    ] == [1]
    restored = restore_run_blocks(config.output_dir, manifest)
    with gzip.open(config.work_dir / "run-blocks.json.gz", "rt") as stream:
        assert '"block-1"' in stream.read()
    assert read_json(restored)["page_count"] == 1


def test_minimal_retention_never_runs_after_failed_validation(tmp_path: Path) -> None:
    config = make_successful_result(tmp_path)
    write_json(
        config.work_dir / "diagnostics" / "validation.json", {"status": "failed"}
    )
    assert minimize_successful_result(config) is None
    assert (config.work_dir / "table-extraction").is_dir()
