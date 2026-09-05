from pathlib import Path

from llmpdf import pipeline
from llmpdf.io_utils import read_json
from llmpdf.models import PipelineConfig, TaskResult


class FakeTask:
    name = "fake"
    dependencies = ()

    def is_cached(self, config: PipelineConfig) -> bool:
        return False

    def execute(self, config: PipelineConfig) -> TaskResult:
        session = config.work_dir / "nested" / "pi-session.jsonl"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text("session\n", encoding="utf-8")
        return TaskResult(self.name, "completed")


class ObserveQueueTask:
    dependencies = ()

    def __init__(self, name: str, seen: list[object]) -> None:
        self.name = name
        self.seen = seen

    def is_cached(self, config: PipelineConfig) -> bool:
        return False

    def execute(self, config: PipelineConfig) -> TaskResult:
        self.seen.append(config.agent_executor)
        return TaskResult(self.name, "completed")


def test_run_all_removes_sessions_after_success(tmp_path: Path, monkeypatch) -> None:
    config = PipelineConfig(pdf=tmp_path / "input.pdf", output_dir=tmp_path / "out")
    config.pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(pipeline, "TASKS", (FakeTask(),))
    pipeline.run_all(config)
    assert not list(config.work_dir.glob("**/*session*.jsonl"))
    status = read_json(config.work_dir / "status.json")
    assert status["status"] == "completed"
    assert status["pipeline"]["successful_tasks"] == 1


def test_run_all_keeps_sessions_when_requested(tmp_path: Path, monkeypatch) -> None:
    config = PipelineConfig(
        pdf=tmp_path / "input.pdf",
        output_dir=tmp_path / "out",
        keep_sessions=True,
    )
    config.pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(pipeline, "TASKS", (FakeTask(),))
    pipeline.run_all(config)
    assert len(list(config.work_dir.glob("**/*session*.jsonl"))) == 1


def test_run_tasks_reuses_one_agent_queue_across_stages(tmp_path: Path) -> None:
    config = PipelineConfig(pdf=tmp_path / "input.pdf", output_dir=tmp_path / "out")
    config.pdf.write_bytes(b"%PDF-test")
    seen: list[object] = []
    pipeline.run_tasks(
        config,
        (ObserveQueueTask("first", seen), ObserveQueueTask("second", seen)),
    )
    assert seen[0] is not None
    assert seen[0] is seen[1]
    assert config.agent_executor is None
    status = read_json(config.work_dir / "status.json")
    assert status["status"] == "completed"
    assert status["pipeline"]["successful_tasks"] == 2


def test_run_tasks_records_preflight_failure(tmp_path: Path) -> None:
    config = PipelineConfig(
        pdf=tmp_path / "missing.pdf",
        output_dir=tmp_path / "out",
    )
    try:
        pipeline.run_tasks(config, (FakeTask(),))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected a missing source PDF failure")
    status = read_json(config.work_dir / "status.json")
    assert status["status"] == "failed"
    assert status["error"]["type"] == "FileNotFoundError"
