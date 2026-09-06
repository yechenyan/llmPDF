from pathlib import Path

from llmpdf.io_utils import (
    read_json,
    relative_reference,
    sha256_file,
    write_json,
)
from llmpdf.review import (
    ReviewProject,
    ReviewSource,
    markdown_table_rows,
    resolve_results,
    rows_to_markdown,
)


def make_result(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "batch-test" / "sample"
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-review-test")
    csv_path = root / "assets" / "tables" / "table-0001.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("A,B\n1,2\n", encoding="utf-8")
    docling = root / "assets" / "docling-tables" / "docling-table-0001.md"
    docling.parent.mkdir(parents=True)
    docling.write_text("| A | B |\n| --- | --- |\n| 3 | 4 |\n", encoding="utf-8")
    table_markdown = rows_to_markdown([["A", "B"], ["1", "2"]], "Example", 1)
    (root / "output.md").write_text(
        f"<!-- page:1 -->\n\n<!-- table:table-0001 page:1 -->\n\n"
        f"{table_markdown}\n\n<!-- /table:table-0001 page:1 -->\n",
        encoding="utf-8",
    )
    write_json(
        root / "assets" / "metadata.json",
        {
            "schema_version": 3,
            "source": {
                "file": "sample.pdf",
                "path": relative_reference(pdf, root),
                "sha256": sha256_file(pdf),
                "page_count": 1,
            },
            "tables": [
                {
                    "id": "table-0001",
                    "page": 1,
                    "source_pages": [1],
                    "page_table_index": 1,
                    "name": "Example",
                    "header_rows": 1,
                    "bbox": {"x0": 1, "top": 2, "x1": 100, "bottom": 50},
                    "csv": "assets/tables/table-0001.csv",
                    "extra_csvs": [],
                    "merge": {
                        "action": "replaced_docling_table",
                        "matched_block": "block-1",
                        "overlap": 1,
                    },
                    "lineage": {
                        "action": "replaced_docling_table",
                        "docling_table_ids": ["docling-table-0001"],
                        "relations": [
                            {
                                "docling_table_id": "docling-table-0001",
                                "block_id": "block-1",
                                "method": "bbox_overlap",
                            }
                        ],
                    },
                }
            ],
            "docling_tables": [
                {
                    "id": "docling-table-0001",
                    "page": 1,
                    "block_id": "block-1",
                    "bbox": {"x0": 1, "top": 2, "x1": 100, "bottom": 50},
                    "status": "replaced",
                    "replacement_table_ids": ["table-0001"],
                    "retained": True,
                    "markdown": "assets/docling-tables/docling-table-0001.md",
                }
            ],
            "images": [],
            "output": {"markdown": "output.md", "sha256": "before-review"},
        },
    )
    return root


def test_markdown_table_rows_parses_docling_markdown() -> None:
    assert markdown_table_rows("| A | B |\n| --- | --- |\n| 1 | 2 |\n") == [
        ["A", "B"],
        ["1", "2"],
    ]


def test_rows_to_markdown_flattens_cell_line_breaks() -> None:
    rendered = rows_to_markdown(
        [["Period", "Value"], ["2023 to 2028\n(t+5)", "12"]],
        None,
        1,
    )
    assert "2023 to 2028 (t+5)" in rendered
    assert "<br>" not in rendered


def test_review_detail_and_publish_round_trip(tmp_path: Path) -> None:
    root = make_result(tmp_path)
    source = ReviewSource(root=root, id="sample")
    assert source.pdf_path() == (tmp_path / "sample.pdf").resolve()
    assert source.summary()["path_base"] == "result_dir"
    assert source.summary()["result_dir"] == "."
    assert source.summary()["pdf_path"] == str((tmp_path / "sample.pdf").resolve())
    assert source.summary()["pdf_relative_path"] == "../sample.pdf"
    detail = source.detail("table-0001")
    assert detail["ai_rows"] == [["A", "B"], ["1", "2"]]
    assert detail["docling_rows"] == [["A", "B"], ["3", "4"]]
    assert source.summary()["tables"][0]["difference_count"] == 2
    assert source.summary()["tables"][0]["manual_difference_count"] == 0
    assert source.summary()["modified_table_count"] == 0
    assert source.summary()["noted_table_count"] == 0
    assert source.summary()["marked_table_count"] == 0

    source.save_draft(
        "table-0001",
        {
            "status": "approved",
            "selected_source": "docling",
            "note": "Docling is correct",
            "marked": True,
            "rows": detail["docling_rows"],
        },
    )
    assert source.summary()["tables"][0]["manual_difference_count"] == 2
    assert source.summary()["modified_table_count"] == 1
    assert source.summary()["noted_table_count"] == 1
    assert source.summary()["marked_table_count"] == 1
    result = source.publish()

    assert result["published_tables"] == ["table-0001"]
    assert result["history"].startswith("work/review/history/")
    assert not Path(result["history"]).is_absolute()
    assert (root / "assets" / "tables" / "table-0001.csv").read_text() == "A,B\n3,4\n"
    assert "| 3 | 4 |" in (root / "output.md").read_text()
    assert source.detail("table-0001")["ai_rows"] == [["A", "B"], ["1", "2"]]
    assert (
        root / "work" / "review" / "source" / "tables" / "table-0001.csv"
    ).read_text() == "A,B\n1,2\n"
    metadata = read_json(root / "assets" / "metadata.json")
    assert metadata["tables"][0]["review"]["status"] == "approved"
    assert metadata["tables"][0]["review"]["selected_source"] == "docling"
    assert metadata["tables"][0]["review"]["marked"] is True
    assert (
        read_json(root / "work" / "diagnostics" / "validation.json")["status"]
        == "passed"
    )
    assert list((root / "work" / "review" / "history").glob("*/output.md"))


def test_review_prefers_ai_page_bboxes_for_preview(tmp_path: Path) -> None:
    root = make_result(tmp_path)
    metadata_path = root / "assets" / "metadata.json"
    metadata = read_json(metadata_path)
    ai_bbox = {"x0": 10, "top": 20, "x1": 90, "bottom": 40}
    metadata["tables"][0]["page_bboxes"] = {"1": ai_bbox}
    write_json(metadata_path, metadata)

    detail = ReviewSource(root=root, id="sample").detail("table-0001")

    assert detail["preview_regions"]["1"] == ai_bbox


def test_review_comparison_exposes_markdown_and_only_public_assets(
    tmp_path: Path,
) -> None:
    root = make_result(tmp_path)
    source = ReviewSource(root=root, id="sample")
    image = root / "assets" / "images" / "example.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")

    comparison = source.comparison()

    assert comparison["markdown"].startswith("<!-- page:1 -->")
    assert comparison["selected_pages"] == [1]
    assert comparison["pdf_url"] == "/api/sources/sample/pdf"
    assert comparison["artifact_base_url"] == "/api/sources/sample/artifacts/"
    assert source.artifact_path("assets/images/example.png") == image
    for relative in ("output.md", "../sample.pdf", "/etc/passwd"):
        try:
            source.artifact_path(relative)
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError(f"unexpected public artifact: {relative}")


def test_review_application_state_is_persisted(tmp_path: Path) -> None:
    root = make_result(tmp_path)
    source = ReviewSource(root=root, id="sample")
    assert source.summary()["application_state"] == "no_apply_needed"

    source.save_draft(
        "table-0001",
        {"status": "approved", "rows": [["A", "B"], ["3", "4"]]},
    )
    assert source.summary()["application_state"] == "pending_apply"

    source.publish()
    applied = source.summary()
    assert applied["application_state"] == "applied"
    assert applied["last_applied_at"]

    source.save_draft(
        "table-0001",
        {"status": "ignored", "rows": [["A", "B"], ["5", "6"]]},
    )
    pending = source.summary()
    assert pending["application_state"] == "pending_apply"
    assert pending["draft_updated_at"] > pending["last_applied_at"]


def test_note_and_mark_do_not_require_apply(tmp_path: Path) -> None:
    root = make_result(tmp_path)
    source = ReviewSource(root=root, id="sample")

    source.save_draft(
        "table-0001",
        {
            "status": "unreviewed",
            "note": "Check this again",
            "marked": True,
            "rows": [["A", "B"], ["1", "2"]],
        },
    )

    summary = source.summary()
    assert summary["application_state"] == "no_apply_needed"
    assert summary["noted_table_count"] == 1
    assert summary["marked_table_count"] == 1


def test_review_status_does_not_control_published_rows(tmp_path: Path) -> None:
    root = make_result(tmp_path)
    source = ReviewSource(root=root, id="sample")
    source.save_draft(
        "table-0001",
        {
            "status": "ignored",
            "selected_source": "custom",
            "rows": [["A", "B"], ["changed", "value"]],
        },
    )

    result = source.publish()

    assert result["published_tables"] == ["table-0001"]
    assert result["ignored_tables"] == ["table-0001"]
    assert "| changed | value |" in (root / "output.md").read_text()
    assert (
        root / "assets" / "tables" / "table-0001.csv"
    ).read_text() == "A,B\nchanged,value\n"
    metadata = read_json(root / "assets" / "metadata.json")
    assert metadata["tables"][0]["review"]["status"] == "ignored"
    assert metadata["tables"][0]["review"]["selected_source"] == "custom"
    assert source.summary()["tables"][0]["status"] == "ignored"
    assert source.detail("table-0001")["ai_rows"] == [["A", "B"], ["1", "2"]]

    source.save_draft(
        "table-0001",
        {"status": "unreviewed", "rows": [["A", "B"], ["still", "applied"]]},
    )
    source.publish()
    assert "| still | applied |" in (root / "output.md").read_text()


def test_legacy_review_statuses_are_normalized(tmp_path: Path) -> None:
    root = make_result(tmp_path)
    source = ReviewSource(root=root, id="sample")
    write_json(
        source.draft_path,
        {"schema_version": 1, "tables": {"table-0001": {"status": "edited"}}},
    )
    assert source.summary()["tables"][0]["status"] == "approved"


def test_resolve_multiple_results_and_batch(tmp_path: Path) -> None:
    first = make_result(tmp_path / "one")
    second = make_result(tmp_path / "two")
    roots = resolve_results(results=[first], batches=[second.parent])
    assert roots == [first.resolve(), second.resolve()]
    assert ReviewProject(roots).catalog()["source_count"] == 2


def test_review_catalog_skips_temporarily_missing_result(tmp_path: Path) -> None:
    stable = make_result(tmp_path / "stable")
    changing = make_result(tmp_path / "changing")
    project = ReviewProject([stable, changing])

    (changing / "assets" / "metadata.json").unlink()

    catalog = project.catalog()
    assert catalog["source_count"] == 1
    assert catalog["table_count"] == 1
