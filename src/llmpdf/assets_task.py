from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from .io_utils import copy_file, read_json, relativize, sha256_file, write_json
from .models import BBox, PipelineConfig, TaskResult
from .review_tables import read_csv_rows, rows_to_markdown
from .task import PipelineTask


def infer_header_rows(name: str | None) -> int:
    normalized = (name or "").casefold()
    headerless_terms = ("abkürz", "abbreviation", "glossar", "glossary")
    return 0 if any(term in normalized for term in headerless_terms) else 1


def csv_to_markdown(
    csv_path: Path, title: str | None, header_rows: int | None = None
) -> str:
    rows = read_csv_rows(csv_path)
    if not rows:
        return ""
    header_rows = infer_header_rows(title) if header_rows is None else header_rows
    return rows_to_markdown(rows, title, header_rows) + "\n"


class CollectAssetsTask(PipelineTask):
    name = "06-collect-assets"
    dependencies = ("05-extract-tables",)

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value["asset_logic_version"] = 7
        summary = config.work_dir / "table-extraction" / "summary.json"
        if summary.is_file():
            value["extraction_summary_sha256"] = sha256_file(summary)
            extraction = read_json(summary)
            artifacts: dict[str, str] = {}
            for relative_directory in extraction.get("table_directories", []):
                directory = config.output_dir / relative_directory
                for pattern in ("output_*.csv", "extract.py", "metadata.yaml"):
                    for path in sorted(directory.glob(pattern)):
                        artifacts[relativize(path, config.output_dir)] = sha256_file(
                            path
                        )
            value["extraction_artifacts"] = artifacts
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        extraction = read_json(config.work_dir / "table-extraction" / "summary.json")
        source_dirs = [
            config.output_dir / path for path in extraction.get("table_directories", [])
        ]
        continuation_by_leader = {
            int(group["leader_page"]): [int(page) for page in group["pages"]]
            for group in extraction.get("continuation_groups", [])
        }
        records = []
        sortable = []
        for source_dir in source_dirs:
            metadata = yaml.safe_load(
                (source_dir / "metadata.yaml").read_text(encoding="utf-8")
            )
            sortable.append(
                (
                    int(metadata["page"]),
                    int(metadata["page_table_index"]),
                    source_dir,
                    metadata,
                )
            )
        sortable.sort()

        internal_root = config.work_dir / "table-assets"
        tables_root = config.assets_dir / "tables"
        if config.force:
            for path in (internal_root, tables_root):
                if path.exists():
                    shutil.rmtree(path)
        internal_root.mkdir(parents=True, exist_ok=True)
        tables_root.mkdir(parents=True, exist_ok=True)
        for global_index, (page, page_index, source_dir, metadata) in enumerate(
            sortable, 1
        ):
            table_id = f"table-{global_index:04d}"
            internal = internal_root / table_id
            internal.mkdir(parents=True, exist_ok=True)
            csv_paths = sorted(source_dir.glob("output_*.csv"))
            if not csv_paths:
                continue
            primary_csv = copy_file(csv_paths[0], tables_root / f"{table_id}.csv")
            extra_csvs = []
            for extra_index, extra in enumerate(csv_paths[1:], 2):
                extra_csvs.append(
                    copy_file(extra, tables_root / f"{table_id}-{extra_index}.csv")
                )
            extractor = copy_file(source_dir / "extract.py", internal / "extract.py")
            source_metadata = copy_file(
                source_dir / "metadata.yaml", internal / "metadata.yaml"
            )
            markdown_path = internal / "table.md"
            header_rows = infer_header_rows(metadata.get("name"))
            markdown_path.write_text(
                csv_to_markdown(primary_csv, metadata.get("name"), header_rows),
                encoding="utf-8",
            )

            raw_page_bboxes = metadata.get("page_bboxes") or {}
            page_bboxes = {
                str(int(physical_page)): {
                    "coordinate_system": "pdfplumber_top_left",
                    "unit": "pt",
                    **BBox.from_dict(value).to_dict(),
                }
                for physical_page, value in raw_page_bboxes.items()
            }
            if page_bboxes:
                source_pages = sorted(int(value) for value in page_bboxes)
                page = source_pages[0]
                bbox_data = page_bboxes[str(page)]
            else:
                bbox_data = metadata["bbox"]
                source_pages = [
                    int(value) for value in metadata.get("source_pages", [page])
                ]
            bbox = BBox.from_dict(bbox_data)
            if source_pages == [page] and page in continuation_by_leader:
                anchored = [
                    item
                    for item in sortable
                    if int(item[0]) == page and f"page-{page:04d}" in item[2].parts
                ]
                # Older Agent outputs did not record source_pages. The fallback
                # is safe only when the continuation leader has one anchored table.
                if len(anchored) == 1:
                    source_pages = continuation_by_leader[page]
            record = {
                "id": table_id,
                "page": page,
                "source_pages": source_pages,
                "page_table_index": page_index,
                "name": metadata.get("name"),
                "header_rows": header_rows,
                "bbox": {
                    "coordinate_system": "pdfplumber_top_left",
                    "unit": "pt",
                    **bbox.to_dict(),
                },
                "page_bboxes": page_bboxes,
                "csv": relativize(primary_csv, config.output_dir),
                "extra_csvs": [
                    relativize(path, config.output_dir) for path in extra_csvs
                ],
                "internal": {
                    "markdown": relativize(markdown_path, config.output_dir),
                    "extractor": relativize(extractor, config.output_dir),
                    "metadata": relativize(source_metadata, config.output_dir),
                },
            }
            records.append(record)
        manifest = internal_root / "tables.json"
        write_json(manifest, {"schema_version": 2, "tables": records})
        return TaskResult(
            self.name,
            "completed",
            [relativize(manifest, config.output_dir)],
            {"table_count": len(records)},
        )
