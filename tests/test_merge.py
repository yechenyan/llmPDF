from pathlib import Path

from llmpdf.io_utils import read_json, write_json
from llmpdf.merge_task import (
    MergeMarkdownTask,
    build_table_lineage,
    choose_docling_table,
    collect_direct_table_relations,
    insertion_order,
    mapped_continuation_block_ids,
    materialize_docling_tables,
    normalize_table_insertions,
    normalized_text,
    public_table_record,
    render_image_markdown,
)
from llmpdf.models import BBox, DocumentBlock, PipelineConfig


def block(
    identifier: str,
    order: int,
    kind: str,
    bbox: BBox,
    page: int = 1,
) -> DocumentBlock:
    return DocumentBlock(identifier, page, order, kind, "text", bbox)


def test_choose_docling_table_uses_bbox_overlap() -> None:
    blocks = [
        block("a", 1, "table", BBox(0, 0, 100, 100)),
        block("b", 2, "table", BBox(0, 200, 100, 300)),
    ]
    matched, score = choose_docling_table(BBox(5, 5, 95, 95), blocks, set())
    assert matched is not None and matched.id == "a"
    assert score > 0.8


def test_insertion_order_uses_absorbed_content_first() -> None:
    absorbed = [block("inside", 7, "text", BBox(10, 50, 90, 60))]
    assert insertion_order(BBox(0, 40, 100, 80), absorbed, absorbed) == 7


def test_normalized_text_supports_caption_deduplication() -> None:
    assert normalized_text("**Tabelle 1:  Example**") == normalized_text(
        "Tabelle 1: Example"
    )


def test_two_tables_on_one_page_map_to_distinct_blocks() -> None:
    blocks = [
        block("upper", 4, "table", BBox(0, 100, 100, 180)),
        block("lower", 9, "table", BBox(0, 300, 100, 380)),
    ]
    used: set[str] = set()
    first, _ = choose_docling_table(BBox(2, 102, 98, 178), blocks, used)
    assert first is not None
    used.add(first.id)
    second, _ = choose_docling_table(BBox(2, 302, 98, 378), blocks, used)
    assert (first.id, second.id if second else None) == ("upper", "lower")


def test_missing_table_is_inserted_after_nearest_overlapping_block_above() -> None:
    blocks = [
        block("far", 1, "text", BBox(200, 20, 300, 40)),
        block("heading", 5, "text", BBox(0, 80, 100, 95)),
        block("below", 7, "text", BBox(0, 250, 100, 270)),
    ]
    assert insertion_order(BBox(0, 100, 100, 200), blocks, []) == 5.5


def test_same_page_tables_keep_physical_order_when_docling_slots_are_reversed() -> None:
    insertions = [
        (9.0, 0, "upper table"),
        (4.0, 1, "lower table"),
        (7.0, 2, "bottom table"),
    ]

    assert normalize_table_insertions(insertions) == [
        (4.0, 0, "upper table"),
        (7.0, 1, "lower table"),
        (9.0, 2, "bottom table"),
    ]


def test_public_table_metadata_hides_work_paths() -> None:
    public = public_table_record(
        {
            "id": "table-0001",
            "page": 1,
            "source_pages": [1, 2],
            "csv": "assets/tables/table-0001.csv",
            "internal": {"markdown": "work/table-assets/table-0001/table.md"},
            "merge": {"action": "replaced_docling_table"},
            "lineage": {"action": "replaced_docling_table"},
        }
    )
    assert "internal" not in public
    assert public["csv"].startswith("assets/")
    assert public["source_pages"] == [1, 2]
    assert public["lineage"]["action"] == "replaced_docling_table"


def table(
    identifier: str,
    page: int,
    source_pages: list[int],
    bbox: BBox,
) -> dict:
    return {
        "id": identifier,
        "page": page,
        "source_pages": source_pages,
        "bbox": bbox.to_dict(),
    }


def test_lineage_maps_direct_docling_table_bidirectionally() -> None:
    tables = [table("table-0001", 1, [1], BBox(0, 10, 100, 100))]
    docling = [block("#/tables/0", 2, "table", BBox(0, 10, 100, 100))]
    lineage, source = build_table_lineage(
        tables,
        docling,
        {
            "table-0001": [
                {"block_id": "#/tables/0", "method": "bbox_overlap", "overlap": 1.0}
            ]
        },
    )
    assert lineage["table-0001"]["action"] == "replaced_docling_table"
    assert lineage["table-0001"]["docling_table_ids"] == ["docling-table-0001"]
    assert source[0]["replacement_table_ids"] == ["table-0001"]
    assert source[0]["status"] == "replaced"


def test_lineage_attaches_continuation_to_merged_table() -> None:
    tables = [table("table-0001", 1, [1, 2], BBox(0, 10, 100, 100))]
    docling = [
        block("#/tables/0", 2, "table", BBox(0, 10, 100, 100), page=1),
        block("#/tables/1", 2, "table", BBox(0, 10, 100, 100), page=2),
    ]
    lineage, source = build_table_lineage(
        tables,
        docling,
        {
            "table-0001": [
                {"block_id": "#/tables/0", "method": "bbox_overlap", "overlap": 1.0}
            ]
        },
    )
    assert lineage["table-0001"]["action"] == "merged_from_docling_tables"
    assert lineage["table-0001"]["docling_table_ids"] == [
        "docling-table-0001",
        "docling-table-0002",
    ]
    assert source[1]["status"] == "absorbed_into_merged_table"


def test_lineage_does_not_force_ambiguous_continuation() -> None:
    tables = [
        table("table-0001", 1, [1, 2], BBox(0, 10, 100, 100)),
        table("table-0002", 1, [1, 2], BBox(0, 120, 100, 200)),
    ]
    continuation = block(
        "#/tables/continuation", 2, "table", BBox(0, 10, 100, 100), page=2
    )
    lineage, source = build_table_lineage(tables, [continuation], {})
    assert not lineage["table-0001"]["docling_table_ids"]
    assert not lineage["table-0002"]["docling_table_ids"]
    assert source[0]["status"] == "ambiguous_continuation"
    assert source[0]["candidate_replacement_table_ids"] == [
        "table-0002",
        "table-0001",
    ]


def test_single_nonoverlapping_continuation_is_preserved_as_ambiguous() -> None:
    tables = [table("table-0001", 1, [1, 2], BBox(0, 10, 100, 100))]
    unrelated = block("#/tables/unrelated", 2, "table", BBox(300, 10, 400, 100), page=2)
    lineage, source = build_table_lineage(tables, [unrelated], {})
    assert not lineage["table-0001"]["docling_table_ids"]
    assert source[0]["status"] == "ambiguous_continuation"
    assert mapped_continuation_block_ids(lineage) == set()


def test_continuation_mapping_preserves_independent_table_on_same_page() -> None:
    tables = [
        table("merged", 1, [1, 2], BBox(0, 10, 100, 100)),
        table("independent", 2, [2], BBox(200, 120, 300, 200)),
    ]
    docling = [
        block("leader", 1, "table", BBox(0, 10, 100, 100), page=1),
        block("continuation", 1, "table", BBox(0, 10, 100, 100), page=2),
        block("independent-source", 2, "table", BBox(200, 120, 300, 200), page=2),
    ]
    tables_by_page = {1: [tables[0]], 2: [tables[1]]}
    blocks_by_page = {1: [docling[0]], 2: docling[1:]}
    direct = collect_direct_table_relations(tables_by_page, blocks_by_page)
    lineage, _source = build_table_lineage(tables, docling, direct)
    assert lineage["independent"]["relations"][0]["block_id"] == "independent-source"
    assert lineage["merged"]["docling_table_ids"] == [
        "docling-table-0001",
        "docling-table-0002",
    ]
    assert mapped_continuation_block_ids(lineage) == {"continuation"}


def test_materialize_docling_tables_writes_and_clears_assets(tmp_path) -> None:
    config = PipelineConfig(
        pdf=tmp_path / "input.pdf",
        output_dir=tmp_path / "out",
        retain_docling_tables=True,
    )
    source = block("#/tables/0", 2, "table", BBox(0, 10, 100, 100))
    source.markdown = "| A |\n|---|\n| 1 |"
    records = [{"id": "docling-table-0001", "block_id": source.id}]
    outputs = materialize_docling_tables(config, records, [source])
    assert outputs == ["assets/docling-tables/docling-table-0001.md"]
    assert (config.output_dir / outputs[0]).read_text(encoding="utf-8").endswith("\n")

    config.retain_docling_tables = False
    materialize_docling_tables(config, records, [source])
    assert not (config.assets_dir / "docling-tables").exists()
    assert records[0]["markdown"] is None


def test_render_image_markdown_includes_description_and_chart(tmp_path) -> None:
    config = PipelineConfig(pdf=tmp_path / "input.pdf", output_dir=tmp_path / "out")
    chart = config.assets_dir / "chart-tables" / "image-0001.csv"
    chart.parent.mkdir(parents=True)
    chart.write_text("Year,Value\n2025,12\n", encoding="utf-8")
    rendered = render_image_markdown(
        {
            "id": "image-0001",
            "image": "assets/images/image-0001.png",
            "caption": None,
            "analysis": {
                "alt_text": "Annual load",
                "description": "Shows annual load.",
            },
            "chart_table": {"csv": "assets/chart-tables/image-0001.csv"},
        },
        3,
        config,
    )
    assert "<!-- image:image-0001 page:3 -->" in rendered
    assert "<!-- /image:image-0001 page:3 -->" in rendered
    assert "![Annual load](assets/images/image-0001.png)" in rendered
    assert "Shows annual load." in rendered
    assert "| Year | Value |" in rendered


def test_metadata_source_path_is_relative_to_result_directory(tmp_path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "out")
    write_json(
        config.work_dir / "docling" / "blocks.json",
        {"page_count": 1, "blocks": []},
    )
    write_json(config.work_dir / "table-assets" / "tables.json", {"tables": []})
    write_json(
        config.work_dir / "image-analysis" / "images.json",
        {"images": [], "ignored_picture_blocks": []},
    )
    write_json(config.work_dir / "candidate-pages.json", {"pages": [], "sources": {}})

    MergeMarkdownTask().run(config)

    metadata = read_json(config.assets_dir / "metadata.json")
    assert metadata["source"]["path"] == "../input.pdf"
    assert not Path(metadata["source"]["path"]).is_absolute()


def test_merge_outputs_only_selected_physical_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "out", selected_pages=(2, 4))
    blocks = [
        DocumentBlock("page-2", 2, 1, "text", "second page"),
        DocumentBlock("page-4", 4, 2, "text", "fourth page"),
    ]
    write_json(
        config.work_dir / "docling" / "blocks.json",
        {
            "page_count": 5,
            "selected_pages": [2, 4],
            "blocks": [value.to_dict() for value in blocks],
        },
    )
    write_json(config.work_dir / "table-assets" / "tables.json", {"tables": []})
    write_json(
        config.work_dir / "image-analysis" / "images.json",
        {"images": [], "ignored_picture_blocks": []},
    )
    write_json(config.work_dir / "candidate-pages.json", {"pages": [], "sources": {}})

    MergeMarkdownTask().run(config)

    markdown = (config.output_dir / "output.md").read_text(encoding="utf-8")
    metadata = read_json(config.assets_dir / "metadata.json")
    assert "<!-- page:2 -->" in markdown
    assert "<!-- page:4 -->" in markdown
    assert "<!-- page:1 -->" not in markdown
    assert metadata["source"]["page_count"] == 5
    assert metadata["source"]["selected_pages"] == [2, 4]
