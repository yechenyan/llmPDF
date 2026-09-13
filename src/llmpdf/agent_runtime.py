"""Pi-first execution bridge; Claude Code emits a Pi-compatible final message.

Claude's raw response is retained separately. Usage is deliberately omitted:
existing Pi accounting treats these calls as having no reported usage.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
from pathlib import Path
from typing import Any, Callable

from .table.io_utils import normalize_host_paths


def run_agent(
    command: list[str], *, config: Any,
    runner: Callable[..., Any] = subprocess.run, **kwargs: Any,
) -> Any:
    backend = config.agent_backend
    if backend == "pi":
        return runner(command, **kwargs)
    if backend != "claude-code":
        raise ValueError(f"Unknown agent backend: {backend}")

    # Keep the Pi request as the common contract, including its attachment order.
    prompt_index = command.index("--print") + 1
    references = command[prompt_index:]
    prompt = Path(references[0].removeprefix("@"))
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt.read_text(encoding="utf-8")}]
    for reference in references[1:]:
        path = Path(reference.removeprefix("@"))
        media_type = mimetypes.guess_type(path.name)[0]
        if media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise ValueError(f"Unsupported Claude image attachment: {path}")
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media_type,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }})
    model = command[command.index("--model") + 1]
    # Existing defaults belong to Pi. Let Claude choose its own default model.
    executable = (
        str(Path(config.claude_executable).expanduser().resolve())
        if config.claude_executable is not None else "claude"
    )
    claude_command = [executable, "-p",
                      "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                      "--permission-mode", "dontAsk"]
    if model not in {"gpt-5.6-sol", "gpt-5.6-terra"}:
        claude_command.extend(["--model", model])
    # An explicit settings source avoids inheriting user/project permission rules.
    claude_command.extend(["--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'])
    if "--no-tools" in command:
        claude_command.extend(["--tools", ""])
    else:
        tools = "Read,Write,Edit,Bash,Glob,Grep"
        claude_command.extend(["--tools", tools, "--allowedTools", tools,
                               "--add-dir", str(Path(kwargs["cwd"]).resolve().parent)])
    if not config.keep_sessions:
        claude_command.append("--no-session-persistence")
    request = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
    stdout = kwargs.pop("stdout")
    completed = runner(claude_command, input=request, stdout=subprocess.PIPE, **kwargs)
    artifact_root = getattr(config, "artifact_root", None) or getattr(config, "output_dir", None)
    artifact_root = Path(artifact_root or Path(kwargs["cwd"]).resolve().parent)
    extra_paths = (Path(executable).parent,) if config.claude_executable is not None else ()
    raw_output = normalize_host_paths(completed.stdout or "", artifact_root, extra_paths)
    completed.stderr = normalize_host_paths(completed.stderr or "", artifact_root, extra_paths)
    raw_path = Path(stdout.name).with_suffix(".claude.json")
    raw_path.write_text(raw_output, encoding="utf-8")
    try:
        events = [json.loads(line) for line in raw_output.splitlines() if line.strip()]
        response = next((event for event in reversed(events)
                         if isinstance(event, dict) and event.get("type") == "result"), None)
        if not isinstance(response, dict):
            raise ValueError("Claude returned no final result")
        if response.get("is_error") or response.get("type") != "result":
            raise ValueError(str(response.get("result") or response.get("errors") or response))
        text = response.get("result", "")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Claude returned no final text")
    except (ValueError, TypeError) as error:
        return subprocess.CompletedProcess(claude_command, completed.returncode or 1,
                                           stderr=f"{completed.stderr or ''}\nClaude Code: {error}")
    stdout.write(json.dumps({"type": "message_end", "message": {
        "role": "assistant", "content": [{"type": "text", "text": text}],
    }, "agent_backend": "claude-code", "session_id": response.get("session_id")}) + "\n")
    stdout.flush()
    return completed
