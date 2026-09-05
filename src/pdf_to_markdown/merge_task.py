from __future__ import annotations

import shutil
from collections import defaultdict
from typing import Any

from .assets_task import csv_to_markdown
from .io_utils import read_json, relative_reference, relativize, sha256_file, write_json
from .merge_rules import find_image_absorbed_blocks, find_table_absorbed_blocks
from .models import BBox, DocumentBlock, PipelineConfig, TaskResult
from .task import PipelineTask


def normalized_text(value: str) -> str:
    return " ".join(value.replace("*", "").replace("#", "").split()).casefold()


def horizontal_overlap(a: BBox, b: BBox) -> float:
    overlap = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
    denominator = min(a.width, b.width)
    return overlap / denominator if denominator else 0.0


def choose_docling_table(
    table_bbox: BBox, blocks: list[DocumentBlock], used: set[str]
) -> tuple[DocumentBlock | None, float]:
    candidates = []
    for block in blocks:
        if block.id in used or block.kind != "table" or block.bbox is None:
            continue
        score = table_bbox.overlap_over_smaller(block.bbox)
        if score > 0:
            candidates.append((score, block))
    if not candidates:
        return None, 0.0
    score, block = max(candidates, key=lambda item: item[0])
    return (block, score) if score >= 0.35 else (None, score)


def insertion_order(
    table_bbox: BBox, blocks: list[DocumentBlock], absorbed: list[DocumentBlock]
) -> float:
    if absorbed:
        return float(min(block.order for block in absorbed))
    above = [
        block
        for block in blocks
        if block.bbox is not None
        and block.bbox.bottom <= table_bbox.top + 2
        and horizontal_overlap(table_bbox, block.bbox) >= 0.15
    ]
    if above:
        closest = min(above, key=lambda block: table_bbox.top - block.bbox.bottom)  # type: ignore[union-attr]
        return closest.order + 0.5
    return min((block.order for block in blocks), default=0) - 0.5


def normalize_table_insertions(
    insertions: list[tuple[float, int, str]],
) -> list[tuple[float, int, str]]:
    """Keep physically ordered tables in the available document-order slots.

    The input must already be in top-to-bottom page order. Docling can map those
    tables to source blocks whose order is reversed. Reassigning the sorted set
    of proposed slots preserves the slots relative to surrounding prose while
    preventing same-page tables from swapping places.
    """
    available_orders = sorted(order for order, _index, _markdown in insertions)
    return [
        (order, index, markdown)
        for order, (_old_order, index, markdown) in zip(
            available_orders, insertions, strict=True
        )
    ]


def public_table_record(table: dict[str, Any]) -> dict[str, Any]:
    """Remove work-only implementation details from the final metadata."""
    return {
        key: table[key]
        for key in (
            "id",
            "page",
            "source_pages",
            "page_table_index",
            "name",
            "header_rows",
            "bbox",
            "csv",
            "extra_csvs",
            "merge",
            "lineage",
        )
        if key in table
    }


def collect_direct_table_relations(
    tables_by_page: dict[int, list[dict[str, Any]]],
    blocks_by_page: dict[int, list[DocumentBlock]],
) -> dict[str, list[dict[str, Any]]]:
    """Map Pi tables to same-page Docling tables before rendering."""
    relations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page, page_tables in tables_by_page.items():
        blocks = sorted(blocks_by_page.get(page, []), key=lambda block: block.order)
        used_docling: set[str] = set()
        for table in sorted(page_tables, key=lambda value: value["bbox"]["top"]):
            table_id = str(table["id"])
            bbox = BBox.from_dict(table["bbox"])
            matched, overlap = choose_docling_table(bbox, blocks, used_docling)
            if matched:
                used_docling.add(matched.id)
                relations[table_id].append(
                    {
                        "block_id": matched.id,
                        "method": "bbox_overlap",
                        "overlap": round(overlap, 4),
                    }
                )
            for block in blocks:
                if (
                    block.id in used_docling
                    or block.kind != "table"
                    or block.bbox is None
                    or not bbox.contains_center(block.bbox, margin=2.0)
                ):
                    continue
                used_docling.add(block.id)
                relations[table_id].append(
                    {
                        "block_id": block.id,
                        "method": "bbox_contains_center",
                        "overlap": round(bbox.overlap_over_smaller(block.bbox), 4),
                    }
                )
    return relations


def build_table_lineage(
    tables: list[dict[str, Any]],
    docling_tables: list[DocumentBlock],
    direct_relations: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build conservative, bidirectional Pi-to-Docling table lineage."""
    ordered_docling = sorted(
        docling_tables, key=lambda block: (block.page, block.order, block.id)
    )
    docling_ids = {
        block.id: f"docling-table-{index:04d}"
        for index, block in enumerate(ordered_docling, start=1)
    }
    blocks_by_id = {block.id: block for block in ordered_docling}
    tables_by_id = {str(table["id"]): table for table in tables}

    relations: dict[str, list[dict[str, Any]]] = {
        table_id: [] for table_id in tables_by_id
    }
    assigned_blocks: set[str] = set()
    for table_id, values in direct_relations.items():
        if table_id not in relations:
            continue
        for value in values:
            block_id = str(value["block_id"])
            if block_id not in blocks_by_id or block_id in assigned_blocks:
                continue
            relations[table_id].append(dict(value))
            assigned_blocks.add(block_id)

    ambiguous_candidates: dict[str, list[str]] = {}
    for block in ordered_docling:
        if block.id in assigned_blocks or block.bbox is None:
            continue
        candidates = []
        for table_id, table in tables_by_id.items():
            source_pages = [
                int(page) for page in table.get("source_pages", [table["page"]])
            ]
            if block.page not in source_pages or block.page == int(table["page"]):
                continue
            score = horizontal_overlap(BBox.from_dict(table["bbox"]), block.bbox)
            candidates.append((score, table_id))
        candidates.sort(reverse=True)
        if len(candidates) == 1 and candidates[0][0] >= 0.35:
            score, table_id = candidates[0]
            relations[table_id].append(
                {
                    "block_id": block.id,
                    "method": "continuation_source_page",
                    "overlap": round(score, 4),
                }
            )
            assigned_blocks.add(block.id)
        elif len(candidates) > 1:
            top_score, top_table_id = candidates[0]
            second_score = candidates[1][0]
            if top_score >= 0.35 and top_score - second_score > 0.05:
                relations[top_table_id].append(
                    {
                        "block_id": block.id,
                        "method": "continuation_horizontal_overlap",
                        "overlap": round(top_score, 4),
                    }
                )
                assigned_blocks.add(block.id)
            else:
                ambiguous_candidates[block.id] = [
                    table_id for _, table_id in candidates
                ]
        elif candidates:
            ambiguous_candidates[block.id] = [candidates[0][1]]

    lineage_by_table: dict[str, dict[str, Any]] = {}
    replacement_ids_by_block: dict[str, list[str]] = defaultdict(list)
    for table_id, table in tables_by_id.items():
        table_relations = []
        for relation in relations[table_id]:
            block_id = str(relation["block_id"])
            replacement_ids_by_block[block_id].append(table_id)
            public_relation = {
                "docling_table_id": docling_ids[block_id],
                "block_id": block_id,
                "method": relation["method"],
            }
            if "overlap" in relation:
                public_relation["overlap"] = relation["overlap"]
            table_relations.append(public_relation)
        source_pages = [
            int(page) for page in table.get("source_pages", [table["page"]])
        ]
        if not table_relations:
            action = "inserted_without_docling_table"
        elif len(table_relations) > 1 or len(source_pages) > 1:
            action = "merged_from_docling_tables"
        else:
            action = "replaced_docling_table"
        lineage_by_table[table_id] = {
            "action": action,
            "docling_table_ids": [
                relation["docling_table_id"] for relation in table_relations
            ],
            "relations": table_relations,
        }

    public_docling = []
    for block in ordered_docling:
        replacement_ids = replacement_ids_by_block.get(block.id, [])
        candidate_ids = ambiguous_candidates.get(block.id, [])
        methods = {
            relation["method"]
            for table_id in replacement_ids
            for relation in relations[table_id]
            if relation["block_id"] == block.id
        }
        if replacement_ids:
            status = (
                "absorbed_into_merged_table"
                if any(method.startswith("continuation_") for method in methods)
                else "replaced"
            )
        elif candidate_ids:
            status = "ambiguous_continuation"
        else:
            status = "unmatched"
        record: dict[str, Any] = {
            "id": docling_ids[block.id],
            "page": block.page,
            "block_id": block.id,
            "source_ref": block.source_ref,
            "bbox": (
                {
                    "coordinate_system": "pdfplumber_top_left",
                    "unit": "pt",
                    **block.bbox.to_dict(),
                }
                if block.bbox
                else None
            ),
            "status": status,
            "replacement_table_ids": replacement_ids,
        }
        if candidate_ids:
            record["candidate_replacement_table_ids"] = candidate_ids
        public_docling.append(record)
    return lineage_by_table, public_docling


def materialize_docling_tables(
    config: PipelineConfig,
    records: list[dict[str, Any]],
    blocks: list[DocumentBlock],
) -> list[str]:
    """Write optional source-table Markdown assets and remove stale copies."""
    target_dir = config.assets_dir / "docling-tables"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    blocks_by_id = {block.id: block for block in blocks}
    outputs: list[str] = []
    for record in records:
        record["retained"] = config.retain_docling_tables
        record["markdown"] = None
        if not config.retain_docling_tables:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{record['id']}.md"
        markdown = blocks_by_id[record["block_id"]].markdown.rstrip() + "\n"
        path.write_text(markdown, encoding="utf-8")
        record["markdown"] = relativize(path, config.output_dir)
        outputs.append(record["markdown"])
    return outputs


def mapped_continuation_block_ids(
    lineage_by_table: dict[str, dict[str, Any]],
) -> set[str]:
    """Return only Docling blocks confidently assigned as continuations."""
    return {
        str(relation["block_id"])
        for lineage in lineage_by_table.values()
        for relation in lineage["relations"]
        if str(relation["method"]).startswith("continuation_")
    }


def render_image_markdown(
    image: dict[str, Any], page: int, config: PipelineConfig
) -> str:
    analysis = image.get("analysis") or {}
    alt = str(
        analysis.get("alt_text") or image.get("caption") or f"PDF image on page {page}"
    )
    alt = " ".join(alt.replace("[", "").replace("]", "").split())
    parts = [
        f"<!-- image:{image['id']} page:{page} -->",
        f"![{alt}]({image['image']})",
    ]
    description = str(analysis.get("description") or "").strip()
    caption = str(image.get("caption") or "").strip()
    if description and (
        not caption
        or (
            normalized_text(description) not in normalized_text(caption)
            and normalized_text(caption) not in normalized_text(description)
        )
    ):
        parts.append(description)
    chart_csv = (image.get("chart_table") or {}).get("csv")
    if chart_csv:
        chart_markdown = csv_to_markdown(
            config.output_dir / str(chart_csv), None, header_rows=1
        ).strip()
        if chart_markdown:
            parts.append(chart_markdown)
    parts.append(f"<!-- /image:{image['id']} page:{page} -->")
    return "\n\n".join(parts)


class MergeMarkdownTask(PipelineTask):
    name = "08-merge-markdown"
    dependencies = ("01-docling", "06-collect-assets", "07-analyze-images")

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value["merge_logic_version"] = 12
        value["retain_docling_tables"] = config.retain_docling_tables
        for name, path in {
            "blocks": config.work_dir / "docling" / "blocks.json",
            "tables": config.work_dir / "table-assets" / "tables.json",
            "images": config.work_dir / "image-analysis" / "images.json",
        }.items():
            if path.is_file():
                value[f"{name}_sha256"] = sha256_file(path)
        table_markdown = sorted(
            (config.work_dir / "table-assets").glob("table-*/table.md")
        )
        value["table_markdown"] = {
            relativize(path, config.output_dir): sha256_file(path)
            for path in table_markdown
        }
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        block_payload = read_json(config.work_dir / "docling" / "blocks.json")
        table_payload = read_json(config.work_dir / "table-assets" / "tables.json")
        image_payload = read_json(config.work_dir / "image-analysis" / "images.json")
        images_by_block = {
            image["block_id"]: image
            for image in image_payload["images"]
            if image.get("block_id")
        }
        synthetic_images_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        all_images_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for image in image_payload["images"]:
            all_images_by_page[int(image["page"])].append(image)
            if not image.get("block_id"):
                synthetic_images_by_page[int(image["page"])].append(image)
        ignored_pictures = set(image_payload.get("ignored_picture_blocks", []))
        ignored_pictures.update(
            str(image["block_id"])
            for image in image_payload["images"]
            if image.get("block_id") and not image.get("include_in_markdown", True)
        )
        page_count = int(block_payload["page_count"])
        selected_pages = [
            int(page)
            for page in block_payload.get("selected_pages", range(1, page_count + 1))
        ]
        blocks_by_page: dict[int, list[DocumentBlock]] = defaultdict(list)
        for value in block_payload["blocks"]:
            block = DocumentBlock.from_dict(value)
            blocks_by_page[block.page].append(block)
        docling_tables = [
            block
            for blocks in blocks_by_page.values()
            for block in blocks
            if block.kind == "table"
        ]
        tables_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for table in table_payload["tables"]:
            tables_by_page[int(table["page"])].append(table)

        merge_records = []
        image_merge_records = []
        direct_relations = collect_direct_table_relations(
            tables_by_page, blocks_by_page
        )
        lineage_by_table, public_docling_tables = build_table_lineage(
            table_payload["tables"], docling_tables, direct_relations
        )
        for table in table_payload["tables"]:
            table["lineage"] = lineage_by_table[str(table["id"])]
        continuation_absorbed_ids = mapped_continuation_block_ids(lineage_by_table)
        output_pages = []
        for page in selected_pages:
            blocks = sorted(blocks_by_page.get(page, []), key=lambda block: block.order)
            used_docling: set[str] = set()
            absorbed_ids: set[str] = {
                caption.id
                for caption in blocks
                if caption.kind == "caption"
                and normalized_text(caption.markdown)
                and any(
                    other.id != caption.id
                    and other.kind in {"picture", "table"}
                    and normalized_text(caption.markdown)
                    in normalized_text(other.markdown)
                    for other in blocks
                )
            }
            absorbed_ids.update(
                block.id for block in blocks if block.id in ignored_pictures
            )
            # A continuation page may also contain independent tables. Remove
            # only Docling blocks already mapped to the merged Pi table.
            absorbed_ids.update(
                block.id for block in blocks if block.id in continuation_absorbed_ids
            )
            for block in blocks:
                image = images_by_block.get(block.id)
                if image and image.get("include_in_markdown", True):
                    block.markdown = render_image_markdown(image, page, config)
                    if image.get("caption_block_id"):
                        absorbed_ids.add(image["caption_block_id"])
            insertions: list[tuple[float, int, str]] = []
            for image_index, image in enumerate(synthetic_images_by_page.get(page, [])):
                if not image.get("include_in_markdown", True):
                    continue
                insertions.append(
                    (
                        float(image.get("order", 0)),
                        500 + image_index,
                        render_image_markdown(image, page, config),
                    )
                )
            page_tables = sorted(
                tables_by_page.get(page, []),
                key=lambda table: (
                    float(table["bbox"]["top"]),
                    int(table["page_table_index"]),
                ),
            )
            table_insertions: list[tuple[float, int, str]] = []
            table_merges: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for table_index, table in enumerate(page_tables):
                bbox = BBox.from_dict(table["bbox"])
                matched, overlap = choose_docling_table(bbox, blocks, used_docling)
                absorbed = find_table_absorbed_blocks(
                    bbox, blocks, used_docling | absorbed_ids
                )
                if matched:
                    used_docling.add(matched.id)
                    absorbed_ids.add(matched.id)
                    order = float(matched.order)
                    action = "replaced_docling_table"
                else:
                    order = insertion_order(bbox, blocks, absorbed)
                    action = (
                        "replaced_text_fragments"
                        if absorbed
                        else "inserted_missing_table"
                    )
                for block in absorbed:
                    absorbed_ids.add(block.id)
                    if block.kind == "table":
                        used_docling.add(block.id)
                    image = images_by_block.get(block.id)
                    if image:
                        image["include_in_markdown"] = False
                        image["merge"] = {
                            "action": "absorbed_by_table",
                            "table_id": table["id"],
                        }
                markdown = (
                    (config.output_dir / table["internal"]["markdown"])
                    .read_text(encoding="utf-8")
                    .strip()
                )
                end_page = max(
                    int(value) for value in table.get("source_pages", [page])
                )
                wrapped = (
                    f"<!-- table:{table['id']} page:{page} -->\n\n"
                    f"{markdown}\n\n"
                    f"<!-- /table:{table['id']} page:{end_page} -->"
                )
                merge = {
                    "action": action,
                    "matched_block": matched.id if matched else None,
                    "matched_source_ref": matched.source_ref if matched else None,
                    "overlap": round(overlap, 4),
                    "absorbed_blocks": [block.id for block in absorbed],
                    "insertion_order": order,
                }
                table_insertions.append((order, table_index, wrapped))
                table_merges.append((table, merge))

            normalized_table_insertions = normalize_table_insertions(
                table_insertions
            )
            for insertion, (table, merge) in zip(
                normalized_table_insertions, table_merges, strict=True
            ):
                normalized_order = insertion[0]
                original_order = float(merge["insertion_order"])
                if normalized_order != original_order:
                    merge["source_insertion_order"] = original_order
                merge["insertion_order"] = normalized_order
                table["merge"] = merge
                merge_records.append(
                    {"table_id": table["id"], "page": page, **merge}
                )
            insertions.extend(normalized_table_insertions)

            for image in all_images_by_page.get(page, []):
                absorbed = find_image_absorbed_blocks(image, blocks, absorbed_ids)
                if not absorbed:
                    continue
                absorbed_ids.update(block.id for block in absorbed)
                image_merge = {
                    "action": "absorbed_text_fragments",
                    "absorbed_blocks": [block.id for block in absorbed],
                }
                image["merge"] = image_merge
                image_merge_records.append(
                    {"image_id": image["id"], "page": page, **image_merge}
                )

            entries: list[tuple[float, int, str]] = []
            for block in blocks:
                if block.id not in absorbed_ids and block.markdown.strip():
                    entries.append((float(block.order), 1000, block.markdown.strip()))
            entries.extend(insertions)
            entries.sort(key=lambda item: (item[0], item[1]))
            body = "\n\n".join(item[2] for item in entries).strip()
            output_pages.append(f"<!-- page:{page} -->\n\n{body}".rstrip())

        output_markdown = config.output_dir / "output.md"
        output_markdown.write_text(
            "\n\n---\n\n".join(output_pages).rstrip() + "\n", encoding="utf-8"
        )
        retained_outputs = materialize_docling_tables(
            config, public_docling_tables, docling_tables
        )
        merge_report = config.work_dir / "diagnostics" / "merge-report.json"
        write_json(
            merge_report,
            {
                "schema_version": 5,
                "tables": merge_records,
                "images": image_merge_records,
                "lineage": lineage_by_table,
                "docling_tables": public_docling_tables,
            },
        )
        metadata = config.assets_dir / "metadata.json"
        candidate_payload = read_json(config.work_dir / "candidate-pages.json")
        public_tables = [
            public_table_record(table) for table in table_payload["tables"]
        ]
        write_json(
            metadata,
            {
                "schema_version": 3,
                "source": {
                    "file": config.pdf.name,
                    "path": relative_reference(config.pdf, config.output_dir),
                    "sha256": sha256_file(config.pdf),
                    "page_count": page_count,
                    "selected_pages": selected_pages,
                },
                "table_detection": {
                    "pages": candidate_payload.get("pages", []),
                    "sources": candidate_payload.get("sources", {}),
                },
                "tables": public_tables,
                "docling_tables": public_docling_tables,
                "images": image_payload["images"],
                "output": {
                    "markdown": "output.md",
                    "sha256": sha256_file(output_markdown),
                },
            },
        )
        return TaskResult(
            self.name,
            "completed",
            [
                relativize(output_markdown, config.output_dir),
                relativize(metadata, config.output_dir),
                relativize(merge_report, config.output_dir),
                *retained_outputs,
            ],
            {
                "page_count": page_count,
                "selected_pages": selected_pages,
                "table_count": len(merge_records),
                "docling_table_count": len(public_docling_tables),
                "retained_docling_table_count": len(retained_outputs),
            },
        )
