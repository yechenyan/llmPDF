from pathlib import Path

from pdf_to_markdown.io_utils import write_json
from pdf_to_markdown.models import PipelineConfig, TaskResult
from pdf_to_markdown.task import PipelineTask


class ExampleTask(PipelineTask):
    name = "example"

    def run(self, config: PipelineConfig) -> TaskResult:
        return TaskResult(self.name, "completed")


def setup_cache(tmp_path: Path) -> tuple[PipelineConfig, ExampleTask, Path, Path]:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "output")
    task = ExampleTask()
    output = config.work_dir / "result.json"
    write_json(output, {"ok": True})
    state = task.state_path(config)
    write_json(
        state,
        {
            "status": "completed",
            "signature": task.signature(config),
            "outputs": [output.relative_to(config.output_dir).as_posix()],
        },
    )
    return config, task, state, output


def test_cache_accepts_readable_nonempty_output(tmp_path: Path) -> None:
    config, task, _state, _output = setup_cache(tmp_path)
    assert task.is_cached(config)


def test_cache_rejects_invalid_state_json(tmp_path: Path) -> None:
    config, task, state, _output = setup_cache(tmp_path)
    state.write_text("not-json", encoding="utf-8")
    assert not task.is_cached(config)


def test_cache_rejects_invalid_json_output(tmp_path: Path) -> None:
    config, task, _state, output = setup_cache(tmp_path)
    output.write_text("not-json", encoding="utf-8")
    assert not task.is_cached(config)


def test_cache_rejects_empty_output(tmp_path: Path) -> None:
    config, task, _state, output = setup_cache(tmp_path)
    output.write_bytes(b"")
    assert not task.is_cached(config)


def test_cache_rejects_missing_output_list(tmp_path: Path) -> None:
    config, task, state, _output = setup_cache(tmp_path)
    write_json(
        state,
        {"status": "completed", "signature": task.signature(config)},
    )
    assert not task.is_cached(config)
