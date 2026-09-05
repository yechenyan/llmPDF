import gzip
from pathlib import Path

from llmpdf import rerun as rerun_module
from llmpdf.io_utils import read_json, sha256_file, write_json
from llmpdf.models import TaskResult


def test_rerun_tables_restores_minimal_context_and_preserves_other_usage(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    result = tmp_path / "result"
    (result / "assets").mkdir(parents=True)
    (result / "output.md").write_text("before\n", encoding="utf-8")
    write_json(
        result / "assets" / "metadata.json",
        {
            "source": {"page_count": 1},
            "tables": [],
            "docling_tables": [],
            "images": [],
        },
    )
    write_json(
        result / "work" / "metrics.json",
        {
            "timing": {},
            "tokens": {
                "detection": {
                    "noncached_input_tokens": 10,
                    "cached_input_tokens": 0,
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                },
                "image_analysis": {
                    "noncached_input_tokens": 20,
                    "cached_input_tokens": 0,
                    "input_tokens": 20,
                    "output_tokens": 3,
                    "total_tokens": 23,
                },
            },
            "billing": {},
        },
    )
    blocks = result / "work" / "run-blocks.json.gz"
    blocks.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(blocks, "wt", encoding="utf-8") as stream:
        stream.write('{"page_count": 1, "blocks": []}\n')
    write_json(
        result / "work" / "run-manifest.json",
        {
            "source": {"path": "../source.pdf", "sha256": sha256_file(source)},
            "table_detection": {
                "pages": [],
                "sources": {},
                "may_merge_with_previous": {},
            },
            "ignored_picture_blocks": [],
            "configuration": {},
            "retained": {
                "blocks": "work/run-blocks.json.gz",
                "blocks_sha256": sha256_file(blocks),
            },
        },
    )

    monkeypatch.setattr(rerun_module, "run_preflight", lambda *_args: None)

    def fake_extract(_self, config):
        write_json(
            config.work_dir / "table-extraction" / "summary.json",
            {
                "candidate_pages": [],
                "table_count": 0,
                "table_directories": [],
                "continuation_groups": [],
            },
        )
        return TaskResult("05-extract-tables", "completed")

    monkeypatch.setattr(rerun_module.ExtractTablesTask, "run", fake_extract)
    monkeypatch.setattr(
        rerun_module.CollectAssetsTask,
        "run",
        lambda _self, _config: TaskResult("06-collect-assets", "completed"),
    )
    monkeypatch.setattr(
        rerun_module.MergeMarkdownTask,
        "run",
        lambda _self, _config: TaskResult("08-merge-markdown", "completed"),
    )

    def fake_validate(_self, config):
        write_json(
            config.work_dir / "diagnostics" / "validation.json",
            {"status": "passed"},
        )
        return TaskResult("09-validate", "completed")

    monkeypatch.setattr(rerun_module.ValidateTask, "run", fake_validate)
    monkeypatch.setattr(rerun_module, "minimize_successful_result", lambda _: None)

    summary = rerun_module.rerun_tables(result)
    assert summary["status"] == "completed"
    assert summary["validation"] == "passed"
    metrics = read_json(result / "work" / "metrics.json")
    assert metrics["tokens"]["total"]["total_tokens"] == 35
    assert metrics["reruns"][0]["kind"] == "tables"
