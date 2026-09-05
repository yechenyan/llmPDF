import json
import subprocess
from pathlib import Path

from pdf_to_markdown.extraction_task import (
    TABLE_EXTRACTION_TARGET,
    ExtractTablesTask,
    continuation_groups,
    run_legacy_parse_table,
)
from pdf_to_markdown.io_utils import read_json, write_json
from pdf_to_markdown.models import PipelineConfig


def test_table_extraction_target_is_english_and_handles_empty_pages() -> None:
    assert TABLE_EXTRACTION_TARGET == (
        "Extract all genuine data tables on this page. "
        "If the page contains no tables, do not create any table directories."
    )


def test_continuation_groups_use_actual_agent_decisions(tmp_path) -> None:
    for page, accepted in ((26, True), (27, True), (28, False)):
        directory = tmp_path / f"page-{page:04d}"
        directory.mkdir()
        (directory / "continuation_pi_metrics.json").write_text(
            json.dumps({"merge_with_previous": accepted}), encoding="utf-8"
        )
    jobs = [
        {"page": 25, "may_merge_with_previous": False},
        {"page": 26, "may_merge_with_previous": True},
        {"page": 27, "may_merge_with_previous": True},
        {"page": 28, "may_merge_with_previous": True},
    ]
    assert continuation_groups(tmp_path, jobs) == [{"leader_page": 25, "pages": [25, 26, 27]}]


def test_continuation_groups_read_batched_decisions(tmp_path) -> None:
    for page, accepted in ((26, True), (27, False), (28, True)):
        directory = tmp_path / f"page-{page:04d}"
        directory.mkdir()
        (directory / "continuation_decision.json").write_text(
            json.dumps({"page": page, "merge_with_previous": accepted}), encoding="utf-8"
        )
    jobs = [
        {"page": 25, "may_merge_with_previous": False},
        {"page": 26, "may_merge_with_previous": True},
        {"page": 27, "may_merge_with_previous": True},
        {"page": 28, "may_merge_with_previous": True},
    ]
    assert continuation_groups(tmp_path, jobs) == [
        {"leader_page": 25, "pages": [25, 26]},
        {"leader_page": 27, "pages": [27, 28]},
    ]


def test_default_extraction_uses_bundled_engine(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "result")
    write_json(
        config.work_dir / "candidate-pages.json",
        {"pages": [1], "may_merge_with_previous": {}},
    )
    calls = []

    def fake_bundled(jobs, runs, received_config, trailing_jobs=None):
        calls.append((jobs, runs, received_config))
        assert trailing_jobs is None
        write_json(runs / "run_summary.json", {})
        return {}

    monkeypatch.setattr(
        "pdf_to_markdown.extraction_task.run_bundled_parse_table", fake_bundled
    )
    result = ExtractTablesTask().run(config)

    assert len(calls) == 1
    assert calls[0][0][0]["page"] == 1
    assert calls[0][0][0]["pdf"] == "../input.pdf"
    assert not Path(calls[0][0][0]["pdf"]).is_absolute()
    assert read_json(config.work_dir / "table-extraction" / "summary.json")[
        "engine"
    ] == "bundled"
    assert result.status == "completed"


def test_legacy_runner_uses_temporary_absolute_paths_only_at_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF-test")
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "result")
    jobs_file = config.work_dir / "table-extraction" / "jobs.json"
    write_json(jobs_file, [{"id": "page-0001", "pdf": "../input.pdf", "page": 1}])
    runs = config.work_dir / "table-extraction" / "runs"
    seen_runtime_jobs = []

    def fake_run(command, **_kwargs):
        runtime_jobs_file = Path(command[command.index("--jobs") + 1])
        seen_runtime_jobs.extend(read_json(runtime_jobs_file))
        write_json(runs / "run_summary.json", {})
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("pdf_to_markdown.extraction_task.subprocess.run", fake_run)
    run_legacy_parse_table(tmp_path / "parse-table", jobs_file, runs, config)

    assert read_json(jobs_file)[0]["pdf"] == "../input.pdf"
    assert Path(seen_runtime_jobs[0]["pdf"]).is_absolute()
    assert Path(seen_runtime_jobs[0]["pdf"]) == pdf.resolve()
