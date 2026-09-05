from pathlib import Path

from llmpdf.extraction_task import find_table_executable


def test_explicit_table_executable_wins(tmp_path: Path) -> None:
    executable = tmp_path / "llmpdf-table"
    executable.write_text("test", encoding="utf-8")
    executable.chmod(0o755)
    assert find_table_executable(executable) == executable.resolve()


def test_table_executable_can_come_from_environment(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "llmpdf-table"
    executable.write_text("test", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("LLMPDF_TABLE_EXECUTABLE", str(executable))
    assert find_table_executable() == executable.resolve()
