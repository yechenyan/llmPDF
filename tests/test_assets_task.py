from pathlib import Path

import yaml

from llmpdf.assets_task import CollectAssetsTask
from llmpdf.io_utils import read_json, write_json
from llmpdf.models import PipelineConfig


def test_collect_assets_preserves_page_bboxes(tmp_path: Path) -> None:
    config = PipelineConfig(
        pdf=tmp_path / "source.pdf",
        output_dir=tmp_path / "result",
    )
    table_dir = (
        config.work_dir
        / "table-extraction"
        / "runs"
        / "page-0027"
        / "agent-output"
        / "table_1"
    )
    table_dir.mkdir(parents=True)
    (table_dir / "output_1.csv").write_text("A\n1\n", encoding="utf-8")
    (table_dir / "extract.py").write_text("# generated\n", encoding="utf-8")
    page_27 = {
        "coordinate_system": "pdfplumber_top_left",
        "unit": "pt",
        "approximate": True,
        "x0": 70.0,
        "top": 510.0,
        "x1": 567.0,
        "bottom": 724.0,
    }
    page_28 = {**page_27, "top": 110.0, "bottom": 185.0}
    (table_dir / "metadata.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": "Spanning table",
                "page": 27,
                "source_pages": [27, 28],
                "page_table_index": 1,
                "bbox": page_27,
                "page_bboxes": {"27": page_27, "28": page_28},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    write_json(
        config.work_dir / "table-extraction" / "summary.json",
        {
            "table_directories": [
                str(table_dir.relative_to(config.output_dir))
            ],
            "continuation_groups": [
                {"leader_page": 27, "pages": [27, 28]}
            ],
        },
    )

    CollectAssetsTask().run(config)

    table = read_json(config.work_dir / "table-assets" / "tables.json")["tables"][
        0
    ]
    assert table["page"] == 27
    assert table["source_pages"] == [27, 28]
    assert table["bbox"]["top"] == 510.0
    assert table["page_bboxes"]["28"]["top"] == 110.0
