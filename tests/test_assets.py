from pathlib import Path

import pytest

from llmpdf.assets_task import csv_to_markdown, infer_header_rows
from llmpdf.review_tables import markdown_table_rows


def test_csv_to_markdown_escapes_cells(tmp_path: Path) -> None:
    source = tmp_path / "table.csv"
    source.write_text('A,B\n"x|y","two\nlines"\n', encoding="utf-8")
    rendered = csv_to_markdown(source, "Example")
    assert "**Example**" in rendered
    assert "x\\|y" in rendered
    assert "two<br>lines" in rendered
    assert markdown_table_rows(rendered) == [["A", "B"], ["x|y", "two\nlines"]]


@pytest.mark.parametrize("line_break", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("header_rows", [0, 1])
def test_csv_to_markdown_preserves_each_cell_break(
    tmp_path: Path, line_break: str, header_rows: int
) -> None:
    source = tmp_path / "table.csv"
    content = (
        f'"Header{line_break}unit",B\r\n'
        f'"first{line_break}{line_break}second",plain\r\n'
    )
    source.write_bytes(content.encode())
    rendered = csv_to_markdown(source, None, header_rows)
    assert "Header<br>unit" in rendered
    assert "first<br><br>second" in rendered
    expected = [["Header\nunit", "B"], ["first\n\nsecond", "plain"]]
    if not header_rows:
        expected.insert(0, ["", ""])
    assert markdown_table_rows(rendered) == expected


def test_abbreviation_table_is_rendered_without_promoting_first_row(tmp_path: Path) -> None:
    source = tmp_path / "table.csv"
    source.write_text("BDEW,Meaning\nBNetzA,Agency\n", encoding="utf-8")
    assert infer_header_rows("Abkürzungsverzeichnis") == 0
    rendered = csv_to_markdown(source, "Abkürzungsverzeichnis")
    assert "|  |  |\n| --- | --- |\n| BDEW | Meaning |" in rendered
