from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .io_utils import read_json, relativize, sha256_file, write_json
from .models import PipelineConfig, TaskResult
from .task import PipelineTask


def empty_usage() -> dict[str, float | int]:
    return {
        "noncached_input_tokens": 0,
        "cached_input_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "pi_api_price_estimate_usd": 0.0,
    }


def add_usage(target: dict[str, float | int], usage: dict[str, Any]) -> None:
    noncached = int(usage.get("input", usage.get("noncached_input_tokens", 0)) or 0)
    cached = int(usage.get("cacheRead", usage.get("cached_input_tokens", 0)) or 0)
    output = int(usage.get("output", usage.get("output_tokens", 0)) or 0)
    target["noncached_input_tokens"] += noncached
    target["cached_input_tokens"] += cached
    target["input_tokens"] += noncached + cached
    target["output_tokens"] += output
    target["total_tokens"] += noncached + cached + output
    cost = usage.get("cost", {})
    if isinstance(cost, dict):
        target["pi_api_price_estimate_usd"] += float(cost.get("total", 0) or 0)


def usage_from_pi_log(path: Path) -> dict[str, float | int]:
    result = empty_usage()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        usage = message.get("usage")
        if isinstance(usage, dict):
            add_usage(result, usage)
    result["pi_api_price_estimate_usd"] = round(
        float(result["pi_api_price_estimate_usd"]), 6
    )
    return result


def combined_usage(items: list[dict[str, float | int]]) -> dict[str, float | int]:
    result = empty_usage()
    for item in items:
        for key in result:
            result[key] += item.get(key, 0)
    result["pi_api_price_estimate_usd"] = round(
        float(result["pi_api_price_estimate_usd"]), 6
    )
    input_tokens = int(result["input_tokens"])
    result["cache_hit_ratio"] = (
        round(int(result["cached_input_tokens"]) / input_tokens, 4)
        if input_tokens
        else 0.0
    )
    return result


class MetricsTask(PipelineTask):
    name = "10-metrics"
    dependencies = ("09-validate",)

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        state_dir = config.work_dir / "tasks"
        value["metrics_logic_version"] = 9
        value["task_states"] = {
            path.stem: sha256_file(path)
            for path in sorted(state_dir.glob("*.json"))
            if path.stem != self.name
        }
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        task_states: dict[str, dict[str, Any]] = {}
        for state_path in sorted((config.work_dir / "tasks").glob("*.json")):
            if state_path.stem == self.name:
                continue
            state = read_json(state_path)
            task_states[state_path.stem] = {
                "status": state.get("status"),
                "elapsed_seconds": round(float(state.get("elapsed_seconds", 0)), 3),
            }
        successful_wall_seconds = round(
            sum(
                float(state["elapsed_seconds"])
                for state in task_states.values()
                if state.get("status") == "completed"
            ),
            3,
        )

        detection_items = []
        for log in sorted((config.work_dir / "detection").glob("batch-*/pi.jsonl")):
            detection_items.append(
                {
                    "batch": log.parent.name,
                    "usage": usage_from_pi_log(log),
                }
            )

        extraction_summary = read_json(
            config.work_dir / "table-extraction" / "summary.json"
        )
        candidate_pages = [
            int(page) for page in extraction_summary.get("candidate_pages", [])
        ]
        extraction_items = []
        extraction_work_seconds = 0.0
        for page in candidate_pages:
            job_dir = config.work_dir / "table-extraction" / "runs" / f"page-{page:04d}"
            logs = sorted(job_dir.glob("*pi.jsonl"))
            metric_files = sorted(job_dir.glob("*metrics.json"))
            metric_payloads = [read_json(path) for path in metric_files]
            elapsed = sum(
                float(payload.get("elapsed_seconds", 0)) for payload in metric_payloads
            )
            extraction_work_seconds += elapsed
            item = {
                "page": page,
                "elapsed_seconds": round(elapsed, 3),
                "usage": combined_usage([usage_from_pi_log(log) for log in logs]),
            }
            covered_pages = sorted(
                {
                    int(covered)
                    for payload in metric_payloads
                    for covered in payload.get("pages", [])
                }
            )
            if covered_pages:
                item["pages"] = covered_pages
            extraction_items.append(item)

        detection_usage = combined_usage([item["usage"] for item in detection_items])
        extraction_usage = combined_usage([item["usage"] for item in extraction_items])
        image_items = []
        image_work_seconds = 0.0
        for job_dir in sorted(
            (config.work_dir / "image-analysis" / "jobs").glob("page-*")
        ):
            logs = sorted(job_dir.glob("*pi.jsonl"))
            metric_payloads = [
                read_json(path) for path in sorted(job_dir.glob("*metrics.json"))
            ]
            elapsed = sum(
                float(payload.get("elapsed_seconds", 0)) for payload in metric_payloads
            )
            image_work_seconds += elapsed
            result_path = job_dir / "result.json"
            result = read_json(result_path) if result_path.is_file() else {}
            image_items.append(
                {
                    "page": int(job_dir.name.removeprefix("page-")),
                    "status": result.get("status", "unknown"),
                    "image_count": len(result.get("images", [])),
                    "elapsed_seconds": round(elapsed, 3),
                    "usage": combined_usage([usage_from_pi_log(log) for log in logs]),
                }
            )
        image_usage = combined_usage([item["usage"] for item in image_items])
        total_usage = combined_usage([detection_usage, extraction_usage, image_usage])
        block_payload = read_json(config.work_dir / "docling" / "blocks.json")
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "source": {
                "page_count": int(block_payload["page_count"]),
                "selected_pages": [
                    int(page) for page in block_payload["selected_pages"]
                ],
            },
            "timing": {
                "successful_pipeline_wall_seconds": successful_wall_seconds,
                "successful_pipeline_wall_minutes": round(
                    successful_wall_seconds / 60, 3
                ),
                "table_agent_work_seconds": round(extraction_work_seconds, 3),
                "table_agent_work_minutes": round(extraction_work_seconds / 60, 3),
                "image_agent_work_seconds": round(image_work_seconds, 3),
                "image_agent_work_minutes": round(image_work_seconds / 60, 3),
                "note": "Pipeline wall time sums the latest successful task durations; table and image agent work sum per-job durations before parallelization.",
                "tasks": task_states,
            },
            "tokens": {
                "total": total_usage,
                "detection": detection_usage,
                "table_extraction": extraction_usage,
                "image_analysis": image_usage,
                "detection_batches": detection_items,
                "table_pages": extraction_items,
                "image_pages": image_items,
                "note": "Token counts come from Pi logs. pi_api_price_estimate_usd is Pi's local API-price-table estimate, not an OpenAI invoice or Codex plan charge.",
            },
            "billing": {
                "provider": "openai-codex",
                "api": "openai-codex-responses",
                "billing_mode": "chatgpt_codex_plan",
                "actual_openai_charge_usd": None,
                "pi_api_price_estimate_usd": total_usage["pi_api_price_estimate_usd"],
                "note": "This run used Codex sign-in rather than an OpenAI API key. Actual included-plan or purchased-credit consumption must be read from the Codex Usage dashboard.",
            },
        }
        if config.agent_backend == "claude-code":
            report["tokens"]["note"] = "Claude Code token usage is not collected; zero values mean unreported usage."
            report["billing"].update({
                "provider": "claude-code", "api": None, "billing_mode": None,
                "note": "Claude Code cost is not collected; zero values are not an actual charge.",
            })
        scheduler = getattr(config, "agent_executor", None)
        snapshot = getattr(scheduler, "snapshot", None)
        if snapshot is not None:
            report["agent_scheduler"] = snapshot()
        output = config.work_dir / "metrics.json"
        write_json(output, report)
        return TaskResult(
            self.name,
            "completed",
            [relativize(output, config.output_dir)],
            {
                "wall_seconds": successful_wall_seconds,
                "total_tokens": total_usage["total_tokens"],
                "pi_api_price_estimate_usd": total_usage["pi_api_price_estimate_usd"],
            },
        )
