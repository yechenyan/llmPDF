from pathlib import Path

from pdf_to_markdown.io_utils import read_json, write_json
from pdf_to_markdown.models import PipelineConfig
from pdf_to_markdown.validate_task import ValidateTask


def test_missing_optional_lineage_reference_is_a_warning(tmp_path: Path) -> None:
    config = PipelineConfig(pdf=tmp_path / "input.pdf", output_dir=tmp_path / "out")
    config.output_dir.mkdir()
    config.assets_dir.mkdir()
    (config.output_dir / "output.md").write_text(
        "<!-- page:1 -->\n\n<!-- table:table-0001 page:1 -->\n\n"
        "| A |\n|---|\n\n<!-- /table:table-0001 page:1 -->\n",
        encoding="utf-8",
    )
    table_csv = config.assets_dir / "tables" / "table-0001.csv"
    table_csv.parent.mkdir()
    table_csv.write_text("A\n", encoding="utf-8")
    write_json(
        config.assets_dir / "metadata.json",
        {
            "schema_version": 3,
            "source": {"page_count": 1},
            "tables": [
                {
                    "id": "table-0001",
                    "page": 1,
                    "source_pages": [1],
                    "page_table_index": 1,
                    "bbox": {"top": 10},
                    "csv": "assets/tables/table-0001.csv",
                    "merge": {
                        "action": "replaced_docling_table",
                        "matched_block": "missing-block",
                        "overlap": 0.9,
                    },
                    "lineage": {
                        "action": "replaced_docling_table",
                        "docling_table_ids": ["missing-docling-table"],
                        "relations": [{"docling_table_id": "missing-docling-table"}],
                    },
                }
            ],
            "docling_tables": [],
            "images": [],
        },
    )

    result = ValidateTask().run(config)
    report = read_json(config.work_dir / "diagnostics" / "validation.json")
    assert result.status == "completed"
    assert report["status"] == "passed"
    assert "references missing Docling tables" in report["warnings"][0]


def test_validation_accepts_non_contiguous_selected_pages(tmp_path: Path) -> None:
    config = PipelineConfig(pdf=tmp_path / "input.pdf", output_dir=tmp_path / "out")
    config.output_dir.mkdir()
    config.assets_dir.mkdir()
    (config.output_dir / "output.md").write_text(
        "<!-- page:2 -->\n\nTwo\n\n---\n\n<!-- page:4 -->\n\nFour\n",
        encoding="utf-8",
    )
    write_json(
        config.assets_dir / "metadata.json",
        {
            "schema_version": 3,
            "source": {"page_count": 5, "selected_pages": [2, 4]},
            "tables": [],
            "docling_tables": [],
            "images": [],
        },
    )

    result = ValidateTask().run(config)
    assert result.status == "completed"
