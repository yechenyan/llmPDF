from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .io_utils import relative_reference, write_json
from .models import PipelineConfig, TaskResult


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _usage(work_dir: Path) -> dict[str, float | int]:
    noncached = cached = output = 0
    price = 0.0
    for path in work_dir.glob("**/*pi.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "message_end":
                continue
            message = event.get("message", {})
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            found = message.get("usage", {})
            if not isinstance(found, dict):
                continue
            noncached += int(found.get("input", found.get("inputTokens", 0)) or 0)
            cached += int(found.get("cacheRead", found.get("cachedInput", 0)) or 0)
            output += int(found.get("output", found.get("outputTokens", 0)) or 0)
            cost = found.get("cost", {})
            if isinstance(cost, dict):
                price += float(cost.get("total", 0) or 0)
    result: dict[str, float | int] = {
        "noncached_input_tokens": noncached,
        "cached_input_tokens": cached,
        "input_tokens": noncached + cached,
        "output_tokens": output,
        "total_tokens": noncached + cached + output,
        "pi_api_price_estimate_usd": round(price, 6),
    }
    metrics_path = work_dir / "metrics.json"
    if result["total_tokens"] == 0 and metrics_path.is_file():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            retained = metrics.get("tokens", {}).get("total", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            retained = {}
        if isinstance(retained, dict):
            for key in result:
                value = retained.get(key)
                if isinstance(value, (int, float)):
                    result[key] = value
    return result


class RunStatusTracker:
    def __init__(self, config: PipelineConfig, total_tasks: int) -> None:
        self.config = config
        self.path = config.work_dir / "status.json"
        self.total_tasks = total_tasks
        self.started_at = _utc_now()
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._successful: set[str] = set()
        self._failed: set[str] = set()
        self._cached: set[str] = set()
        self._current_stage: str | None = None
        self._current_stage_index = 0
        self._scheduler: dict[str, Any] | None = None
        self._scheduler_event: str | None = None
        self._state = "running"
        self._error: dict[str, str] | None = None
        self._completed_at: str | None = None
        self._usage: dict[str, float | int] = {
            "noncached_input_tokens": 0,
            "cached_input_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "pi_api_price_estimate_usd": 0.0,
        }

    def start(self) -> None:
        with self._lock:
            self._write_locked(refresh_usage=True)

    def stage_started(self, index: int, name: str) -> None:
        with self._lock:
            self._current_stage_index = index
            self._current_stage = name
            self._write_locked()

    def stage_finished(self, result: TaskResult) -> None:
        with self._lock:
            self._successful.add(result.task)
            if result.status == "cached":
                self._cached.add(result.task)
            self._write_locked(refresh_usage=True)

    def scheduler_updated(self, snapshot: dict[str, Any], event: str) -> None:
        with self._lock:
            self._scheduler = snapshot
            self._scheduler_event = event
            terminal_event = " completed " in event or " failed " in event
            self._write_locked(refresh_usage=terminal_event)

    def complete(self) -> None:
        with self._lock:
            self._state = "completed"
            self._current_stage = None
            self._completed_at = _utc_now()
            self._write_locked(refresh_usage=True)

    def fail(self, error: BaseException, stage: str | None = None) -> None:
        with self._lock:
            self._state = "failed"
            self._current_stage = stage or self._current_stage
            if self._current_stage:
                self._failed.add(self._current_stage)
            self._error = {
                "type": type(error).__name__,
                "message": str(error),
            }
            self._completed_at = _utc_now()
            self._write_locked(refresh_usage=True)

    def _write_locked(self, *, refresh_usage: bool = False) -> None:
        if refresh_usage:
            current_usage = _usage(self.config.work_dir)
            for key, value in current_usage.items():
                self._usage[key] = max(self._usage.get(key, 0), value)
        scheduler = dict(self._scheduler or {})
        agent_records = scheduler.get("tasks", [])
        successful_agents = sum(
            item.get("status") == "completed" for item in agent_records
        )
        failed_agents = sum(item.get("status") == "failed" for item in agent_records)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "status": self._state,
            "source_pdf": relative_reference(self.config.pdf, self.config.output_dir),
            "selected_pages": (
                list(self.config.selected_pages)
                if self.config.selected_pages is not None
                else None
            ),
            "started_at": self.started_at,
            "updated_at": _utc_now(),
            "completed_at": self._completed_at,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "pipeline": {
                "current_stage": self._current_stage,
                "current_stage_index": self._current_stage_index,
                "total_tasks": self.total_tasks,
                "successful_tasks": len(self._successful),
                "failed_tasks": len(self._failed),
                "cached_tasks": len(self._cached),
            },
            "agents": {
                "task_order": scheduler.get("task_order", []),
                "max_workers": scheduler.get("max_workers", 0),
                "peak_running": scheduler.get("peak_running", 0),
                "discovered_tasks": len(agent_records)
                + int(scheduler.get("running", 0))
                + sum(int(value) for value in scheduler.get("ready", {}).values()),
                "running_tasks": int(scheduler.get("running", 0)),
                "ready_tasks": scheduler.get("ready", {}),
                "successful_tasks": successful_agents,
                "failed_tasks": failed_agents,
                "last_event": self._scheduler_event,
            },
            "usage": dict(self._usage),
            "billing": {
                "currency": "USD",
                "actual_openai_charge_usd": None,
                "pi_api_price_estimate_usd": self._usage[
                    "pi_api_price_estimate_usd"
                ],
                "note": (
                    "The estimate comes from Pi's API price table; it is not an "
                    "OpenAI invoice or a Codex plan charge."
                ),
            },
        }
        if self._error is not None:
            payload["error"] = self._error
        if self._state == "completed":
            payload["output"] = "output.md"
            payload["metrics"] = "work/metrics.json"
            manifest = self.config.work_dir / "run-manifest.json"
            payload["manifest"] = (
                "work/run-manifest.json" if manifest.is_file() else None
            )
        write_json(self.path, payload)
