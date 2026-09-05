from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pdfplumber

from parse_table.io_utils import normalize_jsonl_paths

from .io_utils import read_json, relativize, sha256_file, write_json
from .models import PipelineConfig, TaskResult
from .pi_runtime import PI_PACKAGE, find_pi, pi_environment
from .progress import report_progress
from .task import PipelineTask


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def last_assistant_text(log: Path) -> str:
    found = ""
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message", {})
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = _content_text(message.get("content"))
            if text:
                found = text
    return found


def parse_json_response(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    candidates.extend(
        re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    )
    first, last = text.find("{"), text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("pages"), list):
            return value
    raise ValueError("Pi response does not contain a valid pages JSON object")


def detection_prompt(pages: list[int], context_pages: list[int] | None = None) -> str:
    context_pages = context_pages or pages
    return f"""Determine which PDF thumbnail pages contain real data tables and whether each page contains meaningful images.

The input is a contact sheet. Each page has a physical page number in the form PAGE 0001 at the top. The contact sheet contains these context pages: {context_pages}. Return results only for these target pages: {pages}.

## Table detection

A table is a data grid with headers and row/column relationships. A table of contents, abbreviation list, glossary, contact list, map legend, ordinary key-value list, regular multi-column prose, flowchart, simple bulleted list, header, or footer is not a table.
Prioritize recall: when something may be a table but is uncertain, set has_table=true and lower confidence. Do not extract table contents.

## Image detection

Meaningful images include charts, flowcharts, architecture diagrams, schematics, maps, photographs, screenshots, and infographics.
A plain data table is not an image. Headers, footers, page numbers, separator lines, background blocks, repeated small company logos, decorative icons, and ordinary formulas are not images.
A chart still counts as an image even when it contains axes, legends, text, or a small data area.
Only determine whether a page contains images and estimate their count. Do not describe image contents, read chart data, or estimate image coordinates.

Return valid JSON only, without Markdown fences or explanations. Use this structure:
{{
  "pages": [
    {{
      "page": 1,
      "has_table": false,
      "table_count_estimate": 0,
      "regions": [{{"top": 0.0, "bottom": 1.0}}],
      "may_merge_with_previous": false,
      "confidence": 0.0,
      "reason": "brief reason for the table decision",
      "has_image": false,
      "image_count_estimate": 0,
      "image_kinds": [],
      "image_confidence": 0.0,
      "image_reason": "brief reason for the image decision"
    }}
  ]
}}

The pages array must cover exactly {pages}, sorted by page number. regions describes table regions only, using approximate vertical coordinates from 0 to 1; use an empty array when there is no table. Do not put image regions in regions.
may_merge_with_previous indicates whether the current page may contain a table that can merge with a table on the previous physical page. Set it to false only when merging is clearly impossible; set it to true when uncertain. Set it to false when either the current or previous page has no table.
image_kinds may contain only chart, diagram, flowchart, map, photo, screenshot, infographic, or other. If the image type is unclear but a meaningful image may exist, set has_image=true, image_kinds=["other"], and lower image_confidence.
"""


def normalize_image_detection(item: dict[str, Any]) -> dict[str, Any]:
    """Add image-only defaults without changing any table-detection field."""
    normalized = dict(item)
    count = max(0, int(normalized.get("image_count_estimate", 0) or 0))
    normalized["has_image"] = bool(normalized.get("has_image", count > 0))
    normalized["image_count_estimate"] = count
    kinds = normalized.get("image_kinds", [])
    normalized["image_kinds"] = (
        [str(value) for value in kinds] if isinstance(kinds, list) else []
    )
    normalized["image_confidence"] = max(
        0.0, min(1.0, float(normalized.get("image_confidence", 0.0) or 0.0))
    )
    normalized["image_reason"] = str(normalized.get("image_reason", ""))
    return normalized


def select_vision_pages(items: list[dict[str, Any]]) -> set[int]:
    return {
        int(item["page"])
        for item in items
        if item.get("has_table") or int(item.get("table_count_estimate", 0) or 0) > 0
    }


def ready_candidate_groups(
    selected_pages: list[int],
    candidates: set[int],
    detection_by_page: dict[int, dict[str, Any]],
    scheduled_pages: set[int],
) -> list[list[int]]:
    """Return newly complete table groups whose physical boundaries are known."""
    selected = set(selected_pages)
    groups: list[list[int]] = []
    considered: set[int] = set()
    for page in sorted(candidates):
        if (
            page in considered
            or page in scheduled_pages
            or page not in detection_by_page
        ):
            continue
        left = page
        blocked = False
        while detection_by_page[left].get("may_merge_with_previous"):
            previous = left - 1
            if previous not in selected:
                break
            if previous not in detection_by_page:
                blocked = True
                break
            if previous not in candidates:
                break
            left = previous
        if blocked:
            continue
        group = [left]
        current = left
        while True:
            following = current + 1
            if following not in selected:
                break
            if following not in detection_by_page:
                blocked = True
                break
            if (
                following in candidates
                and detection_by_page[following].get("may_merge_with_previous")
            ):
                group.append(following)
                current = following
                continue
            break
        if blocked or any(item in scheduled_pages for item in group):
            continue
        considered.update(group)
        groups.append(group)
    return groups


def static_candidate_pages(config: PipelineConfig) -> set[int]:
    blocks = read_json(config.work_dir / "docling" / "blocks.json")
    candidates = {int(page) for page in blocks.get("docling_table_pages", [])}
    selected = {int(page) for page in blocks.get("selected_pages", [])}
    with pdfplumber.open(config.pdf) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            if page_number not in selected:
                continue
            if page.find_tables() or len(page.lines) + len(page.rects) >= 20:
                candidates.add(page_number)
    return candidates


class DetectTablesTask(PipelineTask):
    name = "03-detect-tables"
    dependencies = ("02-screenshots",)

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value.update(
            {
                "model": config.model,
                "thinking": config.thinking,
                "concurrency": config.find_concurrency,
                "transport": "auto",
                "layout_version": 6,
                "log_path_serialization_version": 2,
            }
        )
        value["agent_timeout_seconds"] = config.agent_timeout_seconds
        screenshots = config.work_dir / "detection" / "screenshots.json"
        if screenshots.is_file():
            value["screenshots_sha256"] = sha256_file(screenshots)
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        screenshot_manifest = read_json(
            config.work_dir / "detection" / "screenshots.json"
        )
        root = config.work_dir / "detection"
        root.mkdir(parents=True, exist_ok=True)
        executable = config.pi_executable or find_pi()
        launcher = [str(executable)] if executable else ["npx", "--yes", PI_PACKAGE]
        batches = screenshot_manifest["batches"]
        selected_pages = [
            int(page) for page in screenshot_manifest.get("selected_pages", [])
        ]
        static_candidates = (
            static_candidate_pages(config) if config.dynamic_agent_scheduling else set()
        )
        detected_by_page: dict[int, dict[str, Any]] = {}
        scheduled_pages: set[int] = set()
        config.early_table_jobs = {} if config.dynamic_agent_scheduling else None
        config.early_table_futures = {} if config.dynamic_agent_scheduling else None

        def run_batch(batch_index: int, batch: dict[str, Any]) -> list[dict[str, Any]]:
            batch_dir = root / f"batch-{batch_index:03d}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            prompt = batch_dir / "prompt.md"
            prompt.write_text(
                detection_prompt(
                    batch["pages"], batch.get("context_pages", batch["pages"])
                ),
                encoding="utf-8",
            )
            image = config.output_dir / batch["image"]
            log = batch_dir / "pi.jsonl"
            session = batch_dir / "pi-session.jsonl"
            command = [
                *launcher,
                "--offline",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-context-files",
                "--session",
                str(session.resolve()),
                "--provider",
                "openai-codex",
                "--model",
                config.model,
                "--thinking",
                config.thinking,
                "--mode",
                "json",
                "--print",
                f"@{prompt}",
                f"@{image}",
            ]
            try:
                with (
                    pi_environment(transport="auto") as environment,
                    log.open("w", encoding="utf-8") as stdout,
                ):
                    completed = subprocess.run(
                        command,
                        cwd=batch_dir,
                        stdout=stdout,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=environment,
                        timeout=config.agent_timeout_seconds,
                        check=False,
                    )
            except subprocess.TimeoutExpired as error:
                raise TimeoutError(
                    f"Pi detection batch {batch_index} timed out after "
                    f"{config.agent_timeout_seconds:g} seconds"
                ) from error
            finally:
                if not config.keep_sessions:
                    session.unlink(missing_ok=True)
                normalize_jsonl_paths(log, config.output_dir)
            if completed.returncode:
                raise RuntimeError(
                    f"Pi detection batch {batch_index} failed: {completed.stderr.strip()}"
                )
            response = parse_json_response(last_assistant_text(log))
            actual = [int(item["page"]) for item in response["pages"]]
            expected = [int(page) for page in batch["pages"]]
            if actual != expected:
                raise ValueError(
                    f"Detection batch {batch_index} returned pages {actual}, expected {expected}"
                )
            return [normalize_image_detection(item) for item in response["pages"]]

        workers = min(config.find_concurrency, len(batches)) if batches else 1
        report_progress(
            config,
            f"Find Agent queue: {len(batches)} batches, {workers} workers",
        )
        batch_results: dict[int, list[dict[str, Any]]] = {}
        owned_executor = (
            None
            if config.agent_executor is not None
            else ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="pdf-find-agent"
            )
        )
        executor = config.agent_executor or owned_executor
        assert executor is not None

        def schedule_ready_tables() -> None:
            if not config.dynamic_agent_scheduling:
                return
            from parse_table.agent_runner import PiConfig
            from parse_table.models import ExtractionJob
            from parse_table.orchestration import run_dynamic_group
            from parse_table.prepare import prepare_job

            from .extraction_task import TABLE_EXTRACTION_TARGET

            candidates = static_candidates | select_vision_pages(
                list(detected_by_page.values())
            )
            groups = ready_candidate_groups(
                selected_pages,
                candidates,
                detected_by_page,
                scheduled_pages,
            )
            if not groups:
                report_progress(
                    config,
                    "dynamic discovery: "
                    f"table_pages={len(candidates)} scheduled={len(scheduled_pages)} "
                    f"blocked={len(candidates - scheduled_pages)}",
                )
                return
            pi_config = PiConfig(
                model=config.model,
                thinking=config.thinking,
                pi_executable=config.pi_executable,
                transport="auto",
                timeout_seconds=config.agent_timeout_seconds,
                keep_sessions=config.keep_sessions,
                artifact_root=config.output_dir,
            )
            runs = config.work_dir / "table-extraction" / "runs"
            for pages in groups:
                prepared = []
                for page in pages:
                    item = prepare_job(
                        ExtractionJob(
                            pdf=config.pdf,
                            page=page,
                            target=TABLE_EXTRACTION_TARGET,
                            job_id=f"page-{page:04d}",
                            may_merge_with_previous=bool(
                                detected_by_page[page].get(
                                    "may_merge_with_previous", False
                                )
                            ),
                        ),
                        runs,
                        pdftoppm=config.pdftoppm,
                        artifact_root=config.output_dir,
                        max_image_patches=config.table_image_max_patches,
                    )
                    config.early_table_jobs[page] = item
                    prepared.append(item)
                kind = "cross_table" if len(pages) > 1 else "table"
                label = (
                    f"pages {pages[0]}-{pages[-1]}"
                    if len(pages) > 1
                    else f"page {pages[0]}"
                )
                future = executor.submit_task(
                    kind,
                    label,
                    run_dynamic_group,
                    prepared,
                    pi_config,
                )
                config.early_table_futures[future] = pages
                scheduled_pages.update(pages)
            report_progress(
                config,
                "dynamic discovery: "
                f"table_pages={len(candidates)} scheduled={len(scheduled_pages)} "
                f"blocked={len(candidates - scheduled_pages)}",
            )
        try:
            submit_task = getattr(executor, "submit_task", None)
            futures = {
                (
                    submit_task(
                        "find",
                        f"batch {batch_index} pages {batch['pages']}",
                        run_batch,
                        batch_index,
                        batch,
                    )
                    if submit_task is not None
                    else executor.submit(run_batch, batch_index, batch)
                ): (batch_index, batch)
                for batch_index, batch in enumerate(batches, 1)
            }
            for completed_count, future in enumerate(as_completed(futures), 1):
                batch_index, batch = futures[future]
                batch_results[batch_index] = future.result()
                detected_by_page.update(
                    {int(item["page"]): item for item in batch_results[batch_index]}
                )
                schedule_ready_tables()
                report_progress(
                    config,
                    f"Find Agent tasks completed {completed_count}/{len(batches)} "
                    f"(batch {batch_index}, pages {batch['pages']})",
                )
            schedule_ready_tables()
        finally:
            if owned_executor is not None:
                owned_executor.shutdown(wait=True)

        all_pages = [
            page
            for batch_index in sorted(batch_results)
            for page in batch_results[batch_index]
        ]
        output = config.work_dir / "detection" / "table-pages.json"
        write_json(
            output, {"schema_version": 2, "model": config.model, "pages": all_pages}
        )
        detected = [int(item["page"]) for item in all_pages if item.get("has_table")]
        image_pages = [int(item["page"]) for item in all_pages if item.get("has_image")]
        return TaskResult(
            self.name,
            "completed",
            [relativize(output, config.output_dir)],
            {
                "detected_pages": detected,
                "image_pages": image_pages,
                "batch_count": len(batches),
                "workers": workers,
            },
        )


class CandidatePagesTask(PipelineTask):
    name = "04-candidate-pages"
    dependencies = ("01-docling", "03-detect-tables")

    def signature_payload(self, config: PipelineConfig) -> dict:
        value = super().signature_payload(config)
        value.update(
            {
                "confidence_threshold": config.confidence_threshold,
                "candidate_logic_version": 4,
            }
        )
        for name, path in {
            "blocks": config.work_dir / "docling" / "blocks.json",
            "detection": config.work_dir / "detection" / "table-pages.json",
        }.items():
            if path.is_file():
                value[f"{name}_sha256"] = sha256_file(path)
        return value

    def run(self, config: PipelineConfig) -> TaskResult:
        blocks = read_json(config.work_dir / "docling" / "blocks.json")
        detection = read_json(config.work_dir / "detection" / "table-pages.json")
        docling_pages = {int(page) for page in blocks.get("docling_table_pages", [])}
        vision_pages = select_vision_pages(detection["pages"])
        merge_hints = {
            str(int(item["page"])): bool(item.get("may_merge_with_previous", False))
            for item in detection["pages"]
        }
        heuristic_pages: set[int] = set()
        heuristic_details: dict[str, Any] = {}
        selected_pages = set(
            int(page)
            for page in blocks.get(
                "selected_pages", range(1, int(blocks["page_count"]) + 1)
            )
        )
        with pdfplumber.open(config.pdf) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                if page_number not in selected_pages:
                    continue
                table_count = len(page.find_tables())
                line_count = len(page.lines) + len(page.rects)
                suspicious = table_count > 0 or line_count >= 20
                if suspicious:
                    heuristic_pages.add(page_number)
                heuristic_details[str(page_number)] = {
                    "pdfplumber_tables": table_count,
                    "lines_and_rects": line_count,
                    "suspicious": suspicious,
                }
        candidates = sorted(docling_pages | vision_pages | heuristic_pages)
        output = config.work_dir / "candidate-pages.json"
        write_json(
            output,
            {
                "schema_version": 1,
                "pages": candidates,
                "sources": {
                    "docling": sorted(docling_pages),
                    "vision": sorted(vision_pages),
                    "heuristic": sorted(heuristic_pages),
                },
                "may_merge_with_previous": merge_hints,
                "heuristic_details": heuristic_details,
            },
        )
        return TaskResult(
            self.name,
            "completed",
            [relativize(output, config.output_dir)],
            {"candidate_pages": candidates},
        )
