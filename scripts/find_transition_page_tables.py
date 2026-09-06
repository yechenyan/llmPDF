#!/usr/bin/env python3
"""Find pages where a multi-page table ends above another table."""

from __future__ import annotations

import argparse
import ast
import gzip
import json
import re
from pathlib import Path
from typing import Any


TABLE_TITLE = re.compile(r"\b(?:tabelle|table)\s*\d+", re.IGNORECASE)


def _constant_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _range_values(node: ast.AST) -> list[int]:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "range"
    ):
        return []
    values = [_constant_int(argument) for argument in node.args]
    if any(value is None for value in values):
        return []
    return list(range(*values))  # type: ignore[arg-type]


def _extractor_pages(path: Path) -> set[int]:
    if not path.is_file():
        return set()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    pages = {int(value) for value in re.findall(r"page_(\d{4})\.pdf", source)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not any(
                isinstance(target, ast.Name) and "PAGE" in target.id.upper()
                for target in targets
            ):
                continue
            pages.update(_range_values(value))
            if isinstance(value, (ast.List, ast.Tuple)):
                pages.update(
                    page
                    for item in value.elts
                    if (page := _constant_int(item)) is not None
                )
        elif (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and "page" in node.target.id.lower()
        ):
            pages.update(_range_values(node.iter))
    return pages


def _bbox(table: dict[str, Any], page: int) -> dict[str, float] | None:
    page_bbox = (table.get("page_bboxes") or {}).get(str(page))
    if page_bbox:
        return page_bbox
    if int(table.get("page", -1)) == page:
        return table.get("bbox")
    return None


def _pages(table: dict[str, Any], result_dir: Path) -> list[int]:
    anchor = int(table["page"])
    pages = {
        int(page) for page in table.get("source_pages", [anchor])
    }
    inferred = _extractor_pages(
        result_dir / "work" / "table-code" / table["id"] / "extract.py"
    )
    if inferred and (
        anchor in inferred
        or anchor + 1 in inferred
        or min(inferred) <= anchor <= max(inferred)
    ):
        pages.update(inferred)
    return sorted(pages)


def _is_below(first: dict[str, float], second: dict[str, float]) -> bool:
    return float(second["top"]) >= float(first["bottom"]) - 3


def audit_result(result_dir: Path) -> list[dict[str, Any]]:
    metadata_path = result_dir / "assets" / "metadata.json"
    blocks_path = result_dir / "work" / "run-blocks.json.gz"
    if not metadata_path.is_file() or not blocks_path.is_file():
        return []

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    with gzip.open(blocks_path, "rt", encoding="utf-8") as stream:
        blocks = json.load(stream)["blocks"]
    tables = metadata.get("tables") or []
    by_page: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        by_page.setdefault(int(block["page"]), []).append(block)

    findings = []
    for table in tables:
        source_pages = _pages(table, result_dir)
        if len(source_pages) < 2:
            continue
        page = source_pages[-1]
        continuation_bbox = _bbox(table, page)
        page_blocks = by_page.get(page, [])
        docling_tables = sorted(
            (block for block in page_blocks if block.get("kind") == "table"),
            key=lambda block: float(block["bbox"]["top"]),
        )

        other_ai_tables = []
        for other in tables:
            if other["id"] == table["id"]:
                continue
            other_pages = {
                int(value)
                for value in other.get("source_pages", [other.get("page")])
            }
            other_bbox = _bbox(other, page)
            if page in other_pages and (
                continuation_bbox is None
                or other_bbox is None
                or _is_below(continuation_bbox, other_bbox)
            ):
                other_ai_tables.append(
                    {"id": other["id"], "name": other.get("name")}
                )

        lower_docling = []
        if continuation_bbox is not None:
            lower_docling = [
                block
                for block in docling_tables
                if _is_below(continuation_bbox, block["bbox"])
                and float(block["bbox"]["top"])
                > float(continuation_bbox["top"]) + 3
            ]
        elif len(docling_tables) > 1:
            lower_docling = docling_tables[1:]

        boundary_markers = []
        for lower in lower_docling:
            lower_top = float(lower["bbox"]["top"])
            upper_bottom = (
                float(continuation_bbox["bottom"])
                if continuation_bbox is not None
                else float(docling_tables[0]["bbox"]["bottom"])
            )
            for block in page_blocks:
                if block.get("kind") == "table":
                    continue
                bbox = block.get("bbox") or {}
                top = float(bbox.get("top", -1))
                bottom = float(bbox.get("bottom", -1))
                markdown = str(block.get("markdown", ""))
                if (
                    top >= upper_bottom - 3
                    and bottom <= lower_top + 3
                    and (
                        block.get("kind") == "section_header"
                        or TABLE_TITLE.search(markdown)
                    )
                ):
                    boundary_markers.append(
                        {"kind": block.get("kind"), "text": markdown.strip()}
                    )

        if not other_ai_tables and not boundary_markers:
            continue
        findings.append(
            {
                "result_dir": str(result_dir),
                "pdf": str(result_dir / metadata["source"]["file"]),
                "continuation_table": table["id"],
                "continuation_name": table.get("name"),
                "source_pages": source_pages,
                "transition_page": page,
                "other_ai_tables": other_ai_tables,
                "boundary_markers": boundary_markers,
                "confidence": "high" if other_ai_tables else "candidate",
            }
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path("/Users/maxiao/Documents/code2/NAP-markdown/nap-markdwon"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    findings = []
    for metadata_path in sorted(args.root.glob("*/assets/metadata.json")):
        findings.extend(audit_result(metadata_path.parent.parent))

    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            others = ", ".join(
                table["id"] for table in finding["other_ai_tables"]
            ) or "Docling boundary marker"
            relative = Path(finding["pdf"]).relative_to(args.root)
            print(
                f"{relative}: page {finding['transition_page']}, "
                f"{finding['continuation_table']} -> {others} "
                f"[{finding['confidence']}]"
            )
        print(f"\n{len(findings)} transition-page table(s) found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
