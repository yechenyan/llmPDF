#!/usr/bin/env python3
"""Report actionable unmatched Docling tables."""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


TABLE_NUMBER = re.compile(r"\b(?:tabelle|table)\s*[:.]?\s*(\d+)\b", re.IGNORECASE)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower().replace("<br>", " ")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^a-z0-9äöüß€]+", " ", value)
    return " ".join(value.split())


def _useful(value: str) -> bool:
    return len(value) >= 2 and value not in {
        "ja",
        "nein",
        "kw",
        "km",
        "mw",
        "mva",
        "euro",
        "mio €",
    }


def _markdown_cells(path: Path) -> set[str]:
    cells = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells.update(
            value
            for cell in line.strip().strip("|").split("|")
            if _useful(value := _normalize(cell))
        )
    return cells


def _csv_cells(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {
            value
            for row in csv.reader(stream)
            for cell in row
            if _useful(value := _normalize(cell))
        }


def _horizontal_overlap(first: dict[str, float], second: dict[str, float]) -> float:
    intersection = max(
        0.0,
        min(float(first["x1"]), float(second["x1"]))
        - max(float(first["x0"]), float(second["x0"])),
    )
    smaller = min(
        float(first["x1"]) - float(first["x0"]),
        float(second["x1"]) - float(second["x0"]),
    )
    return intersection / smaller if smaller else 0.0


def _table_number(text: str) -> str | None:
    match = TABLE_NUMBER.search(text[:300])
    return match.group(1) if match else None


def _actionable_tables(
    result_dir: Path,
    metadata: dict[str, Any],
    unmatched: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ai_tables = []
    for table in metadata.get("tables", []):
        csv_path = result_dir / table["csv"]
        if not csv_path.is_file():
            continue
        ai_tables.append(
            {
                "id": table["id"],
                "page": int(table["page"]),
                "number": _table_number(str(table.get("name") or "")),
                "cells": _csv_cells(csv_path),
            }
        )

    documents = {}
    selected: dict[str, dict[str, Any]] = {}
    targets: dict[str, dict[str, Any]] = {}
    for table in unmatched:
        markdown_path = result_dir / str(table.get("markdown") or "")
        text = (
            markdown_path.read_text(encoding="utf-8", errors="replace")
            if markdown_path.is_file()
            else ""
        )
        cells = _markdown_cells(markdown_path) if markdown_path.is_file() else set()
        documents[table["id"]] = {"text": text, "cells": cells}

        best = None
        for ai_table in ai_tables:
            shared = cells & ai_table["cells"]
            recall = len(shared) / len(cells) if cells else 0.0
            score = (recall, len(shared), -abs(int(table["page"]) - ai_table["page"]))
            if len(shared) >= 3 and recall >= 0.45 and (
                best is None or score > best[0]
            ):
                best = (score, ai_table)

        reason = None
        target = None
        if best is not None:
            reason = "content_overlap"
            target = best[1]
        else:
            number = _table_number(text)
            numbered = [
                ai_table
                for ai_table in ai_tables
                if number is not None
                and ai_table["number"] == number
                and abs(int(table["page"]) - ai_table["page"]) <= 5
            ]
            if len(numbered) == 1:
                reason = "table_number_match"
                target = numbered[0]

        if target is not None:
            selected[table["id"]] = {
                "id": table["id"],
                "page": int(table["page"]),
                "block_id": table["block_id"],
                "reason": reason,
                "candidate_ai_table": target["id"],
            }
            targets[table["id"]] = target

    # Include blank or badly parsed continuation fragments between a selected
    # Docling table and its corresponding AI table.
    for selected_id, target in list(targets.items()):
        selected_table = next(table for table in unmatched if table["id"] == selected_id)
        left, right = sorted((int(selected_table["page"]), target["page"]))
        previous = selected_table
        for table in sorted(unmatched, key=lambda value: int(value["page"])):
            page = int(table["page"])
            if table["id"] in selected or not left <= page <= right:
                continue
            if page > int(previous["page"]) + 1:
                continue
            if not previous.get("bbox") or not table.get("bbox"):
                continue
            if _horizontal_overlap(previous["bbox"], table["bbox"]) < 0.75:
                continue
            selected[table["id"]] = {
                "id": table["id"],
                "page": page,
                "block_id": table["block_id"],
                "reason": "continuation_chain",
                "candidate_ai_table": target["id"],
            }
            previous = table

    return sorted(selected.values(), key=lambda table: (table["page"], table["id"]))


def audit(root: Path, *, include_all: bool) -> tuple[int, int, list[dict[str, Any]]]:
    scanned = 0
    raw_count = 0
    findings = []
    for report_path in sorted(root.glob("*/work/diagnostics/merge-report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        scanned += 1
        unmatched = [
            table
            for table in report.get("docling_tables", [])
            if table.get("status") == "unmatched"
        ]
        raw_count += len(unmatched)
        if not unmatched:
            continue
        result_dir = report_path.parents[2]
        metadata = json.loads(
            (result_dir / "assets" / "metadata.json").read_text(encoding="utf-8")
        )
        selected = (
            [
                {
                    "id": table["id"],
                    "page": int(table["page"]),
                    "block_id": table["block_id"],
                    "reason": "raw_unmatched",
                }
                for table in unmatched
            ]
            if include_all
            else _actionable_tables(result_dir, metadata, unmatched)
        )
        if not selected:
            continue
        findings.append(
            {
                "pdf": str(result_dir / metadata["source"]["file"]),
                "count": len(selected),
                "tables": selected,
            }
        )
    return scanned, raw_count, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path("/Users/maxiao/Documents/code2/NAP-markdown/nap-markdwon"),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include every raw unmatched table without actionable filtering",
    )
    args = parser.parse_args()

    scanned, raw_count, findings = audit(args.root, include_all=args.all)
    unmatched_count = sum(finding["count"] for finding in findings)
    if args.json:
        print(
            json.dumps(
                {
                    "scanned_pdfs": scanned,
                    "raw_unmatched_tables": raw_count,
                    "pdfs_with_unmatched": len(findings),
                    "unmatched_tables": unmatched_count,
                    "findings": findings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    for finding in findings:
        relative = Path(finding["pdf"]).relative_to(args.root)
        tables = ", ".join(
            f"{table['id']}@p{table['page']}" for table in finding["tables"]
        )
        print(f"{relative}: {finding['count']} unmatched ({tables})")
    print(
        f"\nScanned {scanned} PDFs; {len(findings)} PDFs contain "
        f"{unmatched_count} {'raw' if args.all else 'actionable'} unmatched "
        f"Docling tables ({raw_count} raw unmatched before filtering)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
