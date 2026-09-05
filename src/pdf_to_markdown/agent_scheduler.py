from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from typing import Any


TASK_ORDER = ("find", "cross_table", "table", "image")


@dataclass
class _QueuedTask:
    future: Future
    kind: str
    label: str
    callback: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    submitted_at: float
    sequence: int


class PriorityAgentExecutor(Executor):
    """Fixed worker pool backed by ordered, per-task-type FIFO queues."""

    def __init__(
        self,
        max_workers: int = 5,
        *,
        kind_limits: dict[str, int] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        if not 1 <= max_workers <= 5:
            raise ValueError("max_workers must be between 1 and 5")
        self.max_workers = max_workers
        configured_limits = kind_limits or {}
        self._kind_limits = {
            "find": max(1, int(configured_limits.get("find", max_workers))),
            "table": max(1, int(configured_limits.get("table", max_workers))),
            "image": max(1, int(configured_limits.get("image", max_workers))),
        }
        self._progress = progress
        self._queues: dict[str, deque[_QueuedTask]] = {
            kind: deque() for kind in TASK_ORDER
        }
        self._condition = threading.Condition()
        self._shutdown = False
        self._sequence = 0
        self._running: dict[int, _QueuedTask] = {}
        self._peak_running = 0
        self._completed = 0
        self._records: list[dict[str, Any]] = []
        self._threads = [
            threading.Thread(
                target=self._worker,
                name=f"pdf-agent-{index + 1}",
                daemon=True,
            )
            for index in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()
        self._emit(f"queue started: workers={max_workers}")

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        return self.submit_task("table", getattr(fn, "__name__", "table"), fn, *args, **kwargs)

    def submit_task(
        self,
        kind: str,
        label: str,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future:
        if kind not in self._queues:
            raise ValueError(f"Unknown Agent task type: {kind}")
        future: Future = Future()
        with self._condition:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            self._sequence += 1
            task = _QueuedTask(
                future=future,
                kind=kind,
                label=label,
                callback=fn,
                args=args,
                kwargs=kwargs,
                submitted_at=time.monotonic(),
                sequence=self._sequence,
            )
            self._queues[kind].append(task)
            state = self._state_locked()
            self._condition.notify()
        self._emit(f"queued {kind} {label}; {state}")
        return future

    def _running_count_locked(self, kind: str) -> int:
        if kind in {"cross_table", "table"}:
            return sum(
                task.kind in {"cross_table", "table"}
                for task in self._running.values()
            )
        return sum(task.kind == kind for task in self._running.values())

    def _can_run_locked(self, kind: str) -> bool:
        limit_kind = "table" if kind == "cross_table" else kind
        return self._running_count_locked(kind) < self._kind_limits[limit_kind]

    def _next_locked(self) -> _QueuedTask | None:
        for kind in TASK_ORDER:
            if self._queues[kind] and self._can_run_locked(kind):
                return self._queues[kind].popleft()
        return None

    def _has_queued_locked(self) -> bool:
        return any(self._queues[kind] for kind in TASK_ORDER)

    def _has_runnable_locked(self) -> bool:
        return any(
            self._queues[kind] and self._can_run_locked(kind)
            for kind in TASK_ORDER
        )

    def _state_locked(self) -> str:
        ready = " ".join(
            f"{kind}={len(self._queues[kind])}" for kind in TASK_ORDER
        )
        discovered = self._completed + len(self._running) + sum(
            len(queue) for queue in self._queues.values()
        )
        return (
            f"running={len(self._running)}/{self.max_workers} "
            f"ready[{ready}] completed={self._completed} discovered={discovered}"
        )

    def _emit(self, message: str) -> None:
        if self._progress is not None:
            self._progress(f"[scheduler] {message}")

    def _worker(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._has_runnable_locked()
                    or (self._shutdown and not self._has_queued_locked())
                )
                task = self._next_locked()
                if task is None:
                    if self._shutdown and not self._has_queued_locked():
                        return
                    continue
                if not task.future.set_running_or_notify_cancel():
                    continue
                worker_id = threading.get_ident()
                self._running[worker_id] = task
                self._peak_running = max(self._peak_running, len(self._running))
                state = self._state_locked()
            self._emit(f"started {task.kind} {task.label}; {state}")
            started_at = time.monotonic()
            try:
                result = task.callback(*task.args, **task.kwargs)
            except BaseException as error:
                task.future.set_exception(error)
                status = "failed"
            else:
                task.future.set_result(result)
                status = "completed"
            finished_at = time.monotonic()
            with self._condition:
                self._running.pop(worker_id, None)
                self._completed += 1
                self._records.append(
                    {
                        "sequence": task.sequence,
                        "type": task.kind,
                        "label": task.label,
                        "status": status,
                        "queue_wait_seconds": round(started_at - task.submitted_at, 3),
                        "run_seconds": round(finished_at - started_at, 3),
                    }
                )
                state = self._state_locked()
                self._condition.notify_all()
            self._emit(
                f"{status} {task.kind} {task.label} in "
                f"{finished_at - started_at:.1f}s; {state}"
            )

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                "task_order": list(TASK_ORDER),
                "max_workers": self.max_workers,
                "kind_limits": dict(self._kind_limits),
                "completed": self._completed,
                "running": len(self._running),
                "peak_running": self._peak_running,
                "ready": {
                    kind: len(self._queues[kind]) for kind in TASK_ORDER
                },
                "tasks": list(self._records),
            }

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._condition:
            self._shutdown = True
            if cancel_futures:
                for queue in self._queues.values():
                    while queue:
                        queue.popleft().future.cancel()
            self._condition.notify_all()
        if wait:
            for thread in self._threads:
                thread.join()
