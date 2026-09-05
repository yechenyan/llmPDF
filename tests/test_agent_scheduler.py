import threading

import pytest

from pdf_to_markdown.agent_scheduler import PriorityAgentExecutor


def test_scheduler_selects_ready_tasks_by_type_then_fifo() -> None:
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    order: list[str] = []

    def blocking_find() -> None:
        blocker_started.set()
        assert release_blocker.wait(timeout=2)

    def record(label: str) -> None:
        order.append(label)

    with PriorityAgentExecutor(max_workers=1) as scheduler:
        first = scheduler.submit_task("find", "initial", blocking_find)
        assert blocker_started.wait(timeout=2)
        futures = [
            scheduler.submit_task("image", "image", record, "image"),
            scheduler.submit_task("table", "table-1", record, "table-1"),
            scheduler.submit_task("cross_table", "cross", record, "cross"),
            scheduler.submit_task("find", "find", record, "find"),
            scheduler.submit_task("table", "table-2", record, "table-2"),
        ]
        release_blocker.set()
        first.result(timeout=2)
        for future in futures:
            future.result(timeout=2)
        snapshot = scheduler.snapshot()

    assert order == ["find", "cross", "table-1", "table-2", "image"]
    assert snapshot["task_order"] == ["find", "cross_table", "table", "image"]
    assert snapshot["peak_running"] == 1


def test_scheduler_rejects_more_than_five_workers() -> None:
    with pytest.raises(ValueError, match="between 1 and 5"):
        PriorityAgentExecutor(max_workers=6)


def test_scheduler_skips_a_priority_type_at_its_running_limit() -> None:
    table_started = threading.Event()
    release_table = threading.Event()
    image_completed = threading.Event()

    def blocking_table() -> None:
        table_started.set()
        assert release_table.wait(timeout=2)

    with PriorityAgentExecutor(
        max_workers=2,
        kind_limits={"find": 2, "table": 1, "image": 2},
    ) as scheduler:
        first = scheduler.submit_task("table", "table-1", blocking_table)
        assert table_started.wait(timeout=2)
        second = scheduler.submit_task("table", "table-2", lambda: None)
        image = scheduler.submit_task("image", "image", image_completed.set)
        assert image_completed.wait(timeout=2)
        assert not second.done()
        release_table.set()
        first.result(timeout=2)
        second.result(timeout=2)
        image.result(timeout=2)
