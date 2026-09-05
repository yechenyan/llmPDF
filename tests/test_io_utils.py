from pathlib import Path

import pytest

from llmpdf.table.io_utils import normalize_jsonl_paths
from llmpdf import io_utils


def test_write_json_replaces_destination_atomically(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    destination.write_text('{"old": true}\n', encoding="utf-8")

    io_utils.write_json(destination, {"new": True})

    assert io_utils.read_json(destination) == {"new": True}
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_write_json_preserves_old_file_if_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    destination = tmp_path / "state.json"
    original = '{"old": true}\n'
    destination.write_text(original, encoding="utf-8")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(io_utils.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        io_utils.write_json(destination, {"new": True})

    assert destination.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_relative_reference_supports_paths_outside_artifact_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results" / "document"
    source = tmp_path / "source.pdf"

    assert io_utils.relative_reference(source, root) == "../../source.pdf"
    assert io_utils.relative_reference(root / "assets" / "image.png", root) == (
        "assets/image.png"
    )


def test_pi_jsonl_paths_are_relative_to_artifact_root(tmp_path: Path) -> None:
    root = tmp_path / "result"
    log = root / "work" / "pi.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        '{"cwd":"'
        + str(root / "work" / "agent-output")
        + '","error":"at '
        + str(Path.home() / ".npm" / "tool.js")
        + '"}\n',
        encoding="utf-8",
    )

    normalize_jsonl_paths(log, root)

    content = log.read_text(encoding="utf-8")
    assert str(root.resolve()) not in content
    assert f'"error":"at {Path.home().resolve()}' not in content
    assert '"cwd":"./work/agent-output"' in content
