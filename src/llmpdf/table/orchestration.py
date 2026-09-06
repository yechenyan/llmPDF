from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor, as_completed
from itertools import pairwise
from math import ceil
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from .agent_runner import PiConfig, run_pi, run_prepared_job
from .io_utils import write_json
from .models import PreparedJob
from .prompt import (
    build_confirmed_group_prompt,
    build_continuation_prompt,
    build_merge_plan_prompt,
)


def parse_merge_decision(text: str) -> bool | None:
    candidates = [text.strip()]
    candidates.extend(
        re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    )
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        decision = (
            payload.get("merge_with_previous") if isinstance(payload, dict) else None
        )
        if isinstance(decision, bool):
            return decision
    match = re.search(
        r'"merge_with_previous"\s*:\s*(true|false)', text, flags=re.IGNORECASE
    )
    return match.group(1).lower() == "true" if match else None


def _json_object(text: str) -> dict | None:
    candidates = [text.strip()]
    first = text.find("{")
    last = text.rfind("}")
    if 0 <= first < last:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_merge_groups(text: str, pages: list[int]) -> list[list[int]] | None:
    payload = _json_object(text)
    raw_groups = payload.get("groups") if payload else None
    if not isinstance(raw_groups, list) or not raw_groups:
        return None
    groups: list[list[int]] = []
    for raw in raw_groups:
        if not isinstance(raw, list) or not raw:
            return None
        group = [int(page) for page in raw if isinstance(page, int)]
        if len(group) != len(raw) or any(
            right != left + 1 for left, right in pairwise(group)
        ):
            return None
        groups.append(group)
    if sorted({page for group in groups for page in group}) != pages:
        return None
    for previous, current in pairwise(groups):
        if current[0] not in {previous[-1], previous[-1] + 1}:
            return None
    counts = {page: sum(page in group for group in groups) for page in pages}
    if any(count > 2 for count in counts.values()):
        return None
    return groups


def parse_processed_pages(text: str, pages: list[int]) -> bool:
    payload = _json_object(text)
    return bool(payload and payload.get("processed_pages") == pages)


def _overview(source: Path, target: Path, max_side: int = 1600) -> Path:
    with Image.open(source) as image:
        converted = image.convert("RGB")
        converted.thumbnail((max_side, max_side))
        converted.save(target, format="PNG", optimize=True)
    return target


def _copy_group_assets(leader: PreparedJob, group: list[PreparedJob]) -> list[Path]:
    assets = leader.directory / "assets"
    overviews = []
    for current in group:
        page = current.job.page
        pdf = assets / f"page_{page:04d}.pdf"
        image = assets / f"page_{page:04d}_dynamic.png"
        page_info = assets / f"page_info_{page:04d}.json"
        if current.single_page_pdf.resolve() != pdf.resolve():
            shutil.copy2(current.single_page_pdf, pdf)
        if current.page_image.resolve() != image.resolve():
            shutil.copy2(current.page_image, image)
        source_info = current.directory / "assets" / "page_info.json"
        if source_info.resolve() != page_info.resolve():
            shutil.copy2(source_info, page_info)
        overviews.append(_overview(image, assets / f"page_{page:04d}_overview.png"))
    return overviews


def build_contact_sheets(
    overviews: list[Path], pages: list[int], assets: Path
) -> list[Path]:
    sheets = []
    columns = 3
    pages_per_sheet = 12
    thumb_size = (500, 650)
    padding = 12
    label_height = 24
    for sheet_index in range(0, len(overviews), pages_per_sheet):
        chunk = list(
            zip(
                pages[sheet_index : sheet_index + pages_per_sheet],
                overviews[sheet_index : sheet_index + pages_per_sheet],
                strict=True,
            )
        )
        thumbnails = []
        for page, path in chunk:
            with Image.open(path) as image:
                thumbnail = image.convert("RGB")
                thumbnail.thumbnail(thumb_size, Image.Resampling.LANCZOS)
            thumbnails.append((page, thumbnail))
        cell_height = max(image.height for _page, image in thumbnails) + label_height
        rows = ceil(len(thumbnails) / columns)
        sheet = Image.new(
            "RGB",
            (
                columns * thumb_size[0] + (columns + 1) * padding,
                rows * cell_height + (rows + 1) * padding,
            ),
            "white",
        )
        draw = ImageDraw.Draw(sheet)
        for index, (page, thumbnail) in enumerate(thumbnails):
            row, column = divmod(index, columns)
            x = padding + column * (thumb_size[0] + padding)
            y = padding + row * (cell_height + padding)
            draw.text((x, y), f"Page {page}", fill="black")
            sheet.paste(thumbnail, (x, y + label_height))
        target = assets / f"merge-plan-contact-{sheet_index // pages_per_sheet + 1:03d}.png"
        sheet.save(target, format="PNG", optimize=True)
        sheets.append(target)
    return sheets


def plan_long_chain(
    chain: list[PreparedJob], config: PiConfig
) -> list[list[PreparedJob]]:
    leader = chain[0]
    pages = [item.job.page for item in chain]
    overviews = _copy_group_assets(leader, chain)
    contact_sheets = build_contact_sheets(
        overviews, pages, leader.directory / "assets"
    )
    prompt = leader.directory / "merge_plan_prompt.md"
    prompt.write_text(build_merge_plan_prompt(pages), encoding="utf-8")
    work = leader.directory / "merge-plan-agent-output"
    work.mkdir(exist_ok=True)
    session = leader.directory / "merge-plan-session.jsonl"
    try:
        result = run_pi(
            job_dir=leader.directory,
            working_dir=work,
            prompt=prompt,
            attachments=contact_sheets,
            log=leader.directory / "merge_plan_pi.jsonl",
            session=session,
            config=config,
        )
    finally:
        if not config.keep_sessions:
            session.unlink(missing_ok=True)
    page_groups = (
        None
        if result["returncode"]
        else parse_merge_groups(result["last_message"], pages)
    )
    if page_groups is None:
        raise RuntimeError("Merge planner returned invalid overlapping page groups")
    by_page = {item.job.page: item for item in chain}
    groups = [[by_page[page] for page in group] for group in page_groups]
    write_json(
        leader.directory / "merge_plan.json",
        {"pages": pages, "groups": page_groups, "result": result},
    )
    for previous, current in pairwise(pages):
        decision = any(previous in group and current in group for group in page_groups)
        target = by_page[current].directory / "continuation_decision.json"
        write_json(target, {"page": current, "merge_with_previous": decision})
    return groups


def _continuation_assets(
    leader: PreparedJob, current: PreparedJob
) -> tuple[Path, list[Path]]:
    page = current.job.page
    assets = leader.directory / "assets"
    pdf = assets / f"page_{page:04d}.pdf"
    image = assets / f"page_{page:04d}_dynamic.png"
    page_info = assets / f"page_info_{page:04d}.json"
    shutil.copy2(current.single_page_pdf, pdf)
    shutil.copy2(current.page_image, image)
    shutil.copy2(current.directory / "assets" / "page_info.json", page_info)
    prompts = leader.directory / "continuations"
    prompts.mkdir(exist_ok=True)
    prompt = prompts / f"page_{page:04d}.md"
    prompt.write_text(build_continuation_prompt(), encoding="utf-8")
    return prompt, [pdf, image, page_info]


def run_prepared_chain(
    prepared: list[PreparedJob], config: PiConfig
) -> dict[str, dict]:
    if not prepared:
        return {}
    results: dict[str, dict] = {}
    leader = prepared[0]
    initial = run_prepared_job(leader.directory, config)
    results[leader.job.id] = initial
    if initial["returncode"]:
        return results

    for current in prepared[1:]:
        prompt, attachments = _continuation_assets(leader, current)
        session = current.directory / "continuation-session.jsonl"
        try:
            probe = run_pi(
                job_dir=current.directory,
                working_dir=leader.agent_output,
                prompt=prompt,
                attachments=attachments,
                log=current.directory / "continuation_pi.jsonl",
                session=session,
                config=config,
            )
        finally:
            if not config.keep_sessions:
                session.unlink(missing_ok=True)
        decision = parse_merge_decision(str(probe.get("last_message", "")))
        probe["merge_with_previous"] = decision
        results[current.job.id] = probe
        if probe["returncode"]:
            break
        if decision is True:
            continue

        fresh = run_prepared_job(current.directory, config)
        fresh["continuation_probe"] = probe
        results[current.job.id] = fresh
        leader = current
        if fresh["returncode"]:
            break
    return results


def confirmed_group_attachments(page_images: list[Path]) -> list[Path]:
    if len(page_images) < 2:
        raise ValueError(
            "A confirmed multi-page group requires at least two page images"
        )
    return list(dict.fromkeys((page_images[0], page_images[1], page_images[-1])))


def _boundary_bboxes(output_dir: Path, page: int) -> list[tuple[float, float, float, float]]:
    bboxes = []
    for metadata_path in sorted(output_dir.glob("table_*/metadata.yaml")):
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        value = (metadata.get("page_bboxes") or {}).get(str(page))
        if value:
            bboxes.append(
                tuple(float(value[key]) for key in ("x0", "top", "x1", "bottom"))
            )
    if not bboxes:
        raise RuntimeError(f"No extracted bbox found for boundary page {page}")
    return bboxes


def redact_prepared_page(
    prepared: PreparedJob,
    bboxes: list[tuple[float, float, float, float]],
) -> None:
    """Mark consumed regions in the task image while preserving the source PDF."""
    page_info = json.loads(
        (prepared.directory / "assets" / "page_info.json").read_text(encoding="utf-8")
    )
    scale_x = float(page_info["scale_x"])
    scale_y = float(page_info["scale_y"])
    with Image.open(prepared.page_image) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=24)
    for x0, top, x1, bottom in bboxes:
        rectangle = (x0 * scale_x, top * scale_y, x1 * scale_x, bottom * scale_y)
        draw.rectangle(
            rectangle,
            fill="#e5e5e5",
            outline="#666666",
            width=2,
        )
        draw.text(
            (rectangle[0] + 8, rectangle[1] + 8),
            "IGNORE",
            fill="#333333",
            font=font,
        )
    image.save(prepared.page_image, format="PNG", optimize=True)
    write_json(
        prepared.directory / "redaction.json",
        {"page": prepared.job.page, "bboxes": bboxes},
    )


def run_confirmed_group(group: list[PreparedJob], config: PiConfig) -> dict[str, dict]:
    if len(group) <= 1:
        item = group[0]
        return {item.job.id: run_prepared_job(item.directory, config)}
    leader = group[0]
    results: dict[str, dict] = {}
    pages = [item.job.page for item in group]
    _copy_group_assets(leader, group)
    page_infos = [
        json.loads(
            (leader.directory / "assets" / f"page_info_{page:04d}.json").read_text(
                encoding="utf-8"
            )
        )
        for page in pages
    ]
    prompt = leader.directory / f"confirmed_group_{pages[0]:04d}_{pages[-1]:04d}.md"
    prompt.write_text(
        build_confirmed_group_prompt(pages, leader.job.target, page_infos),
        encoding="utf-8",
    )
    # The first parse request receives the confirmed group and both visual
    # endpoints. Every group page remains available under assets.
    page_images = [
        leader.directory / "assets" / f"page_{page:04d}_dynamic.png" for page in pages
    ]
    attachments = confirmed_group_attachments(page_images)
    probe = run_pi(
        job_dir=leader.directory,
        working_dir=leader.agent_output,
        prompt=prompt,
        attachments=attachments,
        log=leader.directory / "confirmed_group_pi.jsonl",
        session=leader.directory / "pi-session.jsonl",
        config=config,
    )
    probe["pages"] = pages
    if not probe["returncode"] and not parse_processed_pages(
        probe["last_message"], pages
    ):
        probe["returncode"] = 2
        probe["stderr"] = (
            str(probe.get("stderr", "")) + "\nInvalid processed_pages result"
        ).strip()
    metrics = leader.directory / "confirmed_group_pi_metrics.json"
    write_json(metrics, probe)
    results[leader.job.id] = probe
    for current in group[1:]:
        results[current.job.id] = {
            "id": current.job.id,
            "returncode": probe["returncode"],
            "group_parent": leader.job.id,
            "pages": pages,
        }
    return results


def run_dynamic_group(
    group: list[PreparedJob], config: PiConfig
) -> dict[str, dict]:
    """Plan a candidate chain, then extract overlapping groups in order."""
    if len(group) == 1:
        return run_prepared_chain(group, config)

    planned = plan_long_chain(group, config)
    results: dict[str, dict] = {}
    for index, confirmed in enumerate(planned):
        callback = run_confirmed_group if len(confirmed) > 1 else run_prepared_chain
        current_results = callback(confirmed, config)
        results.update(current_results)
        if any(int(result.get("returncode", 0)) for result in current_results.values()):
            return results
        if len(confirmed) == 1:
            continue

        boundary = confirmed[-1]
        bboxes = _boundary_bboxes(confirmed[0].agent_output, boundary.job.page)
        redact_prepared_page(boundary, bboxes)
        next_group = planned[index + 1] if index + 1 < len(planned) else None
        if next_group and next_group[0].job.page == boundary.job.page:
            continue
        cleanup_results = run_prepared_chain([boundary], config)
        results.update(cleanup_results)
        if any(int(result.get("returncode", 0)) for result in cleanup_results.values()):
            return results
    return results


def group_prepared_jobs(prepared: list[PreparedJob]) -> list[list[PreparedJob]]:
    chains: list[list[PreparedJob]] = []
    for item in prepared:
        previous = chains[-1][-1] if chains else None
        continues = bool(
            previous
            and item.job.may_merge_with_previous
            and item.job.pdf == previous.job.pdf
            and item.job.page == previous.job.page + 1
        )
        if continues:
            chains[-1].append(item)
        else:
            chains.append([item])
    return chains


def prioritize_parse_groups(
    planned: list[tuple[list[PreparedJob], bool]],
) -> list[tuple[list[PreparedJob], bool]]:
    def priority(item: tuple[list[PreparedJob], bool]) -> tuple[int, int, int]:
        group, confirmed = item
        if confirmed and len(group) > 1:
            category = 0
        elif len(group) == 2:
            category = 1
        else:
            category = 2
        return category, -len(group), group[0].job.page

    return sorted(planned, key=priority)


def preflight_python(python: Path) -> None:
    subprocess.run(
        [
            str(python),
            "-c",
            "import llmpdf.table.run_all, pdfplumber, pypdf, yaml",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def run_prepared_jobs(
    prepared: list[PreparedJob],
    config: PiConfig,
    concurrency: int,
    *,
    executor: Executor | None = None,
    trailing_jobs: list[tuple[str, Callable[[], object]]] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict:
    if not prepared:
        raise ValueError("No prepared jobs to run")
    preflight_python(prepared[0].directory / "tools" / "python")
    started = time.time()
    results: dict[str, object] = {}
    chains = group_prepared_jobs(prepared)
    planned = prioritize_parse_groups([(chain, False) for chain in chains])
    workers = min(max(1, concurrency), len(prepared))
    owned_executor = (
        ThreadPoolExecutor(max_workers=workers) if executor is None else None
    )
    queue = owned_executor or executor
    assert queue is not None

    def submit_task(kind: str, label: str, callback, *args):
        prioritized = getattr(queue, "submit_task", None)
        if prioritized is not None:
            return prioritized(kind, label, callback, *args)
        return queue.submit(callback, *args)

    try:
        # The pipeline scheduler selects by task type at dispatch time. A plain
        # Executor remains supported for the standalone llmpdf-table command.
        table_futures: dict[Future, list[PreparedJob]] = {}
        for group, _confirmed in planned:
            cross_table = len(group) > 1
            kind = "cross_table" if cross_table else "table"
            pages = [item.job.page for item in group]
            label = (
                f"pages {pages[0]}-{pages[-1]}"
                if len(pages) > 1
                else f"page {pages[0]}"
            )
            future = submit_task(
                kind,
                label,
                run_dynamic_group,
                group,
                config,
            )
            table_futures[future] = group
        trailing_futures = {
            identifier: submit_task("image", identifier, callback)
            for identifier, callback in (trailing_jobs or [])
        }
        for completed_count, future in enumerate(as_completed(table_futures), 1):
            results.update(future.result())
            if progress_callback is not None:
                progress_callback("table", completed_count, len(table_futures))
        if trailing_futures:
            if owned_executor is None:
                results["trailing_job_futures"] = trailing_futures
            else:
                results["trailing_job_results"] = {
                    identifier: future.result()
                    for identifier, future in trailing_futures.items()
                }
        results["parallel_wall_seconds"] = time.time() - started
        results["candidate_chain_count"] = len(chains)
        results["parse_group_count"] = len(planned)
        return results
    finally:
        if owned_executor is not None:
            owned_executor.shutdown(wait=True)
        # Sessions are useful only while one PDF is being processed. Logs and
        # metrics remain, but full conversation histories are removed.
        if not config.keep_sessions:
            for item in prepared:
                (item.directory / "pi-session.jsonl").unlink(missing_ok=True)
                (item.directory / "merge-plan-session.jsonl").unlink(missing_ok=True)
