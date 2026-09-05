from __future__ import annotations

import argparse
import csv
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path

import yaml

from .io_utils import write_json

from .table_guard import inspect_csv


Extractor = Callable[[Path, Path], None]


def _csv_summary(csv_path: Path) -> dict:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))

    widths = [len(row) for row in rows]
    expected_columns = widths[0] if widths else 0
    ragged_rows = [index + 1 for index, width in enumerate(widths) if width != expected_columns]
    empty_cells = [
        {"row": row_index + 1, "column": column_index + 1}
        for row_index, row in enumerate(rows)
        for column_index, value in enumerate(row)
        if not value.strip()
    ]
    repeated_header_rows = [
        index + 1
        for index, row in enumerate(rows[1:], 1)
        if row == rows[0]
    ] if rows else []
    return {
        "csv": csv_path.name,
        "rows": len(rows),
        "columns": max(widths, default=0),
        "header": rows[0] if rows else [],
        "first_rows": rows[1:4],
        "last_row": rows[-1] if len(rows) > 1 else [],
        "anomalies": {
            "ragged_rows": ragged_rows[:10],
            "empty_cell_count": len(empty_cells),
            "empty_cells": empty_cells[:10],
            "repeated_header_rows": repeated_header_rows[:10],
        },
    }


def _validation_status(spatial_status: str | None) -> str:
    if spatial_status is None:
        return "NOT_RUN"
    if spatial_status == "NO_SPATIAL_ANOMALY":
        return "PASSED"
    if spatial_status == "NOT_APPLICABLE":
        return "REQUIRES_VISUAL_REVIEW"
    return "REVIEW_REQUIRED"


def _page_info(output_dir: Path) -> dict:
    for parent in (output_dir, *output_dir.parents):
        path = parent / "assets" / "page_info.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("Could not find assets/page_info.json above the output directory")


def _table_index(output_dir: Path) -> int:
    match = re.fullmatch(r"table_(\d+)", output_dir.name)
    if match is None or int(match.group(1)) < 1:
        raise ValueError("The output directory name must be table_<positive number>")
    return int(match.group(1))


def _validated_bbox(bbox: Sequence[float], page_info: dict) -> tuple[float, float, float, float]:
    if len(bbox) != 4:
        raise ValueError("bbox must contain exactly four values: x0, top, x1, bottom")
    x0, top, x1, bottom = (float(value) for value in bbox)
    width = float(page_info["display_width_pt"])
    height = float(page_info["display_height_pt"])
    if not (0 <= x0 < x1 <= width and 0 <= top < bottom <= height):
        raise ValueError(
            f"bbox {(x0, top, x1, bottom)} is outside the page bounds {(width, height)}"
        )
    return x0, top, x1, bottom


def finalize_table(
    *,
    pdf_path: Path,
    output_dir: Path,
    name: str | None,
    bbox: Sequence[float],
    spatial_check: bool = False,
) -> Path:
    output_dir = output_dir.resolve()
    csv_paths = sorted(output_dir.glob("output_*.csv"))
    if not csv_paths:
        raise FileNotFoundError(f"No output_*.csv was generated in {output_dir}")

    page_info = _page_info(output_dir)
    x0, top, x1, bottom = _validated_bbox(bbox, page_info)
    metadata = {
        "schema_version": 1,
        "name": name,
        "page": int(page_info["physical_page"]),
        "page_table_index": _table_index(output_dir),
        "bbox": {
            "coordinate_system": "pdfplumber_top_left",
            "unit": "pt",
            "approximate": True,
            "x0": x0,
            "top": top,
            "x1": x1,
            "bottom": bottom,
        },
    }
    metadata_path = output_dir / "metadata.yaml"
    metadata_path.write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    if spatial_check:
        for csv_path in csv_paths:
            result = inspect_csv(pdf_path, csv_path)
            result.write_json(csv_path.with_suffix(".guard.json"))
            report = _csv_summary(csv_path)
            report["table"] = output_dir.name
            report["spatial_check"] = {
                "status": result.status,
                "message": result.message,
                "checked_cells": result.checked_cells,
                "skipped_cells": result.skipped_cells,
                "suspect_cells": len(result.suspects),
            }
            report["validation_status"] = _validation_status(result.status)
            report_path = csv_path.with_suffix(".validation.json")
            write_json(report_path, report)
            print("TABLE_REPORT")
            print(json.dumps(report, ensure_ascii=False, indent=2))

    return metadata_path


def run_extractor(
    extractor: Extractor,
    *,
    name: str | None,
    bbox: Sequence[float],
) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--spatial-check", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    extractor(args.pdf, args.output_dir)
    finalize_table(
        pdf_path=args.pdf,
        output_dir=args.output_dir,
        name=name,
        bbox=bbox,
        spatial_check=args.spatial_check,
    )
