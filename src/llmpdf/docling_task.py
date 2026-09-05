from __future__ import annotations

from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version
import subprocess
import sys
from typing import Any

from .io_utils import read_json, relativize, write_json
from .models import BBox, DocumentBlock, PipelineConfig, TaskResult
from .pages import effective_pages
from .progress import report_progress
from .task import PipelineTask

DEFAULT_DOCLING_OPTIONS: dict[str, Any] = {
    "do_ocr": False,
    "images_scale": 2.0,
    "generate_picture_images": True,
}

DOCLING_SINGLE_THREAD_FALLBACK: dict[str, Any] = {
    "accelerator_options": {"num_threads": 1},
    "layout_batch_size": 1,
    "table_batch_size": 1,
    "ocr_batch_size": 1,
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def merged_docling_options(override: dict[str, Any]) -> dict[str, Any]:
    return deep_merge(DEFAULT_DOCLING_OPTIONS, override)


def build_pdf_pipeline_options(
    override: dict[str, Any],
) -> tuple[object, dict[str, Any]]:
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    merged = merged_docling_options(override)
    unknown = sorted(set(merged) - set(PdfPipelineOptions.model_fields))
    if unknown:
        raise ValueError(
            f"Unknown Docling PDF pipeline option(s): {', '.join(unknown)}"
        )
    pipeline_options = PdfPipelineOptions(**merged)
    effective = pipeline_options.model_dump(mode="json")
    return pipeline_options, effective


def converter_name(converter: object) -> str:
    converter_type = type(converter)
    return f"{converter_type.__module__}.{converter_type.__qualname__}"


def item_markdown(item: object, document: object) -> str:
    label = str(
        getattr(
            getattr(item, "label", None), "value", getattr(item, "label", "unknown")
        )
    )
    if label == "table" and hasattr(item, "export_to_markdown"):
        return str(item.export_to_markdown(document)).strip()
    if label == "picture" and hasattr(item, "export_to_markdown"):
        from docling_core.types.doc import ImageRefMode

        return str(
            item.export_to_markdown(document, image_mode=ImageRefMode.PLACEHOLDER)
        ).strip()
    text = str(getattr(item, "text", getattr(item, "code", ""))).strip()
    if not text:
        return ""
    if label == "title":
        return f"# {text}"
    if label == "section_header":
        level = max(2, min(6, int(getattr(item, "level", 2) or 2)))
        return f"{'#' * level} {text}"
    if label == "list_item":
        marker = str(getattr(item, "marker", "-") or "-")
        return f"{marker} {text}"
    if label == "code":
        return f"```\n{text}\n```"
    if label == "formula":
        return f"$$\n{text}\n$$"
    return text


class DoclingTask(PipelineTask):
    name = "01-docling"

    def is_cached(self, config: PipelineConfig) -> bool:
        if (
            config.document_converter is not None
            and not config.document_converter_cache_key
        ):
            return False
        return super().is_cached(config)

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        try:
            value["docling_version"] = version("docling")
        except PackageNotFoundError:
            value["docling_version"] = "unavailable"
        value["layout_version"] = 5
        if config.document_converter is not None:
            value["document_converter"] = converter_name(config.document_converter)
            value["document_converter_cache_key"] = config.document_converter_cache_key
        else:
            options = merged_docling_options(config.docling_options)
            value["docling_options"] = options
            value["do_ocr"] = options.get("do_ocr")
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        if config.document_converter is not None:
            return self._run_in_process(config)

        result = self._run_isolated(config, config.docling_options)
        if result is not None:
            return result

        fallback_options = deep_merge(
            config.docling_options, DOCLING_SINGLE_THREAD_FALLBACK
        )
        if fallback_options == config.docling_options:
            raise RuntimeError("Docling failed while already using one parsing thread")
        report_progress(
            config,
            "Docling process failed; retrying this PDF once with one parsing thread",
        )
        result = self._run_isolated(config, fallback_options)
        if result is None:
            raise RuntimeError(
                "Docling failed with its requested settings and again with one "
                "parsing thread"
            )
        result.details["single_thread_fallback"] = True
        return result

    def _run_isolated(
        self, config: PipelineConfig, docling_options: dict[str, Any]
    ) -> TaskResult | None:
        control_dir = config.work_dir / "docling"
        control_dir.mkdir(parents=True, exist_ok=True)
        request_path = control_dir / "worker-request.json"
        result_path = control_dir / "worker-result.json"
        result_path.unlink(missing_ok=True)
        write_json(
            request_path,
            {
                "pdf": str(config.pdf.resolve()),
                "output_dir": str(config.output_dir.resolve()),
                "selected_pages": list(config.selected_pages)
                if config.selected_pages
                else None,
                "docling_options": docling_options,
                "show_progress": config.show_progress,
                "result_path": str(result_path.resolve()),
            },
        )
        completed = subprocess.run(
            [sys.executable, "-m", "llmpdf.docling_worker", str(request_path)],
            check=False,
        )
        request_path.unlink(missing_ok=True)
        if completed.returncode != 0 or not result_path.is_file():
            result_path.unlink(missing_ok=True)
            report_progress(
                config,
                f"isolated Docling process exited with code {completed.returncode}",
            )
            return None
        payload = read_json(result_path)
        result_path.unlink(missing_ok=True)
        if not isinstance(payload, dict):
            return None
        return TaskResult(
            task=str(payload["task"]),
            status=str(payload["status"]),
            outputs=list(payload.get("outputs") or []),
            details=dict(payload.get("details") or {}),
        )

    def _run_in_process(self, config: PipelineConfig) -> TaskResult:
        output = config.work_dir / "docling"
        output.mkdir(parents=True, exist_ok=True)
        pictures_dir = output / "pictures"
        pictures_dir.mkdir(parents=True, exist_ok=True)
        effective_options = output / "effective-options.json"
        if config.document_converter is not None:
            converter = config.document_converter
            write_json(
                effective_options,
                {
                    "mode": "injected_document_converter",
                    "converter": converter_name(converter),
                    "cache_key": config.document_converter_cache_key,
                },
            )
        else:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter, PdfFormatOption

            pipeline_options, effective = build_pdf_pipeline_options(
                config.docling_options
            )
            converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
            write_json(
                effective_options,
                {"mode": "pdf_pipeline_options", "options": effective},
            )
        if config.document_converter is not None or config.selected_pages is None:
            result = converter.convert(config.pdf.resolve())
        else:
            first, last = config.selected_pages[0], config.selected_pages[-1]
            report_progress(
                config,
                f"Docling processing physical pages {first}-{last} "
                f"({len(config.selected_pages)} selected)",
            )
            result = converter.convert(config.pdf.resolve(), page_range=(first, last))
        document = result.document

        document_json = output / "document.json"
        raw_markdown = output / "raw.md"
        blocks_json = output / "blocks.json"
        page_markdown_dir = output / "pages"
        page_markdown_dir.mkdir(exist_ok=True)

        write_json(document_json, document.export_to_dict())
        if config.document_converter is None:
            from pypdf import PdfReader

            page_count = len(PdfReader(config.pdf).pages)
        else:
            page_count = len(document.pages)
        selected_pages = effective_pages(config.selected_pages, page_count)
        selected_set = set(selected_pages)
        raw_markdown.write_text(
            "\n\n<!-- page-break -->\n\n".join(
                document.export_to_markdown(page_no=page).rstrip()
                for page in selected_pages
            ).rstrip()
            + "\n",
            encoding="utf-8",
        )

        blocks: list[DocumentBlock] = []
        table_pages: set[int] = set()
        for order, (item, _level) in enumerate(
            document.iterate_items(with_groups=False)
        ):
            provenance = getattr(item, "prov", None) or []
            if not provenance:
                continue
            prov = provenance[0]
            page = int(prov.page_no)
            if page not in selected_set:
                continue
            page_item = document.pages[page]
            converted = prov.bbox.to_top_left_origin(page_item.size.height)
            bbox = BBox(
                float(converted.l),
                float(converted.t),
                float(converted.r),
                float(converted.b),
            )
            label = str(
                getattr(
                    getattr(item, "label", None),
                    "value",
                    getattr(item, "label", "unknown"),
                )
            )
            markdown = item_markdown(item, document)
            if label == "table":
                table_pages.add(page)
            block_id = f"block-{order:06d}"
            blocks.append(
                DocumentBlock(
                    id=block_id,
                    page=page,
                    order=order,
                    kind=label,
                    markdown=markdown,
                    bbox=bbox,
                    source_ref=str(getattr(item, "self_ref", "")) or None,
                )
            )
            if label == "picture" and hasattr(item, "get_image"):
                image = item.get_image(document)
                if image is not None:
                    image.save(pictures_dir / f"{block_id}.png", "PNG", optimize=True)

        for page in selected_pages:
            text = document.export_to_markdown(page_no=page).rstrip() + "\n"
            (page_markdown_dir / f"page_{page:04d}.md").write_text(
                text, encoding="utf-8"
            )
        write_json(
            blocks_json,
            {
                "schema_version": 1,
                "coordinate_system": "pdfplumber_top_left",
                "page_count": page_count,
                "selected_pages": selected_pages,
                "docling_table_pages": sorted(table_pages),
                "blocks": [block.to_dict() for block in blocks],
            },
        )
        outputs = [
            document_json,
            raw_markdown,
            blocks_json,
            page_markdown_dir,
            pictures_dir,
            effective_options,
        ]
        return TaskResult(
            self.name,
            "completed",
            [relativize(path, config.output_dir) for path in outputs],
            {
                "page_count": page_count,
                "selected_pages": selected_pages,
                "block_count": len(blocks),
                "table_pages": sorted(table_pages),
            },
        )
