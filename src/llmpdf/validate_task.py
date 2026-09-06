from __future__ import annotations

import re

from .io_utils import read_json, relativize, sha256_file, write_json
from .models import PipelineConfig, TaskResult
from .task import PipelineTask


class ValidateTask(PipelineTask):
    name = "09-validate"
    dependencies = ("08-merge-markdown",)

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value["validation_logic_version"] = 8
        value["retain_docling_tables"] = config.retain_docling_tables
        output = config.output_dir / "output.md"
        metadata = config.assets_dir / "metadata.json"
        if output.is_file():
            value["output_sha256"] = sha256_file(output)
        if metadata.is_file():
            value["metadata_sha256"] = sha256_file(metadata)
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        markdown_path = config.output_dir / "output.md"
        metadata_path = config.assets_dir / "metadata.json"
        markdown = markdown_path.read_text(encoding="utf-8")
        metadata = read_json(metadata_path)
        errors: list[str] = []
        warnings: list[str] = []
        if int(metadata.get("schema_version", 0)) != 3:
            errors.append("metadata schema_version is not 3")
        pages = [int(value) for value in re.findall(r"<!-- page:(\d+) -->", markdown)]
        page_coverage: list[int] = []
        merged_page_markers: list[tuple[int, int, str]] = []
        for marker in re.finditer(
            r"<!-- page:(\d+) -->|<!-- pages:(\d+)-(\d+) merged-into:([^ ]+) -->",
            markdown,
        ):
            if marker.group(1):
                page_coverage.append(int(marker.group(1)))
                continue
            start = int(marker.group(2))
            end = int(marker.group(3))
            table_id = str(marker.group(4))
            merged_page_markers.append((start, end, table_id))
            page_coverage.extend(range(start, end + 1))
        source = metadata["source"]
        expected_pages = [
            int(page)
            for page in source.get(
                "selected_pages", range(1, int(source["page_count"]) + 1)
            )
        ]
        if expected_pages != sorted(set(expected_pages)):
            errors.append("source.selected_pages must be sorted and unique")
        if any(page < 1 or page > int(source["page_count"]) for page in expected_pages):
            errors.append("source.selected_pages contains an out-of-range page")
        if page_coverage != expected_pages:
            errors.append(
                f"page markers cover {page_coverage}, expected {expected_pages}"
            )
        table_markers = re.findall(r"<!-- table:([^ ]+) page:(\d+) -->", markdown)
        ordered_tables = sorted(
            metadata["tables"],
            key=lambda table: (
                int(table["page"]),
                float(table["bbox"]["top"]),
                int(table["page_table_index"]),
            ),
        )
        expected_tables = [
            (table["id"], str(table["page"])) for table in ordered_tables
        ]
        tables_by_id = {str(table["id"]): table for table in metadata["tables"]}
        for start, end, table_id in merged_page_markers:
            table = tables_by_id.get(table_id)
            continuation_pages = list(range(start, end + 1))
            expected_continuation = (
                [int(page) for page in table.get("source_pages", [table["page"]])][1:]
                if table
                else []
            )
            if table is None or continuation_pages != expected_continuation:
                errors.append(
                    f"merged page marker {start}-{end} does not match {table_id} source_pages"
                )
        if table_markers != expected_tables:
            errors.append(
                "table markers do not match metadata page/bbox order or contain duplicates"
            )
        table_end_markers = re.findall(r"<!-- /table:([^ ]+) page:(\d+) -->", markdown)
        expected_table_ends = [
            (
                str(table["id"]),
                str(
                    max(
                        int(page) for page in table.get("source_pages", [table["page"]])
                    )
                ),
            )
            for table in ordered_tables
        ]
        if table_end_markers != expected_table_ends:
            errors.append("table end markers do not match metadata source-page order")
        pi_table_ids = {str(table["id"]) for table in metadata["tables"]}
        pi_tables_by_id = {str(table["id"]): table for table in metadata["tables"]}
        docling_tables = metadata.get("docling_tables", [])
        docling_by_id = {str(table["id"]): table for table in docling_tables}
        docling_ids = set(docling_by_id)
        if len(docling_ids) != len(docling_tables):
            warnings.append("duplicate Docling table ids in metadata")
        matched_blocks: list[str] = []
        for table in metadata["tables"]:
            source_pages = [
                int(page) for page in table.get("source_pages", [table["page"]])
            ]
            if (
                source_pages != sorted(set(source_pages))
                or int(table["page"]) not in source_pages
            ):
                errors.append(f"{table['id']} has invalid source_pages: {source_pages}")
            for field in ("csv",):
                if not (config.output_dir / table[field]).is_file():
                    errors.append(f"missing {field} for {table['id']}: {table[field]}")
                elif not table[field].startswith("assets/"):
                    errors.append(
                        f"final {field} is outside assets for {table['id']}: {table[field]}"
                    )
            for extra_csv in table.get("extra_csvs", []):
                if not (config.output_dir / extra_csv).is_file():
                    errors.append(f"missing extra CSV for {table['id']}: {extra_csv}")
            if "internal" in table or any(
                field in table for field in ("markdown", "extractor", "metadata")
            ):
                errors.append(
                    f"{table['id']} exposes work-only paths in final metadata"
                )
            merge = table.get("merge", {})
            matched = merge.get("matched_block")
            if matched:
                matched_blocks.append(str(matched))
                if float(merge.get("overlap", 0)) < 0.35:
                    errors.append(
                        f"{table['id']} matched Docling block below overlap threshold"
                    )
            elif merge.get("action") not in {
                "replaced_text_fragments",
                "inserted_missing_table",
            }:
                errors.append(
                    f"{table['id']} has invalid unmapped merge action: {merge.get('action')}"
                )
            lineage = table.get("lineage", {})
            lineage_action = lineage.get("action")
            if lineage_action not in {
                "replaced_docling_table",
                "merged_from_docling_tables",
                "inserted_without_docling_table",
            }:
                warnings.append(
                    f"{table['id']} has invalid lineage action: {lineage_action}"
                )
            lineage_docling_ids = [
                str(value) for value in lineage.get("docling_table_ids", [])
            ]
            relation_docling_ids = [
                str(relation.get("docling_table_id"))
                for relation in lineage.get("relations", [])
            ]
            if lineage_docling_ids != relation_docling_ids:
                warnings.append(f"{table['id']} lineage ids do not match relations")
            if len(lineage_docling_ids) != len(set(lineage_docling_ids)):
                warnings.append(f"{table['id']} has duplicate Docling lineage ids")
            missing_docling = set(lineage_docling_ids) - docling_ids
            if missing_docling:
                warnings.append(
                    f"{table['id']} references missing Docling tables: {sorted(missing_docling)}"
                )
            if (
                lineage_action == "inserted_without_docling_table"
                and lineage_docling_ids
            ):
                warnings.append(f"{table['id']} has inserted lineage with Docling ids")
            if (
                lineage_action != "inserted_without_docling_table"
                and not lineage_docling_ids
            ):
                warnings.append(f"{table['id']} has mapped lineage without Docling ids")
            for docling_id in lineage_docling_ids:
                if docling_id not in docling_by_id:
                    continue
                replacements = {
                    str(value)
                    for value in docling_by_id[docling_id].get(
                        "replacement_table_ids", []
                    )
                }
                if str(table["id"]) not in replacements:
                    warnings.append(
                        f"{table['id']} and {docling_id} have asymmetric lineage"
                    )
        if len(matched_blocks) != len(set(matched_blocks)):
            errors.append(
                "multiple extracted tables mapped to the same Docling table block"
            )
        retained_count = 0
        for docling in docling_tables:
            docling_id = str(docling["id"])
            if docling.get("status") == "ambiguous_continuation":
                warnings.append(
                    f"{docling_id} has an ambiguous continuation mapping and was preserved"
                )
            replacements = {
                str(value) for value in docling.get("replacement_table_ids", [])
            }
            candidates = {
                str(value)
                for value in docling.get("candidate_replacement_table_ids", [])
            }
            missing_pi = (replacements | candidates) - pi_table_ids
            if missing_pi:
                warnings.append(
                    f"{docling_id} references missing Pi tables: {sorted(missing_pi)}"
                )
            for table_id in replacements:
                if table_id not in pi_tables_by_id:
                    continue
                reverse_ids = set(
                    pi_tables_by_id[table_id]
                    .get("lineage", {})
                    .get("docling_table_ids", [])
                )
                if docling_id not in reverse_ids:
                    warnings.append(
                        f"{docling_id} and {table_id} have asymmetric lineage"
                    )
            retained = bool(docling.get("retained"))
            retained_count += int(retained)
            markdown_asset = docling.get("markdown")
            if retained:
                if not isinstance(markdown_asset, str) or not markdown_asset.startswith(
                    "assets/docling-tables/"
                ):
                    errors.append(f"{docling_id} has invalid retained Markdown path")
                elif not (config.output_dir / markdown_asset).is_file():
                    errors.append(f"missing retained Docling table: {markdown_asset}")
            elif markdown_asset is not None:
                errors.append(f"{docling_id} has a Markdown path but is not retained")
        retained_dir = config.assets_dir / "docling-tables"
        if config.retain_docling_tables:
            if retained_count != len(docling_tables):
                errors.append("not all Docling tables were retained")
            if docling_tables and not retained_dir.is_dir():
                errors.append("retained Docling table directory is missing")
        else:
            if retained_count:
                errors.append("Docling tables retained while option is disabled")
            if retained_dir.exists():
                errors.append(
                    "stale Docling table directory remains while option is disabled"
                )
        image_links = re.findall(r"!\[[^\]]*\]\((assets/images/[^)]+)\)", markdown)
        included_images = [
            image
            for image in metadata.get("images", [])
            if image.get("include_in_markdown", True)
        ]
        expected_images = [image["image"] for image in included_images]
        if image_links != expected_images:
            errors.append(
                "Markdown image links do not match metadata image order or contain duplicates"
            )
        for image in metadata.get("images", []):
            if not (config.output_dir / image["image"]).is_file():
                errors.append(f"missing image asset: {image['image']}")
            if image.get("analysis_status") == "failed":
                warnings.append(f"image analysis failed: {image['id']}")
            chart = image.get("chart_table") or {}
            chart_csv = chart.get("csv")
            if chart_csv:
                if chart.get("status") not in {"exact", "approximate"}:
                    errors.append(f"{image['id']} has CSV with invalid chart status")
                if not str(chart_csv).startswith("assets/chart-tables/"):
                    errors.append(f"{image['id']} chart CSV is outside chart-tables")
                elif not (config.output_dir / str(chart_csv)).is_file():
                    errors.append(f"missing chart CSV for {image['id']}: {chart_csv}")
            elif chart.get("status") in {"exact", "approximate"}:
                errors.append(f"{image['id']} readable chart has no CSV")
        image_markers = re.findall(r"<!-- image:([^ ]+) page:(\d+) -->", markdown)
        expected_image_markers = [
            (str(image["id"]), str(image["page"])) for image in included_images
        ]
        if image_markers != expected_image_markers:
            errors.append(
                "image markers do not match metadata image order or contain duplicates"
            )
        image_end_markers = re.findall(r"<!-- /image:([^ ]+) page:(\d+) -->", markdown)
        if image_end_markers != expected_image_markers:
            errors.append(
                "image end markers do not match metadata image order or contain duplicates"
            )
        if "<!-- image -->" in markdown:
            errors.append("unresolved Docling image placeholder remains in output.md")
        if not markdown.strip():
            errors.append("output.md is empty")
        report = config.work_dir / "diagnostics" / "validation.json"
        write_json(
            report,
            {
                "schema_version": 1,
                "status": "failed" if errors else "passed",
                "errors": errors,
                "warnings": warnings,
                "page_count": len(page_coverage),
                "table_count": len(table_markers),
                "docling_table_count": len(docling_tables),
                "retained_docling_table_count": retained_count,
                "image_count": len(image_links),
            },
        )
        if errors:
            raise ValueError("; ".join(errors))
        return TaskResult(
            self.name,
            "completed",
            [relativize(report, config.output_dir)],
            {
                "page_count": len(page_coverage),
                "table_count": len(table_markers),
                "docling_table_count": len(docling_tables),
                "retained_docling_table_count": retained_count,
                "image_count": len(image_links),
                "warnings": warnings,
            },
        )
