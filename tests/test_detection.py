import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from llmpdf.agent_scheduler import PriorityAgentExecutor
from llmpdf.detection_task import (
    DetectTablesTask,
    detection_prompt,
    normalize_image_detection,
    parse_json_response,
    ready_candidate_groups,
    select_vision_pages,
)
from llmpdf.io_utils import read_json, write_json
from llmpdf.models import PipelineConfig
from llmpdf.pi_runtime import pi_environment


def test_auto_environment_uses_source_settings_directly() -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "agent"
        source.mkdir()
        (source / "auth.json").write_text('{"token":"test"}\n', encoding="utf-8")
        (source / "settings.json").write_text(
            '{"transport":"auto"}\n', encoding="utf-8"
        )
        with pi_environment(source, transport="auto") as environment:
            runtime = Path(environment["PI_CODING_AGENT_DIR"])
            assert runtime == source.resolve()
            assert (
                json.loads((runtime / "settings.json").read_text(encoding="utf-8"))[
                    "transport"
                ]
                == "auto"
            )
            assert not (runtime / "auth.json").is_symlink()
        assert runtime.exists()
        assert (
            json.loads((source / "settings.json").read_text(encoding="utf-8"))[
                "transport"
            ]
            == "auto"
        )


def test_parse_json_response_accepts_plain_json() -> None:
    assert parse_json_response('{"pages":[{"page":1}]}')["pages"][0]["page"] == 1


def test_parse_json_response_accepts_fence() -> None:
    value = parse_json_response('result\n```json\n{"pages": []}\n```')
    assert value == {"pages": []}


def test_parse_json_response_rejects_invalid_payload() -> None:
    with pytest.raises(ValueError):
        parse_json_response("no json")


def test_negative_page_confidence_does_not_make_it_a_candidate() -> None:
    pages = select_vision_pages(
        [
            {
                "page": 1,
                "has_table": False,
                "table_count_estimate": 0,
                "confidence": 0.99,
            },
            {
                "page": 2,
                "has_table": True,
                "table_count_estimate": 1,
                "confidence": 0.60,
            },
        ]
    )
    assert pages == {2}


def test_detection_prompt_uses_one_conservative_merge_boolean() -> None:
    prompt = detection_prompt([9, 10], [8, 9, 10])
    assert "may_merge_with_previous" in prompt
    assert "set it to true when uncertain" in prompt
    assert "context pages: [8, 9, 10]" in prompt
    for excluded in (
        "table of contents",
        "abbreviation list",
        "glossary",
        "contact list",
        "map legend",
        "ordinary key-value list",
    ):
        assert excluded in prompt


def test_detection_prompt_adds_page_level_images_without_reusing_table_regions() -> (
    None
):
    prompt = detection_prompt([3])
    assert '"has_image": false' in prompt
    assert '"image_count_estimate": 0' in prompt
    assert "estimate image coordinates" in prompt
    assert "regions describes table regions only" in prompt


def test_image_detection_defaults_do_not_change_table_fields() -> None:
    original = {
        "page": 4,
        "has_table": True,
        "table_count_estimate": 2,
        "regions": [{"top": 0.2, "bottom": 0.8}],
    }
    normalized = normalize_image_detection(original)
    assert normalized["has_table"] is True
    assert normalized["regions"] == original["regions"]
    assert normalized["has_image"] is False
    assert normalized["image_count_estimate"] == 0


def test_ready_groups_hold_batch_tail_until_next_page_is_known() -> None:
    detection = {
        page: {"page": page, "may_merge_with_previous": page in {7, 8}}
        for page in range(1, 9)
    }
    groups = ready_candidate_groups(
        list(range(1, 11)),
        {3, 6, 7, 8, 9},
        detection,
        set(),
    )
    assert groups == [[3]]

    detection[9] = {"page": 9, "may_merge_with_previous": True}
    detection[10] = {"page": 10, "may_merge_with_previous": False}
    groups = ready_candidate_groups(
        list(range(1, 11)),
        {3, 6, 7, 8, 9},
        detection,
        {3},
    )
    assert groups == [[6, 7, 8, 9]]


def test_ready_groups_wait_for_unknown_previous_page_when_merge_is_possible() -> None:
    detection = {
        9: {"page": 9, "may_merge_with_previous": True},
        10: {"page": 10, "may_merge_with_previous": False},
    }
    assert ready_candidate_groups([8, 9, 10], {9}, detection, set()) == []

    detection[8] = {"page": 8, "may_merge_with_previous": False}
    assert ready_candidate_groups([8, 9, 10], {8, 9}, detection, set()) == [
        [8, 9]
    ]


def test_find_batches_run_concurrently_and_are_written_in_page_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = PipelineConfig(
        pdf=tmp_path / "input.pdf",
        output_dir=tmp_path / "result",
        find_concurrency=2,
    )
    batches = [
        {"pages": [1], "context_pages": [1], "image": "batch-1.jpg"},
        {"pages": [2], "context_pages": [1, 2], "image": "batch-2.jpg"},
    ]
    write_json(
        config.work_dir / "detection" / "screenshots.json",
        {"batches": batches},
    )
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fake_run(command, *, cwd, stdout, **_kwargs):
        nonlocal active, maximum_active
        batch_index = int(Path(cwd).name.rsplit("-", 1)[1])
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        page = batch_index
        stdout.write(
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"pages": [{"page": page, "has_table": True}]}
                                ),
                            }
                        ],
                    }
                }
            )
            + "\n"
        )
        stdout.flush()
        with lock:
            active -= 1
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("llmpdf.detection_task.find_pi", lambda: Path("pi"))
    monkeypatch.setattr("llmpdf.detection_task.subprocess.run", fake_run)

    result = DetectTablesTask().run(config)
    detected = read_json(config.work_dir / "detection" / "table-pages.json")

    assert maximum_active == 2
    assert [item["page"] for item in detected["pages"]] == [1, 2]
    assert result.details["workers"] == 2


def test_completed_find_batch_releases_table_work_before_other_find_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "input.pdf"
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=100, height=100)
    with pdf.open("wb") as stream:
        writer.write(stream)
    config = PipelineConfig(pdf=pdf, output_dir=tmp_path / "result")
    config.dynamic_agent_scheduling = True
    write_json(
        config.work_dir / "docling" / "blocks.json",
        {
            "selected_pages": [1, 2, 3, 4],
            "docling_table_pages": [],
            "blocks": [],
        },
    )
    write_json(
        config.work_dir / "detection" / "screenshots.json",
        {
            "selected_pages": [1, 2, 3, 4],
            "batches": [
                {
                    "pages": [1, 2],
                    "context_pages": [1, 2],
                    "image": "batch-1.jpg",
                },
                {
                    "pages": [3, 4],
                    "context_pages": [2, 3, 4],
                    "image": "batch-2.jpg",
                },
            ],
        },
    )
    table_started = threading.Event()

    def fake_run(command, *, cwd, stdout, **_kwargs):
        batch_index = int(Path(cwd).name.rsplit("-", 1)[1])
        if batch_index == 2:
            assert table_started.wait(timeout=2)
        pages = [1, 2] if batch_index == 1 else [3, 4]
        response = {
            "pages": [
                {
                    "page": page,
                    "has_table": page == 1,
                    "may_merge_with_previous": False,
                }
                for page in pages
            ]
        }
        stdout.write(
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": json.dumps(response)}],
                    },
                }
            )
            + "\n"
        )
        stdout.flush()
        return SimpleNamespace(returncode=0, stderr="")

    def fake_prepare(job, *_args, **_kwargs):
        return SimpleNamespace(job=job, directory=tmp_path / job.id)

    def fake_dynamic_group(prepared, _config):
        table_started.set()
        return {prepared[0].job.id: {"returncode": 0}}

    monkeypatch.setattr("llmpdf.detection_task.find_pi", lambda: Path("pi"))
    monkeypatch.setattr("llmpdf.detection_task.subprocess.run", fake_run)
    monkeypatch.setattr("llmpdf.table.prepare.prepare_job", fake_prepare)
    monkeypatch.setattr(
        "llmpdf.table.orchestration.run_dynamic_group", fake_dynamic_group
    )

    with PriorityAgentExecutor(max_workers=2) as scheduler:
        config.agent_executor = scheduler
        DetectTablesTask().run(config)
        for future in config.early_table_futures:
            future.result(timeout=2)

    assert table_started.is_set()
    assert set(config.early_table_jobs) == {1}
