from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = [list(row) for row in csv.reader(stream)]
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def csv_text(rows: list[list[str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue()


def rows_to_markdown(rows: list[list[str]], title: str | None, header_rows: int) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]

    def clean(value: str) -> str:
        # A literal newline would end the Markdown table row. Keep each cell
        # line break, including consecutive breaks, as an inline break instead.
        return re.sub(r"\r\n|\r|\n", "<br>", value).replace("|", "\\|")

    lines: list[str] = []
    if title:
        lines.extend([f"**{title.strip()}**", ""])
    header = padded[0] if header_rows else [""] * width
    body = padded[1:] if header_rows else padded
    lines.append("| " + " | ".join(clean(value) for value in header) + " |")
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend(
        "| " + " | ".join(clean(value) for value in row) + " |" for row in body
    )
    return "\n".join(lines).rstrip()


def split_markdown_row(line: str) -> list[str]:
    value = line.strip().removeprefix("|")
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    cells = re.split(r"(?<!\\)\|", value)
    return [cell.strip().replace("\\|", "|").replace("<br>", "\n") for cell in cells]


def markdown_table_rows(markdown: str) -> list[list[str]]:
    lines = markdown.splitlines()
    for index in range(len(lines) - 1):
        if "|" not in lines[index] or "|" not in lines[index + 1]:
            continue
        header = split_markdown_row(lines[index])
        separator = split_markdown_row(lines[index + 1])
        if not header or len(header) != len(separator):
            continue
        if not all(
            re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator
        ):
            continue
        rows = [header]
        for line in lines[index + 2 :]:
            if "|" not in line:
                break
            row = split_markdown_row(line)
            rows.append(row + [""] * (len(header) - len(row)))
        return [row[: len(header)] for row in rows]
    return []


def combine_docling_rows(fragments: list[dict[str, Any]]) -> list[list[str]]:
    combined: list[list[str]] = []
    for fragment in fragments:
        rows = fragment.get("rows") or []
        if not rows:
            continue
        if combined and [value.strip().casefold() for value in rows[0]] == [
            value.strip().casefold() for value in combined[0]
        ]:
            combined.extend(rows[1:])
        else:
            combined.extend(rows)
    width = max((len(row) for row in combined), default=0)
    return [row + [""] * (width - len(row)) for row in combined]


def count_row_differences(left: list[list[str]], right: list[list[str]]) -> int:
    row_count = max(len(left), len(right))
    width = max((len(row) for row in [*left, *right]), default=0)
    return sum(
        (left[row][column] if row < len(left) and column < len(left[row]) else "")
        != (right[row][column] if row < len(right) and column < len(right[row]) else "")
        for row in range(row_count)
        for column in range(width)
    )
