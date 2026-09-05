from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from pdf_to_markdown import image_analysis_task
from pdf_to_markdown.image_analysis_task import (
    AnalyzeImagesTask,
    _annotate_picture_hints,
    _bounded_attachment,
    candidate_image_pages,
    image_analysis_prompt,
    image_pi_command,
    parse_image_response,
    validate_image_response,
)
from pdf_to_markdown.io_utils import read_json, write_json
from pdf_to_markdown.models import PipelineConfig


def valid_chart() -> dict:
    return {
        "page": 3,
        "images": [
            {
                "page_image_index": 1,
                "bbox": {"left": 0.1, "top": 0.2, "right": 0.9, "bottom": 0.7},
                "matched_docling_picture_ids": ["block-000001"],
                "classification": "chart",
                "alt_text": "Bar chart of annual load changes",
                "description": "Compares load changes across years.",
                "confidence": 0.95,
                "include_in_markdown": True,
                "needs_table_review": False,
                "chart": {
                    "status": "exact",
                    "columns": ["Year", "Load"],
                    "rows": [["2025", "12"]],
                    "unit": "MW",
                    "notes": [],
                },
            }
        ],
    }


def test_page_prompt_requires_all_images_and_forbids_ocr_tools() -> None:
    prompt = image_analysis_prompt(3, {"page": 3})
    assert "every meaningful image" in prompt
    assert "Each red box marks an image" in prompt
    assert "unmarked high-resolution full-page image" in prompt
    assert "single-page PDF" not in prompt
    assert "visually resemble ordinary body text" in prompt
    assert "primarily consists of continuous body text" not in prompt
    assert "Do not call tools" in prompt
    assert "any OCR program" in prompt
    assert "write the estimated values directly without adding an approximation symbol" in prompt
    assert 'description must begin with "AI visual extraction; values may be inaccurate: "' in prompt
    assert '"page": 3' in prompt


def test_image_pi_disables_all_tools() -> None:
    command = image_pi_command(
        ["pi"],
        Path("prompt.md"),
        [Path("page.png")],
        Path("session.jsonl"),
        "model",
        "medium",
    )
    assert "--no-tools" in command
    assert "@page.png" in command


def test_parse_and_validate_chart_response() -> None:
    parsed = parse_image_response(json.dumps(valid_chart(), ensure_ascii=False))
    result = validate_image_response(parsed, 3)
    assert result["images"][0]["chart"]["rows"] == [["2025", "12"]]


def test_chart_rows_must_match_columns() -> None:
    payload = valid_chart()
    payload["images"][0]["chart"]["rows"] = [["2025"]]
    with pytest.raises(ValueError, match="row width"):
        validate_image_response(payload, 3)


def test_non_chart_cannot_return_chart_data() -> None:
    payload = valid_chart()
    payload["images"][0]["classification"] = "diagram"
    with pytest.raises(ValueError, match="non-chart"):
        validate_image_response(payload, 3)


def test_candidate_pages_union_finder_and_docling() -> None:
    pages = candidate_image_pages(
        [
            {"page": 1, "has_image": False, "image_count_estimate": 0},
            {"page": 2, "has_image": True, "image_count_estimate": 1},
        ],
        [{"page": 3}],
    )
    assert pages == [2, 3]


def test_picture_hint_annotation_numbers_boxes_without_changing_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clean.png"
    annotated = tmp_path / "annotated.png"
    Image.new("RGB", (1000, 1200), "white").save(source)
    pictures = [
        {
            "order": 2,
            "bbox": {"x0": 200, "top": 300, "x1": 700, "bottom": 900},
        }
    ]

    _annotate_picture_hints(source, annotated, pictures, 1000, 1200)

    with Image.open(source) as clean:
        assert clean.getpixel((196, 300)) == (255, 255, 255)
    with Image.open(annotated) as marked:
        assert marked.getpixel((196, 300)) == (230, 45, 65)


def test_large_extracted_image_gets_model_only_bounded_copy(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    destination = tmp_path / "model.png"
    Image.new("RGB", (4096, 4096), "white").save(source)

    attachment = _bounded_attachment(source, destination, 10_000)

    assert attachment == destination
    with Image.open(source) as original:
        assert original.size == (4096, 4096)
    with Image.open(destination) as bounded:
        width, height = bounded.size
    assert ((width + 31) // 32) * ((height + 31) // 32) <= 10_000


def test_page_analysis_materializes_visual_image_and_chart_csv(
    tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "input.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    with pdf.open("wb") as stream:
        writer.write(stream)
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "out")
    write_json(
        config.work_dir / "docling" / "blocks.json",
        {"page_count": 1, "blocks": []},
    )
    write_json(
        config.work_dir / "detection" / "table-pages.json",
        {"pages": [{"page": 1, "has_image": True, "image_count_estimate": 1}]},
    )
    write_json(
        config.work_dir / "images" / "images.json",
        {"images": [], "ignored_picture_blocks": []},
    )
    write_json(config.work_dir / "table-assets" / "tables.json", {"tables": []})

    def fake_render(_config, _page, destination, _width_pt, _height_pt):
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1200), "white").save(destination)
        return {
            "target_dpi": 240,
            "dpi": 240,
            "estimated_width_px": 800,
            "estimated_height_px": 1200,
            "estimated_patches": 950,
            "max_image_patches": 10_000,
            "dpi_reduced": False,
            "width_px": 800,
            "height_px": 1200,
            "patches": 950,
        }

    def fake_analyze(_job_dir, page, _context, _attachments, _config):
        assert _attachments[0].name == "page_0001_annotated.png"
        assert _attachments[1].name == "page_0001_full.png"
        payload = valid_chart()
        payload["page"] = page
        payload["images"][0]["matched_docling_picture_ids"] = []
        return {"status": "completed", **payload}

    monkeypatch.setattr(image_analysis_task, "_render_page", fake_render)
    monkeypatch.setattr(image_analysis_task, "_analyze_job", fake_analyze)
    result = AnalyzeImagesTask().run(config)
    manifest = read_json(config.work_dir / "image-analysis" / "images.json")
    context = read_json(
        config.work_dir
        / "image-analysis"
        / "jobs"
        / "page-0001"
        / "page-context.json"
    )
    image = manifest["images"][0]
    assert result.details["chart_table_count"] == 1
    assert (config.output_dir / image["image"]).is_file()
    assert (config.output_dir / image["chart_table"]["csv"]).is_file()
    assert context["single_page_pdf"] == (
        "work/image-analysis/jobs/page-0001/assets/page_0001.pdf"
    )
    assert context["render"]["dpi"] == 240
