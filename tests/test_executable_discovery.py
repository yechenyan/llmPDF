from pathlib import Path

from pdf_to_markdown.extraction_task import find_parse_table


def test_explicit_parse_table_executable_wins(tmp_path: Path) -> None:
    executable = tmp_path / "parse-table"
    executable.write_text("test", encoding="utf-8")
    executable.chmod(0o755)
    assert find_parse_table(executable) == executable.resolve()


def test_parse_table_can_come_from_environment(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "parse-table"
    executable.write_text("test", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PARSE_TABLE_EXECUTABLE", str(executable))
    assert find_parse_table() == executable.resolve()
