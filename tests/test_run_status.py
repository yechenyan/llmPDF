import json
from pathlib import Path

from pdf_to_markdown.io_utils import read_json
from pdf_to_markdown.models import PipelineConfig, TaskResult
from pdf_to_markdown.run_status import RunStatusTracker


def make_config(tmp_path: Path) -> PipelineConfig:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    return PipelineConfig(
        pdf=pdf,
        output_dir=tmp_path / "result",
        selected_pages=(2, 3),
    )


def test_status_tracks_pipeline_agents_usage_and_completion(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    log = config.work_dir / "detection" / "batch-0001" / "pi.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": 10,
                        "cacheRead": 5,
                        "output": 3,
                        "cost": {"total": 0.01234567},
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    tracker = RunStatusTracker(config, total_tasks=2)
    tracker.start()
    tracker.stage_started(1, "first")
    tracker.stage_finished(TaskResult("first", "completed"))
    tracker.scheduler_updated(
        {
            "task_order": ["find", "cross_table", "table", "image"],
            "max_workers": 5,
            "peak_running": 2,
            "running": 1,
            "ready": {"find": 0, "cross_table": 0, "table": 1, "image": 0},
            "tasks": [
                {"label": "find-1", "status": "completed"},
                {"label": "image-1", "status": "failed"},
            ],
        },
        "completed image image-1",
    )
    tracker.stage_started(2, "second")
    tracker.stage_finished(TaskResult("second", "cached"))
    tracker.complete()

    status = read_json(config.work_dir / "status.json")
    assert status["status"] == "completed"
    assert status["selected_pages"] == [2, 3]
    assert status["pipeline"] == {
        "current_stage": None,
        "current_stage_index": 2,
        "total_tasks": 2,
        "successful_tasks": 2,
        "failed_tasks": 0,
        "cached_tasks": 1,
    }
    assert status["agents"]["discovered_tasks"] == 4
    assert status["agents"]["successful_tasks"] == 1
    assert status["agents"]["failed_tasks"] == 1
    assert status["usage"]["total_tokens"] == 18
    assert status["usage"]["pi_api_price_estimate_usd"] == 0.012346
    assert status["billing"]["actual_openai_charge_usd"] is None
    assert status["elapsed_seconds"] >= 0


def test_status_records_failed_current_stage(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    tracker = RunStatusTracker(config, total_tasks=2)
    tracker.start()
    tracker.stage_started(1, "broken")
    tracker.fail(ValueError("bad input"))

    status = read_json(config.work_dir / "status.json")
    assert status["status"] == "failed"
    assert status["pipeline"]["successful_tasks"] == 0
    assert status["pipeline"]["failed_tasks"] == 1
    assert status["error"] == {"type": "ValueError", "message": "bad input"}
