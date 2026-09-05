"""Reference-only spatial checks for CSV tables extracted from PDF text layers."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pdfplumber

from .io_utils import write_json as atomic_write_json


MAX_PRINTED_SUSPECTS = 12


@dataclass
class SuspectCell:
    row: int
    column: int
    csv_text: str
    spatial_text: str
    reason: str
    similarity: float


@dataclass
class InspectionResult:
    status: str
    message: str
    checked_cells: int
    skipped_cells: int
    suspects: list[SuspectCell]
    candidate_bbox: tuple[float, float, float, float] | None = None

    def to_text(self) -> str:
        lines = [
            f"TABLE_GUARD: {self.status}",
            self.message,
            "The reference check only locates text that may cross cell boundaries; it does not determine whether the table as a whole is correct.",
            f"checked_cells={self.checked_cells} skipped_cells={self.skipped_cells} "
            f"suspect_cells={len(self.suspects)}",
        ]
        for suspect in self.suspects[:MAX_PRINTED_SUSPECTS]:
            lines.extend(
                [
                    "",
                    f"row={suspect.row + 1} column={suspect.column + 1}",
                    f"csv: {compact_preview(suspect.csv_text)}",
                    f"spatial: {compact_preview(suspect.spatial_text)}",
                    f"reason: {suspect.reason}; similarity={suspect.similarity:.3f}",
                ]
            )
        remaining = len(self.suspects) - MAX_PRINTED_SUSPECTS
        if remaining > 0:
            lines.append(f"\n{remaining} more suspect cells are listed in the inspection JSON.")
        return "\n".join(lines)

    def write_json(self, path: Path) -> None:
        atomic_write_json(path, asdict(self))


def compact_preview(text: str, limit: int = 180) -> str:
    compact = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def normalized(text: str) -> str:
    return "".join(text.replace("\r", "").replace("\n", "").split())


def char_center(char: dict[str, Any]) -> tuple[float, float]:
    return (
        (float(char["x0"]) + float(char["x1"])) / 2,
        (float(char["top"]) + float(char["bottom"])) / 2,
    )


def chars_in_bbox(page: Any, bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    x0, top, x1, bottom = bbox
    selected = []
    for char in page.chars:
        x, y = char_center(char)
        if x0 <= x < x1 and top <= y < bottom:
            selected.append(char)
    return selected


def text_from_chars(chars: list[dict[str, Any]], line_tolerance: float = 1.0) -> str:
    if not chars:
        return ""
    lines: list[dict[str, Any]] = []
    for char in sorted(chars, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(char["top"])
        line = next(
            (candidate for candidate in lines if abs(top - candidate["top"]) <= line_tolerance),
            None,
        )
        if line is None:
            line = {"top": top, "chars": []}
            lines.append(line)
        line["chars"].append(char)
    rendered = []
    for line in sorted(lines, key=lambda item: item["top"]):
        ordered = sorted(line["chars"], key=lambda item: float(item["x0"]))
        rendered.append("".join(str(char.get("text", "")) for char in ordered).strip())
    return "\n".join(part for part in rendered if part).strip()


def read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream))


def table_shape(table: Any) -> tuple[int, int]:
    rows = len(table.rows)
    columns = max((len(row.cells) for row in table.rows), default=0)
    return rows, columns


def choose_table(page: Any, rows: int, columns: int) -> tuple[Any | None, str]:
    candidates = [table for table in page.find_tables() if table_shape(table) == (rows, columns)]
    if len(candidates) == 1:
        return candidates[0], ""
    if not candidates:
        return None, f"No PDF grid uniquely matches the {rows}×{columns} CSV structure."
    return None, f"Found {len(candidates)} PDF grids matching the {rows}×{columns} CSV structure; no reliable choice is possible."


def reason_for_difference(actual: str, spatial: str) -> str:
    actual_norm = normalized(actual)
    spatial_norm = normalized(spatial)
    if not actual_norm and spatial_norm:
        return "The CSV may be missing text from this cell boundary"
    if actual_norm and not spatial_norm:
        return "The CSV text has no spatial support within this cell boundary"
    if spatial_norm and spatial_norm in actual_norm and len(actual_norm) > len(spatial_norm):
        return "The CSV may contain text from an adjacent cell"
    if actual_norm and actual_norm in spatial_norm and len(spatial_norm) > len(actual_norm):
        return "The CSV may omit part of the text within this cell boundary"
    return "The CSV differs substantially from text reconstructed by character-center assignment"


def inspect_csv(pdf_path: Path, csv_path: Path) -> InspectionResult:
    rows = read_csv(csv_path)
    if not rows:
        return InspectionResult("REVIEW_SPATIAL_ASSIGNMENT", "The CSV is empty.", 0, 0, [])
    width = max(len(row) for row in rows)
    if any(len(row) != width for row in rows):
        return InspectionResult("REVIEW_SPATIAL_ASSIGNMENT", "CSV rows have inconsistent column counts.", 0, 0, [])

    with pdfplumber.open(pdf_path) as pdf:
        if len(pdf.pages) != 1:
            return InspectionResult("NOT_APPLICABLE", "The inspector only supports single-page PDFs.", 0, 0, [])
        page = pdf.pages[0]
        if not page.chars:
            return InspectionResult("NOT_APPLICABLE", "The page has no usable PDF text layer.", 0, 0, [])
        table, explanation = choose_table(page, len(rows), width)
        if table is None:
            return InspectionResult("NOT_APPLICABLE", explanation, 0, 0, [])

        suspects: list[SuspectCell] = []
        checked = 0
        skipped = 0
        for row_index, (csv_row, table_row) in enumerate(zip(rows, table.rows)):
            for column_index, (actual, bbox) in enumerate(zip(csv_row, table_row.cells)):
                if bbox is None:
                    skipped += 1
                    continue
                chars = chars_in_bbox(page, bbox)
                if chars and any(char.get("upright") is False for char in chars):
                    skipped += 1
                    continue
                spatial = text_from_chars(chars)
                actual_norm = normalized(actual)
                spatial_norm = normalized(spatial)
                checked += 1
                if actual_norm == spatial_norm:
                    continue
                similarity = SequenceMatcher(None, actual_norm, spatial_norm).ratio()
                threshold = 0.8 if min(len(actual_norm), len(spatial_norm)) < 10 else 0.9
                if similarity >= threshold:
                    continue
                suspects.append(
                    SuspectCell(
                        row=row_index,
                        column=column_index,
                        csv_text=actual,
                        spatial_text=spatial,
                        reason=reason_for_difference(actual, spatial),
                        similarity=similarity,
                    )
                )

        if suspects:
            return InspectionResult(
                "REVIEW_SPATIAL_ASSIGNMENT",
                f"Found {len(suspects)} cells that may require verification against the page image.",
                checked,
                skipped,
                suspects,
                tuple(table.bbox),
            )
        return InspectionResult(
            "NO_SPATIAL_ANOMALY",
            "No obvious cross-boundary text, duplicate assignment, or omission was detected.",
            checked,
            skipped,
            [],
            tuple(table.bbox),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = inspect_csv(args.pdf, args.csv)
    json_output = args.json_output or args.csv.with_suffix(".guard.json")
    result.write_json(json_output)
    print(result.to_text())


if __name__ == "__main__":
    main()
