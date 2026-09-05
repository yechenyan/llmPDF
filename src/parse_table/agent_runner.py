from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import WORKFLOW_VERSION
from .io_utils import normalize_jsonl_paths, relative_reference, write_json
from .pi_runtime import PI_PACKAGE, find_pi
from .pi_runtime import pi_environment as shared_pi_environment


@dataclass(frozen=True)
class PiConfig:
    model: str = "gpt-5.6-sol"
    thinking: str = "medium"
    pi_executable: Path | None = None
    agent_dir: Path | None = None
    transport: str = "auto"
    timeout_seconds: float = 1800.0
    keep_sessions: bool = False
    artifact_root: Path | None = None


@contextmanager
def pi_environment(config: PiConfig):
    with shared_pi_environment(config.agent_dir, config.transport) as environment:
        yield environment


def find_cached_pi_executable(package: str = PI_PACKAGE) -> Path | None:
    return find_pi(package)


def content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def parse_log(log_path: Path) -> tuple[dict[str, int | None], int, str, dict[str, int]]:
    usage: dict[str, int | None] = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "noncached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": None,
    }
    tool_calls = 0
    event_types: dict[str, int] = {}
    last_message = ""
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(event.get("type", ""))
        event_types[event_type] = event_types.get(event_type, 0) + 1
        if event_type in {"tool_execution_start", "tool_call"}:
            tool_calls += 1
        if event_type != "message_end":
            continue
        message = event.get("message", {})
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        found = message.get("usage", {})
        if isinstance(found, dict):
            noncached = int(found.get("input", found.get("inputTokens", 0)) or 0)
            cached = int(found.get("cacheRead", found.get("cachedInput", 0)) or 0)
            usage["input_tokens"] = int(usage["input_tokens"] or 0) + noncached + cached
            usage["noncached_input_tokens"] = (
                int(usage["noncached_input_tokens"] or 0) + noncached
            )
            usage["cached_input_tokens"] = (
                int(usage["cached_input_tokens"] or 0) + cached
            )
            usage["cache_write_input_tokens"] = int(
                usage["cache_write_input_tokens"] or 0
            ) + int(found.get("cacheWrite", 0) or 0)
            usage["output_tokens"] = int(usage["output_tokens"] or 0) + int(
                found.get("output", found.get("outputTokens", 0)) or 0
            )
        text = content_text(message.get("content"))
        if text:
            last_message = text
    return usage, tool_calls, last_message, event_types


def run_pi(
    *,
    job_dir: Path,
    working_dir: Path,
    prompt: Path,
    attachments: list[Path],
    log: Path,
    session: Path,
    config: PiConfig,
) -> dict:
    job_dir = job_dir.resolve()
    manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    executable = config.pi_executable or find_cached_pi_executable()
    launcher = (
        [str(executable)] if executable is not None else ["npx", "--yes", PI_PACKAGE]
    )
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
        *(f"@{path}" for path in attachments),
    ]
    started = time.time()
    timed_out = False
    try:
        with pi_environment(config) as environment:
            package_root = str(Path(__file__).resolve().parents[1])
            existing_python_path = environment.get("PYTHONPATH", "")
            environment["PYTHONPATH"] = os.pathsep.join(
                value for value in (package_root, existing_python_path) if value
            )
            with log.open("w", encoding="utf-8") as stdout:
                completed = subprocess.run(
                    command,
                    cwd=working_dir,
                    stdout=stdout,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                    timeout=config.timeout_seconds,
                    check=False,
                )
        returncode = completed.returncode
        stderr = completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        returncode = 124
        stderr = f"Pi Agent timed out after {config.timeout_seconds:g} seconds"
        if error.stderr:
            detail = (
                error.stderr.decode(errors="replace")
                if isinstance(error.stderr, bytes)
                else str(error.stderr)
            )
            stderr = f"{stderr}: {detail.strip()}"
    artifact_root = (
        config.artifact_root.resolve()
        if config.artifact_root is not None
        else job_dir.parent
    )
    normalize_jsonl_paths(log, artifact_root)
    elapsed = time.time() - started
    usage, tool_calls, last_message, event_types = parse_log(log)
    (job_dir / "last_message.md").write_text(last_message + "\n", encoding="utf-8")
    result = {
        "id": manifest["id"],
        "workflow_version": WORKFLOW_VERSION,
        "elapsed_seconds": elapsed,
        "returncode": returncode,
        "stderr": stderr,
        "timed_out": timed_out,
        "timeout_seconds": config.timeout_seconds,
        "usage": usage,
        "tool_calls": tool_calls,
        "event_types": event_types,
        "last_message": last_message,
        "provider": "openai-codex",
        "model": config.model,
        "thinking": config.thinking,
        "transport": config.transport,
        "pi_package": PI_PACKAGE,
        "pi_executable": (
            relative_reference(executable, artifact_root)
            if executable is not None
            else None
        ),
        "job_dir": relative_reference(job_dir, artifact_root),
    }
    metrics_name = (
        "metrics.json" if log.name == "pi.jsonl" else f"{log.stem}_metrics.json"
    )
    write_json(job_dir / metrics_name, result)
    return result


def run_prepared_job(
    job_dir: Path, config: PiConfig, *, keep_session: bool = False
) -> dict:
    job_dir = job_dir.resolve()
    manifest = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    page = int(manifest["page"])
    session = job_dir / "pi-session.jsonl"
    try:
        return run_pi(
            job_dir=job_dir,
            working_dir=job_dir / "agent-output",
            prompt=job_dir / "prompt.md",
            attachments=[job_dir / "assets" / f"page_{page:04d}_dynamic.png"],
            log=job_dir / "pi.jsonl",
            session=session,
            config=config,
        )
    finally:
        if not keep_session and not config.keep_sessions:
            session.unlink(missing_ok=True)
