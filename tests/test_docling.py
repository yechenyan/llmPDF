from pathlib import Path
from types import SimpleNamespace

import llmpdf.docling_task as docling_module
from llmpdf.docling_task import (
    DEFAULT_DOCLING_OPTIONS,
    DoclingTask,
    merged_docling_options,
)
from llmpdf.io_utils import read_json
from llmpdf.io_utils import write_json
from llmpdf.models import PipelineConfig


def test_docling_cache_signature_records_that_ocr_is_disabled(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    payload = DoclingTask().signature_payload(
        PipelineConfig(pdf=pdf, output_dir=tmp_path / "out")
    )
    assert payload["do_ocr"] is False


def test_docling_options_deep_merge_and_participate_in_signature(
    tmp_path: Path,
) -> None:
    merged = merged_docling_options(
        {"images_scale": 3.0, "table_structure_options": {"mode": "accurate"}}
    )
    assert merged["do_ocr"] is False
    assert merged["images_scale"] == 3.0
    assert merged["table_structure_options"] == {"mode": "accurate"}
    assert DEFAULT_DOCLING_OPTIONS["images_scale"] == 2.0

    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(
        pdf=pdf,
        output_dir=tmp_path / "out",
        docling_options={"do_ocr": True},
    )
    assert DoclingTask().signature_payload(config)["docling_options"]["do_ocr"] is True


def test_injected_converter_without_cache_key_disables_docling_cache(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")

    class Converter:
        def convert(self, _path):
            return None

    config = PipelineConfig(
        pdf=pdf,
        output_dir=tmp_path / "out",
        document_converter=Converter(),
    )
    assert DoclingTask().is_cached(config) is False


def test_docling_task_uses_injected_converter_and_records_it(tmp_path: Path) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")

    class Document:
        def __init__(self):
            self.pages = {}

        def export_to_dict(self):
            return {"name": "fake"}

        def export_to_markdown(self, **_kwargs):
            return "fake markdown"

        def iterate_items(self, **_kwargs):
            return iter(())

    class Result:
        def __init__(self):
            self.document = Document()

    class Converter:
        def __init__(self):
            self.paths = []

        def convert(self, path):
            self.paths.append(path)
            return Result()

    converter = Converter()
    config = PipelineConfig(
        pdf=pdf,
        output_dir=tmp_path / "out",
        document_converter=converter,
        document_converter_cache_key="fake-v1",
    )
    result = DoclingTask().run(config)
    effective = read_json(config.work_dir / "docling" / "effective-options.json")
    assert converter.paths == [pdf.resolve()]
    assert effective["mode"] == "injected_document_converter"
    assert effective["cache_key"] == "fake-v1"
    assert result.details["page_count"] == 0


def test_docling_failure_retries_once_with_single_thread(
    tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(
        pdf=pdf,
        output_dir=tmp_path / "out",
        docling_options={"do_ocr": True, "images_scale": 3.0},
    )
    requests = []

    def fake_run(command, check):
        assert check is False
        request = read_json(Path(command[-1]))
        requests.append(request)
        if len(requests) == 1:
            return SimpleNamespace(returncode=-11)
        write_json(
            Path(request["result_path"]),
            {
                "task": "01-docling",
                "status": "completed",
                "outputs": ["work/docling/blocks.json"],
                "details": {"page_count": 1},
            },
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(docling_module.subprocess, "run", fake_run)
    result = DoclingTask().run(config)

    assert len(requests) == 2
    assert requests[0]["docling_options"] == {
        "do_ocr": True,
        "images_scale": 3.0,
    }
    retry = requests[1]["docling_options"]
    assert retry["do_ocr"] is True
    assert retry["images_scale"] == 3.0
    assert retry["accelerator_options"]["num_threads"] == 1
    assert retry["layout_batch_size"] == 1
    assert result.details["single_thread_fallback"] is True
