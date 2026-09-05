from pathlib import Path

from pdf_to_markdown.assets_task import csv_to_markdown, infer_header_rows


def test_csv_to_markdown_escapes_cells(tmp_path: Path) -> None:
    source = tmp_path / "table.csv"
    source.write_text('A,B\n"x|y","two\nlines"\n', encoding="utf-8")
    rendered = csv_to_markdown(source, "Example")
    assert "**Example**" in rendered
    assert "x\\|y" in rendered
    assert "two lines" in rendered
    assert "<br>" not in rendered


def test_abbreviation_table_is_rendered_without_promoting_first_row(tmp_path: Path) -> None:
    source = tmp_path / "table.csv"
    source.write_text("BDEW,Meaning\nBNetzA,Agency\n", encoding="utf-8")
    assert infer_header_rows("Abkürzungsverzeichnis") == 0
    rendered = csv_to_markdown(source, "Abkürzungsverzeichnis")
    assert "|  |  |\n| --- | --- |\n| BDEW | Meaning |" in rendered
